# -*- coding: utf-8 -*-
"""
collector_eng.py — 大工资讯聚合采集器 · 工程化版

与 collector.py 的关系：
  - 复用 collector.py 的全部纯算法（打标引擎 tag_article / apply_profile /
    deadline 提取 / 标题·日期·正文提取），通过 import 引入，不重复造轮子。
  - 但把"管线"换成工程化底座 eng_lib：
        * 信源：  sources.json 配置驱动（加源不改代码）
        * 抓取：  RetryingFetcher（指数退避 + UA 轮换 + 编码探测）
                  + PerHostLimiter（每域名令牌桶限速，防反爬封号）
        * 存储：  SqliteStore（url_hash 唯一约束可靠去重，替代 JSON+内存set）
        * 历史：  每次 run 落库，失败项可追溯、可重试
        * 安全：  UrlGuard（限 http/https，拒内网/回环，防 SSRF）
  - 输出仍写 data/data.js（window.DUT_DATA 格式）→ 前端 index.html 无需改动。

用法：
  python collector_eng.py            # 正常增量抓取
  python collector_eng.py --limit 2  # 只抓前 2 个信源（联调/测试用）
  python collector_eng.py --failures # 查看最近失败项
  python collector_eng.py --runs     # 查看运行历史
"""
import os
import re
import sys
import json
import html
import time
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# ---------------- 复用原算法（纯函数，不触发 collector.py 的 main） ----------------
import collector  # noqa  (import 时 __name__ 非 __main__，不会执行 main)
from collector import (tag_article, apply_profile, extract_deadline,
                       extract_title, extract_date, extract_content_text,
                       cut_summary, clean_text, MAX_PER_SOURCE)

# ---------------- 工程化底座 ----------------
from eng_lib import (SqliteStore, RetryingFetcher, PerHostLimiter,
                     UrlGuard)

ROOT = Path(__file__).parent
# 兼容 PyInstaller 打包：采集数据写到用户可写目录（_MEIPASS 只读）
_env_data = os.environ.get("DUT_DATA_DIR", "")
if _env_data:
    DATA_DIR = Path(_env_data)
elif getattr(sys, "frozen", False):
    DATA_DIR = Path(os.getenv("LOCALAPPDATA", ROOT)) / "DutNewsAggregator" / "data"
else:
    DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "collector.db"
SOURCES_FILE = ROOT / "sources.json"
MAX_TOTAL = 1500


# ---------------------------------------------------------------- 信源加载（配置驱动）
def load_sources(cfg_path=SOURCES_FILE):
    """从 sources.json 读信源配置。信源变更只需改 JSON，不动代码。"""
    with open(cfg_path, encoding="utf-8") as f:
        raw = json.load(f)
    srcs = []
    for s in raw["sources"]:
        srcs.append({
            "key": s["key"],
            "name": s["name"],
            "campus": s["campus"],
            "list": s["listUrl"],
            "link_re": s["linkRe"],
            "base": s.get("baseUrl", ""),
            "per_host_qps": s.get("perHostQps", 1.5),
        })
    return srcs


# ---------------------------------------------------------------- 列表页解析（用集体器的 link_re）
def parse_list_with_fetcher(fetcher, src):
    """抓列表页并通过 src['link_re'] 提取文章链接。返回 [{'url','title'}]。"""
    res = fetcher.fetch(src["list"])
    if not res.ok:
        return [], res.error if res.status == 0 else f"HTTP {res.status}"
    page = res.text
    plow = page.lower()
    found, seen = [], set()
    for m in re.finditer(src["link_re"], page):
        href = html.unescape(m.group(1))
        url = UrlGuard.normalize(href, src["list"])
        if url in seen or "javascript" in url or not UrlGuard.validate(url):
            continue
        seen.add(url)
        # 提取 <a> 内标题（与原版逻辑一致）
        start = plow.rfind("<a ", max(0, m.start() - 400), m.start() + len(m.group(0)))
        end = plow.find("</a>", m.end())
        block = page[start:end + 4] if start != -1 and end != -1 else ""
        title = ""
        if block:
            tm = re.search(r'title="([^"]+)"', block)
            if tm:
                title = tm.group(1).strip()
            if not title:
                lines = [ln.strip() for ln in re.sub(r"<[^>]+>", "\n", block).split("\n")
                         if ln.strip()]
                title = ""
                for ln in lines:
                    title += ln
                    if len(title) >= 15:
                        break
                if not title and lines:
                    title = lines[0]
        title = re.sub(r"\s+", " ", html.unescape(title)).strip()
        if 4 <= len(title) <= 150 and not re.search(r"(首页|登录|更多|MORE|上一页|下一页)", title):
            found.append({"url": url, "title": title})
        if len(found) >= MAX_PER_SOURCE:
            break
    return found, ""


