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
    for k in ("pool_path", "pool_path_hk", "out_md"):
        if cfg.get(k):
            cfg[k] = jdlib.resolve(cfg[k])
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


def load_pool(path):
    if not os.path.exists(path):
        raise RuntimeError("找不到岗位池：%s" % path)
    with io.open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def mtime_of(path):
    """池子快照的日期 —— 简报里要标出来，提醒该刷新了。"""
    if not path or not os.path.exists(path):
        return None
    return datetime.date.fromtimestamp(os.path.getmtime(path))


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


def norm_title(t):
    """去掉所有标点/空白再比对 —— 同一个岗位常被收录两次，
    差别只是「,」还是「空格」（高盛、中信证券都中过）。"""
    return re.sub(r"[^\w一-鿿]+", "", (t or "")).lower()


def _filter_rows(rows, cfg, now, cities, excl, quota, require_fin, row_ok=None,
                 excl_type=()):
    """通用筛选：已投 → 死链 → 社招 → 夕阳行业 → 吃资源/纯研发 → 地区 → 非金融口 → 已过截止 → 重复。"""
    applied = set(str(x).strip() for x in (cfg.get("applied_ids") or []))
    # 死链名单：check_links.py 探到 404/410 后写进去的 id，同 applied_ids 的机制
    dead = set(str(x).strip() for x in (cfg.get("dead_ids") or []))
    # 面板四态：blocked = {id: {reason, ...}} 永不再推；later = {id: 推迟次数} 第二天排最前
    blocked = set(str(x) for x in (cfg.get("blocked") or {}))
    later = cfg.get("later") or {}
    urgent_days = int(cfg.get("urgent_days", 3))
    lookahead = int(cfg.get("lookahead_days", 7))
    sunset_ind = cfg.get("exclude_industry") or []
    # blocked_companies = 面板里点「不想投这家公司」攒下来的，跟手写的 exclude_company 一起生效
    sunset_co = list(cfg.get("exclude_company") or []) + list(cfg.get("blocked_companies") or [])

    drop = {"已投": 0, "死链": 0, "屏蔽": 0, "社招": 0, "夕阳行业": 0, "吃资源/纯研发": 0,
            "地区不符": 0, "已过截止": 0, "非金融口": 0, "重复": 0}
    alive = []
    seen = set()
    for r in rows:
        rid = (r.get("id") or "").strip()
        if rid and rid in applied:
            drop["已投"] += 1
            continue
        if rid and rid in dead:
            drop["死链"] += 1
            continue
        if rid and rid in blocked:
            drop["屏蔽"] += 1
            continue
        if excl_type and (r.get("type") or "").strip() in excl_type:
            drop["社招"] += 1
            continue
        if any(k in (r.get("industry") or "") for k in sunset_ind) or \
                any(k in (r.get("company") or "") for k in sunset_co):
            drop["夕阳行业"] += 1
            continue
        title = r.get("title") or ""
        if any(k in title for k in excl):
            drop["吃资源/纯研发"] += 1
            continue
        if not any(c in (r.get("city") or "") for c in cities):
            drop["地区不符"] += 1
            continue
        if row_ok is not None:
            if not row_ok(r):
                drop["非金融口"] += 1
                continue
        elif require_fin and (r.get("fin") or "").strip() != "True":
            drop["非金融口"] += 1
            continue
        d = parse_date(r.get("deadline"))
        if d is not None and d < now:
            drop["已过截止"] += 1
            continue
        # 同公司 + 归一化标题相同 = 同一岗位被收录两次，只留分高的那条
        key = ((r.get("company") or "").strip(), norm_title(title))
        if key in seen:
            drop["重复"] += 1
            continue
        seen.add(key)
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
    today = ranked[:quota]

    return {"urgent": urgent, "soon": soon, "today": today, "alive": len(alive),
            "ranked": ranked,             # 面板补位 / 再来 5 家 用的完整排序
            "alive_rows": alive,          # check_links.py --all 用；其他消费者忽略
            "rolling": len(rolling), "fixed": len(fixed), "total": len(rows),
            "drop": drop}


def select(cfg, now=None):
    """大陆池 + 香港池各排各的，再合并成一份结果。"""
    now = now or datetime.date.today()
    require_fin = bool(cfg.get("require_fin", True))

    cn = _filter_rows(
        load_pool(cfg["pool_path"]), cfg, now,
        cities=cfg.get("allowed_cities") or [],
        excl=cfg.get("exclude_title") or [],
        quota=int(cfg.get("daily_quota_cn", cfg.get("daily_quota", 10))),
        require_fin=require_fin)

    # 互联网/大厂 产品·运营·数据 岗：不要求 fin，但要「大厂公司名 + 岗位方向词」。
    # 并进 cn 结果（consumers 只认 cn/hk 两个 key），cn 名额里划 daily_quota_tech 个给它
    nt = int(cfg.get("daily_quota_tech", 0))
    if nt > 0:
        cos = cfg.get("tech_companies") or []
        kws = cfg.get("tech_title_kw") or []
        tech = _filter_rows(
            load_pool(cfg["pool_path"]), cfg, now,
            cities=cfg.get("allowed_cities") or [],
            excl=cfg.get("exclude_title_tech") or [],
            quota=nt, require_fin=False,
            row_ok=lambda r: any(c in (r.get("company") or "") for c in cos)
            and any(k in (r.get("title") or "") for k in kws))
        n_fin = max(0, int(cfg.get("daily_quota_cn", 5)) - nt)
        have = set(str(r.get("id")) for r in cn["today"][:n_fin])
        extra = [r for r in tech["today"] if str(r.get("id")) not in have]
        cn["today"] = cn["today"][:n_fin] + extra[:nt]
        ids = set(str(r.get("id")) for r in cn["ranked"])
        merged = cn["ranked"] + [r for r in tech["ranked"] if str(r.get("id")) not in ids]
        cn["ranked"] = sorted(merged, key=lambda r: -r["_later"])
        for k in ("urgent", "soon"):
            ids = set(str(r.get("id")) for r in cn[k])
            cn[k] = cn[k] + [r for r in tech[k] if str(r.get("id")) not in ids]
        cn["alive"] += tech["alive"]

    hk = None
    hk_path = cfg.get("pool_path_hk")
    if hk_path and os.path.exists(hk_path):
        hk = _filter_rows(
            load_pool(hk_path), cfg, now,
            cities=["香港"],
            excl=cfg.get("exclude_title_hk") or [],
            quota=int(cfg.get("daily_quota_hk", 5)),
            require_fin=require_fin,
            excl_type=tuple(cfg.get("exclude_type_hk") or ()))

    parts = [p for p in (cn, hk) if p]
    stats = {
        "total": sum(p["total"] for p in parts),
        "alive": sum(p["alive"] for p in parts),
        "urgent": sum(len(p["urgent"]) for p in parts),
        "soon": sum(len(p["soon"]) for p in parts),
        "rolling": sum(p["rolling"] for p in parts),
        "drop": {k: sum(p["drop"].get(k, 0) for p in parts)
                 for k in cn["drop"]},
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
