#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
job-digest · 手动补录岗位

在官网、群里、就业网看到的岗位，顺手加进池子（香港写进 data/manual_hk.csv，大陆写进 data/manual_cn.csv）。
方向标签不填就按标题自动打。

    python tools/add_job.py                                交互式：一项项问
    python tools/add_job.py --hk --company "示例银行" --title "Graduate Programme 2027" \\
        --url https://... --deadline 2026-11-30            一行加完
    python tools/add_job.py --list                         看手动加过的
    python tools/add_job.py --remove m-hk-20261011-1       删掉一条
"""
import os
import sys
import argparse
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import jdlib  # noqa: E402
import pool  # noqa: E402

FILES = {"hk": "data/manual_hk.csv", "cn": "data/manual_cn.csv"}


def path_of(key):
    return jdlib.resolve(FILES[key])


def load(key):
    p = path_of(key)
    return pool.load_pool(p) if os.path.exists(p) else []


def ask(label, default=""):
    v = input("%s%s：" % (label, "（回车 = %s）" % default if default else "")).strip()
    return v or default


def check_config(key):
    """提醒：手动文件要列在 config.json 的 pool_path(_hk) 里才会被读到。"""
    cfg = pool.load_config()
    k = "pool_path_hk" if key == "hk" else "pool_path"
    if os.path.normcase(path_of(key)) not in [os.path.normcase(x) for x in cfg.get(k) or []]:
        print("⚠️ config.json 的 %s 里还没有 %s，加上才会进简报" % (k, FILES[key]))


def add(key, a):
    if not a.company:
        a.company = ask("公司")
        a.title = ask("岗位名")
        a.url = ask("申请链接（尽量填岗位详情页）")
        a.city = ask("城市", "香港" if key == "hk" else "")
        a.deadline = ask("截止日 YYYY-MM-DD（滚动招聘就回车）")
        a.tags = ask("方向标签，; 分隔（回车 = 按标题自动打）")
        a.note = ask("备注（投递方式、要求…）")
    if not (a.company and a.title):
        raise SystemExit("公司和岗位名必须填")
    d = pool.parse_date(a.deadline) if a.deadline else None
    if a.deadline and d is None:
        raise SystemExit("截止日看不懂：%s（要 YYYY-MM-DD）" % a.deadline)
    rows = load(key)
    today = datetime.date.today()
    # id 带上池子：香港 m-hk-…、大陆 m-cn-…，已投记录按 id 记，两边不能撞号
    prefix = "m-%s-%s-" % (key, today.strftime("%Y%m%d"))
    n = 1 + max([int(r["id"].rsplit("-", 1)[1]) for r in rows if r["id"].startswith(prefix)] or [0])
    r = {"id": prefix + str(n), "company": a.company, "title": a.title,
         "city": a.city or ("香港" if key == "hk" else ""), "deadline": d.isoformat() if d else "",
         "deadline_kind": "fixed" if d else "until_full", "deadline_text": d.isoformat() if d else "招满即止",
         "apply_url": a.url or "", "type": a.type or "校招", "industry": a.industry or "",
         "score": a.score, "first_seen": today.isoformat(), "reasons": a.note or "手动补录"}
    r["tags"] = a.tags or pool.auto_tags(r, pool.all_presets(pool.load_config()))
    pool.write_pool(path_of(key), rows + [r])
    print("✓ 已加进 %s：%s · %s（id=%s，方向：%s）" % (FILES[key], r["company"], r["title"], r["id"], r["tags"] or "无"))
    check_config(key)


def main():
    ap = argparse.ArgumentParser(description="手动补录岗位")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--hk", action="store_true", help="香港岗（默认）")
    g.add_argument("--cn", action="store_true", help="大陆岗")
    ap.add_argument("--company")
    ap.add_argument("--title")
    ap.add_argument("--url")
    ap.add_argument("--city")
    ap.add_argument("--deadline", help="YYYY-MM-DD，不填 = 滚动招聘")
    ap.add_argument("--tags", help="方向标签，; 分隔，不填就自动打")
    ap.add_argument("--type", help="校招 / 实习 / 社招，默认校招")
    ap.add_argument("--industry")
    ap.add_argument("--note", help="备注")
    ap.add_argument("--score", type=float, default=90, help="匹配分，默认 90（自己挑的排前面）")
    ap.add_argument("--list", action="store_true", help="列出手动加过的")
    ap.add_argument("--remove", metavar="ID", help="删掉一条")
    a = ap.parse_args()

    if a.list:
        for key in ("hk", "cn"):
            for r in load(key):
                print("%-14s %s %-20s %-40s %s" % (r["id"], key, r["company"][:20], r["title"][:40], r["deadline"] or "滚动"))
        return 0
    if a.remove:
        for key in ("hk", "cn"):
            rows = load(key)
            keep = [r for r in rows if r["id"] != a.remove]
            if len(keep) < len(rows):
                pool.write_pool(path_of(key), keep)
                print("✓ 已删除 %s" % a.remove)
                return 0
        print("没找到 %s" % a.remove)
        return 1
    add("cn" if a.cn else "hk", a)
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
