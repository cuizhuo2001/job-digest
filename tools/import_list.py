#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
job-digest · 导入岗位清单（学校就业中心邮件、就业网站、群里转发的）

把一段复制来的文字认成岗位：公司 / 岗位名 / 截止日（有链接就带上）。认得出这几种排法：
    公司 ⏎ 岗位 ⏎ 截止日        —— 学校邮件里的表格复制出来就是这样（城大 e-Newsletter）
    Deadline: 29 NOV ⏎ 岗位 ⏎ 公司 —— 就业网站「Latest Job」那种
    公司 <Tab> 岗位 <Tab> 截止日   —— 从网页表格 / Excel 复制的一行
「Graduate Jobs / Internships / Events」这类小标题会被认出来：实习标成实习，活动跳过。
排得太乱认不出来时，用 import_prompt() 那段话让任意 AI 整理成 JSON，再贴回来同样能导。

面板「今日岗位 → 导入岗位清单」用的就是这里；命令行也能用：
    python tools/import_list.py 清单.txt          预览（标出已经在池子里的）
    python tools/import_list.py 清单.txt --save   加进池子（实习、已有的不加）
"""
import os
import re
import sys
import json
import argparse
import datetime
import difflib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import jdlib  # noqa: E402
import pool  # noqa: E402

MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
INTERN = re.compile(r"\binterns?(hip)?s?\b|\bsummer\b|\bplacement\b|off-cycle|实习", re.I)
HEADER = re.compile(r"^(company|employer|organi[sz]ation|job title|position|title|application deadline|deadline|"
                    r"closing date|date|event|location|公司|单位|企业|职位|岗位|岗位名称|截止日期|截止时间|地点)\s*:?$", re.I)
# 小标题只认这些固定说法，别拿岗位名当小标题（「2027 Graduate Development Programme」「IT Trainee Programme」都不是）
SEC_SKIP = re.compile(r"^((campus|external|upcoming|forthcoming|latest|other|more)\s+)?(events?|activities|seminars?|"
                      r"workshops?|talks?|info sessions?)\b.*$|^(活动|宣讲会|讲座|校园活动).*$", re.I)
SEC_INTERN = re.compile(r"^((full|part)[- ]time\s+)?(internships?|placements?)(\s+(jobs?|positions?|opportunities))?$|"
                        r"^(实习|实习岗位|暑期实习)$", re.I)
SEC_JOB = re.compile(r"^((graduate|full[- ]time|latest|new)\s+)?(jobs?|positions?|vacancies|openings)(\s+for\s+\w+)?$|"
                     r"^(校招|校招岗位|全职岗位|招聘岗位|最新岗位)$", re.I)
NOISE = re.compile(r"\bdetails?\b|^more\b|read more|unsubscribe|^dear\b|查看更多|详情", re.I)
URL = re.compile(r"<?(https?://[^\s<>\"]+)>?")
CO_HINT = re.compile(r"\b(limited|ltd|bank|banking|corporation|corp|company|co\.|group|holdings?|club|inc|llc|plc|"
                     r"partners|& sons|securities|capital|asset|insurance|consulting|management)\b|公司|集团|银行|证券|基金", re.I)
JOB_HINT = re.compile(r"programme|program|trainee|graduate|analyst|associate|officer|manager|consultant|intern|"
                      r"specialist|assistant|engineer|executive|recruitment|岗|生|招聘|管培|助理|经理", re.I)
MAINLAND = ["北京", "上海", "深圳", "广州", "杭州", "南京", "成都", "武汉", "苏州", "Shanghai", "Beijing", "Shenzhen",
            "Guangzhou", "Mainland China", "中国大陆", "内地"]


# ───────────────────────── 认日期 ─────────────────────────

def parse_day(s, today=None):
    """一整行是不是日期（可以带「Deadline:」前缀）→ date / None。缺年份按「最近的将来」补。"""
    today = today or datetime.date.today()
    s = re.sub(r"^(application\s+)?(deadline|closing date|apply by|截止(日期|时间)?)\s*[:：]?\s*", "", s.strip(), flags=re.I)
    s = s.strip(" .,;")
    if not s or len(s) > 30:
        return None
    y = mo = d = None
    m = re.fullmatch(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?", s)
    if m:
        y, mo, d = map(int, m.groups())
    m = m or re.fullmatch(r"(\d{1,2})月(\d{1,2})日?", s)
    if m and mo is None:
        mo, d = map(int, m.groups())
    if mo is None:
        m = re.fullmatch(r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})", s)          # 香港习惯 dd/mm/yyyy
        if m:
            d, mo, y = map(int, m.groups())
    if mo is None:
        m = re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\.?,?(?:\s+(\d{4}))?", s)
        if m and m.group(2)[:3].lower() in MONTHS:
            d, mo, y = int(m.group(1)), MONTHS[m.group(2)[:3].lower()], int(m.group(3)) if m.group(3) else None
    if mo is None:
        m = re.fullmatch(r"([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?(?:\s+(\d{4}))?", s)
        if m and m.group(1)[:3].lower() in MONTHS:
            mo, d, y = MONTHS[m.group(1)[:3].lower()], int(m.group(2)), int(m.group(3)) if m.group(3) else None
    if mo is None:
        return None
    try:
        if y:
            return datetime.date(y, mo, d)
        out = datetime.date(today.year, mo, d)
        return out if out >= today - datetime.timedelta(days=30) else datetime.date(today.year + 1, mo, d)
    except ValueError:
        return None


# ───────────────────────── 认岗位 ─────────────────────────

def _section(line):
    """短的小标题行 → '校招' / '实习' / 'skip' / None。"""
    line = re.sub(r"\s*read more$", "", line, flags=re.I).strip()
    if len(line) > 45 or parse_day(line) or URL.search(line):
        return None
    if SEC_SKIP.search(line):
        return "skip"
    if SEC_INTERN.search(line):
        return "实习"
    if SEC_JOB.search(line):
        return "校招"
    return None


def _pair(a, b, date_first):
    """两行字 → (公司, 岗位)。默认按排法猜，明显反了再对调。"""
    co, ti = (b, a) if date_first else (a, b)
    score = lambda x, rx: len(rx.findall(x))
    if score(co, CO_HINT) < score(ti, CO_HINT) and score(ti, JOB_HINT) <= score(co, JOB_HINT):
        co, ti = ti, co
    return co, ti


def parse(text, today=None):
    """一段文字 → [{company, title, deadline, url, type}]"""
    today = today or datetime.date.today()
    out, buf = [], []
    typ, skip, pending, date_first = "校招", False, None, None

    def add(co, ti, d, url=""):
        co, ti = co.strip(" -–|·"), ti.strip(" -–|·")
        if not co or not ti:
            return
        t = "实习" if (typ == "实习" or INTERN.search(ti)) else "校招"
        out.append({"company": co, "title": ti, "deadline": d.isoformat() if d else "", "url": url, "type": t})

    for raw in text.replace("\r", "\n").split("\n"):
        line = raw.strip().strip("​﻿").strip()
        if not line:
            continue
        urls = URL.findall(line)
        bare = URL.sub("", line).strip(" -–|·:：")
        if urls and not bare:                         # 单独一行链接：挂到上一条
            if out and not out[-1]["url"] and not buf:
                out[-1]["url"] = urls[0]
            continue
        line = bare
        if HEADER.match(line):
            continue
        sec = _section(line)
        if sec:
            skip = sec == "skip"
            if not skip:
                typ = sec
            buf, pending, date_first = [], None, None
            continue
        if NOISE.search(line):
            continue
        if skip:
            continue
        cells = [c.strip() for c in re.split(r"\t+|\s{3,}|\s\|\s", line) if c.strip()]
        if len(cells) >= 3:                           # 表格行：公司 / 岗位 / 日期
            ds = [(i, parse_day(c, today)) for i, c in enumerate(cells)]
            ds = [(i, d) for i, d in ds if d]
            if ds:
                rest = [c for i, c in enumerate(cells) if i != ds[0][0]]
                add(*_pair(rest[0], rest[1], False), ds[0][1], urls[0] if urls else "")
                buf = []
                continue
        d = parse_day(line, today)
        if d:
            if date_first is None:
                date_first = len(buf) < 2
            if not date_first and len(buf) >= 2:
                add(*_pair(buf[-2], buf[-1], False), d)
                buf = []
            else:
                pending, buf = d, []
            continue
        buf.append(line)
        if pending and len(buf) == 2:
            add(*_pair(buf[0], buf[1], True), pending, urls[0] if urls else "")
            pending, buf = None, []
    return out


# ───────────────────────── 让 AI 整理（认不出来时） ─────────────────────────

def import_prompt():
    return """把下面这段招聘信息整理成岗位清单，只输出 JSON，用 ```json 包起来，不要别的内容。

格式：
```json
[{"company": "公司名", "title": "岗位名", "deadline": "YYYY-MM-DD", "url": "申请链接", "type": "校招", "city": "香港"}]
```
规则：
1. 只要岗位，活动 / 讲座 / 宣讲会不要。
2. 截止日写成 YYYY-MM-DD；没写截止日留空 ""。
3. type 填「校招」或「实习」（Internship、Placement、Summer 都算实习）。
4. 原文有申请链接就填；你能联网的话，可以查官方申请页填进去，查不到留空，不要编。
5. city 写工作城市，看不出来就留空。

招聘信息：
（把邮件 / 网页内容粘贴在这里）"""


def from_json(text):
    m = re.search(r"```(?:json)?\s*(\[.*\])\s*```", text, re.S)
    s = m.group(1) if m else text[text.find("["):text.rfind("]") + 1]
    items = json.loads(s)
    out = []
    for x in items:
        if not isinstance(x, dict) or not (x.get("company") and x.get("title")):
            continue
        d = pool.parse_date(x.get("deadline") or "") or parse_day(str(x.get("deadline") or ""))
        t = "实习" if "实习" in str(x.get("type") or "") or INTERN.search(x["title"]) else "校招"
        out.append({"company": str(x["company"]).strip(), "title": str(x["title"]).strip(),
                    "deadline": d.isoformat() if d else "", "url": str(x.get("url") or "").strip(),
                    "type": t, "city": str(x.get("city") or "").strip()})
    return out


def recognize(text, today=None):
    """面板用：贴进来的是 AI 回的 JSON 就按 JSON 读，否则按文字认。"""
    t = (text or "").strip()
    if t.startswith("[") or "```json" in t:
        return from_json(t)
    return parse(t, today)


