#!/usr/bin/env python3
"""场外基金每日智能分析系统 — 对标 daily_stock_analysis 的详细度"""

import json, os, re, smtplib, textwrap
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta

import requests

# ─── 配置 ───────────────────────────────────────────────────
FUNDS = [
    ("016701", "银华海外数字经济"),
    ("008254", "华宝致远混合"),
    ("002891", "华夏移动互联"),
    ("539002", "建信新兴市场"),
]

ANSPIRE_KEY = os.getenv("ANSPIRE_API_KEYS") or ""
EMAIL_SENDER = os.getenv("EMAIL_SENDER") or ""
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD") or ""
EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER") or ""
EMAIL_SMTP = os.getenv("EMAIL_SMTP") or "smtp.qq.com"
EMAIL_PORT = int(os.getenv("EMAIL_PORT") or "587")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
REFERER = "http://fund.eastmoney.com/"


# ─── 数据获取 ───────────────────────────────────────────────
def fetch_fund_all(code):
    """从天天基金获取完整基金数据"""
    result = {"code": code, "est_nav": None, "est_pct": "0", "nav": None,
              "date": "", "nav_time": "", "history": [],
              "name": code, "manager": "", "size": "", "type": "",
              "benchmark": "", "inception": "", "company": "",
              "stocks_pct": "", "bonds_pct": "", "cash_pct": "",
              "sharpe_1y": "", "max_dd_1y": "", "volatility_1y": "",
              "top_holdings": [], "rating_3y": "", "fee_buy": "", "fee_sell": ""}

    headers = {"Referer": REFERER, "User-Agent": UA}

    # 1. 实时估值
    try:
        url = f"http://fundgz.1234567.com.cn/js/{code}.js"
        r = requests.get(url, headers=headers, timeout=15)
        r.encoding = "utf-8"
        m = re.search(r'jsonpgz\((.+)\)', r.text)
        if m:
            d = json.loads(m.group(1))
            result.update({
                "name": d.get("name", code),
                "nav": float(d.get("dwjz", 0)),
                "est_nav": float(d.get("gsz", 0)),
                "est_pct": d.get("gszzl", "0"),
                "date": d.get("jzrq", ""),
                "nav_time": d.get("gztime", ""),
            })
    except Exception as e:
        result["error"] = str(e)[:100]
        return result

    # 2. 完整历史数据（天天基金 pingzhongdata）
    try:
        r = requests.get(
            f"http://fund.eastmoney.com/pingzhongdata/{code}.js",
            headers=headers, timeout=15
        )
        r.encoding = "utf-8"
        text = r.text

        # 基金名称
        m = re.search(r'fS_name\s*=\s*"([^"]+)"', text)
        if m:
            result["name"] = m.group(1)

        # 基金类型
        m = re.search(r'fS_typename\s*=\s*"([^"]+)"', text)
        if m:
            result["type"] = m.group(1)

        # 基金经理 — 精确匹配 Data_currentFundManager
        m = re.search(r'"JJJLName"\s*:\s*"([^"]+)"', text)
        if not m:
            m = re.search(r'Data_currentFundManager\s*=\s*(\[.+?\]);', text, re.DOTALL)
            if m:
                mgr_data = json.loads(m.group(1))
                if mgr_data:
                    result["manager"] = mgr_data[0].get("name", "") or mgr_data[0].get("JJJLName", "")

        # 成立日期
        m = re.search(r'fS_clrq\s*=\s*"([^"]+)"', text)
        if m:
            result["inception"] = m.group(1)

        # 基金公司
        m = re.search(r'fS_orgname\s*=\s*"([^"]+)"', text)
        if m:
            result["company"] = m.group(1)

        # 业绩基准
        m = re.search(r'fS_syl_1n\s*=\s*"([^"]*)"', text)
        if m:
            result["benchmark"] = m.group(1)[:100]

        # 历史净值 Data_netWorthTrend
        m = re.search(r'Data_netWorthTrend\s*=\s*(\[.+?\]);', text, re.DOTALL)
        if m:
            nav_data = json.loads(m.group(1))
            result["history"] = [
                {"date": datetime.fromtimestamp(x["x"] / 1000).strftime("%Y-%m-%d"), "nav": x["y"]}
                for x in nav_data
            ]

        # 累计净值 Data_ACWorthTrend
        m = re.search(r'Data_ACWorthTrend\s*=\s*(\[.+?\]);', text, re.DOTALL)
        if m:
            ac_data = json.loads(m.group(1))
            # 合并
            for i, d in enumerate(ac_data):
                if i < len(result["history"]):
                    result["history"][i]["ac_nav"] = round(d["y"], 4)

        # 资产配置 Data_assetAllocation
        m = re.search(r'Data_assetAllocation\s*=\s*(\[.+?\]);', text, re.DOTALL)
        if m:
            alloc = json.loads(m.group(1))
            if alloc:
                latest = alloc[-1]
                result["stocks_pct"] = str(latest.get("zq","?"))[:6]
                result["bonds_pct"] = str(latest.get("zq","?"))[:6]

        # 持仓 Data_fundSharesPositions
        m = re.search(r'Data_fundSharesPositions\s*=\s*(\[.+?\]);', text, re.DOTALL)
        if m:
            try:
                positions = json.loads(m.group(1))
                if positions:
                    latest_pos = positions[-1] if isinstance(positions[-1], list) else positions
                    # 取 top 10
                    for stock in positions[:10]:
                        result["top_holdings"].append({
                            "name": stock.get("GPDM", "?"),
                            "pct": str(stock.get("JZBL", "?"))[:6],
                        })
            except Exception:
                pass

        # 费率 Data_rateInProportion  - 管理费/托管费
        m = re.search(r'Data_rateInProportion\s*=\s*"([^"]*)"', text)
        if m:
            result["fee"] = m.group(1)

        # 评级 Data_rating
        m = re.search(r'Data_rating\s*=\s*(\[.+?\]);', text, re.DOTALL)
        if m:
            try:
                ratings = json.loads(m.group(1))
                if ratings:
                    result["rating_3y"] = str(ratings[-1].get("3year", "?"))[:10]
            except Exception:
                pass

        # 风险数据 Data_riskEvaluation
        m = re.search(r'"sharpeRatio"\s*:\s*"([^"]*)"', text)
        if m:
            result["sharpe_1y"] = m.group(1)[:6]
        m = re.search(r'"maxRetracement"\s*:\s*"([^"]*)"', text)
        if m:
            result["max_dd_1y"] = m.group(1)[:8]
        m = re.search(r'"standardDeviation"\s*:\s*"([^"]*)"', text)
        if m:
            result["volatility_1y"] = m.group(1)[:6]

    except Exception:
        pass

    # 3. 基金规模（单独 API）
    try:
        r = requests.get(
            f"https://api.fund.eastmoney.com/f10/jbgk",
            params={"fundCode": code, "type": "1"},
            headers={**headers, "Referer": f"https://fundf10.eastmoney.com/jbgk_{code}.html"},
            timeout=10,
        )
        d = r.json().get("Data", {})
        result["size"] = d.get("scl", "") or d.get("zzfe", "") or ""
        if not result["type"]:
            result["type"] = d.get("jjlx", "")
    except Exception:
        pass

    return result


