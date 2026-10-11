#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
job-digest · 链接体检

逐条 HTTP 探测岗位池里的 apply_url，把**确定死掉**的 id 写进 state.json 的
`dead_ids`，简报和弹窗就不再推它（跟 applied_ids 一个机制，可撤销）。

    python check_links.py                 # 只查今天要推的那 10 条（快）
    python check_links.py --all           # 查所有筛完还活着的（慢，几百条）
    python check_links.py --dry           # 只看不写
    python check_links.py --unmark <id>   # 把某条从死链名单里放出来

⚠️ 判定故意保守：**只有 404 / 410 才算死**。
   403/429/超时/TLS 握手失败一律不算 —— 政府站、需登录的 ATS（工银瑞信那种
   老 TLS）经常这样，误杀的代价比漏杀大得多。
⚠️ 它查不出「链接只到校招首页、不到具体岗位」—— 首页也是 200。录池子时就该填岗位详情页。
⚠️ LinkedIn 的 /job-apply/<id> 未登录时也返回 404，但那是链接形式的问题、
   不是岗位死了 —— 录池子时改写成 /jobs/view/<id>。
"""
import os
import sys
import ssl
import time
import argparse
import urllib.request
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import jdlib  # noqa: E402
import pool  # noqa: E402

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
DEAD_CODES = (404, 410)


def _ctx():
    # 政府站/老站点证书五花八门，这里只关心「这个页面还在不在」，不管证书链
    c = ssl.create_default_context()
    c.check_hostname = False
    c.verify_mode = ssl.CERT_NONE
    return c


def probe(url, timeout=20):
    """→ HTTP 状态码（int），或 'ERR' / 'ERR:xxx'（不确定，不当死链）。"""
    if not url:
        return "NOURL"
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        })
        with urllib.request.urlopen(req, timeout=timeout, context=_ctx()) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception as e:
        return "ERR:%s" % type(e).__name__


def candidates(cfg, all_rows=False):
    """默认只查今天要推的那些；--all 查全部筛完还活着的。"""
    d = pool.select(cfg)
    rows = []
    for key in ("hk", "cn"):
        p = d.get(key)
        if not p:
            continue
        rows += (p.get("alive_rows") or []) if all_rows else p["today"]
    out, seen = [], set()
    for r in rows:
        rid = str(r.get("id"))
        if rid in seen:
            continue
        seen.add(rid)
        out.append(r)
    return out


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="查所有活着的岗位（慢）")
    ap.add_argument("--dry", action="store_true", help="只打印，不写 state.json")
    ap.add_argument("--unmark", nargs="+", metavar="ID", help="从死链名单里移除")
    ap.add_argument("--delay", type=float, default=0.4, help="每条之间的间隔秒数")
    args = ap.parse_args()

    cfg = pool.load_config()

    if args.unmark:
        st = jdlib.load_state()
        cur = [str(x) for x in st["dead_ids"]]
        before = len(cur)
        cur = [x for x in cur if x not in [str(u) for u in args.unmark]]
        st["dead_ids"] = cur
        jdlib.save_state(st)
        print("已放回 %d 条（%d → %d）" % (before - len(cur), before, len(cur)))
        return 0

    rows = candidates(cfg, args.all)
    print("体检 %d 条链接%s\n" % (len(rows), "（全量）" if args.all else "（今日名单）"))

    dead, unsure = [], []
    for r in rows:
        url = (r.get("apply_url") or "").strip()
        code = probe(url)
        tag = ""
        if code in DEAD_CODES:
            dead.append(str(r["id"]))
            tag = "  ← 死链，剔"
        elif not isinstance(code, int) or code >= 400:
            unsure.append(str(r["id"]))
            tag = "  （不确定，保留）"
        print("  %-4s %-6s %-22s %-46s %s%s" % (
            str(r["id"]), code, (r.get("company") or "")[:22],
            url[:46], (r.get("type") or ""), tag))
        time.sleep(args.delay)

    print("\n确定死链 %d 条：%s" % (len(dead), ", ".join(dead) or "无"))
    print("不确定 %d 条（403/超时/TLS，保留）：%s" % (len(unsure), ", ".join(unsure) or "无"))

    if dead and not args.dry:
        st = jdlib.load_state()
        cur = [str(x) for x in st["dead_ids"]]
        added = [i for i in dead if i not in cur]
        st["dead_ids"] = cur + added
        jdlib.save_state(st)
        print("\n✓ 已写进 state.json 的 dead_ids（+%d）：%s" % (len(added), ", ".join(added)))
        print("  撤销：python check_links.py --unmark %s" % " ".join(added))
    elif dead:
        print("\n（--dry，没写）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