# ───────────────────────── 分池 / 查重 ─────────────────────────

def route(r):
    """香港还是大陆：岗位、公司、城市里写了大陆城市才算大陆，否则当香港岗（学校清单基本是香港的）。"""
    # 公司英文名里的城市不算（The Hongkong and Shanghai Banking Corporation 是汇丰）
    txt = " ".join([re.sub(r"[A-Za-z]", "", r.get("company", "")), r.get("title", ""), r.get("city", "")])
    hit = [c for c in MAINLAND if c.lower() in txt.lower()]
    if hit and not re.search(r"hong kong|香港", r.get("city", ""), re.I):
        return "cn", r.get("city") or hit[0]
    return "hk", "香港"


def _norm(s):
    s = (s or "").lower()
    s = re.sub(r"\b(the|limited|ltd|co|company|corporation|holdings?|group|hong kong|hk|\(h\.k\.\)|inc|plc)\b", " ", s)
    return re.sub(r"[^0-9a-z一-鿿]+", "", s)


def _tokens(s):
    return set(w for w in re.findall(r"[a-z]{3,}|[一-鿿]{2,}", (s or "").lower())
               if w not in {"the", "limited", "ltd", "company", "corporation", "holdings", "group", "hong", "kong",
                            "and", "bank", "banking", "asia", "international", "global"})