# ─── 分析计算 ───────────────────────────────────────────────
def calc_performance(history):
    """多周期收益计算"""
    if len(history) < 2:
        return {}
    now = history[-1]["nav"]
    result = {}
    for label, days in [("7天", 7), ("1月", 22), ("3月", 66), ("6月", 132), ("1年", 264)]:
        if len(history) > days:
            past = history[-(days + 1)]["nav"]
            result[label] = round((now - past) / past * 100, 2)
    # YTD
    now_year = datetime.now().year
    ytd_start = None
    for h in history:
        if h["date"].startswith(str(now_year)):
            ytd_start = h["nav"]
            break
    if ytd_start:
        result["今年以来"] = round((now - ytd_start) / ytd_start * 100, 2)
    return result


def calc_drawdown(history):
    """从历史高点最大回撤"""
    if len(history) < 10:
        return None
    peak = 0
    max_dd = 0
    for h in history:
        nav = h["nav"]
        if nav > peak:
            peak = nav
        dd = (peak - nav) / peak * 100
        if dd > max_dd:
            max_dd = dd
    return round(max_dd, 2)


def fetch_macro_market():
    """抓取全球主要指数行情 — 市场宏观前景"""
    indices = {
        "上证指数": "1.000001",
        "深证成指": "0.399001",
        "创业板指": "0.399006",
        "恒生指数": "100.HSI",
        "纳斯达克": "100.NDX",
        "标普500": "100.SPX",
    }
    result = {}
    codes = ",".join(indices.values())
    try:
        url = "https://push2.eastmoney.com/api/qt/ulist.np/get"
        params = {"fltt": 2, "fields": "f2,f3,f4,f12,f14", "secids": codes}
        r = requests.get(url, params=params, timeout=10,
                         headers={"Referer": "https://quote.eastmoney.com/"})
        data = r.json()
        for item in data.get("data", {}).get("diff", []):
            secid = item.get("f12", "")
            for name, c in indices.items():
                if secid and (c.endswith(secid) or secid.endswith(c.split(".")[-1]) or c == secid):
                    result[name] = {"price": item.get("f2", "?"), "pct": item.get("f3", "?"),
                                    "change": item.get("f4", "?")}
    except Exception as e:
        result["_error"] = str(e)[:100]

    # 美元人民币汇率单独获取
    try:
        r = requests.get(
            "https://push2.eastmoney.com/api/qt/stock/get",
            params={"secid": "133.USDCNY", "fields": "f43,f169,f170"},
            headers={"Referer": "https://quote.eastmoney.com/"}, timeout=10,
        )
        d = r.json().get("data", {})
        result["美元人民币"] = {"price": d.get("f43", "?"), "pct": d.get("f169", "?"),
                               "change": d.get("f170", "?")}
    except Exception:
        result["美元人民币"] = {"price": "?", "pct": "?"}

    return result


