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
ANSPIRE_BASE = "https://open-gateway.anspire.cn/v6"
ANSPIRE_MODEL = os.getenv("ANSPIRE_MODEL") or "qwen3.5-flash"  # 最便宜 ¥0.2/¥2

EMAIL_SENDER = os.getenv("EMAIL_SENDER") or ""
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD") or ""
EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER") or ""
EMAIL_SMTP = os.getenv("EMAIL_SMTP") or "smtp.qq.com"
EMAIL_PORT = int(os.getenv("EMAIL_PORT") or "587")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


# ─── Anspire AI 分析 ─────────────────────────────────────────
def fetch_ai_analysis(fund):
    """调用 Anspire LLM 网关，获取 AI 增强分析。
    返回 dict: {summary, suggestion, confidence, risk_alert}
    失败时返回 None，不影响主流程。
    """
    if not ANSPIRE_KEY or not ANSPIRE_KEY.startswith("sk-"):
        return None

    p = calc_perf(fund["history"])
    dd = calc_dd(fund["history"])

    # 构建精简上下文
    ctx = {
        "name": fund.get("name", fund["code"]),
        "code": fund["code"],
        "type": fund.get("type", "未知"),
        "nav": fund.get("nav"),
        "est_pct": fund.get("est_pct", "0"),
        "returns": {k: f"{v}%" for k, v in (p or {}).items()},
        "max_drawdown": f"{dd}%" if dd else "无数据",
        "holdings_pct": fund.get("holdings_pct", "未知"),
        "manager": fund.get("manager", "未知"),
        "size": fund.get("size", "未知"),
    }

    prompt = f"""你是专业基金分析师。根据以下数据给出80字以内的简明分析，包含：当前趋势判断、主要风险点、操作建议。

基金数据:
- 名称: {ctx['name']} ({ctx['code']})
- 类型: {ctx['type']}
- 净值: {ctx['nav']} | 今日估算涨跌: {ctx['est_pct']}%
- 多周期收益: {json.dumps(ctx['returns'], ensure_ascii=False)}
- 最大回撤: {ctx['max_drawdown']}
- 股票仓位: {ctx['holdings_pct']}%
- 经理: {ctx['manager']} | 规模: {ctx['size']}

请用以下JSON格式回复（只输出JSON，不要其他内容）:
{{"summary":"趋势判断","risk_alert":"风险提示","suggestion":"操作建议","confidence":"高/中/低"}}"""

    try:
        r = requests.post(
            f"{ANSPIRE_BASE}/chat/completions",
            headers={
                "Authorization": f"Bearer {ANSPIRE_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": ANSPIRE_MODEL,
                "messages": [
                    {"role": "system", "content": "你是专业基金分析师，回复简洁精准，只输出JSON。"},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.3,
                "max_tokens": 300,
            },
            timeout=20,
        )
        if r.status_code != 200:
            return None

        text = r.json()["choices"][0]["message"]["content"]
        # 提取 JSON（可能包裹在 ```json ... ``` 中）
        m = re.search(r"\{[^{}]*\}", text, re.DOTALL)
        if m:
            return json.loads(m.group(0))
        return None
    except Exception:
        return None


# ─── 宏观市场 ───────────────────────────────────────────────
def fetch_macro():
    """抓取全球主要指数 — 使用 yfinance（GitHub Actions 美国机房友好）"""
    import yfinance as yf
    symbols = {
        "上证指数": "000001.SS",
        "深证成指": "399001.SZ",
        "恒生指数": "^HSI",
        "纳斯达克": "^IXIC",
        "标普500": "^GSPC",
    }
    result = {}
    for name, sym in symbols.items():
        try:
            ticker = yf.Ticker(sym)
            hist = ticker.history(period="5d")
            last = hist["Close"].dropna()
            if len(last) >= 2:
                prev, now = last.iloc[-2], last.iloc[-1]
                result[name] = {"price": round(float(now), 2),
                                "pct": round(float((now - prev) / prev * 100), 2)}
            elif len(last) >= 1:
                result[name] = {"price": round(float(last.iloc[-1]), 2), "pct": "?"}
            else:
                result[name] = {"price": "?", "pct": "?"}
        except Exception:
            result[name] = {"price": "?", "pct": "?"}

    # 汇率
    try:
        fx = yf.Ticker("CNY=X")
        hist = fx.history(period="5d")
        last = hist["Close"].dropna()
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
    """评分（基础规则 + 可选 AI 增强）"""
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


# ─── HTML 邮件模板 ───────────────────────────────────────────
CSS = """
body { font-family: -apple-system, 'PingFang SC', 'Microsoft YaHei', sans-serif;
       background:#f5f6fa; margin:0; padding:20px; color:#2d3436; }
.card { max-width:680px; margin:0 auto; background:#fff; border-radius:12px;
        box-shadow:0 2px 12px rgba(0,0,0,.08); overflow:hidden; }
.header { background:linear-gradient(135deg,#6c5ce7,#a29bfe); color:#fff;
          padding:28px 32px; text-align:center; }
.header h1 { margin:0 0 8px; font-size:22px; }
.header p { margin:0; opacity:.85; font-size:14px; }
.stats { display:flex; justify-content:center; gap:24px; padding:20px 32px;
         background:#f8f9ff; border-bottom:1px solid #eee; }
.stat { text-align:center; }
.stat .num { font-size:28px; font-weight:700; }
.stat .label { font-size:12px; color:#636e72; margin-top:2px; }
.section { padding:0 32px 20px; }
.section h2 { font-size:16px; margin:20px 0 12px; padding-bottom:6px;
              border-bottom:2px solid #6c5ce7; display:inline-block; }
table { width:100%; border-collapse:collapse; font-size:13px; }
th, td { padding:8px 10px; text-align:left; border-bottom:1px solid #f0f0f0; }
th { color:#636e72; font-weight:500; font-size:12px; }
.fund-card { background:#fafafa; border-radius:8px; padding:20px; margin:16px 0;
             border-left:4px solid #6c5ce7; }
.fund-card.buy { border-left-color:#00b894; }
.fund-card.hold { border-left-color:#fdcb6e; }
.fund-card.reduce { border-left-color:#e17055; }
.fund-card.pause { border-left-color:#d63031; }
.fund-title { font-size:16px; font-weight:700; margin-bottom:8px; }
.fund-info { font-size:12px; color:#636e72; margin-bottom:10px; }
.metrics { display:flex; flex-wrap:wrap; gap:10px; margin:10px 0; }
.metric { background:#fff; border-radius:6px; padding:10px 14px; flex:1;
          min-width:100px; box-shadow:0 1px 3px rgba(0,0,0,.04); }
.metric .val { font-size:18px; font-weight:700; }
.metric .lbl { font-size:11px; color:#636e72; }
.green { color:#00b894; } .red { color:#d63031; } .orange { color:#e17055; }
.ai-box { background:#f0f4ff; border-radius:8px; padding:12px 16px; margin:12px 0;
          font-size:13px; line-height:1.6; }
.ai-box .ai-label { font-size:11px; color:#6c5ce7; font-weight:600;
                    text-transform:uppercase; margin-bottom:4px; }
.risk-list { font-size:12px; color:#e17055; margin:4px 0; }
.footer { text-align:center; padding:16px; font-size:11px; color:#b2bec3;
          border-top:1px solid #eee; }
.action-badge { display:inline-block; padding:3px 10px; border-radius:12px;
                font-size:12px; font-weight:600; }
.action-badge.buy { background:#d4fceb; color:#00b894; }
.action-badge.hold { background:#fff8e1; color:#f39c12; }
.action-badge.reduce { background:#ffeaa7; color:#e17055; }
.action-badge.pause { background:#ffdcdc; color:#d63031; }
"""


def gen_html(funds, macro, ai_results):
    """生成 HTML 格式报告"""
    today = datetime.now().strftime("%Y-%m-%d")
    wd = ["一","二","三","四","五","六","日"][datetime.now().weekday()]
    buy = sum(1 for f in funds if f.get("t",{}).get("score",0) >= 70)
    watch = sum(1 for f in funds if 35 <= f.get("t",{}).get("score",0) < 70)
    sell = sum(1 for f in funds if f.get("t",{}).get("score",0) < 35)

    def badge_class(score):
        if score >= 70: return "buy"
        if score >= 50: return "hold"
        if score >= 35: return "reduce"
        return "pause"

    def badge_text(score):
        if score >= 70: return "加仓"
        if score >= 50: return "观望"
        if score >= 35: return "减少"
        return "暂停"

    def fmt_pct(v):
        if v is None: return "?"
        if isinstance(v, (int, float)):
            c = "green" if v > 0 else ("red" if v < 0 else "")
            return f'<span class="{c}">{v:+.2f}%</span>'
        return str(v)

    parts = [f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>{CSS}</style></head><body>
<div class="card">
<div class="header">
  <h1>🎯 {today} 周{wd} 基金决策仪表盘</h1>
  <p>共{len(funds)}只基金 · 仅供参考，不构成投资建议</p>
</div>
<div class="stats">
  <div class="stat"><div class="num" style="color:#00b894">{buy}</div><div class="label">🟢 加仓</div></div>
  <div class="stat"><div class="num" style="color:#fdcb6e">{watch}</div><div class="label">🟡 观望</div></div>
  <div class="stat"><div class="num" style="color:#d63031">{sell}</div><div class="label">🔴 暂停</div></div>
</div>"""]

    # 宏观
    has_macro = macro and not macro.get("_error") and any(
        isinstance(macro.get(n, {}).get("pct"), (int, float))
        for n in ["上证指数", "纳斯达克"]
    )
    if has_macro:
        parts.append('<div class="section"><h2>🌍 全球市场</h2><table>'
                     '<tr><th>指数</th><th>最新</th><th>涨跌</th></tr>')
        for n in ["上证指数","深证成指","恒生指数","纳斯达克","标普500","美元人民币"]:
            d = macro.get(n, {})
            pct = d.get("pct", "?")
            parts.append(f'<tr><td>{n}</td><td>{d.get("price","?")}</td>'
                         f'<td>{fmt_pct(pct)}</td></tr>')
        parts.append('</table></div>')

    # 摘要
    parts.append('<div class="section"><h2>📊 决策摘要</h2><table>'
                 '<tr><th>基金</th><th>评分</th><th>动作</th><th>展望</th></tr>')
    for f in funds:
        t = f.get("t", {})
        s = t.get("score", 0)
        bc = badge_class(s)
        parts.append(f'<tr><td>{f["name"]} <span style="color:#999;font-size:11px">{f["code"]}</span></td>'
                     f'<td><b>{s}</b>/100</td>'
                     f'<td><span class="action-badge {bc}">{badge_text(s)}</span></td>'
                     f'<td>{t.get("outlook","?")}</td></tr>')
    parts.append('</table></div>')

    # 每只基金
    for i, f in enumerate(funds):
        t = f.get("t", {})
        p = calc_perf(f["history"])
        dd = calc_dd(f["history"])
        s = t.get("score", 50)
        card_class = badge_class(s)

        parts.append(f'<div class="section"><div class="fund-card {card_class}">'
                     f'<div class="fund-title">{f["name"]} ({f["code"]}) '
                     f'<span class="action-badge {card_class}">{badge_text(s)}</span></div>')

        # 基本信息
        info_parts = []
        if f.get("type"): info_parts.append(f["type"])
        if f.get("manager"): info_parts.append(f"经理: {f['manager']}")
        if f.get("size"): info_parts.append(f"规模: {f['size']}")
        if info_parts:
            parts.append(f'<div class="fund-info">{" | ".join(info_parts)}</div>')

        # 行情指标
        parts.append('<div class="metrics">')
        parts.append(f'<div class="metric"><div class="lbl">净值</div><div class="val">{f.get("nav","?")}</div></div>')
        parts.append(f'<div class="metric"><div class="lbl">估算净值</div><div class="val">{f.get("est_nav","?")}</div></div>')
        est_pct = f.get("est_pct","0")
        c = "green" if float(est_pct or 0) > 0 else ("red" if float(est_pct or 0) < 0 else "")
        parts.append(f'<div class="metric"><div class="lbl">今日涨跌</div>'
                     f'<div class="val {c}">{est_pct}%</div></div>')
        parts.append(f'<div class="metric"><div class="lbl">日期</div><div class="val" style="font-size:14px">{f.get("date","?")}</div></div>')
        parts.append('</div>')

        # 多周期收益
        peri = ["7天","1月","3月","6月","1年","今年以来"]
        parts.append('<div class="metrics">')
        for k in peri:
            v = p.get(k)
            if v is not None:
                c = "green" if v > 0 else ("red" if v < 0 else "")
                parts.append(f'<div class="metric"><div class="lbl">{k}</div>'
                             f'<div class="val {c}" style="font-size:15px">{v:+.2f}%</div></div>')
            else:
                parts.append(f'<div class="metric"><div class="lbl">{k}</div><div class="val" style="font-size:14px">?</div></div>')
        parts.append('</div>')

        # 风险
        risk_items = []
        if dd: risk_items.append(f"最大回撤: {dd}%")
        if f.get("holdings_pct"): risk_items.append(f"股票仓位: {f['holdings_pct']}%")
        if risk_items:
            parts.append(f'<div style="font-size:12px;color:#636e72;margin:8px 0">'
                         f'⚠️ {" | ".join(risk_items)}</div>')

        # 决策
        parts.append(f'<div style="margin:10px 0;font-size:14px">'
                     f'🎯 <b>{t.get("action","?")}</b> | 评分: {s}/100 | {t.get("outlook","?")}'
                     f'</div>')
        if t.get("risks"):
            parts.append(f'<div class="risk-list">风险: {"; ".join(t["risks"])}</div>')

        # AI 分析
        ai = ai_results.get(f["code"]) if ai_results else None
        if ai:
            conf = ai.get('confidence', '')
            conf_display = f"(置信度: {conf})" if conf else ""
            parts.append(f'<div class="ai-box">'
                         f'<div class="ai-label">🤖 AI 分析 {conf_display}</div>'
                         f'{ai.get("summary","")}<br>'
                         f'⚠️ {ai.get("risk_alert","")}<br>'
                         f'💡 {ai.get("suggestion","")}'
                         f'</div>')

        # 持仓
        if f.get("top_holdings"):
            h_str = " | ".join(f"{h['code']} {h['pct']}%" for h in f["top_holdings"][:8])
            parts.append(f'<div style="font-size:11px;color:#999;margin-top:8px">🏢 重仓: {h_str}</div>')

        # 建议
        if s >= 70:
            tip = "趋势向好，可适当加大定投，分批介入勿追高"
        elif s >= 50:
            tip = "趋势平稳，维持正常定投，密切关注走势"
        elif s >= 35:
            tip = "趋势偏弱，建议减少定投，等企稳再恢复"
        else:
            tip = "趋势走弱，建议暂停定投，保留现金等更好时机"
        parts.append(f'<div style="font-size:12px;color:#636e72;margin-top:6px">💡 {tip}</div>')

        parts.append('</div></div>')  # close fund-card, section

    parts.append(f'<div class="footer">⏰ {datetime.now().strftime("%H:%M:%S")} | '
                 f'数据来源: 天天基金 · 仅供参考 | Powered by fund-daily-tracker</div>'
                 f'</div></body></html>')
    return "\n".join(parts)


# ─── 纯文本报告（降级用）────────────────────────────────────
def gen_report(funds, macro, ai_results=None):
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
        for n in ["上证指数","深证成指","恒生指数","纳斯达克","标普500","美元人民币"]:
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

        # AI 分析
        ai = ai_results.get(f["code"]) if ai_results else None
        if ai:
            L.append(f"🤖 AI 分析 (置信度: {ai.get('confidence','?')})")
            L.append(f"  {ai.get('summary','')}")
            L.append(f"  ⚠️ {ai.get('risk_alert','')}")
            L.append(f"  💡 {ai.get('suggestion','')}")
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
def send_email(subj, plain_body, html_body=None):
    if not all([EMAIL_SENDER, EMAIL_PASSWORD, EMAIL_RECEIVER]):
        print(plain_body); return False

    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = subj, EMAIL_SENDER, EMAIL_RECEIVER
    msg.attach(MIMEText(plain_body, "plain", "utf-8"))

    if html_body:
        msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP(EMAIL_SMTP, EMAIL_PORT, timeout=15) as s:
            s.ehlo(); s.starttls(); s.ehlo()
            s.login(EMAIL_SENDER, EMAIL_PASSWORD); s.send_message(msg)
        print("✅ 已发送"); return True
    except Exception as e:
        print(f"❌ {e}\n{plain_body}"); return False


def main():
    print("📊 基金分析\n")

    fund_list = load_funds()
    print("  宏观...")
    macro = fetch_macro()

    funds = []
    ai_results = {}
    ai_used = bool(ANSPIRE_KEY and ANSPIRE_KEY.startswith("sk-"))

    for code, name in fund_list:
        print(f"  {name}...")
        f = fetch_fund(code, name)
        f["t"] = trend(f)
        funds.append(f)

        # AI 分析（异步逐个调用，不阻塞整体流程）
        if ai_used:
            ai = fetch_ai_analysis(f)
            if ai:
                ai_results[code] = ai
                print(f"    🤖 AI 分析完成: {ai.get('summary','')[:40]}...")

    if ai_used and ai_results:
        print(f"  AI 分析: {len(ai_results)}/{len(funds)} 只成功")

    # 生成报告
    plain = gen_report(funds, macro, ai_results if ai_results else None)
    html = gen_html(funds, macro, ai_results if ai_results else None)

    subj = f"🎯 基金仪表盘 {datetime.now().strftime('%Y-%m-%d')}"
    send_email(subj, plain, html)


if __name__ == "__main__":
    main()