def same_title(a, b):
    """同一个岗位名：一样 / 一个包含另一个（短的也得够长）/ 几乎一样。
    「Summer Internship - Private Bank」和「… - Commercial Banking」不算同一个。"""
    if not a or not b:
        return False
    short, long_ = sorted((a, b), key=len)
    if a == b or (len(short) >= 12 and short in long_):
        return True
    if len(short) < 0.9 * len(long_):              # 长度差太多不可能 ≥0.95，省掉慢的比较（大陆池几千条）
        return False
    m = difflib.SequenceMatcher(None, a, b)
    return m.quick_ratio() >= 0.95 and m.ratio() >= 0.95


def index(rows):
    """岗位名预先归一化一次（大陆池几千条，每条都现算太慢）。"""
    return [(_norm(x.get("title")), x) for x in rows]


def find_dup(r, idx):
    """池子里有没有同一个岗位：岗位名对得上，且公司名有交集或截止日相同。→ 那一行 / None"""
    nt = _norm(r["title"])
    if not nt:
        return None
    ct = _tokens(r["company"]) | {_norm(r["company"])}
    for xt_title, x in idx:
        if not same_title(nt, xt_title):
            continue
        xt = _tokens(x.get("company")) | {_norm(x.get("company"))}
        if ct & xt or (r["deadline"] and r["deadline"] == (x.get("deadline") or "")):
            return x
    return None


