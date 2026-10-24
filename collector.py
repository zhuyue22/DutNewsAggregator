# -*- coding: utf-8 -*-
"""
大连理工大学资讯聚合采集器
- 从各官方站点采集文章：标题 / 时间 / 原文简介 / 链接
- 自动分类：盘锦校区 vs 非盘锦
- 自动打标：适用年级、学生工作、分类、重要度指数、领域关键词标签
- 输出：data/articles.json 与 data.js（前端直接引用）
仅使用 Python 标准库。
"""
import io
import re
import sys
import json
import html
import time
import shutil
import subprocess
import http.cookiejar
import urllib.request
import urllib.parse
from pathlib import Path
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
MAX_PER_SOURCE = 40        # 每源列表抓取量：扩大到覆盖近 30 天窗口（保证 30 天内不漏）
ARCHIVE_DAYS = 90          # 滚动存档天数：90 天内文章保留
FRESH_DAYS = 30            # 「新鲜窗口」：30 天内完全重扫，墓碑不拦截
MAX_TOTAL = 1500           # 存档总量上限（超出按日期截断）
LOG_FILE = DATA_DIR / "collect.log"
LOG_KEEP_DAYS = 7
TIMEOUT = 20
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# ---------------------------------------------------------------- 日志（落盘，保留最近 7 天）
class _Tee(io.TextIOBase):
    """print 同时写控制台与日志文件。"""
    def __init__(self, stream, fh):
        self.stream, self.fh = stream, fh
    def write(self, s):
        self.stream.write(s)
        self.fh.write(s)
        return len(s)
    def flush(self):
        self.stream.flush()
        try:
            self.fh.flush()
        except ValueError:
            pass


def start_log():
    """开启日志：先按运行头截掉 7 天前的记录，再 tee 标准输出。"""
    DATA_DIR.mkdir(exist_ok=True)
    old = ""
    if LOG_FILE.exists():
        try:
            old = LOG_FILE.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            old = ""
    cutoff = (datetime.now() - timedelta(days=LOG_KEEP_DAYS)).strftime("%Y-%m-%d")
    kept, chunks = "", old.split("===== ")
    for ch in chunks[1:]:
        if ch[:10] >= cutoff:
            kept += "===== " + ch
    fh = open(LOG_FILE, "w", encoding="utf-8")
    if kept:
        fh.write(kept.rstrip() + "\n")
    fh.write(f"===== {datetime.now():%Y-%m-%d %H:%M:%S} 采集开始 =====\n")
    sys.stdout = _Tee(sys.stdout, fh)
    return fh


# ---------------------------------------------------------------- 存档（90 天滚动 + 增量采集）
PRUNED_FILE = DATA_DIR / "pruned.json"   # 已淘汰 URL 墓碑：{url: 文章日期}，防止旧文每轮被重抓
FAV_FILE = DATA_DIR / "fav_urls.txt"     # 收藏白名单：每行一个 URL，收藏的文章永不删除
PRUNED_KEEP_DAYS = 400


