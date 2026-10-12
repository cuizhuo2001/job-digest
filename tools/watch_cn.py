#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
job-digest · 大陆「关注公司」自动查岗

按 watchlist_cn.json 里的公司清单，直接查它们官网招聘系统的公开接口，拿校园招聘岗位：
工作地在 config 的 allowed_cities（或 sources.cn_watch.cities）里的进大陆池（data/cn_watch.csv），
在香港的进香港池（data/cn_watch_hk.csv）。每条都是岗位详情页直链（有的官网要先登录，登录后会跳回岗位）。

支持的招聘系统（香港那份 watch_hk.py 里的三种也能用）：
    hotjob    Hotjob / wecruit.hotjob.cn（广发、招商、华泰……）
    citics    中信证券自建官网 careers.citics.com
    beisen    北森 *.zhiye.com（中金……）
    workday / oracle   同 watch_hk.py

    python tools/watch_cn.py              查一遍并写文件
    python tools/watch_cn.py --dry        只打印查到什么
    python tools/watch_cn.py --only 招商  只查名字里带「招商」的公司
"""
import os
import re
import sys
import json
import time
import hashlib
import argparse
import datetime
import urllib.parse
import urllib.request
import concurrent.futures as cf

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import jdlib  # noqa: E402
import pool  # noqa: E402
import watch_hk  # noqa: E402
from watch_hk import INTERN, is_hk  # noqa: E402

DEFAULT_OUT = "data/cn_watch.csv"
DEFAULT_OUT_HK = "data/cn_watch_hk.csv"
DEFAULT_CITIES = ["北京", "上海", "广州", "深圳"]
SPLIT = re.compile(r"[、,，/；;\s]+|等$")


def opts(cfg):
    o = dict((cfg.get("sources") or {}).get("cn_watch") or {})
    o.setdefault("watchlist", "watchlist_cn.json")
    o.setdefault("out", DEFAULT_OUT)
    o.setdefault("out_hk", DEFAULT_OUT_HK)
    # 默认跟 allowed_cities 一样（香港岗本来就单独进香港池）
    o.setdefault("cities", [x for x in cfg.get("allowed_cities") or [] if x != "香港"] or DEFAULT_CITIES)
    return o


def http_form(url, form, headers=None, timeout=40, tries=3):
    """表单 POST → JSON。招聘官网偶尔掐断连接（SSL EOF），歇两秒重试。"""
    for i in range(tries):
        try:
            return _http_form(url, form, headers, timeout)
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(2 + 2 * i)


def _http_form(url, form, headers=None, timeout=40):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(form).encode("utf-8"), headers=dict({
        "User-Agent": "Mozilla/5.0 job-digest", "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"}, **(headers or {})))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _any(*locs):
    return True


def _clean(title):
    """「2027校园招聘——投资岗（招证投资）」→「投资岗（招证投资）」"""
    title = " ".join((title or "").split())
    return re.sub(r"^\s*20\d\d\s*(届|年)?\s*(秋季|春季)?(校园招聘|校招|秋招|春招)\s*[—\-–:：]+\s*", "", title).strip()


# ───────────────────────── 两种新系统 ─────────────────────────

def hotjob(c, keywords, want=_any):
    """Hotjob 官网（wecruit.hotjob.cn/<suite>/pb/school.html）的校招列表。"""
    su = c["suite"]
    url = "https://wecruit.hotjob.cn/wecruit/positionInfo/listPosition/%s?iSaJAx=isAjax&request_locale=zh_CN" % su
    rows, page = [], 1
    while True:
        d = http_form(url, {"pageSize": 100, "currentPage": page, "recruitType": c.get("recruit_type", 1)})
        pf = (d.get("data") or {}).get("pageForm") or {}
        data = pf.get("pageData") or []
        rows += data
        if not data or page >= int(pf.get("totalPage") or 1) or page >= 20:
            break
        page += 1
    today = datetime.date.today().isoformat()
    out = []
    for r in rows:
        locs = [x for x in SPLIT.split(r.get("workPlaceStr") or "") if x]
        end = (r.get("endDate") or "")[:10]
        if end > (datetime.date.today() + datetime.timedelta(days=300)).isoformat():
            end = ""                        # 3000-01-01、2030-08-01 这种是「长期有效」的占位
        if (end and end < today) or not want(*locs):
            continue
        title = _clean(r.get("postName"))
        org = (r.get("company") or "").strip()
        if org and org not in title and not org.endswith("总部"):
            title = "%s（%s）" % (title, org)
        typ = "实习" if INTERN.search(title) or "实习" in (r.get("projectName") or "") else "校招"
        out.append({"key": r["postId"], "title": title, "type": typ, "posted": (r.get("publishDate") or "")[:10],
                    "deadline": end, "locs": locs,
                    "url": "https://wecruit.hotjob.cn/%s/pb/posDetail.html?postId=%s&postType=campus" % (su, r["postId"])})
    return out


CITICS_API = "https://global-kong.citics.com/api/v1/recruit/getPositionList"
CITICS_HDR = {"Origin": "https://careers.citics.com", "Referer": "https://careers.citics.com/"}


def citics(c, keywords, want=_any):
    """中信证券官网：总部校招 + 分支机构校招（recruitType 08）。"""
    out = []
    for deptype, page, route in (("Headquarter", "headquarters", "positonDetailHeadquarters"),
                                 ("Branch", "branch", "positonDetail")):
        form = {"sysNo": "CSE001", "recruitType": "08", "deptype": deptype, "pageNum": 1, "pageSize": 300}
        if deptype == "Headquarter":
            form["practice"] = "0"                  # 0 = 正式校招；1 = 实习
        d = http_form(CITICS_API, form, CITICS_HDR)
        if d.get("errorCode") not in (0, "0", None):
            raise RuntimeError(d.get("errorMsg") or d.get("errorCode"))
        for p in d.get("positionList") or []:
            locs = [x for x in SPLIT.split(p.get("workplace") or "") if x]
            if not want(*locs):
                continue
            dept, name = (p.get("deptName") or "").strip(), (p.get("positionName") or "").strip()
            title = name if not dept or dept == name else "%s · %s" % (dept, name)
            out.append({"key": "%s-%s" % (p["deptNo"], p["positionNo"]), "title": title, "type": "校招",
                        "posted": "", "deadline": "", "locs": locs,
                        "url": "https://careers.citics.com/%s?deptNo=%s&positionNo=%s&pageName=%s&resumeType=0"
                               % (route, p["deptNo"], p["positionNo"], page)})
    return out


SYSTEMS = dict(watch_hk.SYSTEMS, hotjob=hotjob, citics=citics)


# ───────────────────────── 汇总 ─────────────────────────

def run(cfg, only=None, dry=False):
    o = opts(cfg)
    wl = jdlib.load_json(jdlib.resolve(o["watchlist"]), {})
    companies = [c for c in wl.get("companies") or [] if not only or only in c["name"]]
    cities = list(o["cities"])
    out_cn, out_hk = jdlib.resolve(o["out"]), jdlib.resolve(o["out_hk"])
    old = {}
    for p in (out_cn, out_hk):
        if os.path.exists(p):
            old.update({r["id"]: r for r in pool.load_pool(p)})
    first_run = not old
    today = datetime.date.today().isoformat()
    presets = pool.all_presets(cfg)

    def where(locs):
        """→ ('hk', '香港') / ('cn', '深圳/上海') / (None, '')：香港优先，大陆只留关注的城市。"""
        if is_hk(*locs):
            return "hk", "香港"
        hit = [x for x in cities if any(x in (l or "") for l in locs)]
        return ("cn", "/".join(hit)) if hit else (None, "")

    def one(c):
        fn = SYSTEMS.get(c.get("system"))
        if not fn:
            raise RuntimeError("不认识的招聘系统 %r" % c.get("system"))
        return fn(c, [], want=lambda *locs: where(locs)[0] is not None)

    rows = {"cn": [], "hk": []}
    fails = []
    with cf.ThreadPoolExecutor(6) as ex:
        futs = {ex.submit(one, c): c for c in companies}
        for f in cf.as_completed(futs):
            c = futs[f]
            try:
                jobs = f.result()
            except Exception as e:
                fails.append("%s（%s）" % (c["name"], str(e)[:60]))
                continue
            for j in jobs:
                region, city = where(j.get("locs") or [])
                if not region:
                    continue
                rid = "w-" + hashlib.sha1((c["name"] + "|" + str(j["key"])).encode("utf-8")).hexdigest()[:10]
                r = {"id": rid, "company": c["name"], "title": j["title"], "city": city,
                     "deadline": j.get("deadline", ""), "deadline_kind": "fixed" if j.get("deadline") else "until_full",
                     "deadline_text": j.get("deadline") or "官网未列截止日", "apply_url": j["url"],
                     "type": j["type"], "industry": c.get("industry", ""), "score": 60,
                     # 见过的保留原来的日期；第一次跑只建基线，不把全部岗位都标成「新」
                     "first_seen": old[rid].get("first_seen", "") if rid in old else ("" if first_run else today),
                     "reasons": "自动查岗（%s）；工作地 %s；发布 %s" % (
                         c["system"], "、".join(j.get("locs") or []) or "?", j.get("posted") or "?")}
                r["tags"] = pool.auto_tags(r, presets)
                rows[region].append(r)
    for k in rows:
        rows[k].sort(key=lambda r: (r["company"], r["title"]))
    allrows = rows["cn"] + rows["hk"]
    new = [r for r in allrows if not first_run and r["id"] not in old]
    if dry:
        for r in allrows:
            print("  %s %-4s %-4s %-10s %-14s %s" % ("🆕" if r in new else "  ", r["type"], r["city"][:8],
                                                  r["deadline"] or "-", r["company"][:14], r["title"][:60]))
    else:
        pool.write_pool(out_cn, rows["cn"])
        pool.write_pool(out_hk, rows["hk"])
    msg = "大陆关注公司 %d/%d 家查到 %d 个校招岗（大陆 %d · 香港 %d，新 %d）" % (
        len(companies) - len(fails), len(companies), len(allrows), len(rows["cn"]), len(rows["hk"]), len(new))
    return msg, ["大陆关注公司·" + f for f in fails]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="只打印，不写文件")
    ap.add_argument("--only", help="只查名字里含这个词的公司")
    args = ap.parse_args()
    msg, fails = run(pool.load_config(), args.only, args.dry)
    print(msg)
    for f in fails:
        print("  ✗", f)


if __name__ == "__main__":
    sys.exit(main() or 0)
