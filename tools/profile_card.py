# -*- coding: utf-8 -*-
"""
job-digest · 「我的资料」

资料存在填表速查卡（Markdown，profile.json 的 card_path 指过去），分 8 个 part：
基本信息 / 联系方式 / 教育经历 / 工作实习经历 / 语言与技能 / 求职意向 / 签证与常见问答 / 附件。
教育、工作每段一张卡（### 小节）。每个字段一行表格：| 字段 | 中文 | English | 备注 |，值写在反引号里。

- 清单外的字段（自定义字段）照样保留、照样显示
- 认不出来的 ## 小节（比如你自己的备注）原样保留，写回时放在最后
- 备注栏里的 [AI翻译待核对:en] 表示这一格是 AI 翻译的，面板上点「确认」就去掉
- 证明材料（学位证、成绩单、实习证明）是文件路径，每段卡片各自登记，中文 / English 两栏放两个语言版本
"""
import io
import os
import re
import copy
import json

# ───────────────────────── 字段清单 ─────────────────────────


def F(key, zh, en, bi=True, opt=False, secret=False, multi=False, path=False):
    return {"key": key, "zh": zh, "en": en, "bi": bi, "opt": opt, "secret": secret, "multi": multi, "path": path}


SCHEMA = [
    {"key": "basic", "zh": "基本信息", "en": "Personal info", "match": ["基本信息"], "fields": [
        F("name", "姓名", "Full name"),
        F("last", "姓", "Last name"),
        F("first", "名", "First name"),
        F("nickname", "英文昵称", "Preferred name", bi=False, opt=True),
        F("gender", "性别", "Gender"),
        F("dob", "出生日期", "Date of birth", bi=False),
        F("nationality", "国籍", "Nationality"),
        F("id_type", "证件类型", "ID type", opt=True),
        F("id_no", "证件号码", "ID number", bi=False, opt=True, secret=True),
        F("hometown", "籍贯", "Place of origin", opt=True),
    ]},
    {"key": "contact", "zh": "联系方式", "en": "Contact", "match": ["联系方式"], "fields": [
        F("email", "主邮箱", "Email", bi=False),
        F("email2", "备用邮箱", "Alternate email", bi=False, opt=True),
        F("phone_hk", "香港手机", "HK mobile", bi=False),
        F("phone_cn", "大陆手机", "Mainland mobile", bi=False),
        F("addr_hk", "香港住址", "HK address"),
        F("addr_cn", "大陆住址", "Mainland address", opt=True),
        F("linkedin", "LinkedIn", "LinkedIn", bi=False, opt=True),
    ]},
    {"key": "education", "zh": "教育经历", "en": "Education", "match": ["教育"], "card": True, "fields": [
        F("school", "学校", "School"),
        F("degree", "学位", "Degree"),
        F("major", "专业", "Major"),
        F("start", "开始", "From", bi=False),
        F("end", "结束", "To", bi=False),
        F("gpa", "成绩 / GPA", "GPA", bi=False),
        F("rank", "排名", "Ranking", bi=False, opt=True),
        F("courses", "主修课程", "Courses", opt=True),
        F("proof", "学位证 / 在读证明", "Degree / enrolment proof", opt=True, path=True),
        F("transcript", "成绩单", "Transcript", opt=True, path=True),
    ]},
    {"key": "work", "zh": "工作/实习经历", "en": "Work experience", "match": ["工作", "实习"], "card": True, "fields": [
        F("company", "单位", "Company"),
        F("dept", "部门", "Department", opt=True),
        F("title", "职位", "Job title"),
        F("city", "城市", "Location"),
        F("start", "开始", "From", bi=False),
        F("end", "结束", "To", bi=False),
        F("reason", "离职原因", "Reason for leaving"),
        F("desc", "工作描述", "Description", multi=True),
        F("proof", "实习 / 工作证明", "Employment certificate", opt=True, path=True),
    ]},
    {"key": "skills", "zh": "语言与技能", "en": "Languages & skills", "match": ["语言", "技能"], "fields": [
        F("english", "英语成绩", "English test"),
        F("mandarin", "普通话", "Mandarin"),
        F("cantonese", "粤语", "Cantonese"),
        F("other_lang", "其他语言", "Other languages", opt=True),
        F("certs", "证书", "Certificates", opt=True),
        F("skills", "技能", "Skills", opt=True),
    ]},
    {"key": "intent", "zh": "求职意向", "en": "Job preferences", "match": ["求职意向", "到岗"], "fields": [
        F("start_date", "最早到岗", "Earliest start date"),
        F("salary_hk", "期望月薪（港币）", "Expected salary (HKD)", bi=False),
        F("salary_cn", "期望月薪（人民币）", "Expected salary (RMB)", bi=False),
        F("cities", "意向城市", "Preferred locations"),
        F("travel", "出差 / 轮岗", "Travel & rotation", opt=True),
    ]},
    {"key": "qa", "zh": "签证与常见问答", "en": "Visa & questions", "match": ["签证", "问答"], "fields": [
        F("sponsorship", "是否需要签证担保", "Sponsorship required"),
        F("visa_type", "现持签证", "Current visa"),
        F("political", "政治面貌", "Political status", opt=True),
        F("source", "如何得知本岗位", "How did you hear about us"),
        F("disclosure", "自愿披露（种族、残障等）", "Voluntary disclosure"),
        F("relatives", "有无亲属在本公司", "Relatives in the company"),
    ]},
    {"key": "files", "zh": "附件", "en": "Attachments", "match": ["附件"], "fields": [
        F("resume_en", "英文简历", "Resume (English)", bi=False, path=True),
        F("resume_cn", "中文简历", "Resume (Chinese)", bi=False, path=True),
        F("transcript", "成绩单", "Transcript", bi=False, opt=True, path=True),
        F("other", "其他附件", "Other attachment", bi=False, opt=True, path=True),
    ]},
]
PARTS = {p["key"]: p for p in SCHEMA}
HEADER = "| 字段 | 中文 | English | 备注 |\n|---|---|---|---|"
AI_TAG = re.compile(r"\s*\[AI翻译待核对:([a-z,]+)\]")