def load_archive():
    """读取上次采集结果 → (url→文章 dict, 墓碑 dict)。旧数据无 collected 字段则补为今天。"""
    aj = DATA_DIR / "articles.json"
    arch = {}
    if aj.exists():
        try:
            arts = json.loads(aj.read_text(encoding="utf-8"))["articles"]
        except Exception:
            arts = []
        today = datetime.now().strftime("%Y-%m-%d %H:%M")
        for a in arts:
            a.setdefault("collected", today)
            arch[a["url"]] = a
    pruned = {}
    if PRUNED_FILE.exists():
        try:
            pruned = json.loads(PRUNED_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return arch, pruned


def save_pruned(pruned_map):
    cutoff = (datetime.now() - timedelta(days=PRUNED_KEEP_DAYS)).strftime("%Y-%m-%d")
    pruned_map = {u: d for u, d in pruned_map.items() if d >= cutoff}
    PRUNED_FILE.write_text(json.dumps(pruned_map, ensure_ascii=False, indent=0),
                           encoding="utf-8")


def load_fav():
    """读取收藏白名单 URL 集合（前端导出 data/fav_urls.txt）。"""
    if not FAV_FILE.exists():
        return set()
    try:
        return {l.strip() for l in FAV_FILE.read_text(encoding="utf-8").splitlines()
                if l.strip().startswith("http")}
    except Exception:
        return set()


def is_pruned_live(url, pruned_map):
    """判断 URL 是否被墓碑拦截。30 天内（新鲜窗口）的文章忽略墓碑，强制重扫。"""
    if url not in pruned_map:
        return False
    d = (pruned_map.get(url) or "")[:10]
    try:
        ad = datetime.strptime(d, "%Y-%m-%d").date()
    except ValueError:
        return True  # 无日期可判断，按墓碑处理
    return (datetime.now().date() - ad).days > FRESH_DAYS


def article_date_str(a):
    return (a.get("date") or a.get("collected") or "")[:10]


def pass_retention(a, fav=None):
    """保留规则：90 天内的文章保留；收藏过的永不删；超过 90 天且未收藏则删。"""
    today = datetime.now().date()
    cutoff = today - timedelta(days=ARCHIVE_DAYS)
    d = article_date_str(a)
    try:
        ad = datetime.strptime(d, "%Y-%m-%d").date()
        in_window = ad >= cutoff
    except ValueError:
        in_window = True  # 日期解析不了，保守保留
    if in_window:
        return True
    # 超出 90 天：若有未过的截止日，或处于收藏白名单，则保留
    dl = a.get("deadline") or ""
    try:
        if dl and datetime.strptime(dl, "%Y-%m-%d").date() >= today:
            return True
    except ValueError:
        pass
    if fav and a["url"] in fav:
        return True
    return False


def cut_summary(text, limit=150):
    """简介截断：优先在句号/问号/叹号处收束，避免断在半句。"""
    text = re.sub(r"\s+", "", text)
    if len(text) <= limit:
        return text
    head = text[:limit - 3]
    m = list(re.finditer(r"[。！？!?]", head))
    if m and m[-1].start() >= 50:   # 50 字以内没有句号才硬截
        return head[:m[-1].start() + 1]
    return head + "…"


# ---------------------------------------------------------------- 信源配置
SOURCES = [
    # ---- 盘锦校区 ----
    {"name": "盘锦校区·通知公告", "campus": "盘锦校区",
     "list": "https://panjin.dlut.edu.cn/tzgg.htm",
     "link_re": r'href="((?:info/\d+/\d+\.htm|content\.jsp\?[^"]*wbnewsid=\d+[^"]*))"'},
    {"name": "盘锦校区·校区文件", "campus": "盘锦校区",
     "list": "https://panjin.dlut.edu.cn/xqwj.htm",
     "link_re": r'href="((?:info/\d+/\d+\.htm|content\.jsp\?[^"]*wbnewsid=\d+[^"]*))"'},
    {"name": "盘锦校区·教学事务部", "campus": "盘锦校区",
     "list": "https://pjteach.dlut.edu.cn/",
     "link_re": r'href="(info/\d+/\d+\.htm)"'},
    {"name": "盘锦校区·学生工作部", "campus": "盘锦校区",
     "list": "https://xsgzb.dlut.edu.cn/index/tzgg.htm",
     "link_re": r'href="(\.\./info/\d+/\d+\.htm)"'},
    {"name": "盘锦校区·党政办公室", "campus": "盘锦校区",
     "list": "https://pjoffice.dlut.edu.cn/",
     "link_re": r'href="(info/\d+/\d+\.htm)"'},
    {"name": "化工海洋与生命学院", "campus": "盘锦校区",
     "list": "https://hyxy.dlut.edu.cn/",
     "link_re": r'href="(info/\d+/\d+\.htm)"'},
    {"name": "盘锦校区·就业信息", "campus": "盘锦校区",
     "list": "https://pjcareer.dlut.edu.cn/",
     "link_re": r'href="(info/\d+/\d+\.htm)"'},
    # ---- 非盘锦（主校区 / 全校） ----
    {"name": "大工主页·通知公告", "campus": "主校区",
     "list": "https://www.dlut.edu.cn/tzgg.htm",
     "link_re": r'href="(info/\d+/\d+\.htm)"'},
    {"name": "学生工作处", "campus": "主校区",
     "list": "https://xsc.dlut.edu.cn/",
     "link_re": r'href="(info/\d+/\d+\.htm)"'},
    {"name": "教务处·教务新闻", "campus": "主校区",
     "list": "https://teach.dlut.edu.cn/jwcxw_bp.htm",
     "link_re": r'href="(info/\d+/\d+\.htm)"'},
    {"name": "教务处·专业通报", "campus": "主校区",
     "list": "https://teach.dlut.edu.cn/zytg_bp.htm",
     "link_re": r'href="(info/\d+/\d+\.htm)"'},
    {"name": "新闻中心", "campus": "主校区",
     "list": "https://news.dlut.edu.cn/",
     "link_re": r'href="(info/\d+/\d+\.htm)"'},
    {"name": "研究生院·招生政策", "campus": "主校区",
     "list": "http://gs.dlut.edu.cn/yjszs/zcwj1.htm",
     "link_re": r'href="((?:\.\./)?info/\d+/\d+\.htm)"'},
    {"name": "盘锦校区·团工委", "campus": "盘锦校区",
     "list": "https://pjxqtw.dlut.edu.cn/",
     "link_re": r'href="(info/\d+/\d+\.htm)"'},
    {"name": "图书馆", "campus": "主校区",
     "list": "https://lib.dlut.edu.cn/",
     "link_re": r'href="(info/\d+/\d+\.htm)"'},
    {"name": "国际合作交流处", "campus": "主校区",
     "list": "https://office.dlut.edu.cn/",
     "link_re": r'href="(info/\d+/\d+\.htm)"'},
    {"name": "教务处·考试安排", "campus": "主校区",
     "list": "https://teach.dlut.edu.cn/ksap/ksap.htm",
     "link_re": r'href="(\.\./info/\d+/\d+\.htm|info/\d+/\d+\.htm)"'},
    # ---- 竞赛官网（2026-10 新增；全国性竞赛统一归主校区·全校） ----
    {"name": "竞赛·机械创新设计大赛", "campus": "主校区",
     "list": "http://12umic.hit.edu.cn/20012/list.htm",
     "link_re": r'href=[\'"](/\d{4}/\d{4}/c\d+a\d+/page\.htm)[\'"]'},
    {"name": "竞赛·挑战杯全国赛", "campus": "主校区",
     "list": "https://www.tiaozhanbei.net/tzb",
     "link_re": r'href="(/article/\d+/)"'},
]

# ---------------------------------------------------------------- 标签词典
_Y = datetime.now().year  # 年级按当前年份动态推算，避免每年手动改
USER_ENTRY_YEAR = 2026  # 用户入学年份（2026级）
GRADE_RULES = [
    ("大一·新生", [f"{_Y}级", "新生", "大一", "本科新生", "迎新"]),
    ("大二", [f"{_Y-1}级", "大二"]),
    ("大三", [f"{_Y-2}级", "大三"]),
    ("大四·毕业", [f"{_Y-3}级", "大四", "毕业生", "毕业班", "毕业季"]),
    ("研究生", ["研究生", "硕士", "博士", "博硕"]),
]
AUDIENCE_RULES = [
    ("学生干部", ["学生干部", "班委", "团支部", "社团负责人", "学生骨干", "主要学生干部"]),
    ("党员·积极分子", ["党员", "入党", "党课", "预备党员", "积极分子", "党团组织"]),
    ("毕业年级", ["毕业生", "毕业班", "毕业季"]),
]
COLLEGES = ["化工海洋与生命学院", "海洋科学与技术学院", "商学院", "文法学院", "知识产权学院",
            "莱斯特国际学院", "公共基础学院", "体育与健康学院", "机械工程学院",
            "材料科学与工程学院", "能源与动力学院", "电子信息与电气工程学部",
            "计算机科学与技术学院", "建设工程学部", "运载工程与力学学部", "环境学院",
            "数学科学学院", "物理学院", "化学学院", "外国语学院", "建筑与艺术学院",
            "经济管理学院", "人文与社会科学学部", "光电工程与仪器科学学院", "软件学院",
            "国际信息与软件学院", "医学部", "未来技术学院", "国际教育学院",
            "航空航天学院", "生物工程学院", "张大煜学院", "马克思主义学院"]
SOURCE_COLLEGE = {"化工海洋与生命学院": ["化工海洋与生命学院"]}
GRADE_STUDENT_WORK = ["学生工作", "学生事务", "辅导员", "评奖", "评优", "违纪", "学生干部",
                      "班委", "团支部", "德育", "综合测评", "素拓", "第二课堂"]
CATEGORY_RULES = [
    ("奖助学金", ["奖学金", "助学金", "励志", "助学贷款", "学费补偿", "资助", "补助"]),
    ("竞赛科研", ["竞赛", "大赛", "挑战赛", "创新杯", "大创", "立项", "科研训练", "学术竞赛",
                "数学建模", "电子设计", "机器人", "挑战杯"]),
    ("教务教学", ["选课", "考试", "考场", "成绩", "学籍", "培养方案", "教学安排", "调课",
                "停课", "补课", "缓考", "补考", "四六级", "转专业", "辅修", "学分", "毕设", "毕业论文"]),
    ("就业实习", ["招聘", "就业", "实习", "宣讲会", "双选会", "用人单位", "求职", "offer", "基层就业"]),
    ("生活服务", ["停电", "停水", "供水", "食堂", "餐饮", "班车", "网络", "校园网", "Wi-Fi", "wifi",
                "宿舍", "公寓", "浴室", "供暖", "维修", "施工", "通行", "进校", "停车", "充电",
                "医疗", "医保", "体检", "快递", "洗衣", "直饮水", "洗浴"]),
    ("校园文化", ["晚会", "社团", "文化节", "艺术节", "演出", "音乐会", "展览", "电影", "马拉松",
                "体育", "运动会", "篮球", "足球", "排球", "羽毛球", "乒乓球", "十佳", "歌手", "读书"]),
    ("讲座交流", ["讲座", "讲坛", "报告会", "论坛", "交流会", "访学", "交换生", "国际交流", "游学"]),
    ("党建思政", ["党员", "党课", "党支部", "主题党日", "预备党员", "入党", "党课培训", "团组织", "团日活动"]),
    ("安全管理", ["安全", "消防", "诈骗", "警示", "台风", "暴雨", "防汛", "应急", "实验室安全", "保密"]),
    ("放假校历", ["放假", "校历", "开学", "假期", "值班安排", "节假日"]),
]
# 领域关键词 → 附加标签（从文章内容提炼）
KEYWORD_TAGS = [
    ("奖学金", ["奖学金"]), ("助学金·资助", ["助学金", "资助", "助学贷款", "学费补偿"]),
    ("保研", ["保研", "推免", "免试攻读"]), ("考研", ["考研", "硕士研究生招生考试"]),
    ("四六级", ["四六级", "CET", "英语六级", "英语四级"]),
    ("转专业", ["转专业"]), ("选课", ["选课", "退课", "补选"]), ("学籍异动", ["休学", "复学", "退学", "学籍异动"]),
    ("考试安排", ["考试安排", "期末考试", "期中考试", "考场"]),
    ("挑战杯", ["挑战杯"]), ("数学建模", ["数学建模"]), ("大创项目", ["大创", "大学生创新创业训练"]),
    ("实习就业", ["实习", "就业", "招聘", "宣讲会"]), ("基层就业", ["基层就业", "选调生", "三支一扶", "西部计划"]),
    ("宿舍生活", ["宿舍", "公寓"]), ("食堂餐饮", ["食堂", "餐饮", "饭菜"]),
    ("校园网", ["校园网", "网络", "Wi-Fi", "wifi", "VPN", "统一身份认证"]),
    ("停电停水", ["停电", "停水", "停电通知", "计划停电"]), ("交通班车", ["班车", "交通", "校车"]),
    ("心理支持", ["心理", "心理健康", "心理咨询"]), ("医疗健康", ["医保", "医疗", "体检", "校医院"]),
    ("征兵国防", ["征兵", "国防", "军训", "退役"]), ("出国交流", ["访学", "交换", "留学", "海外", "国际"]),
    ("讲座论坛", ["讲座", "讲坛", "论坛", "报告会"]), ("社团活动", ["社团", "协会", "招新", "纳新"]),
    ("志愿实践", ["志愿", "社会实践", "支教"]), ("荣誉称号", ["十佳", "优秀", "标兵", "表彰", "先进个人"]),
    ("放假通知", ["放假", "假期"]), ("天气预警", ["台风", "暴雨", "大风", "寒潮", "高温"]),
    ("安全教育", ["诈骗", "消防", "安全", "警示"]), ("新生指南", ["新生", "迎新", "报到"]),
    ("校园卡", ["校园卡", "一卡通"]),
    # —— 2026-10 扩充：覆盖原无标签高发类型 ——
    ("校招信息", ["校招", "校园招聘", "秋招", "春招"]),
    ("媒体报道", ["【科学网】", "【新华网】", "【央视", "【人民日报", "【光明日报", "【中国科学报",
                "【中国教育报", "【辽宁日报"]),
    ("图书馆资源", ["数据库", "图书馆", "文献传递", "借阅", "馆藏", "试用通知", "查收查引"]),
    ("制度文件", ["管理办法", "实施细则", "实施方案", "选聘办法", "工作方案", "工作要点", "发展规划",
                "议事协调", "任务分工"]),
    ("知识产权", ["专利", "知识产权", "软著"]),
    ("学科竞赛", ["数学竞赛", "英语竞赛", "物理竞赛", "化学竞赛", "计算机设计", "节能减排"]),
    ("体测体育", ["体质健康", "体测", "体育课", "体能测试"]),
    ("书院活动", ["书院", "自强书院", "校外导师"]),
    ("活动征集", ["征集", "作品征集", "征文", "短视频大赛"]),
    ("办事指南", ["工作月历", "办事", "办理流程", "服务指南", "业务办理"]),
    ("值班安排", ["值班安排", "带班", "总值班"]),
    ("典型宣传", ["师者如光", "学在大工", "组图", "风采展示", "榜样"]),
    ("补缓考", ["补考", "缓考", "补缓考"]),
]
DEADLINE_WORDS = ["截止", "逾期", "报名", "请于", "前提交", "截止日期", "截止时"]
HIGH_WORDS = ["考试", "选课", "放假", "停电", "停水", "奖学金", "助学金", "保研", "推免",
              "转专业", "补考", "缓考", "毕业", "征兵", "助学贷款"]
RESULT_WORDS = ["结果", "公示", "名单", "获奖", "荣获", "喜报", "获评"]

# 保研相关：分类或标签命中即视为与保研目标相关
BAOYAN_CATS = {"竞赛科研", "奖助学金"}
BAOYAN_TAGS = {"保研", "四六级", "荣誉称号", "出国交流", "大创项目", "挑战杯", "数学建模"}
# 教职工向信息：默认在“新生模式”下隐藏
STAFF_RE = re.compile(r"(招聘|拟聘|任职|任免|选拔.*(领导|管理)|进校人员公示|总值班室|"
                      r"博士后|访学总结公示|因公出国|政审|拟聘人员|人才招聘)")
STUDENT_RE = re.compile(r"(学生干部|纳新|本科|研究生|同学|毕业生|宣讲|双选|校园招聘|"
                        r"管培生|招募|应届|届|选拔学生|选聘|训练营|大赛)")


def extract_deadline(title, full_text):
    """从全文中提取报名/提交截止日期（YYYY-MM-DD），无法确定返回空串。"""
    text = title + "。" + full_text
    now = datetime.now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    delta = __import__("datetime").timedelta
    cands = []
    for m in re.finditer(r"(20\d{2})年(\d{1,2})月(\d{1,2})日", text):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        seg = text[max(0, m.start() - 14):m.end() + 10]
        if re.search(r"(截止|截至|之前|前)", seg):
            try:
                cands.append(datetime(y, mo, d))
            except ValueError:
                pass
    for m in re.finditer(r"(\d{1,2})月(\d{1,2})日\s*[-—–~至到]{1,3}\s*(?:\d{1,2}月)?(\d{1,2})日", text):
        seg = text[max(0, m.start() - 16):m.end() + 8]
        if re.search(r"(报名|时间|申请|提交)", seg):
            mo, d = int(m.group(1)), int(m.group(3))
            y = now.year + (1 if (mo, d) < (now.month, now.day) else 0)
            try:
                cands.append(datetime(y, mo, d))
            except ValueError:
                pass
    for m in re.finditer(r"(?:截止|截至|报名|提交)[^。；\n]{0,10}?(\d{1,2})月(\d{1,2})日", text):
        mo, d = int(m.group(1)), int(m.group(2))
        y = now.year + (1 if (mo, d) < (now.month, now.day) else 0)
        try:
            cands.append(datetime(y, mo, d))
        except ValueError:
            pass
    for m in re.finditer(r"(\d{1,2})月(\d{1,2})日前", text):
        mo, d = int(m.group(1)), int(m.group(2))
        y = now.year + (1 if (mo, d) < (now.month, now.day) else 0)
        try:
            cands.append(datetime(y, mo, d))
        except ValueError:
            pass
    valid = [c for c in cands if delta(days=-3) <= c - today <= delta(days=180)]
    if not valid:
        return ""
    return min(valid, key=lambda c: abs((c - today).days)).strftime("%Y-%m-%d")


# ---------------------------------------------------------------- 工具函数
def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        raw = r.read()
        ctype = r.headers.get("Content-Type", "")
    m = re.search(r"charset=([\w-]+)", ctype, re.I)
    enc = m.group(1) if m else None
    if not enc:
        head = raw[:4000].decode("ascii", "ignore")
        m = re.search(r"charset=([\w-]+)", head, re.I)
        enc = m.group(1) if m else "utf-8"
    for e in [enc, "utf-8", "gb18030"]:
        try:
            return raw.decode(e, "ignore")
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", "ignore")


def clean_text(s):
    s = re.sub(r"<script[\s\S]*?</script>", "", s, flags=re.I)
    s = re.sub(r"<style[\s\S]*?</style>", "", s, flags=re.I)
    s = re.sub(r"<!--.*?-->", "", s, flags=re.S)
    s = re.sub(r"<[^>]+>", "\n", s)
    s = html.unescape(s)
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in s.split("\n")]
    return [ln for ln in lines if ln]


