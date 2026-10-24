# -*- coding: utf-8 -*-
"""
app.py —— 大工资讯聚合 · 桌面软件壳（pywebview + 本地服务器 + 开机临期提醒集成）

把「采集 + 展示 + 开机临期提醒」封装成自带原生窗口的桌面软件：
  1. 启动本地静态服务器（serve.py）托管站点
  2. 打开 pywebview 窗口加载 index.html
  3. 数据过期则后台采集一次（不阻塞窗口秒开）
  4. 前端「🔄 刷新数据」→ api.refresh_data() 重新采集并刷新
  5. 开机临期提醒：算临期事项(3天内截止 且 重要度≥4或含化工海洋与生命 且 未忽略)，
     前端顶部横幅列出，可「我要看看」(定位卡片) 或「不再提醒」(写 dismissed.json)
  6. --autostart：开机隐藏窗口后台跑，有临期才弹出，无则静默

运行：
  python app.py                # 正常桌面软件
  python app.py --no-refresh   # 启动不自动采集
  python app.py --autostart    # 开机模式：隐藏窗口，有临期才显示
打包：见 build.bat
"""
import os
import sys
import json
import shutil
import threading
from datetime import datetime, timedelta
from pathlib import Path

# 运行时根目录：打包后用用户可写目录(LOCALAPPDATA/DutNewsAggregator)，与 collector_eng 一致；
# 首次启动把打包的静态资源复制过去，采集数据也写这里 → 前端能读到最新数据。
if getattr(sys, "frozen", False):
    _MEIPASS = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    ROOT = Path(os.getenv("LOCALAPPDATA", str(_MEIPASS))) / "DutNewsAggregator"
    ROOT.mkdir(parents=True, exist_ok=True)
    for rel in ["index.html"]:
        src, dst = _MEIPASS / rel, ROOT / rel
        if src.exists() and (not dst.exists() or src.stat().st_size != dst.stat().st_size):
            shutil.copyfile(src, dst)
    for rel in ["data/competitions.js"]:
        src, dst = _MEIPASS / rel, ROOT / rel
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not dst.exists():
                shutil.copyfile(src, dst)
else:
    ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DAYS = 3  # 临期窗口（天）


def _check_deadlines():
    """临期事项：DAYS 天内截止 且 (重要度>=4 或 含化工海洋与生命学院) 且 未手动忽略。"""
    js = DATA_DIR / "data.js"
    if not js.exists():
        return []
    try:
        raw = js.read_text(encoding="utf-8").strip()
        if raw.startswith("window.DUT_DATA="):
            raw = raw[len("window.DUT_DATA="):]
        if raw.endswith(";"):
            raw = raw[:-1]
        arts = json.loads(raw).get("articles", [])
    except Exception:
        return []
    dismissed = set()
    dis = DATA_DIR / "dismissed.json"
    if dis.exists():
        try:
            dismissed = {d["t"] for d in json.loads(dis.read_text(encoding="utf-8"))}
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
            "url": a.get("url", ""),
        })
    hits.sort(key=lambda h: (h["deadline"], -h["importance"]))
    return hits


class Api:
    """暴露给前端 JavaScript 调用（window.pywebview.api.*）。"""

    def refresh_data(self):
        from collector_eng import run_collect
        try:
            meta, stat = run_collect(sectionize=True)
            for win in _windows:
                try:
                    win.evaluate_js("location.reload();")
                except Exception:
                    pass
            return (f"采集完成：库内共 {meta['total']} 条，"
                    f"本次新抓 {stat['fetched']}、失败 {stat['failed']}，"
                    f"耗时 {stat['elapsed']:.0f}s")
        except Exception as e:
            return f"采集失败：{e}"

    def current_status(self):
        js = DATA_DIR / "data.js"
        if not js.exists():
            return "从未采集"
        mtime = datetime.fromtimestamp(js.stat().st_mtime)
        age_h = (datetime.now() - mtime).total_seconds() / 3600
        return f"数据更新于 {mtime:%m-%d %H:%M}（{age_h:.1f} 小时前）"

    def get_deadlines(self):
        """返回临期事项列表(JSON 字符串)，供前端横幅显示。"""
        return json.dumps(_check_deadlines(), ensure_ascii=False)

    def dismiss(self, title):
        """永久忽略某条临期事项（写入 dismissed.json）。"""
        dis = DATA_DIR / "dismissed.json"
        arr = []
        if dis.exists():
            try:
                arr = json.loads(dis.read_text(encoding="utf-8"))
            except Exception:
                arr = []
        if not any(d.get("t") == title for d in arr):
            arr.append({"t": title})
            dis.write_text(json.dumps(arr, ensure_ascii=False), encoding="utf-8")
        return "ok"


_windows = []


def _needs_refresh():
    js = DATA_DIR / "data.js"
    if not js.exists():
        return True
    age = datetime.now() - datetime.fromtimestamp(js.stat().st_mtime)
    return age > timedelta(hours=6)


def main():
    import webview

    auto_refresh = "--no-refresh" not in sys.argv
    autostart = "--autostart" in sys.argv

    from serve import StaticServer
    srv = StaticServer(root=str(ROOT))
    url = srv.start()

    api = Api()
    win = webview.create_window(
        "大工资讯聚合 · 盘锦校区新生助手",
        url,
        width=1380, height=900, min_size=(980, 640),
        js_api=api, background_color="#f4f6f9",
    )
    _windows.append(win)

    def _boot():
        if autostart:
            try:
                win.hide()
            except Exception:
                pass
        if auto_refresh and _needs_refresh():
            threading.Thread(target=api.refresh_data, daemon=True).start()
        if autostart:
            # 开机模式：有临期才弹窗，无则静默后台
            if _check_deadlines():
                try:
                    win.show()
                except Exception:
                    pass

    try:
        webview.start(_boot)
    finally:
        srv.stop()
        print("已退出")


if __name__ == "__main__":
    main()
