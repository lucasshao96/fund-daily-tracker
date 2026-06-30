import requests, base64, os

TOKEN = os.getenv("GITHUB_TOKEN") or ""
if not TOKEN:
    raise SystemExit("请设置环境变量 GITHUB_TOKEN")
REPO = "LucasShao96/fund-daily-tracker"
BASE = r"d:\AI\fund-daily-tracker"
HEADERS = {"Authorization": f"token {TOKEN}", "Accept": "application/vnd.github.v3+json"}

for path in ["main.py", "requirements.txt", "README.md", "LICENSE", ".github/workflows/daily.yml"]:
    content = open(os.path.join(BASE, path), encoding="utf-8").read()
    # Need to get sha first if file exists
    r = requests.get(f"https://api.github.com/repos/{REPO}/contents/{path}", headers=HEADERS)
    payload = {"message": f"update {path}", "content": base64.b64encode(content.encode()).decode()}
    if r.status_code == 200:
        payload["sha"] = r.json()["sha"]
    resp = requests.put(f"https://api.github.com/repos/{REPO}/contents/{path}", headers=HEADERS, json=payload)
    print(f"{path}: {resp.status_code}")