def extract_title(page_html, fallback):
    m = re.search(r"<title>(.*?)</title>", page_html, re.S | re.I)
    if m:
        t = html.unescape(m.group(1)).strip()
        # 仅剥离末尾的站点名后缀（如 “-大连理工大学盘锦校区”），
        # 避免误砍 “2025-2026学年” 这类含连字符的标题
        t = re.sub(r"[-_—|]\s*(共青团.{0,25}|大连理工大学.{0,18}|盘锦教学与科研工作部|盘锦.{0,14}|学生处|教务处|"
                   r"党委学生工作部|新闻中心.{0,8})$", "", t).strip()
        t = re.sub(r"\s+", " ", t)
        if 4 <= len(t) <= 120:
            return t
    return fallback


def extract_date(page_html, title=""):
    """提取发布日期；过滤页脚建站时间等异常值，优先取'发布时间'附近的日期。"""
    now = datetime.now()
    # 发布日期不应在未来（最多容忍 1 天时区/时钟误差），否则未来日期会霸占列表顶部
    lo, hi = datetime(2015, 1, 1), now + __import__("datetime").timedelta(days=1)
    cands = []
    for m in re.finditer(r"(20\d{2})[-年./](\d{1,2})[-月./](\d{1,2})", page_html):
        try:
            dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            continue
        if lo <= dt <= hi:
            near = bool(re.search(r"(发布时间|发布日期|时间|日期)",
                                  page_html[max(0, m.start() - 80):m.start()]))
            cands.append((near, m.start(), dt))
    if cands:
        near_hits = [c for c in cands if c[0]]
        pick = min(near_hits, key=lambda c: c[1])[2] if near_hits \
            else max(cands, key=lambda c: c[2])[2]
        return pick.strftime("%Y-%m-%d")
    m = re.search(r"(20\d{2})年", title or "")
    return m.group(1) if m else ""


