# -*- coding: utf-8 -*-
"""
eng_lib.py — 工程化基础设施层（从"脚本"到"产品"的可复用底座）

设计动机（对应 collector.py 的工程化差距）：
  1. RateLimiter      : 不像原版靠固定 sleep，而是"每信源令牌桶限速"。
                        封号/反爬的根因是瞬时 QPS 过高，令牌桶让每个域名独立、平滑限速。
  2. RetryingFetcher  : 指数退避 + UA 轮换 + 超时 + 状态码校验，替代裸 requests/urllib。
  3. SqliteStore      : 用 SQLite + url_hash 唯一约束做可靠去重（替代 JSON + 内存 set），
                        并记录抓取历史，可查询"哪些成功、哪些失败、失败原因"。
  4. UrlGuard         : URL 规范化 + 协议校验，防 SSRF（只允许 http/https，拒绝内网/回环）。
  5. run 历史        : 每次抓取都落库一条 run + 每篇 item 的 outcome，失败可追溯、可重试。

依赖：仅 Python 标准库（sqlite3, threading, time, re, random, urllib）。
"""
import os
import re
import io
import time
import json
import random
import sqlite3
import threading
import urllib.request
import urllib.parse
from datetime import datetime
from pathlib import Path

try:
    from PIL import Image, ImageStat
    _HAS_PIL = True
except Exception:
    _HAS_PIL = False


# ---------------------------------------------------------------- 令牌桶限速器
class RateLimiter:
    """令牌桶：容量 capacity，每秒补充 refill 个令牌。used 满则阻塞等待。

    相比"固定 sleep"：固定 sleep 在慢源浪费、在快源仍可能瞬时打爆；
    令牌桶保证任意 1 秒内最多冲出去 refill 次，平滑且可给不同信源不同配额。
    """
    def __init__(self, capacity: float = 5.0, refill: float = 2.0):
        self.capacity = float(capacity)
        self.refill = float(refill)
        self._tokens = float(capacity)
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, n: float = 1.0) -> float:
        with self._lock:
            # 先按流逝时间补充令牌
            now = time.monotonic()
            self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.refill)
            self._last = now
            if self._tokens >= n:
                self._tokens -= n
                return 0.0
            deficit = n - self._tokens
            wait = deficit / self.refill
            self._tokens = 0.0
        if wait > 0:
            time.sleep(wait)
        return wait


class PerHostLimiter:
    """按域名分桶：每个 host 独立限速，避免一个慢站拖垮全局、或一个快站打爆对方。"""
    def __init__(self, per_host: float = 4.0, refill: float = 1.5):
        self.per_host = per_host
        self.refill = refill
        self._buckets: dict = {}
        self._lock = threading.Lock()

    @staticmethod
    def host_of(url: str) -> str:
        try:
            return urllib.parse.urlparse(url).netloc.lower()
        except Exception:
            return "unknown"

    def acquire(self, url: str) -> None:
        host = self.host_of(url)
        with self._lock:
            b = self._buckets.get(host)
            if b is None:
                b = RateLimiter(self.per_host, self.refill)
                self._buckets[host] = b
        b.acquire(1.0)


# ---------------------------------------------------------------- URL 校验（防 SSRF）
_IPV4_RE = re.compile(r"\d{1,3}(?:\.\d{1,3}){3}")

def _is_local_host(host: str) -> bool:
    h = host.lower().rstrip(".")
    if h in ("localhost", "::1"):
        return True
    # 常见内网/保留网段前缀
    for prefix in ("10.", "172.16.", "172.17.", "172.18.", "172.19.",
                   "172.20.", "172.21.", "172.22.", "172.23.", "172.24.",
                   "172.25.", "172.26.", "172.27.", "172.28.", "172.29.",
                   "172.30.", "172.31.", "192.168.", "127.", "0.", "169.254."):
        if h.startswith(prefix):
            return True
    # 形如 URL 编码/其他伪装跳过（简单判断含端口后主体仍匹配 IPv4 段）
    m = _IPV4_RE.search(h)
    if m:
        try:
            parts = [int(x) for x in m.group(0).split(".")]
            a, b, c, d = parts
            if a == 127 or a == 0 or (a == 10) or (a == 169 and b == 254) \
               or (a == 192 and b == 168) or (a == 172 and 16 <= b <= 31):
                return True
        except Exception:
            pass
    return False


class UrlGuard:
    """URL 规范化 + 安全校验。拒绝非 http(s)、内网/回环地址（防 SSRF）。"""
    _SCHEME_RE = re.compile(r"^(https?)://", re.I)

    @staticmethod
    def normalize(href: str, base: str) -> str:
        """把相对链接解析为绝对 URL。"""
        return urllib.parse.urljoin(base, href)

    @staticmethod
    def validate(url: str) -> bool:
        if not url or len(url) > 2048:
            return False
        if not UrlGuard._SCHEME_RE.match(url):
            return False
        try:
            host = urllib.parse.urlparse(url).netloc
        except Exception:
            return False
        if not host:
            return False
        if _is_local_host(host):
            return False
        return True


