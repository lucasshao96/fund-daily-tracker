#!/usr/bin/env python3
"""场外基金每日净值追踪 & AI 简报生成"""

import json
import os
import re
import sys
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from pathlib import Path

import requests

# ─── 配置 ───────────────────────────────────────────────────
FUNDS = [
    ("016701", "华安科技动力混合"),
    ("008254", "华泰柏瑞质量成长"),
    ("002891", "华夏移动互联混合"),
    ("539002", "建信新兴市场混合"),
]

# 环境变量（GitHub Secrets）
ANSPIRE_KEY = os.getenv("ANSPIRE_API_KEYS", "")  # 可选：AI 评论
EMAIL_SENDER = os.getenv("EMAIL_SENDER", "")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "")
EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER", "")
EMAIL_SMTP = os.getenv("EMAIL_SMTP", "smtp.qq.com")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))

# ─── 数据获取 ───────────────────────────────────────────────
def fetch_fund_info(code):
    """从天天基金获取基金基本信息（名称）"""
    try:
        url = f"http://fundgz.1234567.com.cn/js/{code}.js"
        resp = requests.get(url, headers={"Referer": "http://fund.eastmoney.com/"}, timeout=10)
        resp.encoding = "utf-8"
        match = re.search(r'jsonpgz\((.+)\)', resp.text)
        if match:
            data = json.loads(match.group(1))
            return {
                "code": code,
                "name": data.get("name", code),
                "nav": float(data.get("dwjz", 0)),      # 最新净值
                "est_nav": float(data.get("gsz", 0)),    # 实时估算
                "est_pct": data.get("gszzl", "0"),       # 估算涨跌幅 %
                "date": data.get("jzrq", ""),            # 净值日期
                "nav_time": data.get("gztime", ""),      # 估值时间
            }
    except Exception as e:
        return {"code": code, "name": code, "error": str(e)}
    return None


def fetch_fund_history(code, days=30):
    """从天天基金获取历史净值（用于计算近期表现）"""
    try:
        url = f"https://api.fund.eastmoney.com/f10/lsjz"
        params = {
            "fundCode": code,
            "pageIndex": 1,
            "pageSize": days + 5,
            "startDate": "",
            "endDate": "",
        }
        headers = {
            "Referer": f"https://fundf10.eastmoney.com/jjjz_{code}.html",
            "User-Agent": "Mozilla/5.0",
        }
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        data = resp.json()
        records = data.get("Data", {}).get("LSJZList", [])
        nav_list = []
        for r in records[:days]:
            nav_list.append({
                "date": r["FSRQ"],
                "nav": float(r["DWJZ"]),
                "pct": r.get("JZZZL", "0"),
            })
        return nav_list
    except Exception:
        return []


# ─── AI 评论（可选） ────────────────────────────────────────
def ai_commentary(funds_data):
    """使用 Anspire API 生成简要评论"""
    if not ANSPIRE_KEY:
        return None

    lines = ["今日基金净值："]
    for f in funds_data:
        if "error" in f:
            lines.append(f"- {f['name']}({f['code']}): 获取失败")
        else:
            lines.append(
                f"- {f['name']}({f['code']}): "
                f"净值 {f['nav']}, 估算涨跌 {f.get('est_pct','?')}%"
            )

    prompt = (
        "你是一位基金分析助手。根据以下数据，用 3-5 句话简要总结今日表现，"
        "指出涨跌最大的基金及其可能原因。语气简洁务实，不做投资建议。\n\n"
        + "\n".join(lines)
    )

    try:
        resp = requests.post(
            "https://open.anspire.cn/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {ANSPIRE_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gemini-2.5-flash",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 300,
            },
            timeout=30,
        )
        return resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return f"(AI 评论生成失败: {e})"


# ─── 近期表现计算 ────────────────────────────────────────────
def calc_performance(history):
    """计算近期表现"""
    if len(history) < 2:
        return None
    now_nav = history[0]["nav"]
    result = {}
    for label, offset in [("7天", 7), ("1月", 30)]:
        if len(history) >= offset + 1:
            past_nav = history[offset]["nav"]
            pct = (now_nav - past_nav) / past_nav * 100
            result[label] = round(pct, 2)
    return result


# ─── 报告生成 ────────────────────────────────────────────────
def generate_report(funds_data, funds_history, commentary):
    today = datetime.now().strftime("%Y-%m-%d")
    weekday = ["一", "二", "三", "四", "五", "六", "日"][datetime.now().weekday()]

    lines = [
        f"📊 基金日报 | {today} 周{weekday}",
        "=" * 45,
        "",
    ]

    for i, f in enumerate(funds_data):
        if "error" in f:
            lines.append(f"⚠️ {f['name']}({f['code']}): {f['error']}")
            continue

        lines.append(f"### {f['name']}")
        lines.append(f"代码: {f['code']}")
        lines.append(f"最新净值: {f['nav']} (日期: {f['date']})")
        lines.append(f"今日估算: {f['est_nav']} | 涨跌: {f['est_pct']}%")

        perf = calc_performance(funds_history[i])
        if perf:
            lines.append(f"近7天: {perf.get('7天', '?')}% | 近1月: {perf.get('1月', '?')}%")
        lines.append("")

    if commentary:
        lines.append("---")
        lines.append("### 🤖 AI 简评")
        lines.append(commentary)
        lines.append("")

    lines.append(f"> 生成时间: {datetime.now().strftime('%H:%M:%S')}")
    lines.append("> 数据来源: 天天基金 | 仅供参考，不构成投资建议")
    return "\n".join(lines)


# ─── 邮件发送 ────────────────────────────────────────────────
def send_email(subject, body):
    if not all([EMAIL_SENDER, EMAIL_PASSWORD, EMAIL_RECEIVER]):
        print("⚠️  邮件配置不完整，跳过发送。报告内容：\n")
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
        print("\n--- 报告预览 ---\n")
        print(body)
        return False


# ─── Main ────────────────────────────────────────────────────
def main():
    print("📊 基金每日追踪器\n")

    # 获取基金数据
    funds_data = []
    for code, name in FUNDS:
        print(f"  拉取 {name}({code})...")
        funds_data.append(fetch_fund_info(code))

    # 获取历史净值
    funds_history = []
    for data in funds_data:
        if "error" not in data:
            funds_history.append(fetch_fund_history(data["code"]))
        else:
            funds_history.append([])

    # AI 评论（可选）
    print("  生成 AI 评论...")
    commentary = ai_commentary(funds_data)

    # 生成报告
    report = generate_report(funds_data, funds_history, commentary)

    # 发送
    subject = f"📊 基金日报 {datetime.now().strftime('%Y-%m-%d')}"
    send_email(subject, report)


if __name__ == "__main__":
    main()
