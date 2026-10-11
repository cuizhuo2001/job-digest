#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
job-digest · 大陆岗位库导入

从开源项目 job-autopilot（github.com/keii-2596/job-autopilot，MIT）公开的
2027 届秋招岗位快照（data/jobs.json，数据源：白瓜求职）下载，转成岗位池 CSV。
每条自动打上方向标签（presets/商科.json）。

⚠️ 上游由维护者手动同步，不是每天更新；一条记录常是一家公司的整份校招公告，
   apply_url 多是公司校招首页，投之前还要在官网里找具体岗位。

    python tools/import_cn.py            下载并转换（写到 config.sources.cn_import.out）
    python tools/import_cn.py --file x   用本地的 jobs.json
"""
import os
import re
import sys
import json
import hashlib
import argparse
import datetime
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import jdlib  # noqa: E402
import pool  # noqa: E402

DEFAULT_URL = "https://raw.githubusercontent.com/keii-2596/job-autopilot/main/data/jobs.json"
DEFAULT_OUT = "data/cn_import.csv"
DATE_RE = re.compile(r"(20\d\d)[-./年](\d{1,2})[-./月](\d{1,2})")


def opts(cfg):
    o = dict((cfg.get("sources") or {}).get("cn_import") or {})
    o.setdefault("url", DEFAULT_URL)
    o.setdefault("out", DEFAULT_OUT)
    o.setdefault("graduates", ["2027届"])
    return o


def parse_deadline(text):
    t = (text or "").strip()
    m = DATE_RE.search(t)
    if m:
        try:
            return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat(), "fixed"
        except ValueError:
            pass
    if "尽快" in t:
        return "", "asap"
    if "招满" in t or "长期" in t:
        return "", "until_full"
    return "", "unknown"


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "job-digest"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


def convert(jobs, cfg, today=None):
    today = today or datetime.date.today()
    o = opts(cfg)
    grads = set(o["graduates"] or [])
    presets = pool.all_presets(cfg)
    rows = []
    for j in jobs:
        tg = set(j.get("target_graduates") or [])
        if grads and tg and not (tg & grads):
            continue
        title = re.sub(r"\s+", " ", j.get("title") or "").strip()
        if not title or not j.get("company_name"):
            continue
        deadline, kind = parse_deadline(j.get("deadline_text"))
        first = (j.get("first_seen_at") or "")[:10]
        try:
            age = (today - datetime.date.fromisoformat(first)).days
        except ValueError:
            age = 60
        r = {
            "id": "ja-" + hashlib.sha1(j["source_key"].encode("utf-8")).hexdigest()[:10],
            "company": j["company_name"].strip(),
            "title": title,
            "city": " / ".join(j.get("locations") or []),
            "deadline": deadline,
            "deadline_kind": kind,
            "deadline_text": j.get("deadline_text") or "",
            "apply_url": j.get("apply_url") or j.get("announcement_url") or "",
            "type": j.get("company_type") or "",
            "industry": (j.get("industry") or "").strip(" ,"),
            # 没有个人匹配分：滚动招聘里越新发布的越靠前（50–80）
            "score": 50 + max(0, 30 - age),
            "first_seen": first,
            "reasons": "；".join(x for x in (
                "批次：" + "/".join(j.get("recruitment_batches") or []) if j.get("recruitment_batches") else "",
                "届别：" + "/".join(j.get("target_graduates") or []) if j.get("target_graduates") else "",
                "公告：" + j["announcement_url"] if j.get("announcement_url") else "") if x),
        }
        r["tags"] = pool.auto_tags(r, presets)
        rows.append(r)
    return rows


def run(cfg, local_file=None):
    o = opts(cfg)
    jobs = json.load(open(local_file, encoding="utf-8")) if local_file else fetch(o["url"])
    rows = convert(jobs, cfg)
    out = jdlib.resolve(o["out"])
    pool.write_pool(out, rows)
    # 上游数据本身的日期（最新一条的 last_seen）：简报按它算「池子几天没更新」，而不是按下载时间
    newest = max(((j.get("last_seen_at") or j.get("first_seen_at") or "")[:10] for j in jobs), default="")
    if newest:
        with open(out + ".date", "w", encoding="utf-8") as f:
            f.write(newest)
    return "大陆岗位库 %d 条 → %d 条（%s）" % (len(jobs), len(rows), os.path.relpath(out, jdlib.ROOT)), []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", help="用本地 jobs.json，不下载")
    args = ap.parse_args()
    print(run(pool.load_config(), args.file)[0])


if __name__ == "__main__":
    sys.exit(main() or 0)
