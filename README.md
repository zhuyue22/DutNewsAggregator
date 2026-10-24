# 大工资讯聚合 · 盘锦校区新生助手

> 大连理工大学盘锦校区资讯聚合桌面软件——自动采集校园官网资讯，按对象/分类/校区/学院分层打标签，保研导向个性化加权，让新生「不知道自己要什么也能找到该看的」。

## 它解决什么问题
校园官网分散在十几个子站，新生很难每天挨个刷，更难判断「哪条跟我相关」。本软件定时从各官网采集通知，自动归类打标签、按重要度排序、标注截止日期，新生一开窗口就能看到「保研相关」「临期事项」「面向大一」的精选信息。

## 功能

### 信源（11+ 官网 + 微信公众号 4 通道）
- **官网**：盘锦校区通知公告/校区文件、教务重要通告/工作动态、学工通知/活动预告、就业企业/实习招聘、研究生院招生政策、团工委、图书馆、国际合作交流处、教务处考试安排、化工海洋与生命学院
- **微信**：搜狗微信搜索、官网外链跟随、手动收录（`data/wx_links.txt`）、定向公众号抓取

### 标签与排序
- **6 维分层标签**：对象(受众) / 分类 / 校区 / 学院 / 来源 / 关键词
- **保研个性化加权**：本学院 +1.5、核心保研项(竞赛/科研/奖助/保研/四六级/大创/挑战杯/数模) +1、盘锦 +0.5、面向大一/全体 +0.5
- **截止日期提取**：从全文提取报名/提交截止，倒计时徽标（≤3 天红色）
- **场景预设**：🎯保研关注 / ⏰临期事项 / 👤新生模式（默认隐藏教职工向信息）
- 90 天滚动存档、已读灰化、年级动态化（2027 级 9 月起自动算大一）

### 桌面软件
- pywebview 原生窗口（自带标题栏）
- 启动后台线程自动采集（数据过期 6 小时触发，不阻塞窗口秒开）
- 前端「🔄 刷新数据」按钮 → 一键重新采集并刷新页面

### 开机临期提醒
- **开机自动检查临期事项**：开机时先全量重采，再算临期（3 天内截止 且 (重要度≥4 或 含化工海洋与生命学院) 且 未手动忽略）
- 开机弹窗逐条列出临期事项，每条可「我不需要」（写 `dismissed.json` 永久忽略）或「我要看看」（打开 index.html 红框定位到对应卡片）
- 启用：源码版运行 `setup-shortcuts.ps1` 自动创建开机启动项（指向 `startup-check.ps1` 后台跑）+ 桌面快捷方式；每日 7:30 定时采集配合，形成「采集 → 到期提醒」闭环
- 注：打包 exe 版双击即桌面软件窗口；开机临期弹窗需源码版用 PowerShell 脚本配置（后续可集成进 exe）

## 下载使用

### 方式一：直接用打包好的 exe（推荐，无需 Python 环境）

**运行要求**：Windows 10/11 + WebView2 运行时（Win11 自带；Win10 若缺失，首次运行会提示安装微软 WebView2 Evergreen，一键装即可）。

1. 下载 exe：进仓库 `release/` 目录下载 `DutNewsAggregator.exe`（约 14 MB），或点仓库绿色 **Code → Download ZIP** 解压后在 `release/` 里取
2. 双击运行（首次启动 PyInstaller onefile 会先解压到临时目录，**慢几秒属正常**，之后秒开）
3. 首次启动自动联网采集一次生成 `data/data.js`，之后每 6 小时自动刷新；也可点界面「🔄 刷新数据」手动触发

> 注：部分校园官网 TLS 证书在非校园网环境下可能握手失败，导致采集部分失败、软件保留上次数据——联网后点「🔄 刷新数据」重试即可。

### 方式二：源码运行（需 Python 3.10+，运行要求同上需 WebView2）
```bash
git clone https://github.com/zhuyue22/DutNewsAggregator.git
cd DutNewsAggregator
pip install pywebview
python app.py
```
首次运行会自动采集生成 `data/data.js`（同上，需联网；采集规则在 `rule_set.json`，可自行调整）。

## 技术栈
- **采集**：Python 标准库（urllib.request / http.cookiejar / sqlite3 / re），无第三方 HTTP 依赖
- **前端**：原生 HTML/JS + `data/data.js` 数据文件
- **桌面壳**：[pywebview](https://pywebview.flowrl.com/)（WebView2）+ [PyInstaller](https://pyinstaller.org/) `--onefile` 打包
- **自动化**：每日 7:30 定时全量采集 + 开机到期弹窗（PowerShell WinForms）

## 项目结构
```
app.py               # 桌面软件入口（pywebview 窗口 + 本地服务器 + 后台采集）
serve.py              # 本地静态服务器（托管 index.html）
collector.py          # 采集器核心（信源抓取 / HTML 解析 / 标签 / 微信 / B站）
collector_eng.py      # 工程化采集入口（线程池 / 重试 / 限流）
eng_lib.py            # 采集库（SqliteStore / RetryingFetcher / PerHostLimiter）
sectionize.py         # 90 天滚动分节归档
index.html            # 前端（卡片 + 分层彩色徽标 + 侧栏分组筛选）
sources.json          # 信源配置
build.bat             # PyInstaller 打包脚本
deadline_check.py     # 到期检查（算 hits）
startup-check.ps1     # 开机到期弹窗（PowerShell WinForms）
setup-shortcuts.ps1   # 桌面快捷方式生成
release/              # 打包好的 exe（发行）
assets/               # 图标
data/                 # 采集数据（运行时生成，不入库）
```

## 备注
- 校内专网系统（智慧大工 / i大工 / 智慧学工）需 SSO 登录，公网不可达，暂不入采集；连校园网/VPN 后可再探
- 微信信源签名 URL 几天后过期，靠每日重采刷新；搜狗微信反爬限流时自动跳过
- 部分官网 TLS 证书可能因网络环境握手失败，属信源级失败，保留旧数据不影响已采集内容

## License
MIT