def empty_cell():
    return {"zh": "", "en": "", "note": "", "ai": []}


def empty_part(p):
    if p.get("card"):
        return {"cards": [], "text": []}
    return {"cells": {f["key"]: empty_cell() for f in p["fields"]}, "extra": [], "text": []}


def empty_card(p):
    return {"cells": {f["key"]: empty_cell() for f in p["fields"]}, "extra": [], "text": []}


def empty_profile():
    return {"preface": [], "parts": {p["key"]: empty_part(p) for p in SCHEMA}, "extras": []}


# ───────────────────────── 读 ─────────────────────────

def _val(s):
    """表格单元格 → 值：去掉反引号；— / - / 空 = 没填。"""
    s = (s or "").strip()
    m = re.findall(r"`([^`]*)`", s)
    if m:
        s = " / ".join(x.strip() for x in m)
    return "" if s in ("—", "-", "–", "N/A（未填）") else s


def _match_part(title):
    t = re.sub(r"^[\s\d①-⑳.、]+", "", title)
    for p in SCHEMA:
        if any(k in t for k in p["match"]):
            return p
    return None


def _field_of(p, label):
    label = label.strip()
    for f in p["fields"]:
        if label in (f["zh"], f["en"]):
            return f
    return None


def _fill_table_row(p, holder, cells):
    label = cells[0].strip()
    zh = _val(cells[1]) if len(cells) > 1 else ""
    en = _val(cells[2]) if len(cells) > 2 else ""
    note = cells[3].strip() if len(cells) > 3 else ""
    ai = []
    m = AI_TAG.search(note)
    if m:
        ai = [x for x in m.group(1).split(",") if x in ("zh", "en")]
        note = AI_TAG.sub("", note).strip()
    f = _field_of(p, label)
    if f:
        holder["cells"][f["key"]] = {"zh": zh, "en": "" if not f["bi"] else en, "note": note, "ai": ai}
    else:
        holder["extra"].append({"label": label, "zh": zh, "en": en, "note": note, "ai": ai})


