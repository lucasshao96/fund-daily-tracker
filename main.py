#!/usr/bin/env python3
"""场外基金每日智能分析系统"""

import json, os, re, smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

import requests

# ─── 配置 ───────────────────────────────────────────────────
def load_funds():
    """从 FUND_LIST 环境变量解析基金列表。格式: 代码,代码,..."""
    raw = os.getenv("FUND_LIST") or "016701,008254,002891,539002"
    codes = [c.strip() for c in raw.split(",") if c.strip()]
    return [(c, c) for c in codes]  # name 由 API 自动填充

ANSPIRE_KEY = os.getenv("ANSPIRE_API_KEYS") or ""
EMAIL_SENDER = os.getenv("EMAIL_SENDER") or ""
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD") or ""
EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER") or ""
EMAIL_SMTP = os.getenv("EMAIL_SMTP") or "smtp.qq.com"
EMAIL_PORT = int(os.getenv("EMAIL_PORT") or "587")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


# ─── 宏观市场 ───────────────────────────────────────────────
def fetch_macro():
    """抓取全球主要指数 — 使用 yfinance（GitHub Actions 美国机房友好）"""
    import yfinance as yf
    symbols = {
        "上证指数": "000001.SS",
        "深证成指": "399001.SZ",
        "创业板指": "399006.SZ",
        "恒生指数": "^HSI",
        "纳斯达克": "^IXIC",
        "标普500": "^GSPC",
    }
    result = {}
    try:
        data = yf.download(
            " ".join(symbols.values()),
            period="2d", progress=False, timeout=15
        )
        for name, sym in symbols.items():
            try:
                last = data["Close"][sym].dropna()
                if len(last) >= 2:
                    prev, now = last.iloc[-2], last.iloc[-1]
                    result[name] = {"price": round(float(now), 2),
                                    "pct": round(float((now - prev) / prev * 100), 2)}
                elif len(last) >= 1:
                    result[name] = {"price": round(float(last.iloc[-1]), 2), "pct": "?"}
            except Exception:
                result[name] = {"price": "?", "pct": "?"}
    except Exception as e:
        result["_error"] = str(e)[:80]

    # 汇率
    try:
        fx = yf.download("CNY=X", period="2d", progress=False, timeout=10)
        last = fx["Close"]["CNY=X"].dropna()
        if len(last) >= 2:
            prev, now = last.iloc[-2], last.iloc[-1]
            result["美元人民币"] = {"price": round(float(now), 4),
                                   "pct": round(float((now - prev) / prev * 100), 2)}
    except Exception:
        result["美元人民币"] = {"price": "?", "pct": "?"}
    return result


# ─── 基金数据 ───────────────────────────────────────────────
def fetch_fund(code, name):
    """获取基金净值 + 历史 + 基本信息"""
    result = {"code": code, "name": name, "nav": None, "est_nav": None, "est_pct": "0",
              "date": "", "history": [], "manager": "", "type": "", "company": "",
              "size": "", "holdings_pct": "", "bonds_pct": ""}

    h = {"Referer": "http://fund.eastmoney.com/", "User-Agent": UA}

    # 1. 实时估值 (fundgz — 稳定)
    try:
        r = requests.get(f"http://fundgz.1234567.com.cn/js/{code}.js", headers=h, timeout=15)
        m = re.search(r"jsonpgz\((.+)\)", r.text)
        if m:
            d = json.loads(m.group(1))
            result.update(name=d.get("name", name), nav=float(d.get("dwjz", 0) or 0),
                          est_nav=float(d.get("gsz", 0) or 0), est_pct=d.get("gszzl", "0"),
                          date=d.get("jzrq", ""))
    except Exception:
        return result

    # 2. 历史净值 (api.fund.eastmoney.com — 稳定)
    try:
        all_records = []
        for page in range(1, 25):
            r = requests.get("https://api.fund.eastmoney.com/f10/lsjz", params={
                "fundCode": code, "pageIndex": page, "pageSize": 20,
            }, headers={**h, "Referer": f"https://fundf10.eastmoney.com/jjjz_{code}.html"}, timeout=15)
            records = r.json().get("Data", {}).get("LSJZList", [])
            if not records: break
            all_records.extend(records)
        result["history"] = [
            {"date": x["FSRQ"], "nav": float(x["DWJZ"])}
            for x in reversed(all_records)   # API 返回新→旧，翻转为旧→新
        ]
    except Exception:
        pass

    # 3. 基本信息 (pingzhongdata — 可能不稳定，静默降级)
    try:
        r = requests.get(f"http://fund.eastmoney.com/pingzhongdata/{code}.js", headers=h, timeout=15)
        text = r.text
        for key, pat in [
            ("type", r'fS_typename\s*=\s*"([^"]+)"'),
            ("company", r'fS_orgname\s*=\s*"([^"]+)"'),
        ]:
            m = re.search(pat, text)
            if m: result[key] = m.group(1)

        # 基金经理
        m = re.search(r'Data_currentFundManager\s*=\s*(\[.+?\]);', text, re.DOTALL)
        if m:
            mgr = json.loads(m.group(1))
            if mgr: result["manager"] = mgr[0].get("name", "")

        # 持仓
        m = re.search(r'Data_fundSharesPositions\s*=\s*(\[.+?\]);', text, re.DOTALL)
        if m:
            pos = json.loads(m.group(1))
            result["top_holdings"] = [
                {"code": s.get("GPDM", "?"), "pct": s.get("JZBL", "?")}
                for s in pos[:10] if isinstance(pos, list)
            ]
        # 资产配置
        m = re.search(r'Data_assetAllocation\s*=\s*(\[.+?\]);', text, re.DOTALL)
        if m:
            alloc = json.loads(m.group(1))
            if alloc:
                latest = alloc[-1]
                result["holdings_pct"] = str(latest.get("zq", ""))[:6]  # 股票占比
    except Exception:
        pass

    # 4. 规模
    try:
        r = requests.get(
            "https://api.fund.eastmoney.com/f10/jbgk",
            params={"fundCode": code},
            headers={**h, "Referer": f"https://fundf10.eastmoney.com/jbgk_{code}.html"}, timeout=10,
        )
        d = r.json().get("Data", {})
        result["size"] = d.get("scl", "") or d.get("zzfe", "") or ""
        if not result["type"]:
            result["type"] = d.get("jjlx", "")
    except Exception:
        pass

    return result