def extract_content_text(page_html):
    """从正文容器提取全部纯文本（用于简介截断与截止日期识别）。"""
    for marker in [r'class="v_news_content"', r"id=\"vsb_content\"", r"id='vsb_content'",
                   r'class="news-content"', r'class="detail_content"',
                   r'class="common content"', r'id="zoom"', r'id="js_content"',
                   r'class="[^"]*mt-6 leading-6 text-sm text-primary']:
        m = re.search(marker + r"[^>]*>", page_html)
        if m:
            seg = page_html[page_html.rfind("<", 0, m.start()):]  # 回退到标签开头，避免残段混入正文
            stop = re.search(r'(class="[^"]*(foot|share|pages|page|attach|related)[^"]*"|<footer|上一条|下一条|【关闭】|相关附件)',
                             seg[100:], re.I)
            seg = seg[:stop.start() + 100] if stop else seg[:60000]
            lines = clean_text(seg)
            out = []
            for ln in lines:
                if re.search(r"(上一篇|下一篇|分享到|微信|微博|编辑|摄影|审核|供稿|来源：|发布时间)", ln):
                    continue
                if re.search(r'(mmbiz|data-src|href=|src=)', ln):
                    continue
                out.append(ln)
            if out:
                return "".join(out)
    lines = clean_text(page_html)
    paras = [ln for ln in lines if 40 <= len(ln) <= 500 and
             not re.search(r"(版权|备案|地址：|电话|邮箱|导航|首页|登录)", ln)]
    return paras[0] if paras else ""


def extract_summary(page_html, url):
    """截取正文开头作为简介，保证出自原文（句号处收束）。"""
    return cut_summary(extract_content_text(page_html))