def macro_outlook(macro):
    """基于大盘数据生成市场前景研判"""
    lines = []
    lines.append("🌍 **全球市场概览**")
    lines.append("| 指数 | 最新价 | 涨跌幅 |")
    lines.append("|------|--------|--------|")
    for name in ["上证指数", "深证成指", "创业板指", "恒生指数", "纳斯达克", "标普500", "美元人民币"]:
        d = macro.get(name, {})
        pct = d.get("pct", "?")
        pct_str = f"{pct}%"
        if isinstance(pct, (int, float)):
            sign = "🟢" if pct > 0 else ("🔴" if pct < 0 else "⚪")
            pct_str = f"{sign} {pct}%"
        lines.append(f"| {name} | {d.get('price','?')} | {pct_str} |")
    lines.append("")

    # 判断整体环境
    us_pct = macro.get("纳斯达克", {}).get("pct", 0) or 0
    cn_pct = macro.get("上证指数", {}).get("pct", 0) or 0
    hk_pct = macro.get("恒生指数", {}).get("pct", 0) or 0
    fx = macro.get("美元人民币", {}).get("price", 7.2)

    lines.append("📌 **市场环境研判**")
    signals = []
    if isinstance(us_pct, (int, float)) and us_pct > 0.5:
        signals.append("✅ 美股强势，QDII 基金受益")
    elif isinstance(us_pct, (int, float)) and us_pct < -0.5:
        signals.append("⚠️ 美股承压，QDII 基金需警惕")

    if isinstance(cn_pct, (int, float)) and cn_pct > 0.3:
        signals.append("✅ A 股偏暖，国内基金环境有利")
    elif isinstance(cn_pct, (int, float)) and cn_pct < -0.3:
        signals.append("⚠️ A 股偏弱，注意仓位控制")

    if isinstance(hk_pct, (int, float)) and hk_pct > 0.5:
        signals.append("✅ 港股走强，互联互通基金受益")
    elif isinstance(hk_pct, (int, float)) and hk_pct < -0.5:
        signals.append("⚠️ 港股走弱，中概互联承压")

    if isinstance(fx, (int, float)) and fx > 7.3:
        signals.append("💱 人民币偏弱，QDII 基金有汇兑收益")
    elif isinstance(fx, (int, float)) and fx < 7.0:
        signals.append("💱 人民币走强，QDII 基金有汇兑损失")

    for s in signals:
        lines.append(f"  {s}")

    return "\n".join(lines)


