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
    host = c.get("host", "wecruit.hotjob.cn")       # 有的公司用自己的域名（安永香港 eyhk.hotjob.cn、德勤 ehjobs.deloitte.com.cn）
    url = "https://%s/wecruit/positionInfo/listPosition/%s?iSaJAx=isAjax&request_locale=zh_CN" % (host, su)
    # 官网有两套页面模板：老的 pb（/pb/posDetail.html）和新的 mc（/mc/detail）；公司换了模板，老链接就打开显示「官网不存在」
    try:
        req = urllib.request.Request("https://%s/wecruit/suite/config/%s?iSaJAx=isAjax&request_locale=zh_CN" % (host, su),
                                     headers={"User-Agent": "Mozilla/5.0 job-digest"})
        with urllib.request.urlopen(req, timeout=20) as r:
            tpl = (json.loads(r.read().decode("utf-8")).get("data") or {}).get("templateNum") or "pb"
    except Exception:
        tpl = "pb"
    detail = ("https://%s/%s/mc/detail?postId=%%s&recruitType=campus" if tpl == "mc"
              else "https://%s/%s/pb/posDetail.html?postId=%%s&postType=campus") % (host, su)
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
                    "url": detail % r["postId"]})
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


# Moka 卡片里的英文城市 → 中文（分香港池 / 大陆池、对 allowed_cities 都靠中文名）
CITY_EN = {"Hong Kong": "香港", "Hongkong": "香港", "Beijing": "北京", "Shanghai": "上海", "Guangzhou": "广州",
           "Shenzhen": "深圳", "Chengdu": "成都", "Hangzhou": "杭州", "Nanjing": "南京", "Wuhan": "武汉", "Xi'an": "西安",
           "Xian": "西安", "Tianjin": "天津", "Chongqing": "重庆", "Suzhou": "苏州", "Xiamen": "厦门", "Qingdao": "青岛",
           "Dalian": "大连", "Shenyang": "沈阳", "Changsha": "长沙", "Zhengzhou": "郑州", "Jinan": "济南", "Hefei": "合肥",
           "Ningbo": "宁波", "Fuzhou": "福州", "Foshan": "佛山", "Dongguan": "东莞", "Zhuhai": "珠海", "Kunming": "昆明",
           "Taiyuan": "太原", "Nanning": "南宁", "Haikou": "海口", "Harbin": "哈尔滨", "Changchun": "长春",
           "Shijiazhuang": "石家庄", "Wuxi": "无锡", "Macau": "澳门", "Macao": "澳门"}
CITY_ZH = sorted(set(CITY_EN.values()) | set(DEFAULT_CITIES), key=len, reverse=True)
EMPLOY = re.compile(r"full[- ]?time|part[- ]?time|intern|contract|permanent|temporary|全职|兼职|实习|校招", re.I)

MOKA_JS = r"""() => [...document.querySelectorAll('a[href*="#/job/"]')].map(a => {
  const q = s => a.querySelector(s);
  const desc = q('[class*="job-description"], [class*="short-description"]');
  const meta = [...a.querySelectorAll('.no-adaptive-tooltip')].map(e => e.textContent.trim()).filter(Boolean);
  let loc = '';
  const st = [...a.querySelectorAll('strong, b')].find(s => /^\s*(job\s+|work\s+)?locations?\s*[:：]?\s*$|^\s*(工作)?地点\s*[:：]?\s*$/i.test(s.textContent));
  if (st) {
    for (let n = st.nextSibling; n; n = n.nextSibling) {
      if (n.nodeType === 1 && (/^(STRONG|B|BR)$/.test(n.tagName) || n.querySelector('strong, b'))) break;
      loc += n.textContent;
    }
  }
  return {href: a.getAttribute('href'), title: (q('[class*="title"]') || a).textContent.trim(), meta, loc: loc.trim(),
          jd: desc ? desc.innerText : ''};
})"""


def _cities(text):
    """「Beijing, Shanghai, Shenzhen」「Shanghai Guangzhou, Guangdong Beijing」→ ["北京", "上海", "深圳"]"""
    text = text or ""
    out = []
    for en, zh in CITY_EN.items():
        if re.search(r"\b%s\b" % re.escape(en), text, re.I) and zh not in out:
            out.append(zh)
    for zh in CITY_ZH:
        if zh in text and zh not in out:
            out.append(zh)
    return out


def _moka_settle(pg, seen, timeout=25):
    """翻页后等列表真正换好再读：每页除了 30 个岗位还挂着 8 个「推荐」，翻页时它们先画出来，
    这时候读就只拿到那 8 个旧的。等到列表连续 1 秒多没变、而且出现了没见过的岗位才算翻好。"""
    t0, last, same = time.time(), None, 0
    while time.time() - t0 < timeout:
        cur = pg.evaluate(MOKA_JS)
        hs = tuple(x["href"] for x in cur)
        same = same + 1 if hs == last else 0
        last = hs
        if hs and same >= 3 and any(h not in seen for h in hs):
            return cur
        pg.wait_for_timeout(400)
    return pg.evaluate(MOKA_JS)