# ---------------------------------------------------------------- 打标引擎
def tag_article(title, full_text, source_name, source_campus):
    content = title + "。" + full_text[:600]   # 打标只看标题+文首，避免长文噪声
    allcol = title + "。" + full_text          # 学院名专用（全称短语误报率低）
    tags = []

    # ── 校区层：信源为主，正文出现盘锦关键词则补充
    campus = source_campus
    if source_campus == "主校区" and re.search(r"盘锦校区|盘锦", content):
        tags.append("涉及盘锦")

    # ── 对象层：适用年级 + 特定人群
    grades = [g for g, kws in GRADE_RULES if any(k in content for k in kws)]
    if not grades and re.search(r"全体|各年级|本科生|在校生|学生", content):
        grades = ["全体学生"]
    if not grades:
        grades = ["全体学生"]
    audience = list(grades)
    for name, kws in AUDIENCE_RULES:
        if any(k in content for k in kws) and name not in audience:
            audience.append(name)

    # ── 学生工作相关
    is_stuwork = ("学生工作" in source_name or "学生工作部" in source_name or
                  "团工委" in source_name or
                  any(k in content for k in GRADE_STUDENT_WORK))

    # ── 学院层：全文识别（最多2个，标题命中的优先），叠加信源默认
    college = [c for c in COLLEGES if c in allcol][:4]
    college.sort(key=lambda c: 0 if c in content else 1)
    college = college[:2]
    for src_key, cols in SOURCE_COLLEGE.items():
        if src_key in source_name:
            for c in cols:
                if c not in college:
                    college.append(c)

    # ── 分类层
    cat = "通知公告"
    cat_content = title + title + "。" + content
    cat_hits = {c: sum(2 if k in title else 1 for k in kws if k in cat_content)
                for c, kws in CATEGORY_RULES}
    best = max(cat_hits.items(), key=lambda x: x[1])
    if best[1] > 0:
        cat = best[0]

    # ── 重要度指数 1-5
    score = 2
    reasons = []
    if any(k in content for k in DEADLINE_WORDS):
        score += 2
        reasons.append("含报名/截止时间")
    if any(k in content for k in HIGH_WORDS):
        score += 1
        reasons.append("涉及考试/放假/奖助等高关注事项")
    if re.search(r"全体|所有|各学部|各学院", content):
        score += 1
        reasons.append("面向全体学生")
    if any(k in content for k in RESULT_WORDS):
        score -= 1
        reasons.append("结果/公示类")
    if re.search(r"招聘|拟聘|任职|任免|交流访学.*公示|进校人员公示", content):
        score -= 1
        reasons.append("偏教职工/公示类")
    score = max(1, min(5, score))

    # ── 关键词层：领域标签（最多 6 个）
    extra = []
    for tag, kws in KEYWORD_TAGS:
        if any(k in content for k in kws):
            extra.append(tag)
        if len(extra) >= 6:
            break

    all_tags = []
    for t in (["学生工作"] if is_stuwork else []) + extra + tags:
        if t not in all_tags:
            all_tags.append(t)
    return {
        "campus": campus,
        "category": cat,
        "audience": audience,
        "college": college,
        "studentWork": bool(is_stuwork),
        "importance": score,
        "importanceReason": "；".join(reasons[:2]),
        "tags": all_tags,
    }


# ---------------------------------------------------------------- 微信公众号（搜狗微信搜索）
WX_KEYWORDS = ["化工海洋与生命学院", "大连理工大学盘锦校区", "大工盘锦校区", "大连理工大学 保研 推免"]
# 指定公众号：按号抓近期文章（sogou 公众号主页→文章链接），正文由 parse_article 抓原文
WX_ACCOUNTS = ["大工盘锦校区青年汇", "大工盘锦校区总务部", "大连理工大学创新创业学院",
               "大连理工大学盘锦校区", "DUT青听化海生"]


def wx_search():
    """搜狗微信搜索发现公众号文章：标题/摘要/时间/公众号名均出自搜索结果页（原文摘录）。"""
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

    def get(url, referer=None):
        req = urllib.request.Request(url, headers={
            "User-Agent": UA, **({"Referer": referer} if referer else {})})
        return opener.open(req, timeout=TIMEOUT).read().decode("utf-8", "ignore")

    now = datetime.now().timestamp()
    out = []
    # 防限流：12小时内已全量采集过则跳过搜狗（开机+7:30 一天两次会触发反爬）
    aj = ROOT / "data" / "articles.json"
    if aj.exists():
        try:
            gen = json.loads(aj.read_text(encoding="utf-8"))["meta"]["generated"]
            age = (datetime.now() - datetime.strptime(gen, "%Y-%m-%d %H:%M")).total_seconds()
            if age < 12 * 3600:
                print("[微信] 12小时内已采集，跳过搜狗（防限流）")
                return out
        except Exception:
            pass
    for kw in WX_KEYWORDS:
        try:
            page = get("https://weixin.sogou.com/weixin?type=2&query=" + urllib.parse.quote(kw))
        except Exception as e:
            print(f"  [微信] 搜索失败 {kw}: {e}")
            continue
        if "antispider" in page or "news-list" not in page:
            print(f"  [微信] {kw}: 无结果或触发反爬，跳过")
            continue
        times = re.findall(r"timeConvert\('(\d+)'\)", page)
        blocks = re.split(r'<li id="sogou_vr_11002601_box_\d+"', page)[1:]
        n_kw = 0
        for i, b in enumerate(blocks):
            if n_kw >= 8:
                break
            if i >= len(times) or now - int(times[i]) > 60 * 86400:
                continue  # 只取近60天，滤掉不相关旧文
            tm = re.search(r'<a[^>]*href="([^"]+)"[^>]*uigs="article_title_\d+"[^>]*>([\s\S]*?)</a>', b)
            if not tm:
                continue
            title = re.sub(r"\s+", " ", html.unescape(
                re.sub(r"<[^>]+>", "", tm.group(2)))).strip()
            if not (4 <= len(title) <= 120):
                continue
            sm = re.search(r'class="txt-info"[^>]*>([\s\S]*?)</p>', b)
            summary = re.sub(r"\s+", " ", html.unescape(
                re.sub(r"<[^>]+>", "", sm.group(1)))).strip()[:150] if sm else ""
            am = re.search(r'<span class="all-time-y2">([^<]+)</span>', b)
            account = html.unescape(am.group(1)).strip() if am else ""
            if not any(k in account for k in
                       ("大连理工", "大工", "盘锦", "DUT", "dut", "化工海洋", "莱斯特", "书院")):
                continue  # 只收官方/校园公众号，滤掉营销号
            date = datetime.fromtimestamp(int(times[i])).strftime("%Y-%m-%d")
            try:  # 解析微信真实链接（JS 拼接），失败则跳过
                lp = get(urllib.parse.urljoin("https://weixin.sogou.com", html.unescape(tm.group(1))),
                         referer="https://weixin.sogou.com/")
                chunks = re.findall(r"url \+= '([^']*)'", lp)
                wxurl = "".join(chunks)
                if not wxurl.startswith("http"):
                    continue
            except Exception:
                continue
            campus = "盘锦校区" if "盘锦" in (kw + title + summary) else "主校区"
            out.append({"title": title, "url": wxurl, "date": date, "summary": summary,
                        "source": f"微信公众号·{account}", "campus": campus})
            n_kw += 1
            time.sleep(2.0)
        print(f"[微信] {kw}: {n_kw} 条")
        time.sleep(3.0)

    # ── 指定公众号：type=1 搜索 → 公众号主页 → 近期文章链接
    for account in WX_ACCOUNTS:
        n_ac = 0
        try:
            ap = get("https://weixin.sogou.com/weixin?type=1&s_from=input&query=" +
                     urllib.parse.quote(account))
            if "antispider" in ap:
                print(f"[微信] 公众号 {account}: 反爬，跳过")
                continue
            m = re.search(r'href="([^"]*openid=[^"]+)"', ap)
            if not m:
                print(f"[微信] 公众号 {account}: 未找到主页")
                continue
            gp = get(urllib.parse.urljoin("https://weixin.sogou.com", html.unescape(m.group(1))),
                     referer="https://weixin.sogou.com/")
            if "antispider" in gp:
                print(f"[微信] 公众号 {account}: 主页反爬，跳过")
                continue
            for bm in re.finditer(r'<a[^>]*href="(/link\?url=[^"]+)"[^>]*>([\s\S]*?)</a>', gp):
                if n_ac >= 10:
                    break
                t = re.sub(r"\s+", " ", html.unescape(
                    re.sub(r"<[^>]+>", "", bm.group(2)))).strip()
                if not (4 <= len(t) <= 120):
                    continue
                try:
                    lp = get(urllib.parse.urljoin("https://weixin.sogou.com", html.unescape(bm.group(1))),
                             referer="https://weixin.sogou.com/")
                    wxurl = "".join(re.findall(r"url \+= '([^']*)'", lp))
                    if not wxurl.startswith("http"):
                        continue
                except Exception:
                    continue
                campus = "盘锦校区" if "盘锦" in account else "主校区"
                out.append({"title": "", "url": wxurl, "date": "", "summary": "",
                            "source": account, "campus": campus})
                n_ac += 1
                time.sleep(2.0)
            print(f"[微信] 公众号 {account}: {n_ac} 条")
        except Exception as e:
            print(f"[微信] 公众号 {account}: {e}")
        time.sleep(3.0)
    return out