# ─── 分析 ───────────────────────────────────────────────────
def calc_perf(history):
    """多周期收益"""
    if len(history) < 5: return {}
    # history: oldest→newest
    now = history[-1]["nav"]
    result = {}
    for label, days in [("7天", 7), ("1月", 22), ("3月", 66), ("6月", 132), ("1年", 264)]:
        if len(history) > days:
            result[label] = round((now - history[-(days+1)]["nav"]) / history[-(days+1)]["nav"] * 100, 2)
    # YTD
    yr = str(datetime.now().year)
    for i, h in enumerate(history):
        if h["date"].startswith(yr):
            result["今年以来"] = round((now - h["nav"]) / h["nav"] * 100, 2)
            break
    return result


def calc_dd(history):
    """最大回撤"""
    if len(history) < 10: return None
    peak, dd = 0, 0
    for h in history:
        if h["nav"] > peak: peak = h["nav"]
        d = (peak - h["nav"]) / peak * 100
        if d > dd: dd = d
    return round(dd, 2)


def trend(fund):
    """评分"""
    p = calc_perf(fund["history"])
    est = float(fund.get("est_pct") or 0)
    w, m = p.get("7天") or 0, p.get("1月") or 0
    s = 50
    if est > 0: s += 10
    if est > 0.5: s += 5
    if w > 0: s += 8
    if w > 1: s += 5
    if m > 0: s += 8
    if m > 3: s += 5
    if m > 5: s += 3
    if est < -0.3: s -= 10
    if w < -1: s -= 10
    if m < -3: s -= 15
    s = max(0, min(100, s))

    if s >= 70: a, o = "定投加仓", "看多"
    elif s >= 50: a, o = "正常定投", "震荡偏多"
    elif s >= 35: a, o = "减少定投", "震荡偏空"
    else: a, o = "暂停定投", "看空"

    risks, conds = [], []
    dd = calc_dd(fund["history"])
    if dd and dd > 30: risks.append(f"最大回撤 {dd}%")
    if m and m > 8: risks.append("近1月涨幅较大")
    if fund.get("type") and "QDII" in fund.get("type", ""): risks.append("汇率风险")
    if m and m < 0: conds.append("观察近1月是否止跌")
    if w and w < -2: conds.append("观察超跌反弹信号")

    return {"action": a, "outlook": o, "score": s, "risks": risks, "conditions": conds}


