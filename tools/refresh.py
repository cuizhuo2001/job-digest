#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
job-digest · 刷新岗位池

按 config.json 的 sources 跑数据源：
    cn_import   大陆岗位库导入（import_cn.py）
    hk_watch    香港关注公司查岗（watch_hk.py）
每个源的输出文件超过 refresh_hours 小时才重跑。push.py 每次推送前会自动调用；
某个源失败只记日志、在简报里提一句，不影响用旧数据照常推送。

    python tools/refresh.py            到期的才刷
    python tools/refresh.py --force    全部重刷
"""
import os
import sys
import time
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import jdlib  # noqa: E402
import pool  # noqa: E402
import import_cn  # noqa: E402
import watch_hk  # noqa: E402
import watch_cn  # noqa: E402
import import_mail  # noqa: E402

SOURCES = (("cn_import", "大陆岗位库", import_cn), ("hk_watch", "香港关注公司", watch_hk),
           ("cn_watch", "大陆关注公司", watch_cn), ("mail_import", "学校邮件", import_mail))


def due(path, hours):
    return not os.path.exists(path) or time.time() - os.path.getmtime(path) > hours * 3600


def refresh(cfg, force=False):
    """→ (成功说明列表, 失败说明列表)"""
    src = cfg.get("sources") or {}
    hours = float(src.get("refresh_hours", 20))
    notes, errors = [], []
    for key, label, mod in SOURCES:
        o = src.get(key) or {}
        if not o.get("enabled"):
            continue
        out = jdlib.resolve(o.get("out") or mod.DEFAULT_OUT)
        if not force and not due(out, hours):
            continue
        try:
            msg, problems = mod.run(cfg)          # problems = 部分失败（比如某家公司没查到）
            jdlib.log("  ↻ " + msg)
            for p in problems:
                jdlib.log("    ✗ " + p)
            notes.append(msg)
            errors += problems
        except Exception as e:
            jdlib.log("  ✗ %s刷新失败：%s: %s" % (label, type(e).__name__, e))
            errors.append("%s（%s）" % (label, str(e)[:80]))
    return notes, errors


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="不管多久前刷过，全部重刷")
    args = ap.parse_args()
    notes, errors = refresh(pool.load_config(), args.force)
    if not notes and not errors:
        print("都还新鲜（没超过 refresh_hours），没刷。要强制刷加 --force")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