# ---------------------------------------------------------------- B站（搜索页 __pinia，node 解析）
BILI_KEYWORDS = ["大连理工大学", "大连理工大学盘锦校区"]
BILI_OFFICIAL_MIDS = {215986465}          # 大连理工大学官方账号（搜索时自动扩充）
BILI_OFFICIAL_NAMES = ("大连理工大学", "DUT", "大工")
_BILI_STAMP = DATA_DIR / ".bili_last"


def _find_node():
    """定位 node 可执行文件；找不到返回 None（B站信源自动跳过）。"""
    p = shutil.which("node")
    if p:
        return p
    import glob
    cands = sorted(glob.glob(str(Path.home() / ".workbuddy/binaries/node/versions/*/node.exe")))
    return cands[-1] if cands else None


def bili_search():
    """B站官方号视频：只采标题/简介/日期，不下载视频。12 小时内只跑一次（防风控）。"""
    if _BILI_STAMP.exists():
        try:
            if time.time() - _BILI_STAMP.stat().st_mtime < 12 * 3600:
                print("[B站] 12小时内已采集，跳过")
                return []
        except Exception:
            pass
    node = _find_node()
    if not node:
        print("[B站] 未找到 node，跳过")
        return []
    out = []
    official_mids = set(BILI_OFFICIAL_MIDS)
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

    def get(url):
        req = urllib.request.Request(url, headers={
            "User-Agent": UA, "Referer": "https://www.bilibili.com"})
        return opener.open(req, timeout=TIMEOUT).read().decode("utf-8", "ignore")

    now_ts = time.time()
    seen_bv = set()
    try:
        get("https://www.bilibili.com/")  # 先拿 cookie
    except Exception:
        pass
    for kw in BILI_KEYWORDS:
        try:
            page = get("https://search.bilibili.com/video?keyword=" +
                       urllib.parse.quote(kw) + "&order=pubdate")
        except Exception as e:
            print(f"[B站] 搜索失败 {kw}: {e}")
            continue
        if "__pinia" not in page:
            print(f"[B站] {kw}: 页面无数据（可能风控），跳过")
            continue
        try:
            r = subprocess.run(
                [node, str(ROOT / "bili_extract.js")], input=page,
                capture_output=True, text=True, encoding="utf-8", timeout=60)
            data = json.loads(r.stdout or "{}")
        except Exception as e:
            print(f"[B站] {kw}: 解析失败 {e}")
            continue
        if data.get("err"):
            print(f"[B站] {kw}: 提取异常 {data['err'][:80]}")
        print(f"[B站] {kw}: 原始视频 {len(data.get('videos', []))} 条、"
              f"官方号候选 {len(data.get('users', []))} 个")
        # 搜索到的官方认证账号自动加入白名单
        for u in data.get("users", []):
            if "大连理工大学" in (u.get("official") or "") and u.get("mid"):
                official_mids.add(int(u["mid"]))
        n_kw = 0
        for v in data.get("videos", []):
            if n_kw >= 8 or not v.get("bvid") or v["bvid"] in seen_bv:
                continue
            official = int(v.get("mid") or 0) in official_mids or \
                any(n in (v.get("author") or "") for n in BILI_OFFICIAL_NAMES)
            if not official:
                continue
            seen_bv.add(v["bvid"])
            ts = int(v.get("pubdate") or 0)
            if not ts or now_ts - ts > 60 * 86400:
                continue  # 只取近60天
            title = html.unescape(v.get("title", "")).strip()
            if not (4 <= len(title) <= 120):
                continue
            desc = html.unescape(v.get("desc", ""))
            date = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
            campus = "盘锦校区" if "盘锦" in (title + desc + (v.get("author") or "")) \
                else "主校区"
            out.append({"title": title, "url": f"https://www.bilibili.com/video/{v['bvid']}",
                        "date": date, "summary": desc, "author": v.get("author") or "B站",
                        "campus": campus})
            n_kw += 1
        print(f"[B站] {kw}: {n_kw} 条")
        time.sleep(2.0)
    try:
        _BILI_STAMP.write_text(str(now_ts), encoding="utf-8")
    except Exception:
        pass
    return out


