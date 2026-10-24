# -*- coding: utf-8 -*-
"""
app.py —— 大工资讯聚合 · 桌面软件壳（pywebview + 本地服务器）

把「采集 + 展示」封装成自带原生窗口的桌面软件：
  1. 启动本地静态服务器（serve.py）托管站点
  2. 打开 pywebview 窗口（自带标题栏），加载 index.html
  3. 若本地数据过期/缺失，后台线程自动跑一次采集（不阻塞窗口秒开）
  4. 前端“🔄 刷新数据”按钮 → 调用 js_api.refresh_data() 重新采集并刷新页面
  5. 关闭窗口 → 自动停服务器

运行：
  python app.py                # 桌面软件
  python app.py --no-refresh   # 启动时不自动采集
打包：
  PyInstaller 见 build.bat / 说明文档
"""
import os
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path

# 兼容 PyInstaller 打包：运行时静态资源在 _MEIPASS 解包目录
if getattr(sys, "frozen", False):
    ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
else:
    ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"


class Api:
    """暴露给前端 JavaScript 调用的 Python 方法（window.pywebview.api.*）。"""

    def refresh_data(self):
        """前台手动触发：采集 → 归类 → 刷新页面。返回结果字符串。"""
        from collector_eng import run_collect
        try:
            meta, stat = run_collect(sectionize=True)
            # 让前端重载数据
            for win in _windows:
                try:
                    win.evaluate_js("location.reload();")
                except Exception:
                    pass
            return (f"采集完成：库内共 {meta['total']} 条，"
                    f"本次新抓 {stat['fetched']}、失败 {stat['failed']}，"
                    f"耗时 {stat['elapsed']:.0f}s")
        except Exception as e:  # pragma: no cover
            return f"采集失败：{e}"

    def current_status(self):
        """返回数据文件的新鲜度，供前端显示。"""
        js = DATA_DIR / "data.js"
        if not js.exists():
            return "从未采集"
        mtime = datetime.fromtimestamp(js.stat().st_mtime)
        age_h = (datetime.now() - mtime).total_seconds() / 3600
        return f"数据更新于 {mtime:%m-%d %H:%M}（{age_h:.1f} 小时前）"


_windows = []


def _needs_refresh():
    """数据缺失或超过 6 小时 → 需要自动采集。"""
    js = DATA_DIR / "data.js"
    if not js.exists():
        return True
    age = datetime.now() - datetime.fromtimestamp(js.stat().st_mtime)
    return age > timedelta(hours=6)


def main():
    import webview

    auto_refresh = "--no-refresh" not in sys.argv

    from serve import StaticServer
    srv = StaticServer(root=str(ROOT))
    url = srv.start()
    print("本地服务:", url)

    api = Api()
    win = webview.create_window(
        "大工资讯聚合 · 盘锦校区新生助手",
        url,
        width=1380,
        height=900,
        min_size=(980, 640),
        js_api=api,
        background_color="#f4f6f9",
    )
    _windows.append(win)

    def _boot():
        # 窗口打开后，视需要后台采集一次
        if auto_refresh and _needs_refresh():
            print("检测到数据过期，后台开始自动采集…")
            threading.Thread(target=api.refresh_data, daemon=True).start()

    try:
        webview.start(_boot)
    finally:
        srv.stop()
        print("已退出")


if __name__ == "__main__":
    main()