def moka(c, keywords, want=_any):
    """Moka 校招站（普华永道、毕马威）：数据接口是加密的，只能开一个看不见的浏览器把页面渲染出来，再从网页上读。
    要先装 Playwright（约 150MB）：pip install playwright，再 python -m playwright install chromium。没装就跳过这家。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise watch_hk.Skip("没装 Playwright")
    base = "https://app.mokahr.com/campus-recruitment/%s/%s" % (c["org"], c["site"])
    seen = {}
    with sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception:
            raise watch_hk.Skip("Playwright 的浏览器没装（python -m playwright install chromium）")
        try:
            pg = b.new_page(locale="en-US")
            pg.goto(base + "#/jobs", timeout=60000)
            pg.wait_for_selector('a[href*="#/job/"]', timeout=45000)
            for page in range(1, 40):
                items = _moka_settle(pg, seen)
                new = [x for x in items if x["href"] not in seen]
                for x in new:
                    seen[x["href"]] = x
                fwd = pg.locator('[class*="Pagination-forward"]')
                if not new or not fwd.count() or not fwd.first.is_enabled():
                    break
                fwd.first.click()
        finally:
            b.close()
    out = []
    for href, x in seen.items():
        title = " ".join(x["title"].split())
        m = re.search(r"(?:Locations?|工作地点)\s*[:：]\s*([^\n]{2,120})", x["jd"] or "")
        locs = (_cities(x["loc"]) or _cities(" ".join(e for e in x["meta"] if not EMPLOY.search(e)))
                or _cities(m.group(1) if m else "") or _cities(title))
        if not locs or not want(*locs):
            continue
        typ = "实习" if INTERN.search(title) or any(INTERN.search(e) for e in x["meta"]) else "校招"
        out.append({"key": href.rsplit("/", 1)[-1], "title": title, "type": typ, "posted": "", "deadline": "",
                    "locs": locs, "url": base + href, "jd": x["jd"]})
    return out


SYSTEMS = dict(watch_hk.SYSTEMS, hotjob=hotjob, citics=citics, moka=moka)


# ───────────────────────── 汇总 ─────────────────────────

def run(cfg, only=None, dry=False):
    o = opts(cfg)
    wl = jdlib.load_json(jdlib.resolve(o["watchlist"]), {})
    all_names = {c["name"] for c in wl.get("companies") or []}
    companies = [c for c in wl.get("companies") or [] if not only or only in c["name"]]
    cities = list(o["cities"])
    out_cn, out_hk = jdlib.resolve(o["out"]), jdlib.resolve(o["out_hk"])
    old, old_by = {}, {"cn": [], "hk": []}
    for k, p in (("cn", out_cn), ("hk", out_hk)):
        if os.path.exists(p):
            old_by[k] = pool.load_pool(p)
            old.update({r["id"]: r for r in old_by[k]})
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
    fails, failed, skipped = [], set(), set()
    with cf.ThreadPoolExecutor(6) as ex:
        futs = {ex.submit(one, c): c for c in companies}
        for f in cf.as_completed(futs):
            c = futs[f]
            try:
                jobs = f.result()
            except Exception as e:
                failed.add(c["name"])
                if isinstance(e, watch_hk.Skip):
                    skipped.add(c["name"])
                else:
                    fails.append("%s（%s）" % (c["name"], str(e)[:60]))
                continue
            if any(j.get("jd") for j in jobs):
                import jd_flags                        # 列表里自带 JD 的（Moka）：顺手认粤语 / 永居，省得再去读
                jd_flags.remember({"w-" + hashlib.sha1((c["name"] + "|" + str(j["key"])).encode("utf-8")).hexdigest()[:10]:
                                   j["jd"] for j in jobs if j.get("jd")})
            for j in jobs:
                region, city = where(j.get("locs") or [])
                if not region:
                    continue
                rid = "w-" + hashlib.sha1((c["name"] + "|" + str(j["key"])).encode("utf-8")).hexdigest()[:10]
                r = {"id": rid, "company": j.get("company") or c["name"], "title": j["title"], "city": city,
                     "deadline": j.get("deadline", ""), "deadline_kind": "fixed" if j.get("deadline") else "until_full",
                     "deadline_text": j.get("deadline") or "官网未列截止日", "apply_url": j["url"],
                     "type": j["type"], "industry": c.get("industry", ""), "score": 60,
                     # 见过的保留原来的日期；第一次跑只建基线，不把全部岗位都标成「新」
                     "first_seen": old[rid].get("first_seen", "") if rid in old else ("" if first_run else today),
                     "reasons": "自动查岗（%s）；工作地 %s；发布 %s" % (
                         c["system"], "、".join(j.get("locs") or []) or "?", j.get("posted") or "?"), "watch": c["name"]}
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
        queried = {c["name"] for c in companies}
        for k, p in (("cn", out_cn), ("hk", out_hk)):
            pool.write_pool(p, rows[k] + watch_hk.kept_rows(old_by[k], all_names, queried, failed))
    n = len(companies) - len(skipped)
    msg = "大陆关注公司 %d/%d 家查到 %d 个校招岗（大陆 %d · 香港 %d，新 %d）" % (
        n - len(fails), n, len(allrows), len(rows["cn"]), len(rows["hk"]), len(new))
    if skipped:
        msg += "；%s 要装 Playwright 才能查（见 README），先跳过" % "、".join(sorted(skipped))
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
