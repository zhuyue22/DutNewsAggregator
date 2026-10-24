# -*- coding: utf-8 -*-
"""
serve.py —— 大工资讯聚合 · 本地静态服务器（纯标准库）

作用：用 HTTP 托管整个站点目录（index.html + data/ + assets/），
供桌面窗口（pywebview）或浏览器访问。绑定 127.0.0.1，仅本机可访问。

用法：
    python serve.py            # 默认 port=8123，若被占用自动 +1
    python serve.py 8080       # 指定端口
可作为模块被 app.py 调用，也可单独运行。
"""
import functools
import http.server
import os
import socket
import sys
import threading

# 兼容 PyInstaller 打包：静态资源在 sys._MEIPASS 解包目录
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.abspath(getattr(sys, "_MEIPASS", os.path.dirname(__file__)))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PORT = 8123
HOST = "127.0.0.1"

# 允许的静态扩展名（拒绝 .py/.db 等敏感文件被浏览器直接下载）
SAFE_EXT = {
    ".html", ".js", ".css", ".json", ".txt", ".png", ".jpg", ".jpeg",
    ".gif", ".svg", ".webp", ".ico", ".woff", ".woff2", ".ttf",
}
BLOCKED_NAMES = {"collector.py", "collector_eng.py", "eng_lib.py", "sectionize.py",
                 "serve.py", "app.py", "sources.json", "fav_urls.txt.done"}


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    """SimpleHTTPRequestHandler 的子类：静默日志 + 访问安全检查。"""

    def log_message(self, fmt, *args):  # 不向控制台刷日志
        pass

    def _forbidden(self):
        self.send_error(403, "Forbidden")

    def do_GET(self):
        # 预检路径安全；不安全直接 403，不让父类去碰文件系统
        if self._unsafe():
            self._forbidden()
            return
        super().do_GET()

    def _unsafe(self):
        import posixpath
        import urllib.parse
        p = urllib.parse.unquote(self.path).split("?", 1)[0]
        norm = posixpath.normpath(p).lstrip("/")
        base = posixpath.basename(norm)
        if base in BLOCKED_NAMES:
            return True
        ext = os.path.splitext(base)[1].lower()
        if os.path.isfile(os.path.join(self.directory, norm)) and ext not in SAFE_EXT:
            return True
        # 越界（..）检查
        full = os.path.abspath(os.path.join(self.directory, norm))
        if not full.startswith(os.path.abspath(self.directory)):
            return True
        return False

    def translate_path(self, path):
        # 基于 self.directory（由 partial 注入）
        import posixpath
        import urllib.parse
        p = urllib.parse.unquote(path)
        p = posixpath.normpath(p).lstrip("/")
        if p == "" or p == ".":
            p = "index.html"
        return os.path.join(self.directory, p)


def _find_free_port(preferred):
    """从 preferred 起找空闲端口。"""
    port = preferred
    while port < preferred + 50:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((HOST, port))
                return port
            except OSError:
                port += 1
    raise RuntimeError("找不到可用端口")


class StaticServer:
    """封装一个 HTTP server；start() 返回 (url, port)。"""

    def __init__(self, root=BASE_DIR, port=DEFAULT_PORT):
        self.root = os.path.abspath(root)
        self.port = port
        self.url = None
        self._httpd = None
        self._thread = None

    def start(self):
        port = _find_free_port(self.port)
        handler = functools.partial(QuietHandler, directory=self.root)
        self._httpd = http.server.ThreadingHTTPServer((HOST, port), handler)
        self.port = self._httpd.server_address[1]
        self.url = f"http://{HOST}:{self.port}/index.html"
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self.url

    def stop(self):
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()


if __name__ == "__main__":
    p = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    srv = StaticServer(port=p)
    url = srv.start()
    print("服务已启动:", url)
    print("按 Ctrl+C 停止")
    try:
        while True:
            import time
            time.sleep(3600)
    except KeyboardInterrupt:
        srv.stop()