def parse(text):
    prof = empty_profile()
    lines = text.replace("\r\n", "\n").split("\n")
    cur_p = cur = None          # cur = 当前 holder（part 或 card）
    extra_buf = None
    desc_lang = None
    for ln in lines:
        if ln.startswith("## "):
            desc_lang = None
            p = _match_part(ln[3:])
            if p and p["key"] not in prof.get("_seen", set()):
                prof.setdefault("_seen", set()).add(p["key"])
                cur_p, extra_buf = p, None
                cur = prof["parts"][p["key"]] if not p.get("card") else None
            else:
                cur_p, cur = None, None
                extra_buf = [ln]
                prof["extras"].append(extra_buf)
            continue
        if extra_buf is not None:
            extra_buf.append(ln)
            continue
        if cur_p is None:                    # 第一个 ## 之前 = 文件开头的说明
            if not prof.get("_seen"):
                prof["preface"].append(ln)
            continue
        if ln.startswith("### ") and cur_p.get("card"):
            desc_lang = None
            cur = empty_card(cur_p)
            cur["cells"] = {}
            prof["parts"][cur_p["key"]]["cards"].append(cur)
            continue
        if cur is None:                      # 卡片区里、第一张卡之前的文字
            t = prof["parts"][cur_p["key"]]["text"]
            if ln.strip() or (t and t[-1] != ""):
                t.append(ln)
            continue
        m = re.match(r"^\*\*(.+?)\s*·\s*(中文|English)\*\*(.*)$", ln.strip())
        if m:
            f = _field_of(cur_p, m.group(1))
            if f and f.get("multi"):
                desc_lang = "zh" if m.group(2) == "中文" else "en"
                c = cur["cells"].setdefault(f["key"], empty_cell())
                c[desc_lang] = ""
                if "AI翻译待核对" in m.group(3):
                    c["ai"].append(desc_lang)
                cur["_desc"] = f["key"]
                continue
        if desc_lang and re.match(r"^\s*[-*·•]\s+", ln):
            c = cur["cells"][cur["_desc"]]
            item = re.sub(r"^\s*[-*·•]\s+", "", ln).rstrip()
            c[desc_lang] = (c[desc_lang] + "\n" + item).strip("\n")
            continue
        if ln.strip().startswith("|"):
            desc_lang = None
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            if not cells or cells[0] in ("字段", "问题", "时间") or not cells[0].strip("-: "):
                continue
            _fill_table_row(cur_p, cur, cells)
            continue
        if not ln.strip():                   # 空行不打断工作描述（标题和要点之间本来就有空行）
            if cur["text"] and cur["text"][-1] != "":
                cur["text"].append("")
            continue
        desc_lang = None                     # 其他文字（你自己写的说明）原样留着
        cur["text"].append(ln)
    # 卡片缺的字段补空格子；去掉解析用的临时键
    for p in SCHEMA:
        part = prof["parts"][p["key"]]
        holders = part["cards"] if p.get("card") else [part]
        for h in holders:
            for f in p["fields"]:
                h["cells"].setdefault(f["key"], empty_cell())
            h.pop("_desc", None)
            while h["text"] and not h["text"][-1].strip():
                h["text"].pop()
            if all(not x.strip() or x.strip() == "---" for x in h["text"]):
                h["text"] = []
        while part["text"] and not part["text"][-1].strip():
            part["text"].pop()
    prof.pop("_seen", None)
    while prof["preface"] and not prof["preface"][-1].strip():
        prof["preface"].pop()
    return prof


def load(path):
    if not path or not os.path.exists(path):
        return empty_profile()
    return parse(io.open(path, encoding="utf-8").read())


# ───────────────────────── 写 ─────────────────────────

def _cell_md(v):
    v = (v or "").replace("`", "'").replace("|", "／").replace("\n", " ").strip()
    return "`%s`" % v if v else ""


def _row(label, zh, en, note, ai, bi=True):
    note = (note or "").strip()
    if ai:
        note = (note + " [AI翻译待核对:%s]" % ",".join(ai)).strip()
    return "| %s | %s | %s | %s |" % (label, _cell_md(zh), _cell_md(en) if bi else "—", note)