# ---------------------------------------------------------------- 正文抓取 + 复用集体器提取
def scrape_item(fetcher, item, src, store, run_id, reuse_recent):
    """抓单篇文章 → 复用 collector 的标题/日期/正文/打标算法 → 写 SQLite。

    返回 (status, title)。status ∈ {fetched, reused, failed}。
    """
    url = item["url"]
    if url in reuse_recent:
        store.record_outcome(run_id, url, "reused")
        return "reused", item["title"]
    res = fetcher.fetch(url)
    if not res.ok:
        store.record_outcome(run_id, url, "failed", res.error)
        return "failed", item["title"]
    page = res.text

    # 复用 collector 的提取算法（纯函数，入参一致）
    title = extract_title(page, item["title"])
    date = extract_date(page, title)
    full_text = extract_content_text(page)
    summary = cut_summary(full_text)
    info = tag_article(title, full_text or title, src["name"], src["campus"])
    info = apply_profile(info, title, full_text or title, src["name"], src["campus"])

    store.upsert_item(
        url=url, title=title, date=date, summary=summary,
        source=src["name"], campus=info["campus"],
        category=info["category"], audience=info["audience"],
        college=info["college"], tags=info["tags"],
        importance=info["importance"], importance_reason=info["importanceReason"],
        baoyan=info["baoyan"], staff=info["staff"], deadline=info.get("deadline", ""))
    store.record_outcome(run_id, url, "fetched")
    return "fetched", title


# ---------------------------------------------------------------- 导出（兼容前端）
def export(store, out_delta=None):
    """从 SQLite 导出，写 data/data.js（window.DUT_DATA 格式），前端无需改动。"""
    articles = store.export_articles(limit=MAX_TOTAL)
    panjin = sum(1 for a in articles if a["campus"] == "盘锦校区")
    meta = {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "total": len(articles),
        "panjinCount": panjin,
        "archived": 0,
        "engine": "collector_eng",
    }
    (DATA_DIR / "data.js").write_text(
        "window.DUT_DATA=" + json.dumps({"meta": meta, "articles": articles},
                                        ensure_ascii=False) + ";",
        encoding="utf-8")
    return meta


# ---------------------------------------------------------------- 主流程
def run_collect(limit=None, quiet=False, sectionize=True):
    """采集主流程，可被命令行或桌面 app 调用。

    参数：
      limit      : 只抓前 N 个信源（联调用）
      quiet      : True 时不打印
      sectionize : 采集后自动运行 sectionize 给新数据打「板块」字段
    返回：(meta, 统计dict)
    """
    def log(msg):
        if not quiet:
            print(msg)

    sources = load_sources()
    if limit:
        sources = sources[:limit]
        log(f"[模式] --limit={limit}（只抓 {len(sources)} 个信源，联调用）")
    log(f"[信源] 共加载 {len(sources)} 个（配置驱动 sources.json，加源不改代码）")

    limiter = PerHostLimiter(per_host=4.0, refill=1.5)
    fetcher = RetryingFetcher(timeout=20, max_retries=3, limiter=limiter)

    store = SqliteStore(DB_PATH)
    reuse_recent = store.recent_url_hashes()

    run_id = store.start_run(mode="limit" if limit else "normal")
    t0 = time.time()
    n_fetched = n_reused = n_failed = 0

    for src in sources:
        items, lerr = parse_list_with_fetcher(fetcher, src)
        if lerr:
            log(f"[列表失败] {src['name']}: {lerr}")
            continue
        log(f"[{src['name']}] 列表解析 {len(items)} 条")
        with ThreadPoolExecutor(max_workers=4) as ex:
            futures = {ex.submit(scrape_item, fetcher, it, src, store, run_id,
                                 reuse_recent): it for it in items}
            for fut in as_completed(futures):
                st, _ = fut.result()
                if st == "fetched":
                    n_fetched += 1
                elif st == "reused":
                    n_reused += 1
                else:
                    n_failed += 1
        time.sleep(0.8)

    elapsed = time.time() - t0
    store.finish_run(run_id, store.count(), n_fetched, n_reused, n_failed, elapsed)

    meta = export(store)
    log(f"[完成] 库内共 {meta['total']} 条（盘锦 {meta['panjinCount']}）；"
        f"本次新抓 {n_fetched}、复用 {n_reused}、失败 {n_failed}；"
        f"耗时 {elapsed:.0f}s")
    store.close()

    # 采集后自动给新数据打「板块」，保证前端五大板块导航可用
    if sectionize:
        try:
            import sectionize
            data, prefix = sectionize.load()
            n = 0
            for a in data["articles"]:
                a["section"] = sectionize.classify(a)
                n += 1
            with open(sectionize.DATA_PATH, "w", encoding="utf-8") as f:
                f.write(prefix + json.dumps(data, ensure_ascii=False))
            log(f"[板块] 已为 {n} 条重新归类")
        except Exception as e:  # pragma: no cover
            log(f"[板块] 归类失败（不影响采集）: {e}")

    return meta, {"fetched": n_fetched, "reused": n_reused,
                  "failed": n_failed, "elapsed": elapsed}


def main():
    args = set(sys.argv[1:])
    if "--failures" in args:
        store = SqliteStore(DB_PATH)
        for f in store.recent_failures():
            print(f"[失败] {f['url']} :: {f['error']}")
        return
    if "--runs" in args:
        store = SqliteStore(DB_PATH)
        for r in store.recent_runs():
            print(f"run#{r['id']} {r['started']}→{r['finished']} "
                  f"total={r['total']} 新抓={r['fetched']} 复用={r['reused']} "
                  f"失败={r['failed']} 耗时={r['elapsed']}s {r['mode']}")
        return

    limit = None
    if "--limit" in args:
        i = sys.argv.index("--limit")
        try:
            limit = int(sys.argv[i + 1])
        except (IndexError, ValueError):
            limit = None

    run_collect(limit=limit)


if __name__ == "__main__":
    main()