def trend_assessment(fund):
    """趋势研判 — 类似股票决策信号"""
    perf = calc_performance(fund["history"])
    est_pct = float(fund.get("est_pct", "0") or "0")
    week = perf.get("7天", 0) or 0
    month = perf.get("1月", 0) or 0

    # 评分
    score = 50
    if est_pct > 0: score += 10
    if week > 0: score += 10
    if month > 0: score += 10
    if month > 3: score += 5
    if week > 1: score += 5
    if est_pct < -0.5: score -= 15
    if week < -1: score -= 15
    if month < -3: score -= 20

    score = max(0, min(100, score))

    if score >= 70:
        action, outlook = "定投加仓", "看多"
    elif score >= 50:
        action, outlook = "正常定投", "震荡偏多"
    elif score >= 35:
        action, outlook = "减少定投", "震荡偏空"
    else:
        action, outlook = "暂停定投", "看空"

    # 观察条件
    conditions = []
    if month and month < 0:
        conditions.append("观察近1月是否止跌企稳")
    if week and week < -2:
        conditions.append("观察短期是否有超跌反弹信号")
    if est_pct > 1:
        conditions.append("今日涨幅较大，可适当等待回调")

    # 风险
    risks = []
    dd = calc_drawdown(fund["history"])
    if dd and dd > 20:
        risks.append(f"历史最大回撤 {dd}%，波动较大，需控制仓位")
    if month and month > 10:
        risks.append("近1月涨幅较大，谨防高位回调")
    if fund["type"] and "QDII" in fund["type"]:
        risks.append("QDII 基金存在汇率风险，人民币升值不利")

    return {
        "action": action, "outlook": outlook, "score": score,
        "conditions": conditions, "risks": risks,
    }