# ---------------------------------------------------------------- 带重试的抓取器
_DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " \
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
_UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36",
]


class FetchResult:
    __slots__ = ("ok", "status", "text", "final_url", "error")

    def __init__(self, ok, status=0, text="", final_url="", error=""):
        self.ok = ok
        self.status = status
        self.text = text
        self.final_url = final_url
        self.error = error


class RetryingFetcher:
    """带指数退避重试 + UA 轮换 + 超时 + 编码探测的 HTTP 抓取器。

    相比裸 urllib：失败自动退避重试（0.5s,1s,2s,4s），每次换 UA，
    自动解析 charset 并回退 utf-8/gb18030。可挂 per-host 限速器。
    """
    def __init__(self, timeout=20, max_retries=3, limiter=None, ua=None):
        self.timeout = timeout
        self.max_retries = max_retries
        self.limiter = limiter
        self.ua = ua or _DEFAULT_UA

    def _headers(self):
        return {
            "User-Agent": random.choice(_UA_POOL),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }

    @staticmethod
    def _decode(raw: bytes, content_type: str) -> str:
        m = re.search(r"charset=([\w-]+)", content_type or "", re.I)
        cands = [m.group(1) if m else None, None, "utf-8", "gb18030"]
        # 若无显式 charset，尝试从 HTML head 读
        if cands[0] is None:
            head = raw[:4000].decode("ascii", "ignore")
            m2 = re.search(r"charset=([\w-]+)", head, re.I)
            cands[1] = m2.group(1) if m2 else None
        for enc in cands:
            if not enc:
                continue
            try:
                return raw.decode(enc, "ignore")
            except (LookupError, UnicodeDecodeError):
                continue
        return raw.decode("utf-8", "ignore")

    def fetch(self, url: str) -> FetchResult:
        if self.limiter is not None:
            self.limiter.acquire(url)
        last_err = ""
        for attempt in range(self.max_retries + 1):
            req = urllib.request.Request(url, headers=self._headers())
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    raw = r.read()
                    ctype = r.headers.get("Content-Type", "")
                    return FetchResult(True, r.status, self._decode(raw, ctype),
                                       r.geturl(), "")
            except urllib.error.HTTPError as e:
                # 4xx 一般重试无意义（对方明确拒绝/不存在），直接放弃
                if 400 <= e.code < 500:
                    return FetchResult(False, e.code, "", "", f"HTTP {e.code}")
                last_err = f"HTTP {e.code}"
            except urllib.error.URLError as e:
                last_err = str(e.reason)
            except Exception as e:  # 超时、连接重置等
                last_err = str(e)
            if attempt < self.max_retries:
                time.sleep(0.5 * (2 ** attempt) + random.uniform(0, 0.3))
        return FetchResult(False, 0, "", "", last_err)


# ---------------------------------------------------------------- SQLite 持久化（去重 + 历史）
SCHEMA = """
CREATE TABLE IF NOT EXISTS sources(
    key TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    campus TEXT NOT NULL,
    list_url TEXT NOT NULL,
    link_re TEXT NOT NULL,
    base_url TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    per_host_qps REAL NOT NULL DEFAULT 1.5,
    created TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS items(
    url_hash TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    date TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    campus TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '',
    audience TEXT NOT NULL DEFAULT '',
    college TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '',
    importance INTEGER NOT NULL DEFAULT 0,
    importance_reason TEXT NOT NULL DEFAULT '',
    baoyan INTEGER NOT NULL DEFAULT 0,
    staff INTEGER NOT NULL DEFAULT 0,
    deadline TEXT NOT NULL DEFAULT '',
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_log(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started TEXT NOT NULL,
    finished TEXT,
    mode TEXT NOT NULL DEFAULT 'normal',
    total INTEGER NOT NULL DEFAULT 0,
    fetched INTEGER NOT NULL DEFAULT 0,
    reused INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    elapsed REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS item_outcome(
    run_id INTEGER NOT NULL,
    url_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(run_id, url_hash)
);

CREATE INDEX IF NOT EXISTS idx_items_date ON items(date);
CREATE INDEX IF NOT EXISTS idx_items_campus ON items(campus);
CREATE INDEX IF NOT EXISTS idx_items_source ON items(source);
"""


def _url_hash(url: str) -> str:
    import hashlib
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]


