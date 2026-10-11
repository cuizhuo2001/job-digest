#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
job-digest · 数据层

读岗位池 ── 按 config.json 的规则过滤 ── 排出「今日该投的 N 家」。

两个池子（港硕的两条线）：
    pool_path     大陆池  CSV
    pool_path_hk  香港池  CSV（可选）
字段见 README「岗位池 CSV」。只读本地文件，不联网：池子是快照，新岗要自己补。
"""
import io
import os
import re
import csv
import json
import datetime

import jdlib

HERE = jdlib.HERE
ROOT = jdlib.ROOT


def load_config(path=None):
    """规则（config.json）+ 运行状态（state.json）合并成一个 dict 给下游用。
    ⚠️ 只读：别把它整个存回 config.json，状态要写回用 jdlib.save_state()。"""
    with io.open(path or jdlib.CFG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    for k in ("pool_path", "pool_path_hk"):
        if cfg.get(k):
            cfg[k] = [jdlib.resolve(x) for x in pool_files(cfg[k])]
    if cfg.get("out_md"):
        cfg["out_md"] = jdlib.resolve(cfg["out_md"])
    st = jdlib.load_state()
    for k in jdlib.STATE_DEFAULTS:
        cfg[k] = st[k]
    return cfg


def parse_date(s):
    s = (s or "").strip()
    for fmt in ("%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d"):
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


# 池子 CSV 的标准列（import_cn / watch_hk / add_job 写出来的都是这个格式）
POOL_FIELDS = ["id", "company", "title", "city", "deadline", "deadline_kind", "deadline_text", "apply_url",
               "type", "industry", "tags", "score", "first_seen", "reasons"]


def pool_files(v):
    """pool_path 可以写一个文件，也可以写多个（前面的优先：重复岗位留前面那条）。"""
    if not v:
        return []
    return [v] if isinstance(v, str) else list(v)


def load_pool(path):
    """读一个或多个 CSV 拼起来；不存在的文件跳过，全都不存在才报错。"""
    paths = pool_files(path)
    rows, found = [], 0
    for p in paths:
        if not os.path.exists(p):
            continue
        found += 1
        with io.open(p, encoding="utf-8-sig", newline="") as f:
            rows += list(csv.DictReader(f))
    if not found:
        raise RuntimeError("找不到岗位池：%s" % "、".join(paths))
    return rows


def write_pool(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=POOL_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)


def mtime_of(path):
    """池子数据的日期（多个文件取最新的）—— 简报里要标出来，提醒该刷新了。
    导入的数据旁边有 <文件>.date（上游数据本身的日期），有就用它，不然用文件修改时间。"""
    ds = []
    files = [p for p in pool_files(path) if os.path.exists(p)]
    # 手动补录的文件不算：今天补了一条不代表池子新鲜，别把「几天没更新」的提醒盖掉
    auto = [p for p in files if not os.path.basename(p).startswith("manual_")]
    for p in auto or files:
        try:
            ds.append(datetime.date.fromisoformat(io.open(p + ".date", encoding="utf-8").read().strip()))
        except (OSError, ValueError):
            ds.append(datetime.date.fromtimestamp(os.path.getmtime(p)))
    return max(ds) if ds else None


def pool_mtime(cfg):
    return mtime_of(cfg.get("pool_path"))


def _num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def short_title(t, n=34):
    t = (t or "").replace("\n", " ").strip()
    return t if len(t) <= n else t[:n] + "…"


def norm_url(u):
    """比对用：去掉协议、www、语言段（/en-US/、/zh-CN/）和结尾斜杠；查询参数保留（北森的岗位 id 在里面）。"""
    u = (u or "").strip().lower()
    u = re.sub(r"^https?://(www\.)?", "", u)
    u = re.sub(r"/[a-z]{2}-[a-z]{2}(?=/)", "", u)
    return u.rstrip("/#")


def norm_title(t):
    """去掉所有标点/空白再比对 —— 同一个岗位常被收录两次，
    差别只是「,」还是「空格」（高盛、中信证券都中过）。"""
    return re.sub(r"[^\w一-鿿]+", "", (t or "")).lower()


# ───────────────────────── 关键词与方向 ─────────────────────────

def kw_hit(text, kws):
    """中文按子串；英文不分大小写、按单词开头匹配（Trader 也能命中 Traders），
    3 个字母以内的英文按整词匹配（HR 不会命中 three，PR 不会命中 Programme）。"""
    t = text or ""
    tl = t.lower()
    for k in kws or ():
        k = (k or "").strip()
        if not k:
            continue
        if k.isascii():
            kl = re.escape(k.lower())
            pat = r"(?<![a-z0-9])" + kl + (r"(?![a-z0-9])" if len(k) <= 3 else "")
            if re.search(pat, tl):
                return True
        elif k in t:
            return True
    return False


def row_tags(r):
    tags = set(x for x in re.split(r"[;,，；、/|\s]+", r.get("tags") or "") if x)
    if (r.get("fin") or "").strip() == "True":       # 老池子的 fin 列 = 「金融」标签
        tags.add("金融")
    return tags


def load_directions(cfg):
    """config.json 的 directions：写方向名就用 presets 里的定义；写对象可以自定义或覆盖同名预设。
    没写 directions 时沿用老开关：require_fin=true 等于只要 fin=True 的岗位。"""
    sel = cfg.get("directions")
    if sel is None:
        return [{"name": "金融", "tags": ["金融"]}] if cfg.get("require_fin") else []
    path = jdlib.resolve(cfg.get("direction_presets") or "presets/商科.json")
    presets = {k: v for k, v in jdlib.load_json(path, {}).items() if not k.startswith("_")}
    out = []
    for d in sel:
        if isinstance(d, str):
            if d not in presets:
                raise RuntimeError("config.json 的 directions 里有个不认识的方向「%s」，可选：%s"
                                   % (d, "、".join(presets)))
            out.append(dict(presets[d], name=d))
        else:
            out.append(dict(presets.get(d.get("name"), {}), **d))
    return out


FIN_HINT = ["金融", "银行", "证券", "基金", "保险", "信托", "期货", "资管", "资产管理", "投资", "支付", "Bank", "Securities",
            "Insurance", "Asset Management", "Investment"]


def all_presets(cfg=None):
    path = jdlib.resolve((cfg or {}).get("direction_presets") or "presets/商科.json")
    return [dict(v, name=k) for k, v in jdlib.load_json(path, {}).items() if not k.startswith("_")]


def auto_tags(row, presets):
    """导入/抓取时给岗位打方向标签：命中哪些预设方向就写哪些；行业像金融的再加一个「金融」。"""
    tags = [d["name"] for d in presets if dir_match(row, d, set())]
    if kw_hit(row.get("industry"), FIN_HINT) or kw_hit(row.get("company"), FIN_HINT):
        tags.append("金融")
    return ";".join(tags)


def dir_match(r, d, tags):
    title, co = r.get("title") or "", r.get("company") or ""
    if d.get("must_company") and not kw_hit(co, d["must_company"]):
        return False
    if d.get("must_title") and not kw_hit(title, d["must_title"]):
        return False
    if kw_hit(title, d.get("exclude_title")):
        return False
    if d["name"] in tags or tags & set(d.get("tags") or ()):
        return True
    keys = ("title", "industry", "company")
    if not any(d.get(k) for k in keys) and not d.get("tags"):
        return True                                   # 只写了 must_* 的方向
    return (kw_hit(title, d.get("title")) or kw_hit(r.get("industry"), d.get("industry"))
            or kw_hit(co, d.get("company")))


# ───────────────────────── 筛选与排序 ─────────────────────────

DROP_KEYS = ("已投", "死链", "屏蔽", "岗位类型", "行业/公司排除", "标题排除", "城市不符", "方向不符",
             "已过截止", "重复")


def _filter_rows(rows, cfg, now, cities, excl, quota, dirs, excl_type=(), url_dedupe=False):
    """筛选顺序：已投 → 死链 → 屏蔽 → 岗位类型 → 行业/公司排除 → 标题排除 → 城市 → 方向 → 已过截止 → 重复。"""
    applied = set(str(x).strip() for x in (cfg.get("applied_ids") or []))
    # 死链名单：check_links.py 探到 404/410 后写进去的 id，同 applied_ids 的机制
    dead = set(str(x).strip() for x in (cfg.get("dead_ids") or []))
    # 面板四态：blocked = {id: {reason, ...}} 永不再推；later = {id: 推迟次数} 第二天排最前
    blocked = set(str(x) for x in (cfg.get("blocked") or {}))
    later = cfg.get("later") or {}
    urgent_days = int(cfg.get("urgent_days", 3))
    lookahead = int(cfg.get("lookahead_days", 7))
    ex_ind = cfg.get("exclude_industry") or []
    # blocked_companies = 面板里点「不想投这家公司」攒下来的，跟手写的 exclude_company 一起生效
    ex_co = list(cfg.get("exclude_company") or []) + list(cfg.get("blocked_companies") or [])

    def keys_of(r):
        k = ((r.get("company") or "").strip(), norm_title(r.get("title")))
        return k, (norm_url(r.get("apply_url")) if url_dedupe else "")

    # 先把已投 / 死链 / 屏蔽的岗位记下来：别的来源（自动抓取、导入、手动补录）里的同一个岗位也要跟着跳过，
    # 不然投过的会换个 id 又被推一遍
    done = {}
    for r in rows:
        rid = (r.get("id") or "").strip()
        why = "已投" if rid in applied else "死链" if rid in dead else "屏蔽" if rid in blocked else None
        if rid and why:
            k, u = keys_of(r)
            done.setdefault(k, why)
            if u:
                done.setdefault(u, why)

    drop = dict.fromkeys(DROP_KEYS, 0)
    alive = []
    seen = set()
    seen_url = set()
    for r in rows:
        rid = (r.get("id") or "").strip()
        k, u = keys_of(r)
        why = ("已投" if rid in applied else "死链" if rid in dead else "屏蔽" if rid in blocked else None) \
            if rid else None
        why = why or done.get(k) or (done.get(u) if u else None)
        if why:
            drop[why] += 1
            continue
        title = r.get("title") or ""
        if rid.startswith("m-"):
            # 手动补录 = 用户自己挑的：不过类型/行业/标题/城市/方向这些规则，只查已投、过期、重复
            r["_dirs"] = [x for x in re.split(r"[;,，；、/|\s]+", r.get("tags") or "") if x] or ["手动补录"]
        else:
            if excl_type and (r.get("type") or "").strip() in excl_type:
                drop["岗位类型"] += 1
                continue
            if kw_hit(r.get("industry"), ex_ind) or kw_hit(r.get("company"), ex_co):
                drop["行业/公司排除"] += 1
                continue
            # 大陆池常见「一条公告列一串岗位类别」，总排除词容易误杀；方向可设 ignore_global_exclude 绕开它
            gex = kw_hit(title, excl)
            if gex and not any(d.get("ignore_global_exclude") for d in dirs):
                drop["标题排除"] += 1
                continue
            if not any(c in (r.get("city") or "") for c in cities):
                drop["城市不符"] += 1
                continue
            if dirs:
                tags = row_tags(r)
                hit = [d["name"] for d in dirs
                       if (not gex or d.get("ignore_global_exclude")) and dir_match(r, d, tags)]
                if not hit:
                    drop["标题排除" if gex else "方向不符"] += 1
                    continue
                r["_dirs"] = hit
            else:
                r["_dirs"] = []
        d = parse_date(r.get("deadline"))
        if d is not None and d < now:
            drop["已过截止"] += 1
            continue
        # 同公司 + 归一化标题相同 = 同一岗位被收录两次，只留分高的那条
        # 多个来源（人工名单 / 自动抓取 / 手动补录）可能是同一个岗位：香港池链接相同也算重复
        if k in seen or (u and u in seen_url):
            drop["重复"] += 1
            continue
        seen.add(k)
        if u:
            seen_url.add(u)
        r["_d"] = d
        r["_left"] = (d - now).days if d else None
        r["_score"] = _num(r.get("score"))
        alive.append(r)

    fixed = [r for r in alive if r["_d"] is not None]
    rolling = [r for r in alive if r["_d"] is None]
    fixed.sort(key=lambda r: (r["_d"], -r["_score"]))
    rolling.sort(key=lambda r: -r["_score"])

    urgent = [r for r in fixed if r["_left"] <= urgent_days]
    soon = [r for r in fixed if urgent_days < r["_left"] <= lookahead]
    # 「稍后」过的排最前（推迟越多次越靠前，别一直拖），其余保持「急 → 近 → 滚动」
    # 截止日在 lookahead 之外的固定岗垫在最后 —— 以前它们在进入 7 天窗口前永远排不上，补位会以为池子空了
    far = [r for r in fixed if r["_left"] > lookahead]
    ranked = urgent + soon + rolling + far
    for r in ranked:
        r["_later"] = int(later.get(str(r.get("id")), 0))
    ranked.sort(key=lambda r: -r["_later"])        # 稳定排序，同档内顺序不变

    return {"urgent": urgent, "soon": soon, "today": ranked[:quota], "alive": len(alive),
            "ranked": ranked,             # 面板补位 / 再来 5 家 用的完整排序
            "alive_rows": alive,          # check_links.py --all 用；其他消费者忽略
            "rolling": len(rolling), "fixed": len(fixed), "total": len(rows),
            "drop": drop}


def _pick_today(part, dirs, key, quota):
    """方向写了 quota_cn / quota_hk = 每天固定给它这么多家（不多不少）：
    先按名额从它的岗位里挑，再用其余方向的岗位按排序补满。最后按原排序摆放。"""
    fixed = [d for d in dirs if int(d.get("quota_" + key) or 0) > 0]
    if not fixed:
        return
    fixed_names = set(d["name"] for d in fixed)
    ranked = part["ranked"]
    picks, ids = [], set()
    for d in fixed:
        n = int(d["quota_" + key])
        for r in ranked:
            if n <= 0 or len(picks) >= quota:
                break
            if d["name"] in r["_dirs"] and id(r) not in ids:
                picks.append(r)
                ids.add(id(r))
                n -= 1
    for r in ranked:
        if len(picks) >= quota:
            break
        if id(r) not in ids and set(r["_dirs"]) - fixed_names:
            picks.append(r)
            ids.add(id(r))
    pos = {id(r): i for i, r in enumerate(ranked)}
    part["today"] = sorted(picks, key=lambda r: pos[id(r)])


def select(cfg, now=None):
    """大陆池 + 香港池各排各的，再合并成一份结果。"""
    now = now or datetime.date.today()
    dirs = load_directions(cfg)

    q_cn = int(cfg.get("daily_quota_cn", cfg.get("daily_quota", 10)))
    cn = _filter_rows(load_pool(cfg["pool_path"]), cfg, now,
                      cities=cfg.get("allowed_cities") or [],
                      excl=cfg.get("exclude_title") or [], quota=q_cn, dirs=dirs)
    _pick_today(cn, dirs, "cn", q_cn)

    hk = None
    hk_path = cfg.get("pool_path_hk")
    if hk_path and any(os.path.exists(x) for x in hk_path):
        q_hk = int(cfg.get("daily_quota_hk", 5))
        hk = _filter_rows(load_pool(hk_path), cfg, now, cities=["香港"],
                          excl=cfg.get("exclude_title_hk") or [],
                          quota=q_hk, dirs=dirs, excl_type=tuple(cfg.get("exclude_type_hk") or ()),
                          url_dedupe=True)
        _pick_today(hk, dirs, "hk", q_hk)

    parts = [p for p in (cn, hk) if p]
    stats = {
        "total": sum(p["total"] for p in parts),
        "alive": sum(p["alive"] for p in parts),
        "urgent": sum(len(p["urgent"]) for p in parts),
        "soon": sum(len(p["soon"]) for p in parts),
        "rolling": sum(p["rolling"] for p in parts),
        "drop": {k: sum(p["drop"].get(k, 0) for p in parts) for k in DROP_KEYS},
    }
    return {"cn": cn, "hk": hk, "stats": stats}


if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    c = load_config()
    d = select(c)
    s = d["stats"]
    print("合计 池子 %d → 可用 %d（急 %d / 近 %d / 滚动 %d）" % (
        s["total"], s["alive"], s["urgent"], s["soon"], s["rolling"]))
    for key, label in (("hk", "🇭🇰 香港"), ("cn", "🇨🇳 大陆")):
        p = d.get(key)
        if not p:
            continue
        print("\n%s：可用 %d，今日推 %d" % (label, p["alive"], len(p["today"])))
        for i, r in enumerate(p["today"], 1):
            print("  %2d. [%s] %s · %s · %s" % (
                i, r["_d"] or "滚动", r.get("company"), short_title(r.get("title"), 26),
                (r.get("city") or "")[:16]))
