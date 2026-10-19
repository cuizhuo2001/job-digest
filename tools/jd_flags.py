#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
job-digest · 香港岗位 JD 里的两道硬门槛：要不要粤语、要不要香港永居

    python tools/jd_flags.py            把还没看过的香港岗位 JD 读一遍（平时刷新数据源时会顺手跑）
    python tools/jd_flags.py --show     列出打了标签的岗位

每个岗位第一次出现时把 JD 正文拉一次，按岗位 id 缓存在 data/jd_flags.json，之后不再重复读。
  · 要粤语     JD 里写了 Cantonese / 粤语 / 广东话（同一句里写着 advantage / preferred / 优先 / 更佳 的算「粤语加分」，只提示不筛）
  · 要香港永居  JD 里写了 permanent resident / 永久居民 / right of abode
只写「right to work in Hong Kong」的不算（IANG 签证可以工作）。
读不到 JD 的岗位（网页要登录、前端渲染、网站挂了）不打标签 —— 没标 ≠ 不要求，投之前还是扫一眼 JD。

在面板「设置」里可以打开「我不会粤语：要粤语的岗位不推」「要求香港永居的岗位不推」，默认只打标签不筛。
"""
import os
import re
import sys
import json
import time
import html
import argparse
import datetime
import urllib.parse
import urllib.request
import concurrent.futures as cf

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import jdlib  # noqa: E402

CACHE = "data/jd_flags.json"
LABELS = {"粤语": "要粤语", "粤语加分": "粤语加分", "永居": "要香港永居"}
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/130.0 Safari/537.36", "Accept-Language": "en,zh-CN;q=0.8"}

CANTONESE = re.compile(r"cantonese|粤语|粵語|广东话|廣東話|广府话", re.I)
SOFT = re.compile(r"advantage|a plus|plus\b|prefer|desirable|an asset|bonus|nice to have|优先|優先|加分|更佳|为佳|為佳|尤佳|更好", re.I)
PR = re.compile(r"permanent residen|\bHKPR\b|right of abode|永久(?:性)?居民|居港权|居港權", re.I)
NOT_PR = re.compile(r"non[- ]?permanent|not (?:need|required|necessary)|regardless|不限|无需|無需|不需要", re.I)
LANG = re.compile(r"putonghua|mandarin|english|japanese|korean|french|german|普通话|普通話|国语|國語|英语|英文|日语", re.I)
SKILL = re.compile(r"fluen|proficien|knowledge|command|abilit|skill|literate|流利|熟练|熟練|能力|掌握", re.I)


# ───────────────────────── 认标签 ─────────────────────────

def _cantonese_soft(s):
    """这句里的粤语是「加分」还是硬要求。
    「Proficiency in English and Cantonese, fluency in Putonghua will be an advantage」→ 加分说的是普通话，粤语是硬要求；
    「Proficiency in Cantonese, Mandarin and English is preferred」→ 一串语言一起「preferred」，粤语也是加分。"""
    parts = re.split(r"[,，;；]", s)
    if any(CANTONESE.search(p) and SOFT.search(p) for p in parts):
        return True
    soft = [p for p in parts if SOFT.search(p)]
    # 「加分」那半句另起一项讲别的语言（有 fluency / proficiency 这种词）→ 不算粤语的
    return bool(soft) and not all(LANG.search(p) and SKILL.search(p) and not CANTONESE.search(p) for p in soft)


def flags_of(text):
    """JD 正文 → ["粤语"] / ["粤语加分"] / ["永居"] 的组合；按句子看，避免把「粤语优先」当成硬要求。"""
    out = set()
    for s in re.split(r"(?<=[.;!?。；！？])\s+|[\n\r•●▪·]+|(?<=[。；])", text or ""):
        if CANTONESE.search(s):
            out.add("粤语加分" if _cantonese_soft(s) else "粤语")
        if PR.search(s) and not NOT_PR.search(s):
            out.add("永居")
    if "粤语" in out:
        out.discard("粤语加分")
    return sorted(out)


# ───────────────────────── 读 JD ─────────────────────────

def _get(url, data=None, headers=None, timeout=25):
    req = urllib.request.Request(url, data=data, headers=dict(UA, **(headers or {})))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def _strip(s):
    s = re.sub(r"(?is)<(script|style|noscript|svg)\b.*?</\1>", " ", s or "")
    s = re.sub(r"(?i)<br\s*/?>|</(p|li|div|h\d|tr)>", "\n", s)
    s = html.unescape(re.sub(r"<[^>]+>", " ", s))
    return re.sub(r"[ \t ]+", " ", s)


def _workday(u):
    # https://<租户>.wd3.myworkdayjobs.com/[en-US/]<站点>/job/<地点>/<标题>_<编号>
    m = re.match(r"https://(([\w-]+)\.wd\d+\.myworkdayjobs\.com)/(?:[a-z]{2}-[A-Z]{2}/)?([^/]+)(/job/.+?)/?(?:\?.*)?$", u)
    if not m:
        return None
    host, tenant, site, path = m.groups()
    d = json.loads(_get("https://%s/wday/cxs/%s/%s%s" % (host, tenant, site, path), headers={"Accept": "application/json"}))
    return _strip((d.get("jobPostingInfo") or {}).get("jobDescription"))


def _oracle(u):
    m = re.match(r"https://([^/]+)/hcmUI/CandidateExperience/\w+/sites/([^/]+)/job/(\d+)", u)
    if not m:
        return None
    host, site, rid = m.groups()
    d = json.loads(_get("https://%s/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails?expand=all&onlyData=true"
                        "&finder=ById;Id=%%22%s%%22,siteNumber=%s" % (host, rid, site)))
    it = (d.get("items") or [{}])[0]
    return _strip(" \n".join(it.get(k) or "" for k in ("ExternalDescriptionStr", "ExternalResponsibilitiesStr",
                                                        "ExternalQualificationsStr", "ShortDescriptionStr")))


def _hotjob(u):
    m = re.match(r"https://([^/]+)/(SU\w+)/(?:pb|mc)/.*?[?&]postId=(\w+)", u)
    if not m:
        return None
    host, su, pid = m.groups()
    d = json.loads(_get("https://%s/wecruit/positionInfo/listPositionDetail/%s?iSaJAx=isAjax&request_locale=zh_CN" % (host, su),
                        data=urllib.parse.urlencode({"postId": pid}).encode(),
                        headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"}))
    x = d.get("data") or {}
    return _strip("%s\n%s" % (x.get("workContent") or "", x.get("serviceCondition") or ""))


def _beisen(u):
    m = re.match(r"https://([\w-]+\.zhiye\.com)/.*?[?&]jobAdId=([\w-]+)", u)
    if not m:
        return None
    host, jid = m.groups()
    for cat in (2, 1):
        d = json.loads(_get("https://%s/api/JobAd/GetJobAdInfo?jobAdId=%s&category=%d&displayFields=%s" % (
            host, jid, cat, urllib.parse.quote('["Duty","Require"]'))))
        x = d.get("Data") or {}
        if x.get("Duty") or x.get("Require"):
            return _strip("%s\n%s" % (x.get("Duty") or "", x.get("Require") or ""))
    return ""


def jd_text(url):
    """→ JD 正文；读不到返回 ""（不抛异常）。招聘系统有公开接口的走接口，别的当普通网页读。"""
    url = (url or "").strip()
    if not url.startswith("http"):
        return ""
    try:
        for fn in (_workday, _oracle, _hotjob, _beisen):
            t = fn(url)
            if t is not None:
                return t
        t = _strip(_get(url))
        # 前端渲染的页面拿回来只有导航和页脚：太短就当没读到
        return t if len(re.sub(r"\s+", "", t)) > 400 else ""
    except Exception:
        return ""


# ───────────────────────── 缓存 ─────────────────────────

_MEM = {}


def load():
    """{id: {"d": 读的日期, "f": [标签], "ok": 读到没}}；按文件修改时间缓存，面板频繁读不费事。"""
    p = jdlib.resolve(CACHE)
    try:
        mt = os.path.getmtime(p)
    except OSError:
        return {}
    if _MEM.get("mt") != mt:
        _MEM.update(mt=mt, d=jdlib.load_json(p, {}))
    return _MEM["d"]


def flags(rid):
    return (load().get(str(rid)) or {}).get("f") or []


def labels(rid):
    f = flags(rid)
    return [LABELS[k] for k in ("粤语", "粤语加分", "永居") if k in f]


def blocked_by(rid, cfg):
    """设置里打开了对应开关、这个岗位又命中 → 返回原因；否则 None。"""
    f = flags(rid)
    if "粤语" in f and cfg.get("skip_need_cantonese"):
        return "要粤语"
    if "永居" in f and cfg.get("skip_need_hkpr"):
        return "要香港永居"
    return None


def remember(texts):
    """{岗位 id: JD 正文}：抓列表时已经拿到 JD 的（Moka 卡片里自带），直接记下，不用再读网页。"""
    if not texts:
        return
    cache = dict(load())
    today = datetime.date.today().isoformat()
    for rid, t in texts.items():
        cache[str(rid)] = {"d": today, "f": flags_of(t), "ok": bool((t or "").strip())}
    jdlib.save_json(jdlib.resolve(CACHE), cache)


def update(cfg, budget=150, retry_days=3, workers=6):
    """把今天还在池子里（过了筛选）、还没读过 JD 的香港岗位读一遍。超过 budget 秒就先停，下次接着读。"""
    import pool
    d = pool.select(cfg, datetime.date.today())
    hk = d.get("hk") or {}
    rows = list(hk.get("alive_rows") or []) + list(hk.get("nodir") or [])
    cache = dict(load())
    today = datetime.date.today()

    def stale(e):
        if not e:
            return True
        if e.get("ok"):
            return False
        try:
            return (today - datetime.date.fromisoformat(e.get("d", ""))).days >= retry_days
        except ValueError:
            return True

    todo, seen = [], set()
    for r in rows:
        rid = str(r.get("id") or "")
        if rid and rid not in seen and (r.get("apply_url") or "").startswith("http") and stale(cache.get(rid)):
            seen.add(rid)
            todo.append(r)
    if not todo:
        return 0, 0
    t0, done, hit = time.time(), 0, 0
    with cf.ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(jd_text, r["apply_url"]): r for r in todo}
        try:
            for f in cf.as_completed(futs, timeout=budget):
                r, text = futs[f], f.result()
                fl = flags_of(text)
                cache[str(r["id"])] = {"d": today.isoformat(), "f": fl, "ok": bool(text)}
                done += 1
                hit += bool(fl)
        except cf.TimeoutError:
            for f in futs:
                f.cancel()
    # 120 天前读的、池子里早没了的清掉，别让缓存越攒越大
    live = {str(r.get("id")) for r in rows}
    cut = (today - datetime.timedelta(days=120)).isoformat()
    cache = {k: v for k, v in cache.items() if k in live or v.get("d", "") >= cut}
    jdlib.save_json(jdlib.resolve(CACHE), cache)
    jdlib.log("  ↻ 读了 %d 个香港岗位的 JD（%.0f 秒），%d 个写了粤语 / 永居要求%s" % (
        done, time.time() - t0, hit, "；还剩 %d 个下次读" % (len(todo) - done) if done < len(todo) else ""))
    return done, hit


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", action="store_true", help="列出打了标签的岗位")
    args = ap.parse_args()
    import pool
    cfg = pool.load_config()
    if not args.show:
        done, hit = update(cfg)
        print("读了 %d 个，%d 个有粤语 / 永居要求" % (done, hit))
    c = load()
    d = pool.select(cfg, datetime.date.today())
    hk = d.get("hk") or {}
    rows = list(hk.get("alive_rows") or []) + list(hk.get("nodir") or [])
    ok = sum(1 for r in rows if (c.get(str(r.get("id"))) or {}).get("ok"))
    print("香港池 %d 个岗位，读到 JD 的 %d 个" % (len(rows), ok))
    for r in rows:
        lab = labels(r.get("id"))
        if lab:
            print("  %-16s %-22s %s" % (" ".join(lab), (r.get("company") or "")[:22], (r.get("title") or "")[:60]))


if __name__ == "__main__":
    main()