class SqliteStore:
    """SQLite 存储：url_hash 主键保证天然去重（比 JSON+内存 set 可靠）。

    - upsert_item: 按 url_hash 去重，存在则更新 last_seen，不产生重复行。
    - 记录 run_log + item_outcome，失败可追溯、可重试。
    - 兼容前端：仍可导出 window.DUT_DATA 格式的 data.js。
    """
    def __init__(self, db_path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False + 写锁：允许采集工作线程并发写，由锁保证串行且安全
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self.conn.executescript(SCHEMA)

    # ---- 信源 ----
    def save_source(self, key, name, campus, list_url, link_re, base_url="", per_host_qps=1.5):
        with self.conn:
            self.conn.execute(
                "INSERT INTO sources(key,name,campus,list_url,link_re,base_url,enabled,per_host_qps,created) "
                "VALUES(?,?,?,?,?,?,1,?,?) "
                "ON CONFLICT(key) DO UPDATE SET name=excluded.name,campus=excluded.campus,"
                "list_url=excluded.list_url,link_re=excluded.link_re,base_url=excluded.base_url,"
                "per_host_qps=excluded.per_host_qps",
                (key, name, campus, list_url, link_re, base_url, per_host_qps,
                 datetime.now().strftime("%Y-%m-%d %H:%M")))
            return self.conn.total_changes

    def list_sources(self, enabled_only=True):
        q = "SELECT * FROM sources" + (" WHERE enabled=1" if enabled_only else "") + " ORDER BY name"
        with self._lock:
            return [dict(r) for r in self.conn.execute(q).fetchall()]

    # ---- 去重 upsert ----
    def upsert_item(self, url, title="", date="", summary="", source="", campus="",
                    category="", audience="", college="", tags="", importance=0,
                    importance_reason="", baoyan=False, staff=False, deadline=""):
        h = _url_hash(url)
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        def _j(v):
            if isinstance(v, (list, tuple)):
                return json.dumps(list(v), ensure_ascii=False)
            return v
        with self._lock:
            with self.conn:
                self.conn.execute(
                    "INSERT INTO items(url_hash,url,title,date,summary,source,campus,category,"
                    "audience,college,tags,importance,importance_reason,baoyan,staff,deadline,first_seen,last_seen) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(url_hash) DO UPDATE SET last_seen=excluded.last_seen",
                    (h, url, title, date, summary, source, campus, category,
                     _j(audience), _j(college), _j(tags),
                     importance, importance_reason, 1 if baoyan else 0, 1 if staff else 0,
                     deadline, now, now))
        return h

    def has_url(self, url):
        h = _url_hash(url)
        with self._lock:
            return self.conn.execute("SELECT 1 FROM items WHERE url_hash=?", (h,)).fetchone() is not None

    def recent_url_hashes(self, limit=None):
        q = "SELECT url_hash FROM items WHERE last_seen >= date('now','-7 day')"
        with self._lock:
            rows = self.conn.execute(q).fetchall()
            return {r["url_hash"] for r in rows}

    def count(self):
        with self._lock:
            return self.conn.execute("SELECT COUNT(*) c FROM items").fetchone()["c"]

    # ---- run 历史 / 失败追溯 ----
    def start_run(self, mode="normal"):
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO run_log(started,mode) VALUES(?,?)",
                (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), mode))
            self.conn.commit()
            return cur.lastrowid

    def finish_run(self, run_id, total, fetched, reused, failed, elapsed):
        with self._lock:
            with self.conn:
                self.conn.execute(
                    "UPDATE run_log SET finished=?,total=?,fetched=?,reused=?,failed=?,elapsed=? WHERE id=?",
                    (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), total, fetched,
                     reused, failed, round(elapsed, 1), run_id))

    def record_outcome(self, run_id, url, status, error=""):
        h = _url_hash(url)
        with self._lock:
            with self.conn:
                self.conn.execute(
                    "INSERT OR IGNORE INTO item_outcome(run_id,url_hash,status,error) VALUES(?,?,?,?)",
                    (run_id, h, status, error[:500]))

    def recent_failures(self, limit=50):
        with self._lock:
            rows = self.conn.execute(
                "SELECT o.url_hash,i.url,i.title,o.error,o.run_id "
                "FROM item_outcome o LEFT JOIN items i ON o.url_hash=i.url_hash "
                "WHERE o.status='failed' "
                "ORDER BY o.run_id DESC LIMIT ?", (limit,)).fetchall()
            return [dict(r) for r in rows]

    def recent_runs(self, limit=10):
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM run_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            return [dict(r) for r in rows]

    # ---- 导出（兼容前端）----
    def export_articles(self, limit=1500):
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM items ORDER BY date DESC, title LIMIT ?", (limit,)).fetchall()
        arts = []
        for r in rows:
            try:
                tags = json.loads(r["tags"]) if r["tags"] else []
            except Exception:
                tags = []
            arts.append({
                "title": r["title"], "url": r["url"], "date": r["date"],
                "summary": r["summary"], "source": r["source"], "campus": r["campus"],
                "category": r["category"],
                "audience": json.loads(r["audience"]) if r["audience"] else [],
                "college": json.loads(r["college"].replace("'", '"')) if r["college"] else [],
                "tags": tags,
                "importance": r["importance"],
                "importanceReason": r["importance_reason"],
                "baoyan": bool(r["baoyan"]), "staff": bool(r["staff"]),
                "deadline": r["deadline"] or "",
            })
        return arts

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass

