#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
job-digest · 香港「关注公司」自动查岗

按 watchlist_hk.json 里的公司清单，直接查它们招聘系统的公开接口，
搜 graduate / trainee / 2027 这类校招岗，只留工作地在香港的，写进岗位池 CSV。
每条都是岗位详情页直链；第一次发现的日期记在 first_seen 列（简报会提示「新挂出来的」）。

支持的招聘系统：
    workday   *.myworkdayjobs.com
    oracle    *.oraclecloud.com（Oracle Recruiting Cloud / CandidateExperience）
    beisen    北森 *.zhiye.com
    sfsf      SAP SuccessFactors 招聘站（网址像 /job/<标题>/<数字>/，渣打这类）
    pageup    PageUp（careers.pageuppeople.com/<编号>/…，中银香港这类）
    hotjob / citics   写在 watch_cn.py 里，这边也能用（安永香港是 hotjob）

    python tools/watch_hk.py              查一遍并写文件
    python tools/watch_hk.py --dry        只打印查到什么
    python tools/watch_hk.py --only 花旗  只查名字里带「花旗」的公司（调试新加的公司用）
"""
import os
import re
import sys
import json
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

DEFAULT_OUT = "data/hk_watch.csv"
DEFAULT_KEYWORDS = ["Graduate", "Trainee", "2027", "Management Associate", "Analyst Programme", "Analyst Program",
                    "Campus"]
# ⚠️ intern 要整词：不然 Internal Audit、International Private Bank、China Internet 都会被当成实习
INTERN = re.compile(r"\binterns?(hip)?s?\b|\bsummer\b|\bplacement\b|\bco-?op\b|off-cycle|实习", re.I)
GRAD = re.compile(r"graduate|trainee|management associate|analyst program|2027|campus|fresh|new grad|"
                  r"entry[- ]level|校园|校招|应届|管培|培训生", re.I)
UA = {"User-Agent": "Mozilla/5.0 job-digest", "Content-Type": "application/json", "Accept": "application/json"}


def opts(cfg):
    o = dict((cfg.get("sources") or {}).get("hk_watch") or {})
    o.setdefault("watchlist", "watchlist_hk.json")
    o.setdefault("out", DEFAULT_OUT)
    o.setdefault("keywords", DEFAULT_KEYWORDS)
    return o


def http_json(url, body=None, timeout=40):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def is_hk(*locs):
    return any(re.search(r"hong kong|香港|kowloon|九龍|九龙|hksar", l or "", re.I) for l in locs)


def job_type(title):
    if INTERN.search(title):
        return "实习"
    if GRAD.search(title):
        return "校招"
    return ""                     # 社招：不收


# ───────────────────────── 三种招聘系统 ─────────────────────────

def workday(c, keywords, want=is_hk):
    base = "https://%s/wday/cxs/%s/%s" % (c["host"], c["tenant"], c["site"])

    def search(kw):
        got = []
        for offset in range(0, 100, 20):
            d = http_json(base + "/jobs", {"limit": 20, "offset": offset, "searchText": kw, "appliedFacets": {}})
            posts = d.get("jobPostings") or []
            got += posts
            if len(posts) < 20:
                break
        return got

    found = {}
    # 几个关键词一起查（一个接一个的话，大公司要 40~50 秒）
    with cf.ThreadPoolExecutor(4) as ex:
        for posts in ex.map(search, keywords):
            for p in posts:
                found.setdefault(p["externalPath"], p)
    cand = [(path, p) for path, p in found.items() if job_type(p.get("title") or "")]

    def locs_of(item):
        path, p = item
        loc = p.get("locationsText") or ""
        if re.match(r"\d+ Locations?$", loc):            # 多地点：要再查一次详情才知道有没有香港
            info = http_json(base + path).get("jobPostingInfo") or {}
            return [info.get("location")] + list(info.get("additionalLocations") or [])
        return [loc]

    with cf.ThreadPoolExecutor(4) as ex:
        all_locs = list(ex.map(locs_of, cand))
    out = []
    for (path, p), locs in zip(cand, all_locs):
        if not want(*locs):
            continue
        title = p.get("title") or ""
        out.append({"key": path, "title": title, "type": job_type(title), "posted": p.get("postedOn") or "", "locs": locs,
                    "url": "https://%s/%s%s" % (c["host"], c["site"], path)})
    return out


def oracle(c, keywords, want=is_hk):
    found = {}
    for kw in keywords:
        url = ("https://%s/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true"
               "&expand=requisitionList.secondaryLocations&finder=findReqs;siteNumber=%s,keyword=%s,limit=100"
               % (c["host"], c["site"], urllib.parse.quote(kw)))
        items = http_json(url).get("items") or [{}]
        for r in items[0].get("requisitionList") or []:
            found.setdefault(r["Id"], r)
    out = []
    for rid, r in found.items():
        title = r.get("Title") or ""
        typ = job_type(title)
        locs = [r.get("PrimaryLocation")] + [x.get("Name") for x in (r.get("secondaryLocations") or [])]
        if not typ or not want(*locs):
            continue
        out.append({"key": rid, "title": title, "type": typ, "posted": r.get("PostedDate") or "", "locs": locs,
                    "url": "https://%s/hcmUI/CandidateExperience/en/sites/%s/job/%s" % (c["host"], c["site"], rid)})
    return out


def beisen(c, keywords, want=is_hk):
    url = "https://%s/api/Jobad/GetJobAdPageList" % c["host"]
    detail = c.get("detail") or ("https://%s/campus/detail?jobAdId={id}" % c["host"])
    rows, page = [], 0
    while True:
        d = http_json(url, {"PageIndex": page, "PageSize": 100, "KeyWords": "", "SpecialType": 0,
                            "DisplayFields": ["Category", "Kind", "LocId", "ClassificationOne"]}, timeout=90)
        data = d.get("Data") or []
        rows += data
        page += 1
        if not data or len(rows) >= int(d.get("Count") or 0) or page >= 20:
            break
    out = []
    for r in rows:
        cat, title = r.get("Category") or "", " ".join((r.get("JobAdName") or "").split())
        # 「校园招聘/暑期实习」这种混在一起的类别：标题里没写实习的算校招
        if "校园" in cat or "校招" in cat:
            typ = "实习" if INTERN.search(title) else "校招"
        else:
            typ = "实习" if "实习" in cat else ""
        locs = r.get("LocNames") or []
        if not typ or not want(*locs):
            continue
        end = (r.get("EndTime") or "")[:10]
        out.append({"key": r["Id"], "title": title, "type": typ, "posted": (r.get("PostDate") or "")[:10], "locs": locs,
                    "deadline": "" if end.startswith("0001") else end, "url": detail.format(id=r["Id"])})
    return out


def sfsf(c, keywords, want=is_hk):
    """SAP SuccessFactors 招聘站的搜索接口（网页本身是前端渲染的，接口是公开的 JSON）。"""
    import html as _html
    host, locale = c["host"], c.get("locale", "en_GB")
    api = "https://%s/services/recruiting/v1/jobs" % host
    found = {}
    for kw in keywords:
        for page in range(10):
            d = http_json(api, {"locale": locale, "pageNumber": page, "sortBy": "", "keywords": kw,
                                "location": c.get("location", ""),
                                "facetFilters": {}, "brand": c.get("brand", ""), "skills": [], "categoryId": 0,
                                "alertId": "", "rcmCandidateId": ""})
            res = d.get("jobSearchResult") or []
            for j in res:
                r = j.get("response") or {}
                found.setdefault(str(r.get("id")), r)
            if len(res) < 10:
                break
    today = datetime.date.today()
    out = []
    for rid, r in found.items():
        title = _html.unescape(r.get("unifiedStandardTitle") or r.get("title") or "")
        typ = job_type(title)
        locs = [x.strip() for x in list(r.get("jobLocationShort") or []) + list(r.get("jobLocationCountry") or [])]
        if not typ or not want(*locs):
            continue
        end = ""
        m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", r.get("unifiedStandardEnd") or "")
        if m:
            try:
                dd = datetime.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))   # en_GB：日/月/年
                end = dd.isoformat() if dd >= today else ""
            except ValueError:
                pass
        slug = _html.unescape(r.get("unifiedUrlTitle") or r.get("urlTitle") or "job")
        out.append({"key": rid, "title": title, "type": typ, "posted": "", "deadline": end, "locs": locs,
                    "url": "https://%s/job/%s/%s-%s/" % (host, slug, rid, locale)})
    return out


def pageup(c, keywords, want=is_hk):
    """PageUp 招聘站：搜索结果是服务器直接出的网页。列表里没有地点和截止日，地点按 c["loc"]（默认香港）。"""
    import html as _html
    host = c.get("host", "careers.pageuppeople.com")
    base = "https://%s/%s/%s/%s" % (host, c["site"], c.get("channel", "cw"), c.get("lang", "en"))
    locs = [c.get("loc", "香港")]
    if not want(*locs):
        return []
    found = {}
    for kw in keywords:
        req = urllib.request.Request(base + "/search/?search-keyword=" + urllib.parse.quote(kw),
                                     headers={"User-Agent": UA["User-Agent"]})
        with urllib.request.urlopen(req, timeout=40) as r:
            s = r.read().decode("utf-8", "ignore")
        for h, t in re.findall(r'<a[^>]*class="job-link"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', s, re.S):
            m = re.search(r"/job/(\d+)/", h)
            if m:
                found.setdefault(m.group(1), (h, _html.unescape(re.sub("<.*?>", "", t)).strip()))
    out = []
    for jid, (h, title) in found.items():
        typ = job_type(title)
        if typ:
            out.append({"key": jid, "title": title, "type": typ, "posted": "", "deadline": "", "locs": locs,
                        "url": urllib.parse.urljoin("https://%s/" % host, h)})
    return out


SYSTEMS = {"workday": workday, "oracle": oracle, "beisen": beisen, "sfsf": sfsf, "pageup": pageup}


def systems():
    """hotjob / citics 写在 watch_cn.py（那边会 import 这个文件），用的时候再导，避免循环导入。"""
    import watch_cn
    return dict(SYSTEMS, hotjob=watch_cn.hotjob, citics=watch_cn.citics)


# ───────────────────────── 汇总 ─────────────────────────

def run(cfg, only=None, dry=False):
    o = opts(cfg)
    wl = jdlib.load_json(jdlib.resolve(o["watchlist"]), {})
    companies = [c for c in wl.get("companies") or [] if not only or only in c["name"]]
    out_path = jdlib.resolve(o["out"])
    old = {}
    if os.path.exists(out_path):
        old = {r["id"]: r for r in pool.load_pool(out_path)}
    today = datetime.date.today().isoformat()
    presets = pool.all_presets(cfg)

    sysmap = systems()

    def one(c):
        fn = sysmap.get(c.get("system"))
        if not fn:
            raise RuntimeError("不认识的招聘系统 %r" % c.get("system"))
        return fn(c, o["keywords"], want=is_hk)

    rows, fails = [], []
    with cf.ThreadPoolExecutor(8) as ex:
        futs = {ex.submit(one, c): c for c in companies}
        for f in cf.as_completed(futs):
            c = futs[f]
            try:
                jobs = f.result()
            except Exception as e:
                fails.append("%s（%s）" % (c["name"], str(e)[:60]))
                continue
            for j in jobs:
                rid = "w-" + hashlib.sha1((c["name"] + "|" + j["key"]).encode("utf-8")).hexdigest()[:10]
                r = {"id": rid, "company": c["name"], "title": j["title"], "city": "香港",
                     "deadline": j.get("deadline", ""), "deadline_kind": "fixed" if j.get("deadline") else "until_full",
                     "deadline_text": j.get("deadline") or "官网未列截止日", "apply_url": j["url"],
                     "type": j["type"], "industry": c.get("industry", ""), "score": 60,
                     # 见过的保留原来的日期（基线那批是空的，也要保留空）；
                     # 第一次跑（还没有旧文件）只建基线，不把全部岗位都标成「新」
                     "first_seen": old[rid].get("first_seen", "") if rid in old else (today if old else ""),
                     "reasons": "自动查岗（%s）；发布 %s" % (c["system"], j.get("posted") or "?")}
                r["tags"] = pool.auto_tags(r, presets)
                rows.append(r)
    rows.sort(key=lambda r: (r["company"], r["title"]))
    new = [r for r in rows if old and r["id"] not in old]
    if dry:
        for r in rows:
            print("  %s %-4s %-24s %s" % ("🆕" if r in new else "  ", r["type"], r["company"][:24], r["title"][:70]))
    else:
        pool.write_pool(out_path, rows)
    msg = "香港关注公司 %d/%d 家查到 %d 个香港校招/实习岗（新 %d）" % (
        len(companies) - len(fails), len(companies), len(rows), len(new))
    return msg, ["香港关注公司·" + f for f in fails]


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