def apply_profile(info, title, text, src_name, campus):
    """统一后处理：staff 判定、deadline 提取、保研标记、个性化加权。"""
    info["staff"] = bool(STAFF_RE.search(title)) and not STUDENT_RE.search(title)
    info["deadline"] = extract_deadline(title, text)
    score = float(info["importance"])
    pers = []
    bonus = 0.0
    if any("海洋与生命" in c for c in info.get("college", [])):
        bonus += 1.5
        pers.append("本学院+1.5")
    core_by = info["category"] in BAOYAN_CATS or bool(
        {"保研", "四六级", "大创项目", "挑战杯", "数学建模"} & set(info["tags"]))
    if core_by:
        bonus += 1
        pers.append("保研相关+1")
    if campus == "盘锦校区":
        bonus += 0.5
        pers.append("盘锦校区+0.5")
    elif "涉及盘锦" in info["tags"]:
        bonus += 0.5
        pers.append("涉盘锦+0.5")
    aud = set(info["audience"])
    # 用户年级锚定：跟随用户所在年级（2026级）而非"当前大一"
    _ug = ["大一·新生", "大二", "大三", "大四·毕业"][
        min(max(datetime.now().year - USER_ENTRY_YEAR, 0), 3)]
    if _ug in aud or "全体学生" in aud:
        bonus += 0.5
        pers.append(f"面向本年级({_ug})/全体+0.5")
    elif campus == "主校区":
        score -= 0.5
    score += min(bonus, 2.0)
    if info["staff"]:
        score = min(score, 2.0)
    info["baoyan"] = bool(core_by or BAOYAN_TAGS & set(info["tags"]))
    info["importance"] = max(1, min(5, int(round(score))))
    if pers and not info["staff"]:
        info["importanceReason"] = (info["importanceReason"] + "；" + "、".join(pers[:2])).strip("；")
    return info



def parse_list(src):
    try:
        page = fetch(src["list"])
    except Exception as e:
        print(f"  [列表失败] {src['name']}: {e}")
        return []
    plow = page.lower()  # 部分站点用大写 <A> 标签，长度一致可直接定位
    found, seen = [], set()
    for m in re.finditer(src["link_re"], page):
        href = html.unescape(m.group(1))
        url = urllib.parse.urljoin(src["list"], href)
        if url in seen or "javascript" in url:
            continue
        seen.add(url)
        # 定位包含该 href 的完整 <a> 标签块，取 title 属性或链接文字作为标题
        start = plow.rfind("<a ", max(0, m.start() - 400), m.start() + len(m.group(0)))
        end = plow.find("</a>", m.end())
        block = page[start:end + 4] if start != -1 and end != -1 else ""
        title = ""
        if block:
            tm = re.search(r'title="([^"]+)"', block)
            if tm:
                title = tm.group(1).strip()
            if not title:
                # 取 <a> 内第一个文本节点（有的站点把整段简介也放在 a 里）
                lines = [ln.strip() for ln in re.sub(r"<[^>]+>", "\n", block).split("\n")
                         if ln.strip()]
                # 标题可能被拆到多个节点，首行过短时向后拼接
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
    # 官方页面挂出的微信公众号原文链接（永久 /s/ 链接，最稳的微信信源）
    seen_wx = {f["url"] for f in found}
    for m in re.finditer(r'href="(https://mp\.weixin\.qq\.com/s/[A-Za-z0-9_=-]{8,})"', page):
        if m.group(1) not in seen_wx:
            seen_wx.add(m.group(1))
            found.append({"url": m.group(1), "title": ""})
        if len([f for f in found if "mp.weixin" in f["url"]]) >= 5:
            break
    return found


def parse_wx_article(page, item, src):
    """微信公众号文章页：标题/时间/正文均出自原文页，链接为永久 /s/ 形式。"""
    m = re.search(r"var msg_title = '([^']+)'", page) or \
        re.search(r'<meta property="og:title" content="([^"]+)"', page)
    title = html.unescape(m.group(1)).strip() if m else item.get("title", "")
    title = re.sub(r"\s+", " ", title)
    d = re.search(r'var publish_time = [\'"]([^\'"]+)[\'"]', page) or \
        re.search(r'var createTime = [\'"]([^\'"]+)[\'"]', page) or \
        re.search(r'var ct = "?(\d{9,})"?', page) or \
        re.search(r'<em id="publish_time"[^>]*>([^<]+)</em>', page)
    date = ""
    if d:
        if d.group(1).isdigit():
            date = datetime.fromtimestamp(int(d.group(1))).strftime("%Y-%m-%d")
        else:
            dm = re.search(r"(20\d{2})[-年.](\d{1,2})[-月.](\d{1,2})", d.group(1))
            if dm:
                date = f"{dm.group(1)}-{int(dm.group(2)):02d}-{int(dm.group(3)):02d}"
    full_text = extract_content_text(page)
    summary = cut_summary(full_text)
    account = src["name"]
    am = re.search(r'var nickname = [\'"]([^\'"]+)[\'"]', page)
    if am:
        account = html.unescape(am.group(1)).strip()
    if not summary or len(title) < 4:
        return None
    info = tag_article(title, full_text, f"微信公众号·{account}", src["campus"])
    info = apply_profile(info, title, full_text, f"微信公众号·{account}", src["campus"])
    return {"title": title, "url": item["url"], "date": date, "summary": summary,
            "source": f"微信公众号·{account}", **info}


def parse_article(item, src):
    try:
        page = fetch(item["url"])
    except Exception as e:
        print(f"  [文章失败] {item['url'][:60]}: {e}")
        return None
    if "mp.weixin.qq.com" in item["url"]:
        return parse_wx_article(page, item, src)
    title = extract_title(page, item["title"])
    date = extract_date(page, title)
    full_text = extract_content_text(page)
    summary = cut_summary(full_text)
    info = tag_article(title, full_text, src["name"], src["campus"])
    info = apply_profile(info, title, full_text, src["name"], src["campus"])
    return {
        "title": title, "url": item["url"], "date": date,
        "summary": summary, "source": src["name"], **info,
    }


