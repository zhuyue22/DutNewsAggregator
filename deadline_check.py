# -*- coding: utf-8 -*-
"""开机检查：近 N 天内截止且（重要度>=4 或 化工海洋与生命学院）且未忽略的信息。
结果写入 data\\hits.json，供 startup-check.ps1 弹窗使用。"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).parent
DAYS = 3
for i, arg in enumerate(sys.argv):
    if arg == "--days" and i + 1 < len(sys.argv):
        DAYS = int(sys.argv[i + 1])

arts = json.loads((ROOT / "data" / "articles.json").read_text(encoding="utf-8"))["articles"]
dis_path = ROOT / "data" / "dismissed.json"
dismissed = set()
if dis_path.exists():
    try:
        dismissed = {d["t"] for d in json.loads(dis_path.read_text(encoding="utf-8"))}
    except Exception:
        pass

today = datetime.now().date()
limit = today + timedelta(days=DAYS)
hits = []
for a in arts:
    if not a.get("deadline") or a.get("staff"):
        continue
    try:
        dl = datetime.strptime(a["deadline"], "%Y-%m-%d").date()
    except ValueError:
        continue
    if not (today <= dl <= limit):
        continue
    hy = any("海洋与生命" in c for c in a.get("college", []))
    if a.get("importance", 0) < 4 and not hy:
        continue
    if a["title"] in dismissed:
        continue
    hits.append({
        "title": a["title"], "deadline": a["deadline"],
        "importance": a.get("importance", 0), "hy": hy,
        "source": a.get("source", ""), "url": a.get("url", ""),
    })

hits.sort(key=lambda h: (h["deadline"], -h["importance"]))
(ROOT / "data" / "hits.json").write_text(
    json.dumps(hits, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"hits={len(hits)}")
