# 📊 基金每日决策仪表盘

> 场外基金智能分析 + GitHub Actions 免费自动推送 + 多周期收益评估

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

## ✨ 功能

每天自动分析你的自选基金，生成决策报告推送到 QQ 邮箱：

- 📈 净值 + 实时估算 + 涨跌
- 📊 多周期收益（7天/1月/3月/6月/1年/今年以来）
- ⚠️ 最大回撤 + 风险评估
- 🎯 AI 评分 + 定投建议（加仓/正常/减少/暂停）
- 🌍 全球市场行情（可选）
- 🏢 基金经理/规模/重仓股（可选）

## 🚀 5 分钟上手

### 1. Fork 本项目

右上角点 `Fork` → `Create fork`

### 2. 配置 Secrets

`Settings` → `Secrets and variables` → `Actions` → `New repository secret`

| Secret | 说明 | 必填 |
|--------|------|:--:|
| `FUND_LIST` | 基金代码，逗号分隔，如 `016701,008254,539002` | ✅ |
| `EMAIL_SENDER` | 发件邮箱（如 `your@qq.com` 或 `your@gmail.com`） | ✅ |
| `EMAIL_PASSWORD` | 邮箱 SMTP 授权码（QQ邮箱: 设置→账户→POP3/SMTP→开启; Gmail: 开启两步验证→应用专用密码） | ✅ |
| `EMAIL_RECEIVER` | 收件邮箱（可同发件） | ✅ |
| `EMAIL_SMTP` | SMTP 服务器（默认 `smtp.qq.com`，Gmail 填 `smtp.gmail.com`） | ❌ |
| `ANSPIRE_API_KEYS` | [Anspire](https://open.anspire.cn/) API Key（可选，用于 AI 增强） | ❌ |

### 3. 启用 Actions

`Actions` → `I understand my workflows, go ahead and enable them`

### 4. 手动测试

`Actions` → `基金每日追踪` → `Run workflow` → `Run workflow`

### 5. 设置完成 🎉

每天北京时间 **18:30** 自动推送。要加减基金直接改 `FUND_LIST` 就行。

## 📧 报告预览

```
🎯 2026-06-24 周三 基金决策仪表盘
共4只 | 🟢加仓:4 🟡观望:0 🔴暂停:0

📊 摘要
🟢 华夏移动互联(002891): 定投加仓 | 89分 | 看多
🟢 建信新兴市场(539002): 定投加仓 | 89分 | 看多

📊 收益: 7天 4.95% | 1月 11.9% | 3月 65.69%
⚠️ 风险: 最大回撤 23.95%
🎯 动作: 定投加仓 | 评分: 89/100 | 看多
💡 趋势向好，可适当加大定投，分批介入勿追高
```

## 📝 如何查找基金代码

打开天天基金网，搜索基金名称，地址栏里 `fcode=016701` 就是基金代码。

## 🔧 本地运行

```bash
git clone https://github.com/你的用户名/fund-daily-tracker.git
cd fund-daily-tracker
pip install -r requirements.txt
python main.py
```

## ⚠️ 免责声明

仅供参考，不构成投资建议。投资有风险，入市需谨慎。

## 📄 License

MIT
