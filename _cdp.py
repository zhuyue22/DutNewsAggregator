# -*- coding: utf-8 -*-
"""通过 CDP 连到真实 Edge，探测页面的真实 DOM/JS 状态。"""
import json, os, sys, urllib.request

# 需要 websocket 客户端。优先用 websocket-client，否则用 node 侧做。
def get_ws_url():
    d = json.load(urllib.request.urlopen("http://127.0.0.1:9222/json"))
    for p in d:
        if p.get("type") == "page" and p.get("url", "").startswith("http://127.0.0.1:8799"):
            return p["webSocketDebuggerUrl"]
    raise RuntimeError("找不到目标页面")

if __name__ == "__main__":
    print(get_ws_url())