def _holder_md(p, h, out):
    rows, multi = [], []
    for f in p["fields"]:
        c = h["cells"].get(f["key"]) or empty_cell()
        if f.get("multi"):
            multi.append((f, c))
            continue
        rows.append(_row(f["zh"], c["zh"], c["en"], c.get("note"), c.get("ai"), f["bi"]))
    for x in h.get("extra") or []:
        rows.append(_row(x["label"], x["zh"], x["en"], x.get("note"), x.get("ai")))
    out += ["", HEADER] + rows
    for f, c in multi:
        for lang, name in (("zh", "中文"), ("en", "English")):
            out += ["", "**%s · %s**%s" % (f["zh"], name, "（AI翻译待核对）" if lang in (c.get("ai") or []) else "")]
            items = [x.strip() for x in (c[lang] or "").split("\n") if x.strip()]
            out += [""] + ["- " + x for x in items] if items else ["", "- "]
    if h.get("text"):
        out += [""] + h["text"]


def dump(prof):
    out = list(prof.get("preface") or [])
    if not out:
        out = ["# 填表速查卡", "",
               "> 今日投递面板的「我的资料」读写这份文件。值写在反引号里；没填的留空。",
               "> 可以直接在面板里编辑，也可以用记事本改，格式照着下面的样子。"]
    for i, p in enumerate(SCHEMA, 1):
        part = prof["parts"].get(p["key"]) or empty_part(p)
        out += ["", "## %d %s" % (i, p["zh"])]
        if p.get("card"):
            if part.get("text"):
                out += [""] + part["text"]
            for j, card in enumerate(part["cards"], 1):
                out += ["", "### 第 %d 段" % j]
                _holder_md(p, card, out)
        else:
            _holder_md(p, part, out)
    for ex in prof.get("extras") or []:
        while ex and not ex[-1].strip():
            ex = ex[:-1]
        out += [""] + ex
    return "\n".join(out).rstrip() + "\n"


def save(path, prof):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        f.write(dump(prof))
    os.replace(tmp, path)


# ───────────────────────── 给面板的视图 ─────────────────────────

def _exists(p, h, resolve):
    """文件路径字段：中英文两个版本的文件各自在不在。"""
    out = {}
    for f in p["fields"]:
        if f.get("path"):
            c = h["cells"].get(f["key"]) or {}
            out[f["key"]] = {lang: bool(c.get(lang)) and os.path.exists(resolve(c[lang]) if resolve else c[lang])
                             for lang in ("zh", "en")}
    return out


def view(prof, resolve=None):
    """面板要的数据：字段定义 + 值；文件路径顺便告诉它文件在不在。"""
    parts = []
    for p in SCHEMA:
        part = prof["parts"][p["key"]]
        d = {"key": p["key"], "zh": p["zh"], "en": p["en"], "card": bool(p.get("card")),
             "fields": [{k: f[k] for k in ("key", "zh", "en", "bi", "opt", "secret", "multi", "path")}
                        for f in p["fields"]]}
        if p.get("card"):
            d["cards"] = [{"cells": c["cells"], "extra": c.get("extra") or [], "exists": _exists(p, c, resolve)}
                          for c in part["cards"]]
        else:
            d["cells"] = part["cells"]
            d["extra"] = part.get("extra") or []
            d["exists"] = _exists(p, part, resolve)
        parts.append(d)
    return parts


