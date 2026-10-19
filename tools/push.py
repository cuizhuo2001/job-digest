#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
job-digest · 每日投递简报

把岗位池压成一条能扫的简报，按 config.json 的 notify.channels 推出去
（Telegram / 飞书 / 只打印），同时在电脑上落一份带完整链接的清单 md。

给任务计划调用，不依赖 Claude 开着。

用法：
    python push.py --dry     只打印，不发
    python push.py           真推
"""
import os
import sys
import argparse
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import jdlib  # noqa: E402
import pool  # noqa: E402
import jd_flags  # noqa: E402
import refresh  # noqa: E402
from jdlib import log  # noqa: E402

WD = "一二三四五六日"


def write_state(data):
    """只更新这几个键 —— state.json 里还有已投/屏蔽等记录，别整个覆盖。"""
    st = jdlib.load_state()
    st.update(data)
    jdlib.save_state(st)


# ---------------------------------------------------------------- 排版

def day_label(now):
    return "%s 周%s" % (now.strftime("%m-%d"), WD[now.weekday()])


def left_label(r):
    if r["_d"] is None:
        return "招满即止"
    n = r["_left"]
    if n == 0:
        return "今天截止"
    if n == 1:
        return "明天截止"
    return "%s · 剩 %d 天" % (r["_d"].strftime("%m-%d"), n)


def job_line(r, tag=""):
    t = (r.get("type") or "").strip()
    tag = ("· %s " % t) if t else ""
    warn = "".join("  · " + x if x == "粤语加分" else "  ⚠️" + x for x in jd_flags.labels(r.get("id")))
    return "• [%s] %s %s%s\n    %s%s" % (
        left_label(r), r.get("company", "?"), tag,
        pool.short_title(r.get("title")), (r.get("city") or "")[:28], warn)


def region_block(L, flag, label, part):
    """一个地区的「今日建议投」小段。"""
    if not part:
        return
    L.append("━━━ %s %s · 今日推 %d 家 ━━━" % (flag, label, len(part["today"])))
    if part["today"]:
        for i, r in enumerate(part["today"], 1):
            L.append(job_line(r).replace("• ", "%d. " % i, 1))
    else:
        L.append("（这边没有可投的了 —— 该刷新岗位池了）")
    L.append("")


def build_message(d, cfg, now, dry, warn=None):
    st = d["stats"]
    n_hk = len(d["hk"]["today"]) if d.get("hk") else 0
    n_cn = len(d["cn"]["today"]) if d.get("cn") else 0
    L = []
    L.append("💼 投递简报 · %s" % day_label(now))
    L.append("🇭🇰 香港 %d 家 + 🇨🇳 大陆 %d 家 · 池子可用 %d" % (n_hk, n_cn, st["alive"]))
    L.append("")

    region_block(L, "🇭🇰", "香港", d.get("hk"))
    region_block(L, "🇨🇳", "大陆", d.get("cn"))

    # 截止雷达：名额之外、又快截止的，也提一句（否则 11 条火烧眉毛只看到 5 条）
    seen = set()
    for key in ("cn", "hk"):
        part = d.get(key)
        if part:
            seen.update(id(r) for r in part["today"])
    over = [r for key in ("cn", "hk") if d.get(key)
            for r in d[key]["urgent"] if id(r) not in seen]
    if over:
        over.sort(key=lambda r: (r["_left"], -r["_score"]))
        L.append("━━━ ⏰ 另有 %d 条 %d 天内截止 ━━━" % (len(over), cfg.get("urgent_days", 3)))
        for r in over[:6]:
            L.append(job_line(r))
        if len(over) > 6:
            L.append("    …另有 %d 条（见清单 md）" % (len(over) - 6))
        L.append("")

    # 今天新出现的：关注公司新挂出来的（w-）+ 学校邮件里新导入的（s-），不管排没排进今日名单都提一句
    fresh = [r for key in ("hk", "cn") if d.get(key) for r in d[key]["alive_rows"]
             if r.get("first_seen") == now.date().isoformat() and str(r.get("id", "")).startswith(("w-", "s-"))]
    if fresh:
        L.append("━━━ 🆕 新出现 %d 个岗位（关注公司 / 学校邮件）━━━" % len(fresh))
        for r in fresh[:6]:
            L.append(job_line(r))
        if len(fresh) > 6:
            L.append("    …另有 %d 个（见清单 md）" % (len(fresh) - 6))
        L.append("")

    nod = sorted([r for key in ("hk", "cn") if d.get(key) for r in d[key].get("nodir") or []], key=lambda r: r["_d"])
    if nod:
        L.append("━━━ 🔍 方向没对上、%d 天内截止的 %d 个 ━━━" % (int(cfg.get("lookahead_days", 7)), len(nod)))
        for r in nod[:5]:
            L.append(job_line(r))
        if len(nod) > 5:
            L.append("    …另有 %d 个（见清单 md）" % (len(nod) - 5))
        L.append("    想投就看清单里的链接；常在这里出现的类型，可以在面板「设置」里把方向加上")
        L.append("")

    rem = active_reminders(cfg, now)
    if rem:
        L.append("━━━ 📌 手写提醒 ━━━")
        for _, dd, r in rem:
            L.append("• [%s] %s" % (dd.strftime("%m-%d"), r.get("text", "")))
        L.append("")

    if warn:
        L.append("⚠️ 数据源刷新失败（用的是上次的数据）：%s" % "；".join(warn[:3]))
    L.append("📎 完整链接：%s" % cfg.get("out_md", ""))

    for flag, path in (("🇭🇰", cfg.get("pool_path_hk")),
                       ("🇨🇳", cfg.get("pool_path"))):
        snap = pool.mtime_of(path)
        if not snap:
            continue
        age = (now.date() - snap).days
        if age >= 5:
            L.append("⚠️ %s 池子快照 %s（已 %d 天没刷新，新岗不会自己进来）"
                     % (flag, snap.strftime("%m-%d"), age))
        else:
            L.append("%s 池子快照 %s" % (flag, snap.strftime("%m-%d")))

    return "\n".join(L).strip()          # 超长截断在 jdlib.notify 里统一做


def active_reminders(cfg, now):
    out = []
    for r in cfg.get("reminders") or []:
        d = pool.parse_date(str(r.get("date") or ""))
        if d is None:
            log("! config.json 里这条提醒的日期看不懂，跳过：%r" % r.get("date"))
            continue
        delta = (d - now.date()).days
        if 0 <= delta <= int(r.get("lead_days", 3)):
            out.append((delta, d, r))
    out.sort(key=lambda x: x[0])
    return out


def md_job(r, i=None):
    head = ("%d. " % i) if i else "- "
    dt = r.get("deadline_text") or r.get("deadline") or "-"
    when = ("招满即止（发布 %s）" % dt) if r["_d"] is None else ("截止 %s" % dt)
    out = ["%s**[%s] %s · %s**" % (head, left_label(r), r.get("company"), r.get("title")),
           "    - %s ｜ %s ｜ 匹配分 %g ｜ id=%s" % (
               r.get("city"), when, r.get("_score", 0), r.get("id")),
           "    - %s" % r.get("apply_url")]
    lab = jd_flags.labels(r.get("id"))
    if lab:
        out.insert(2, "    - JD 里写着：%s" % "、".join(lab))
    return out


def write_md(d, cfg, now):
    path = cfg.get("out_md")
    if not path:
        return
    L = ["# 今日投递清单 · %s" % now.strftime("%Y-%m-%d"),
         "",
         "> 由 job-digest 自动生成。投完在面板点「已投」（或往 state.json 的 `applied_ids` 加 id），简报就不再推。",
         "> 香港池快照：%s ｜ 大陆池快照：%s" % (
             pool.mtime_of(cfg.get("pool_path_hk")) or "?",
             pool.pool_mtime(cfg) or "?"),
         ""]

    for key, flag, label in (("hk", "🇭🇰", "香港"), ("cn", "🇨🇳", "大陆")):
        part = d.get(key)
        if not part:
            continue
        L.append("## %s %s · 今日推 %d 家" % (flag, label, len(part["today"])))
        L.append("")
        for i, r in enumerate(part["today"], 1):
            L.extend(md_job(r, i))
        L.append("")
        if part["urgent"]:
            L.append("### 🔥 %d 天内截止（%d）" % (cfg.get("urgent_days", 3), len(part["urgent"])))
            L.append("")
            for r in part["urgent"]:
                L.extend(md_job(r))
            L.append("")

    nod = sorted([r for key in ("hk", "cn") if d.get(key) for r in d[key].get("nodir") or []], key=lambda r: r["_d"])
    if nod:
        L.append("## 🔍 方向没对上、但快截止的（%d）" % len(nod))
        L.append("")
        L.append("> 不在你选的方向里，所以没排进今日名单；想投就点链接。")
        L.append("")
        for r in nod:
            L.extend(md_job(r))
        L.append("")

    st = d["stats"]
    L.append("---")
    L.append("池子 %d 条 → 可用 %d 条（丢：%s）" % (
        st["total"], st["alive"],
        "、".join("%s %d" % (k, v) for k, v in st["drop"].items() if v)))
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    return path


def push_text(msg, cfg, dry):
    h = datetime.datetime.now().hour
    return jdlib.notify(msg, cfg, silent=(h >= 19 or h < 9), dry=dry)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="只打印不发")
    ap.add_argument("--no-refresh", action="store_true", help="不刷新数据源，直接用现有池子")
    args = ap.parse_args()

    now = datetime.datetime.now()
    cfg = pool.load_config()
    log("=== 开始：%s ===" % now.strftime("%Y-%m-%d %H:%M"))

    warn = []
    if not args.no_refresh:
        warn = refresh.refresh(cfg)[1]

    try:
        d = pool.select(cfg, now.date())
    except Exception as e:
        # 「读不到」≠「没有可投的」：要推告警，别让任务计划天天显示成功
        log("✗ 读岗位池失败：%s" % e)
        try:
            push_text("⚠️ 投递简报拉不到岗位池 · %s\n%s\n\n池子文件：%s%s"
                      % (now.strftime("%m-%d %H:%M"), e, cfg.get("pool_path"),
                         ("\n刷新失败：" + "；".join(warn)) if warn else ""), cfg, args.dry)
            return 0
        except Exception as e2:
            log("告警也推不出去：%s" % e2)
            return 2

    st = d["stats"]
    n_hk = len(d["hk"]["today"]) if d.get("hk") else 0
    n_cn = len(d["cn"]["today"]) if d.get("cn") else 0
    log("池子 %d → 可用 %d（急 %d / 近 %d / 滚动 %d）｜ 今日推 港 %d + 陆 %d"
        % (st["total"], st["alive"], st["urgent"], st["soon"], st["rolling"], n_hk, n_cn))

    if not args.dry:
        try:
            write_md(d, cfg, now)
            log("✓ 已写 %s" % cfg.get("out_md"))
        except Exception as e:
            log("! 写 md 失败（不影响推送）：%s" % e)

    msg = build_message(d, cfg, now, args.dry, warn)
    try:
        push_text(msg, cfg, args.dry)
    except Exception as e:
        log("推送失败：%s %s" % (type(e).__name__, e))
        return 2

    if args.dry:
        return 0

    write_state({"last_push_local": now.strftime("%Y-%m-%d %H:%M"),
                 "last_count": n_hk + n_cn})
    log("✓ 已推送")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