# ─── 报告生成 ────────────────────────────────────────────────
def generate_report(funds_data, macro=None):
    today = datetime.now().strftime("%Y-%m-%d")
    weekday = ["一", "二", "三", "四", "五", "六", "日"][datetime.now().weekday()]

    # 统计
    buy = sum(1 for f in funds_data if f.get("trend", {}).get("score", 0) >= 70)
    watch = sum(1 for f in funds_data if 35 <= f.get("trend", {}).get("score", 0) < 70)
    sell = sum(1 for f in funds_data if f.get("trend", {}).get("score", 0) < 35)

    lines = [
        f"🎯 {today} 基金决策仪表盘",
        f"共分析 {len(funds_data)} 只基金 | 🟢定投加仓:{buy} 🟡观望:{watch} 🔴暂停:{sell}",
    ]

    # 市场宏观前景
    if macro:
        lines.append("")
        lines.append(macro_outlook(macro))
        lines.append("")

    lines += [
        "",
        "📊 分析结果摘要",
    ]

    for f in funds_data:
        t = f.get("trend", {})
        s = t.get("score", 0)
        emoji = "🟢" if s >= 70 else ("🟡" if s >= 50 else ("🟠" if s >= 35 else "🔴"))
        lines.append(f"{emoji} {f['name']}({f['code']}): {t.get('action','?')} | 评分 {s} | {t.get('outlook','?')}")

    lines.append("")
    lines.append("=" * 55)

    # 每只基金详情
    for f in funds_data:
        if "error" in f and not f.get("nav"):
            lines.append(f"\n### ⚠️ {f['name']}({f['code']})")
            lines.append(f"数据获取失败: {f.get('error','')}")
            continue

        t = f.get("trend", {})
        perf = calc_performance(f["history"])

        lines.append(f"\n{'=' * 55}")
        lines.append(f"### {f['name']} ({f['code']})")
        lines.append("")

        # 基本信息
        lines.append(f"📋 **基本信息**")
        lines.append(f"| 项目 | 内容 |")
        lines.append(f"|------|------|")
        if f.get("type"): lines.append(f"| 类型 | {f['type']} |")
        if f.get("company"): lines.append(f"| 基金公司 | {f['company']} |")
        if f.get("manager"): lines.append(f"| 基金经理 | {f['manager']} |")
        if f.get("inception"): lines.append(f"| 成立日期 | {f['inception']} |")
        if f.get("size"): lines.append(f"| 基金规模 | {f['size']} |")
        if f.get("fee"): lines.append(f"| 费率 | {f['fee']} |")
        lines.append("")

        # 行情
        lines.append(f"📈 **当日行情**")
        lines.append(f"| 净值 | 估算净值 | 涨跌 | 净值日期 | 估值时间 |")
        lines.append(f"|------|---------|------|---------|---------|")
        lines.append(f"| {f['nav']} | {f.get('est_nav','?')} | {f.get('est_pct','?')}% | {f['date']} | {f.get('nav_time','?')} |")
        lines.append("")

        # 收益表现
        lines.append(f"📊 **收益表现**")
        period_labels = ["7天", "1月", "3月", "6月", "1年", "今年以来"]
        period_keys = ["7天", "1月", "3月", "6月", "1年", "今年以来"]
        vals = []
        for k in period_keys:
            v = perf.get(k)
            vals.append(f"{v}%" if v is not None else "?")
        lines.append(f"| {' | '.join(period_labels)} |")
        lines.append(f"|{'|'.join(['------']*len(period_labels))}|")
        lines.append(f"| {' | '.join(vals)} |")
        lines.append("")

        # 风险评估
        dd = calc_drawdown(f["history"])
        lines.append(f"⚠️ **风险评估**")
        risk_items = []
        if dd: risk_items.append(f"历史最大回撤: {dd}%")
        if f.get("sharpe_1y"): risk_items.append(f"Sharpe(1Y): {f['sharpe_1y']}")
        if f.get("volatility_1y"): risk_items.append(f"波动率(1Y): {f['volatility_1y']}")
        if f.get("max_dd_1y"): risk_items.append(f"官方最大回撤: {f['max_dd_1y']}")
        if risk_items:
            lines.append("  " + " | ".join(risk_items))
        else:
            lines.append("  风险数据暂缺")
        lines.append("")

        # 趋势研判
        lines.append(f"🎯 **AI 决策信号**")
        lines.append(f"动作: {t.get('action','?')} | 评分: {t.get('score','?')}/100 | 趋势: {t.get('outlook','?')}")
        if t.get("conditions"):
            lines.append(f"- 观察条件: {t['conditions']}")
        if t.get("risks"):
            lines.append(f"- 风险提示: {t['risks']}")
        lines.append("")

        # 持仓
        if f.get("top_holdings"):
            lines.append(f"🏢 **前十大重仓股**")
            lines.append(f"| 代码 | 占比 |")
            lines.append(f"|------|------|")
            for h in f["top_holdings"][:10]:
                lines.append(f"| {h['name']} | {h['pct']}% |")
            lines.append("")

        # 操作建议
        lines.append(f"💡 **操作建议**")
        s = t.get("score", 50)
        if s >= 70:
            lines.append("趋势向好，可适当加大定投力度。注意不要追高，分批介入。")
        elif s >= 50:
            lines.append("趋势平稳，维持正常定投节奏。密切关注后续走势。")
        elif s >= 35:
            lines.append("趋势偏弱，建议减少定投金额。等待企稳信号后再恢复。")
        else:
            lines.append("趋势走弱，建议暂停定投。保留现金等待更好时机。")
        lines.append("")

    # 尾部
    lines.append("=" * 55)
    lines.append(f"> 生成时间: {datetime.now().strftime('%H:%M:%S')}")
    lines.append("> 数据来源: 天天基金")
    lines.append("> ⚠️ 仅供参考，不构成投资建议。投资有风险，入市需谨慎。")

    return "\n".join(lines)


# ─── 邮件发送 ────────────────────────────────────────────────
def send_email(subject, body):
    if not all([EMAIL_SENDER, EMAIL_PASSWORD, EMAIL_RECEIVER]):
        print(body)
        return False
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = EMAIL_SENDER
    msg["To"] = EMAIL_RECEIVER
    msg.attach(MIMEText(body, "plain", "utf-8"))
    try:
        with smtplib.SMTP(EMAIL_SMTP, EMAIL_PORT, timeout=15) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.send_message(msg)
        print("✅ 报告已发送")
        return True
    except Exception as e:
        print(f"❌ 邮件发送失败: {e}")
        print(body)
        return False


# ─── Main ────────────────────────────────────────────────────
def main():
    print("📊 基金智能分析系统\n")
    print("  获取市场宏观数据...")
    macro = fetch_macro_market()
    funds_data = []
    for code, name in FUNDS:
        print(f"  分析 {name}({code})...")
        fund = fetch_fund_all(code)
        fund["trend"] = trend_assessment(fund)
        funds_data.append(fund)

    report = generate_report(funds_data, macro)
    subject = f"🎯 基金决策仪表盘 {datetime.now().strftime('%Y-%m-%d')}"
    send_email(subject, report)


if __name__ == "__main__":
    main()