def apply_edit(prof, key, data):
    """面板「编辑 → 保存」：只改值；改过的那一格视为已核对，去掉 AI 翻译标记。备注、段落文字保留。"""
    p = PARTS[key]
    part = prof["parts"][key]

    def merge(old, new):
        out = copy.deepcopy(old)
        for k, c in (new.get("cells") or {}).items():
            o = out["cells"].setdefault(k, empty_cell())
            for lang in ("zh", "en"):
                v = (c.get(lang) or "").strip()
                if v != (o.get(lang) or ""):
                    o["ai"] = [x for x in o.get("ai") or [] if x != lang]
                o[lang] = v
        olds = out.get("extra") or []
        out["extra"] = []
        for i, x in enumerate(new.get("extra") or []):
            o = copy.deepcopy(olds[i]) if i < len(olds) else {"label": x.get("label", ""), "note": "", "ai": []}
            for lang in ("zh", "en"):
                v = (x.get(lang) or "").strip()
                if v != (o.get(lang) or ""):
                    o["ai"] = [y for y in o.get("ai") or [] if y != lang]
                o[lang] = v
            o["label"] = (x.get("label") or o.get("label") or "").strip()
            if o["label"]:
                out["extra"].append(o)
        return out

    if p.get("card"):
        olds = part["cards"]
        cards = []
        for i, c in enumerate(data.get("cards") or []):
            base = olds[c["from"]] if isinstance(c.get("from"), int) and 0 <= c["from"] < len(olds) else empty_card(p)
            cards.append(merge(base, c))
        part["cards"] = cards
    else:
        prof["parts"][key] = merge(part, data)
    return prof


def confirm_ai(prof, key, field, lang, card=None, extra=None):
    part = prof["parts"][key]
    h = part["cards"][card] if card is not None else part
    c = h["extra"][extra] if extra is not None else h["cells"][field]
    c["ai"] = [x for x in c.get("ai") or [] if x != lang]
    return prof


# ───────────────────────── 导入简历 ─────────────────────────

def _template():
    tpl, legend = {}, []
    for p in SCHEMA:
        if p["key"] == "files":
            continue
        item = {}
        for f in p["fields"]:
            if f.get("path"):                # 证明材料是本机文件，AI 管不着
                continue
            if f.get("multi"):
                item[f["key"]] = {"zh": [], "en": []}
            elif f["bi"]:
                item[f["key"]] = {"zh": "", "en": ""}
            else:
                item[f["key"]] = ""
            legend.append("%s.%s = %s（%s）" % (p["key"], f["key"], f["zh"], f["en"]))
        tpl[p["key"]] = [item] if p.get("card") else item
    tpl["_ai_translated"] = []
    return tpl, legend


def import_prompt():
    tpl, legend = _template()
    return """请从我下面贴的简历里提取个人信息，严格按下面的 JSON 格式输出，用 ```json 包起来，除此之外不要输出任何内容。

规则：
1. 只用简历里有的信息，不要编造。简历里没有的一律留空字符串 ""（数组留 []）。
2. 带 "zh" / "en" 的字段要中英文两个版本。简历里只有一种语言时，把另一种翻译出来，并把这个字段的路径写进 "_ai_translated"，例如 "work.0.title.en"、"basic.name.zh"、"education.1.school.en"。
3. 学校、公司如果有官方的中文名 / 英文名就用官方的；拿不准官方译名时留空，不要硬译。
4. 英文姓名按护照拼法，姓全大写，例如 "Tai Man CHAN"；姓、名分别填进 last、first。
5. 日期写成 YYYY-MM，出生日期写成 YYYY-MM-DD。在读的结束时间写预计毕业时间。
6. 工作描述拆成要点数组，一条一句，去掉 ** 这类排版符号。
7. education、work 都是数组，每段一个对象，按时间由近到远排。
8. 签证、期望薪资、离职原因这类简历上通常没有的，留空就好。

JSON 格式：
```json
%s
```

字段含义：
%s

简历：
（把简历全文粘贴在这里）""" % (json.dumps(tpl, ensure_ascii=False, indent=1), "\n".join(legend))


def _extract_json(text):
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    s = m.group(1) if m else text[text.find("{"):text.rfind("}") + 1]
    if not s.strip():
        raise ValueError("没找到 JSON：把 AI 回复的整段（包括 ```json 那几行）都粘贴进来")
    try:
        return json.loads(s)
    except ValueError as e:
        raise ValueError("JSON 格式有误（%s）：让 AI「只输出 JSON，用 ```json 包起来」再来一次" % e)


def _has_cjk(s):
    return bool(re.search(r"[一-鿿]", s or ""))