def plan(items, cfg=None):
    """给预览用：每条加上 pool / city / dup（已在池子里的那条）/ pick（默认勾不勾）/ why。"""
    cfg = cfg or pool.load_config()
    rows = index((pool.load_pool(cfg.get("pool_path_hk")) if cfg.get("pool_path_hk") else [])
                 + pool.load_pool(cfg["pool_path"]))
    excl = {"hk": set(cfg.get("exclude_type_hk") or []), "cn": set(cfg.get("exclude_type") or [])}
    seen, today = [], datetime.date.today().isoformat()
    for r in items:
        key, city = route(r)
        r["pool"], r["city"] = key, r.get("city") or city
        dup = find_dup(r, rows) or find_dup(r, index(seen))
        r["dup"] = {"id": dup.get("id", ""), "company": dup.get("company", ""), "title": dup.get("title", "")} if dup else None
        r["pick"], r["why"] = True, ""
        if dup:
            r["pick"], r["why"] = False, "已在池子里"
        elif r["deadline"] and r["deadline"] < today:
            r["pick"], r["why"] = False, "已过截止日"
        elif r["type"] in excl[key]:
            r["pick"], r["why"] = False, "实习（按你的规则不推）"
        seen.append(r)
    return items


def save(items, note, prefix="m"):
    """勾上的加进池子。prefix='m' = 手动挑的（不过方向 / 关键词筛选）；'s' = 学校邮件自动导入（照常筛选）。"""
    import add_job
    files = {"m": add_job.FILES, "s": {"hk": "data/school_hk.csv", "cn": "data/school_cn.csv"}}[prefix]
    today = datetime.date.today()
    presets = pool.all_presets(pool.load_config())
    added = []
    for key in ("hk", "cn"):
        picks = [r for r in items if r.get("pick") and r.get("pool", "hk") == key]
        if not picks:
            continue
        path = jdlib.resolve(files[key])
        rows = pool.load_pool(path) if os.path.exists(path) else []
        head = "%s-%s-%s-" % (prefix, key, today.strftime("%Y%m%d"))
        n = max([int(r["id"].rsplit("-", 1)[1]) for r in rows if r["id"].startswith(head)] or [0])
        for r in picks:
            n += 1
            d = pool.parse_date(r.get("deadline") or "")
            row = {"id": head + str(n), "company": r["company"], "title": r["title"],
                   "city": r.get("city") or ("香港" if key == "hk" else ""),
                   "deadline": d.isoformat() if d else "", "deadline_kind": "fixed" if d else "until_full",
                   "deadline_text": d.isoformat() if d else "官网未列截止日", "apply_url": r.get("url") or "",
                   "type": r.get("type") or "校招", "industry": "", "score": 85 if prefix == "m" else 70,
                   "first_seen": today.isoformat(), "reasons": note}
            row["tags"] = pool.auto_tags(row, presets)
            rows.append(row)
            added.append(row)
        pool.write_pool(path, rows)
    return added


def main():
    ap = argparse.ArgumentParser(description="导入岗位清单")
    ap.add_argument("file", help="存着复制来的文字的文件（UTF-8）")
    ap.add_argument("--save", action="store_true", help="加进池子（默认只预览）")
    ap.add_argument("--note", default="清单导入", help="写进备注的来源")
    a = ap.parse_args()
    import io
    items = plan(recognize(io.open(a.file, encoding="utf-8-sig").read()))
    for r in items:
        print("%s %-2s %-4s %-10s %-28s %s%s" % ("✓" if r["pick"] else " ", r["pool"], r["type"], r["deadline"] or "滚动",
                                              r["company"][:28], r["title"][:60], "  ← " + r["why"] if r["why"] else ""))
    print("认出 %d 条，默认加 %d 条" % (len(items), sum(r["pick"] for r in items)))
    if a.save:
        print("✓ 已加 %d 条" % len(save(items, a.note)))


if __name__ == "__main__":
    sys.exit(main() or 0)
