# -*- coding: utf-8 -*-
import re, urllib.request, urllib.parse, http.cookiejar

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")
cj = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://www.bilibili.com"})
    return op.open(req, timeout=15).read().decode("utf-8", "ignore")

get("https://www.bilibili.com/")
kw = urllib.parse.quote("大连理工大学")
page = get(f"https://search.bilibili.com/video?keyword={kw}&order=pubdate")
i = page.find("__pinia")
print("before:", repr(page[i-200:i+150]))
# script 边界
sstart = page.rfind("<script", 0, i)
send = page.find("</script>", i)
print("script range:", sstart, send, "len:", send - sstart)
open("_bili_pinia.js.txt", "w", encoding="utf-8").write(page[sstart:send])
print("saved _bili_pinia.js.txt")