def _norm(f, v):
    """AI 给的值 → (zh, en)。容忍它把字符串和对象写混。"""
    def s(x):
        if isinstance(x, list):
            return "\n".join(str(i).replace("**", "").strip() for i in x if str(i).strip())
        return str(x or "").replace("`", "'").strip()
    if isinstance(v, dict):
        zh, en = s(v.get("zh")), s(v.get("en"))
    else:
        t = s(v)
        zh, en = (t, "") if (_has_cjk(t) or not f["bi"]) else ("", t)
    if not f["bi"]:
        return zh or en, ""
    return zh, en


def from_import(text, current):
    """AI 回复 → 合并进当前资料（不落盘）。返回 (新资料, 统计)。
    规则：AI 给了值的覆盖；没给的保留原来的（签证答法这类简历上没有的不会被清掉）；
    教育 / 工作只要 AI 给了至少一段，就整组换成 AI 的（证明材料路径按学校 / 公司名带过去）。附件不动。"""
    obj = _extract_json(text)
    ai_paths = set(str(x).replace("[", ".").replace("]", "") for x in obj.get("_ai_translated") or [])
    new = copy.deepcopy(current)
    st = {"filled": 0, "ai": 0}

    def put(cell, f, v, path):
        zh, en = _norm(f, v)
        if not (zh or en):
            return
        if zh:
            cell["zh"] = zh
        if en and f["bi"]:
            cell["en"] = en
        cell["ai"] = [lang for lang in ("zh", "en") if "%s.%s" % (path, lang) in ai_paths]
        st["filled"] += 1
        st["ai"] += len(cell["ai"])

    for p in SCHEMA:
        if p["key"] == "files":
            continue
        src = obj.get(p["key"])
        if p.get("card"):
            items = [x for x in (src or []) if isinstance(x, dict)] if isinstance(src, list) else []
            if not items:
                continue
            cards = []
            for i, item in enumerate(items):
                card = empty_card(p)
                for f in p["fields"]:
                    if not f.get("path"):
                        put(card["cells"][f["key"]], f, item.get(f["key"]), "%s.%d.%s" % (p["key"], i, f["key"]))
                cards.append(card)
            _carry_paths(p, current["parts"][p["key"]]["cards"], cards)
            new["parts"][p["key"]]["cards"] = cards
        elif isinstance(src, dict):
            for f in p["fields"]:
                put(new["parts"][p["key"]]["cells"].setdefault(f["key"], empty_cell()), f, src.get(f["key"]),
                    "%s.%s" % (p["key"], f["key"]))
    return new, st


def _carry_paths(p, olds, cards):
    """导入会整组换掉教育 / 工作卡片，但 AI 不会给证明材料路径：
    按学校 / 公司名（中英文任一对上）从旧卡片带过来；名字都对不上的，同一位置的旧卡片还没被认领就按位置带。"""
    keys = [f["key"] for f in p["fields"] if f.get("path")]
    name = p["fields"][0]["key"]                     # school / company

    def names(c):
        x = c["cells"].get(name) or {}
        return set(v.strip().lower() for v in (x.get("zh"), x.get("en")) if v and v.strip())
    used = set()
    pairs = []
    for i, card in enumerate(cards):
        j = next((j for j, o in enumerate(olds) if j not in used and names(o) & names(card)), None)
        if j is not None:
            used.add(j)
        pairs.append(j)
    for i, card in enumerate(cards):
        j = pairs[i]
        if j is None and i < len(olds) and i not in used:
            j = i
            used.add(i)
        if j is not None:
            for k in keys:
                card["cells"][k] = copy.deepcopy(olds[j]["cells"].get(k) or empty_cell())


def todo(prof, lang):
    """必填但没填的格子数（按语言）：{part: n}"""
    out = {}
    for p in SCHEMA:
        part = prof["parts"][p["key"]]
        holders = part["cards"] if p.get("card") else [part]
        n = 0
        for h in holders:
            for f in p["fields"]:
                if f["opt"]:
                    continue
                c = h["cells"].get(f["key"]) or empty_cell()
                v = c["zh"] if (not f["bi"] or lang == "zh") else c["en"]
                n += not v
        out[p["key"]] = n
    return out