# ─── 报告 ───────────────────────────────────────────────────
def gen_report(funds, macro):
    today = datetime.now().strftime("%Y-%m-%d")
    wd = ["一","二","三","四","五","六","日"][datetime.now().weekday()]
    buy = sum(1 for f in funds if f.get("t",{}).get("score",0) >= 70)
    watch = sum(1 for f in funds if 35 <= f.get("t",{}).get("score",0) < 70)
    sell = sum(1 for f in funds if f.get("t",{}).get("score",0) < 35)

    L = [f"🎯 {today} 周{wd} 基金决策仪表盘",
         f"共{len(funds)}只 | 🟢加仓:{buy} 🟡观望:{watch} 🔴暂停:{sell}", ""]

    # 宏观 — 仅在有数据时展示
    has_macro = macro and not macro.get("_error") and any(
        isinstance(macro.get(n, {}).get("pct"), (int, float))
        for n in ["上证指数", "纳斯达克"]
    )
    if has_macro:
        L.append("🌍 全球市场")
        L.append("| 指数 | 最新 | 涨跌 |")
        L.append("|------|------|------|")
        for n in ["上证指数","深证成指","创业板指","恒生指数","纳斯达克","标普500","美元人民币"]:
            d = macro.get(n, {})
            pct = d.get("pct","?")
            sign = "🟢" if (isinstance(pct,(int,float)) and pct>0) else ("🔴" if (isinstance(pct,(int,float)) and pct<0) else "")
            L.append(f"| {n} | {d.get('price','?')} | {sign} {pct}% |")
        L.append("")

        us = macro.get("纳斯达克",{}).get("pct",0) or 0
        cn = macro.get("上证指数",{}).get("pct",0) or 0
        fx = macro.get("美元人民币",{}).get("price",7.2)
        L.append("📌 市场环境")
        if isinstance(us,(int,float)) and us>0.3: L.append("  ✅ 美股偏强，QDII有利")
        elif isinstance(us,(int,float)) and us<-0.3: L.append("  ⚠️ 美股承压，QDII谨慎")
        if isinstance(cn,(int,float)) and cn>0.2: L.append("  ✅ A股偏暖")
        elif isinstance(cn,(int,float)) and cn<-0.2: L.append("  ⚠️ A股偏弱")
        if isinstance(fx,(int,float)) and fx>7.3: L.append("  💱 人民币偏弱，QDII有汇兑收益")
        L.append("")

    L.append("📊 摘要")
    for f in funds:
        t = f.get("t",{})
        s = t.get("score",0)
        e = "🟢" if s>=70 else ("🟡" if s>=50 else ("🟠" if s>=35 else "🔴"))
        L.append(f"{e} {f['name']}({f['code']}): {t.get('action','?')} | {s}分 | {t.get('outlook','?')}")
    L.append("")

    # 每只基金
    for f in funds:
        t = f.get("t",{})
        p = calc_perf(f["history"])
        dd = calc_dd(f["history"])

        L.append("=" * 55)
        L.append(f"### {f['name']} ({f['code']})")
        L.append("")

        # 信息
        info = []
        if f.get("type"): info.append(f["type"])
        if f.get("company"): info.append(f["company"])
        if f.get("manager"): info.append(f"经理: {f['manager']}")
        if f.get("size"): info.append(f"规模: {f['size']}")
        if info: L.append(" | ".join(info))
        L.append("")

        # 行情
        L.append(f"净值: {f['nav']} | 估算: {f.get('est_nav','?')} | 涨跌: {f.get('est_pct','?')}% | 日期: {f['date']}")
        L.append("")

        # 收益
        peri = ["7天","1月","3月","6月","1年","今年以来"]
        vals = [f"{p.get(k,'?')}%" if p.get(k) is not None else "?" for k in peri]
        L.append("📊 收益: " + " | ".join(f"{k} {v}" for k,v in zip(peri, vals)))
        L.append("")

        # 风险
        risk_items = []
        if dd: risk_items.append(f"最大回撤 {dd}%")
        if f.get("holdings_pct"): risk_items.append(f"股票仓位 {f['holdings_pct']}%")
        if risk_items: L.append("⚠️ 风险: " + " | ".join(risk_items))
        L.append("")

        # 决策
        L.append(f"🎯 动作: {t.get('action','?')} | 评分: {t.get('score','?')}/100 | {t.get('outlook','?')}")
        if t.get("risks"): L.append(f"  风险: {'; '.join(t['risks'])}")
        if t.get("conditions"): L.append(f"  观察: {'; '.join(t['conditions'])}")
        L.append("")

        # 持仓
        if f.get("top_holdings"):
            L.append("🏢 重仓: " + " | ".join(f"{h['code']} {h['pct']}%" for h in f["top_holdings"][:8]))
            L.append("")

        # 建议
        s = t.get("score", 50)
        if s >= 70: L.append("💡 趋势向好，可适当加大定投，分批介入勿追高")
        elif s >= 50: L.append("💡 趋势平稳，维持正常定投，密切关注走势")
        elif s >= 35: L.append("💡 趋势偏弱，建议减少定投，等企稳再恢复")
        else: L.append("💡 趋势走弱，建议暂停定投，保留现金等更好时机")
        L.append("")

    L.append("=" * 55)
    L.append(f"> {datetime.now().strftime('%H:%M:%S')} | 天天基金 | 仅供参考")
    return "\n".join(L)


# ─── 邮件 ───────────────────────────────────────────────────
def send_email(subj, body):
    if not all([EMAIL_SENDER, EMAIL_PASSWORD, EMAIL_RECEIVER]):
        print(body); return False
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = subj, EMAIL_SENDER, EMAIL_RECEIVER
    msg.attach(MIMEText(body, "plain", "utf-8"))
    try:
        with smtplib.SMTP(EMAIL_SMTP, EMAIL_PORT, timeout=15) as s:
            s.ehlo(); s.starttls(); s.ehlo()
            s.login(EMAIL_SENDER, EMAIL_PASSWORD); s.send_message(msg)
        print("✅ 已发送"); return True
    except Exception as e:
        print(f"❌ {e}\n{body}"); return False


def main():
    print("📊 基金分析\n")
    fund_list = load_funds()
    print("  宏观...")
    macro = fetch_macro()
    funds = []
    for code, name in fund_list:
        print(f"  {name}...")
        f = fetch_fund(code, name)
        f["t"] = trend(f)
        funds.append(f)
    r = gen_report(funds, macro)
    send_email(f"🎯 基金仪表盘 {datetime.now().strftime('%Y-%m-%d')}", r)


if __name__ == "__main__":
    main()
