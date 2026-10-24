# -*- coding: utf-8 -*-
"""
sectionize.py —— 给「大工资讯聚合」数据打上「板块(section)」字段。

背景：
用户需要一个"分区板块"形态：竞赛 / 学生工作 / 学习 / 论文 / 科研。
现有数据只有 category(11类) 和 tags(标签列表)，两个维度都不等于板块。
本脚本用规则把每条记录归入 5 大板块（外加一个兜底「综合」），
输出新的 data.js（前端零改动即可新增板块导航）。

用法：
    python sectionize.py                 # 读 data/data.js，写回 data/data.js
    python sectionize.py --dry-run       # 只打印归类统计，不写文件

规则优先级是从高到低 try/except 式短路，命中即终止。
"""
import json
import os
import sys

DATA_PATH = os.path.join(os.path.dirname(__file__), "data", "data.js")

# 板块顺序（前端导航也按此顺序）
SECTIONS = ["竞赛", "学习", "科研", "论文", "学生工作", "综合"]


def _has(article, *words):
    """article 的 title/summary/tags/category 是否命中任一关键词。"""
    hay = " ".join(
        [
            article.get("title", ""),
            article.get("summary", ""),
            article.get("category", ""),
            " ".join(article.get("tags") or []),
        ]
    )
    return any(w in hay for w in words)


def classify(a):
    """返回板块 key。规则从高优先级往下，命中即返回。"""
    cat = a.get("category") or ""
    tags = " ".join(a.get("tags") or [])
    t = (a.get("title", "") + a.get("summary", "") + cat + tags).lower()

    # —— 竞赛：只看明确的赛事词汇，避免"报名"误抓普通活动 ——
    if _has(a, "竞赛", "挑战杯", "大赛", "设计赛", "挑战赛", "比赛获奖", "参赛通知", "入围",
               "获奖名单", "赛事动态", "科技创新大赛", "学科竞赛", "创新创业大赛", "创青春"):
        return "竞赛"
    # —— 论文 ——
    if _has(a, "论文", "毕业设计", "学位论文", "期刊", "投稿", "学术不端", "查重通知",
               "开题报告", "结题答辩", "发表"):
        return "论文"
    # —— 科研 ——
    if _has(a, "科研", "基金", "项目申报", "项目立项", "结项", "实验室", "学术讲座", "讲座论坛",
               "研讨会", "学术交流", "课题组", "科研训练", "大学生创新项目", "创新训练",
               "学术会议", "学者", "专家讲座", "访问"):
        return "科研"
    # —— 学生工作：校园非学术事务（含就业/荣誉/党团/活动）——
    if _has(a, "学生工作", "学生会", "社团", "团学", "志愿", "勤工助学", "荣誉称号", "评优",
               "奖学金", "助学金", "党团", "书院", "活动征集", "招募", "骨干", "换届", "积极分子",
               "招聘", "招录", "选调", "校招", "实习", "就业", "干部", "表彰", "优秀党员", "培训通知"):
        return "学生工作"
    # —— 学习：教务教学/考试/选课 ——
    if _has(a, "选课", "考试", "补考", "重修", "四六级", "四级", "六级", "教材", "调课", "停课",
               "学籍", "成绩", "新生", "课程建设", "培养方案", "教务", "教学", "开学通知",
               "报到注册", "毕业资格"):
        return "学习"
    # 兜底
    return "综合"


def load():
    with open(DATA_PATH, encoding="utf-8") as f:
        raw = f.read()
    body = raw.split("=", 1)[1].rstrip().rstrip(";")
    return json.loads(body), raw.split("=", 1)[0] + "="


def main():
    dry = "--dry-run" in sys.argv
    data, prefix = load()
    from collections import Counter

    cnt = Counter()
    for a in data["articles"]:
        a["section"] = classify(a)
        cnt[a["section"]] += 1

    if dry:
        print("板块分布（dry-run，未写文件）：")
        for s in SECTIONS:
            print(f"  {s}: {cnt[s]}")
        print(f"  总数: {sum(cnt.values())}")
        return

    with open(DATA_PATH, "w", encoding="utf-8") as f:
        f.write(prefix + json.dumps(data, ensure_ascii=False))
    print("已写回", DATA_PATH)
    for s in SECTIONS:
        print(f"  {s}: {cnt[s]}")
    print(f"  总数: {sum(cnt.values())}")


if __name__ == "__main__":
    main()