def main():
    log_fh = start_log()
    t0 = time.time()
    full = "--full" in sys.argv   # --full：忽略存档全量重抓（标签词典改版后用）
    DATA_DIR.mkdir(exist_ok=True)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    today = datetime.now().date()

    archive, pruned = ({}, {}) if full else load_archive()
    fav = load_fav() if not full else set()
    pruned_new = {}
    if full:
        print("[模式] --full 全量重抓（清库重建）")
    else:
        print(f"[存档] 已有 {len(archive)} 条、墓碑 {len(pruned)} 条、收藏白名单 {len(fav)} 条｜"
              f"{FRESH_DAYS} 天内完全重扫，{FRESH_DAYS}-{ARCHIVE_DAYS} 天靠存档+墓碑")

    all_items, seen_url = [], set()
    n_reused, n_fetched, n_tomb = 0, 0, 0

    def reuse_or_none(url):
        """URL 在存档中则直接复用；墓碑中且超 FRESH_DAYS 的跳过（30 天内忽略墓碑强制重扫）。"""
        nonlocal n_reused, n_tomb
        if is_pruned_live(url, pruned):
            seen_url.add(url)
            n_tomb += 1
            return True
        if url in archive:
            all_items.append(archive[url])
            seen_url.add(url)
            n_reused += 1
            return True
        return False

    jobs = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for src in SOURCES:
            items = parse_list(src)
            print(f"[{src['name']}] 列表解析 {len(items)} 条")
            for it in items:
                if it["url"] in seen_url:
                    continue
                if reuse_or_none(it["url"]):
                    continue
                seen_url.add(it["url"])
                jobs.append(ex.submit(parse_article, it, src))
        for f in as_completed(jobs):
            r = f.result()
            if r and r["summary"]:
                r["collected"] = now_str
                all_items.append(r)
                n_fetched += 1

    # ── 手动收录：data\wx_links.txt 中用户从微信"复制链接"粘贴的公众号文章
    wl = ROOT / "data" / "wx_links.txt"
    if wl.exists():
        links = [l.strip() for l in wl.read_text(encoding="utf-8").splitlines()
                 if l.strip().startswith("http")]
        n_wx = 0
        for u in links:
            if u in archive:
                n_wx += 1
                continue
            try:
                r = parse_article({"url": u, "title": ""},
                                  {"name": "手动收录", "campus": "盘锦校区"})
            except Exception:
                continue
            if r:
                r["collected"] = now_str
                all_items.append(r)
                n_wx += 1
        if links:
            (ROOT / "data" / "wx_links.txt.done").write_text(
                "\n".join(links), encoding="utf-8")
            wl.write_text("", encoding="utf-8")
        print(f"[微信] 手动收录链接: {n_wx}/{len(links)} 条入库")

    # ── 微信公众号（搜狗微信搜索，作为补充发现渠道，被限流时自动跳过）
    for it in wx_search():
        if it["url"] in seen_url or reuse_or_none(it["url"]):
            continue
        seen_url.add(it["url"])
        if not it["summary"]:
            # 公众号主页路径：只有链接，抓原文页提取标题/时间/简介
            try:
                r = parse_article({"url": it["url"], "title": ""},
                                  {"name": it["source"], "campus": it["campus"]})
            except Exception:
                continue
            if r:
                r["collected"] = now_str
                all_items.append(r)
            continue
        info = tag_article(it["title"], it["summary"] or it["title"], it["source"], it["campus"])
        info = apply_profile(info, it["title"], it["title"] + "。" + it["summary"],
                             it["source"], it["campus"])
        all_items.append({"title": it["title"], "url": it["url"], "date": it["date"],
                          "summary": it["summary"] or "(公众号文章，点击查看原文)",
                          "source": it["source"], "collected": now_str, **info})

    # ── B站官方号视频（只采标题/简介，不下载视频）
    for it in bili_search():
        if it["url"] in seen_url or reuse_or_none(it["url"]):
            continue
        seen_url.add(it["url"])
        src_name = f"B站·{it['author']}"
        text = it["title"] + "。" + it["summary"]
        info = tag_article(it["title"], text, src_name, it["campus"])
        info = apply_profile(info, it["title"], text, src_name, it["campus"])
        all_items.append({"title": it["title"], "url": it["url"], "date": it["date"],
                          "summary": cut_summary(it["summary"]) or "(B站视频，点击查看)",
                          "source": src_name, "collected": now_str, **info})

    # ── 兜底保留：存档中本轮未被枚举、且未命中有墓碑拦截的旧条目也并入，
    #    确保某来源因限流/缓存被跳过时其已收录文章绝不消失（含 B站/搜狗/手动）
    for u, a in archive.items():
        if u not in seen_url and not is_pruned_live(u, pruned):
            all_items.append(a)

    # ── 历史遗留清理：未来日期（旧版 extract_date 允许 +45 天）置空
    for a in all_items:
        d = a.get("date") or ""
        try:
            if datetime.strptime(d[:10], "%Y-%m-%d").date() > today + timedelta(days=1):
                a["date"] = ""
        except ValueError:
            pass

    # 去重（同 URL 或同标题+同日期，优先保留盘锦校区来源）
    def campus_rank(a):
        return 0 if a["campus"] == "盘锦校区" else 1
    uniq, seen_url2, seen_t = [], set(), set()
    for a in sorted(all_items, key=campus_rank):
        key = (a["title"], a["date"])
        if a["url"] in seen_url2 or key in seen_t:
            continue
        seen_url2.add(a["url"])
        seen_t.add(key)
        uniq.append(a)
    all_items = uniq

    # ── 留存：90 天内保留 + 收藏永不删 + 超期删除；被删 URL 记入墓碑（记文章日期，供 30 天内重扫）
    before = len(all_items)
    kept_items = [a for a in all_items if pass_retention(a, fav)]
    n_dropped = before - len(kept_items)
    kept_urls = {a["url"] for a in kept_items}
    today_s = today.strftime("%Y-%m-%d")
    for a in all_items:
        if a["url"] not in kept_urls:
            pruned_new[a["url"]] = article_date_str(a) or today_s
    all_items = kept_items

    # 排序（按日期倒序）
    def sort_key(a):
        return (a["date"] or "0000-00-00", a["title"])
    all_items.sort(key=sort_key, reverse=True)
    if len(all_items) > MAX_TOTAL:
        all_items = all_items[:MAX_TOTAL]

    meta = {
        "generated": now_str,
        "total": len(all_items),
        "panjinCount": sum(1 for a in all_items if a["campus"] == "盘锦校区"),
        "archived": n_reused,
    }
    (DATA_DIR / "articles.json").write_text(
        json.dumps({"meta": meta, "articles": all_items}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    (DATA_DIR / "data.js").write_text(
        "window.DUT_DATA=" + json.dumps({"meta": meta, "articles": all_items},
                                        ensure_ascii=False) + ";", encoding="utf-8")
    if pruned_new:
        old_map = {}
        if PRUNED_FILE.exists():
            try:
                old_map = json.loads(PRUNED_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        old_map.update(pruned_new)
        save_pruned(old_map)
    print(f"完成：共 {meta['total']} 条（盘锦 {meta['panjinCount']} 条）｜"
          f"新抓 {n_fetched} 条、复用存档 {n_reused} 条、墓碑跳过 {n_tomb} 条、"
          f"留存淘汰 {n_dropped} 条｜耗时 {time.time() - t0:.0f}s｜{now_str}")
    sys.stdout = sys.stdout.stream   # 恢复标准输出，避免退出时 flush 已关闭的日志
    log_fh.close()


if __name__ == "__main__":
    main()
