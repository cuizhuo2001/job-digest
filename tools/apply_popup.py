#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
job-digest · 今日投递面板（常驻状态栏 + 点开的面板）

pywebview（WebView2 内核）开两个无边框置顶窗口：状态栏（status_bar.html）和面板（apply_popup.html），
后端是只监听 127.0.0.1 的小服务（带随机 token，防别的网页乱 POST）。

- 状态栏：常驻，显示今天投了几家、最近截止、关注公司新岗；三种外观（顶部条 / 卡片 / 胶囊）
  在面板右上角或托盘菜单里换；按住左边的点点能拖，位置会记住
- 面板：点状态栏出现，点别处 / Esc / 再点状态栏就收起；「钉住」后点别处也不收
- 四态：待投 / 已投(applied_ids) / 稍后(later，明天排最前) / 屏蔽(blocked，带原因)
  稍后、屏蔽当场从同一个池子补一家；「再来 5 家」按栏补
- 统计：每次操作追加到 history.jsonl；已投/稍后/屏蔽记在 state.json
- 个人资料（简历、速查卡、求职信模板）读 profile.json，见 profile.example.json
- 打游戏（gaming_guard）时整个窗口藏起来
- 只支持 Windows（窗口靠 Win32 API，剪贴板靠 pywin32）

    pythonw apply_popup.py          常驻（开机自启用这个，只显示状态栏）
    pythonw apply_popup.py --nudge  已在跑就把面板弹出来提醒；没在跑就起一个并提醒
"""
import io, os, re, sys, json, time, ctypes, secrets, datetime, threading, subprocess, webbrowser
import urllib.parse
import urllib.request
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import jdlib  # noqa: E402
import pool  # noqa: E402
import profile_card  # noqa: E402
import import_list  # noqa: E402
import panel_settings  # noqa: E402

HISTORY = jdlib.HISTORY
TODAY = os.path.join(jdlib.ROOT, "popup_today.json")
PORT_FILE = os.path.join(jdlib.ROOT, "popup_port.json")
PAGE_PATH = os.path.join(HERE, "apply_popup.html")
BAR_PATH = os.path.join(HERE, "status_bar.html")
CL_TOOLS = os.path.join(HERE, "cl")
# 用 pythonw 起面板时 sys.executable 是 pythonw.exe；跑转换脚本要它旁边的 python.exe
_py = os.path.join(os.path.dirname(sys.executable), "python.exe")
PY = _py if os.path.exists(_py) else sys.executable
TITLE = "今日投递 · 面板"
V = 5
TOKEN = secrets.token_hex(12)
LOCK = threading.RLock()

REASONS = {"dead": "链接死了", "company": "不想投这家公司", "hard": "GPA/硬性条件卡",
           "dir": "方向不对", "other": "其他"}


# ───────────────────────── 数据层 ─────────────────────────

def today_str():
    return datetime.date.today().isoformat()


def job_of(r, key, repl_for=None):
    return {"id": str(r.get("id")), "pool": key,
            "company": r.get("company", "?"),
            "type": (r.get("type") or "").strip(),
            "title": pool.short_title(r.get("title"), 60),
            "city": (r.get("city") or "")[:30],
            "left": r["_left"] if r["_d"] is not None else None,
            "date": r["_d"].strftime("%m-%d") if r["_d"] is not None else "",
            "deadline": r["_d"].isoformat() if r["_d"] is not None else "",
            "url": r.get("apply_url") or "",
            "later": int(r.get("_later") or 0),
            "repl_for": repl_for}


def load_today():
    """当天名单固定存 popup_today.json；跨天/版本不对就重排。"""
    saved = jdlib.load_json(TODAY, {})
    if saved.get("date") == today_str() and saved.get("v") == V:
        return saved
    d = pool.select(pool.load_config(), datetime.date.today())
    jobs = []
    for key in ("hk", "cn"):
        part = d.get(key)
        if not part:
            continue
        quota = len(part["today"])
        # 稍后过的先上，再按 today 原顺序（含大厂名额）补满
        picks = [r for r in part["ranked"] if r["_later"]][:quota]
        ids = set(str(r.get("id")) for r in picks)
        for r in part["today"]:
            if len(picks) >= quota:
                break
            if str(r.get("id")) not in ids:
                picks.append(r)
                ids.add(str(r.get("id")))
        jobs += [job_of(r, key) for r in picks]
    saved = {"date": today_str(), "v": V, "jobs": jobs, "later_today": [], "need_cl": []}
    jdlib.save_json(TODAY, saved)
    return saved


def refill(t, cfg, key, n=1, repl_for=None):
    """从 key 池按排序补 n 家（不重复今天已有的）；返回实际补了几家。"""
    part = pool.select(cfg, datetime.date.today()).get(key)
    if not part:
        return 0
    have = set(j["id"] for j in t["jobs"])
    picks = [r for r in part["ranked"] if str(r.get("id")) not in have][:n]
    t["jobs"] += [job_of(r, key, repl_for) for r in picks]
    return len(picks)


def status_of(jid, cfg, t):
    if jid in set(str(x) for x in cfg.get("applied_ids") or []):
        return "applied"
    if jid in (cfg.get("blocked") or {}):
        return "blocked"
    if jid in t.get("later_today", []):
        return "later"
    return "pending"


def log_event(job, act, **kw):
    e = {"ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "date": today_str(),
         "id": job["id"], "pool": job["pool"], "company": job["company"], "title": job["title"], "act": act}
    e.update({k: v for k, v in kw.items() if v})
    with io.open(HISTORY, "a", encoding="utf-8") as f:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")


def act(jid, what, reason="", note=""):
    with LOCK:
        cfg = pool.load_config()
        st = jdlib.load_state()
        t = load_today()
        job = next((j for j in t["jobs"] if j["id"] == jid), None)
        if not job:
            raise ValueError("今天的名单里没有 %s" % jid)
        applied = [str(x) for x in st["applied_ids"]]
        later = st["later"]
        blocked = st["blocked"]
        dead = [str(x) for x in st["dead_ids"]]
        bco = list(st["blocked_companies"])
        lt = t.setdefault("later_today", [])
        prev = status_of(jid, cfg, t)
        need_refill = False

        if what == "applied":
            if jid not in applied:
                applied.append(jid)
            later.pop(jid, None)
            if jid in lt:
                lt.remove(jid)
        elif what == "later":
            later[jid] = int(later.get(jid, 0)) + 1
            if jid not in lt:
                lt.append(jid)
            need_refill = True
        elif what == "blocked":
            if reason not in REASONS:
                raise ValueError("未知原因 %s" % reason)
            e = {"reason": reason, "note": note, "date": today_str(),
                 "company": job["company"], "title": job["title"]}
            if reason == "dead" and jid not in dead:
                dead.append(jid)
            if reason == "company" and job["company"] not in bco:
                bco.append(job["company"])
                e["added_company"] = job["company"]
            blocked[jid] = e
            later.pop(jid, None)
            if jid in lt:
                lt.remove(jid)
            need_refill = True
        elif what == "undo":
            if prev == "applied":
                applied = [x for x in applied if x != jid]
            elif prev == "later":
                n = int(later.get(jid, 1)) - 1
                if n > 0:
                    later[jid] = n
                else:
                    later.pop(jid, None)
                lt.remove(jid)
            elif prev == "blocked":
                e = blocked.pop(jid, {})
                if e.get("reason") == "dead":
                    dead = [x for x in dead if x != jid]
                if e.get("added_company"):
                    bco = [x for x in bco if x != e["added_company"]]
            # 当时为它补进来、还没动过的那家撤掉，免得分母虚涨
            t["jobs"] = [j for j in t["jobs"] if not (j.get("repl_for") == jid and
                                                     status_of(j["id"], cfg, t) == "pending")]
        else:
            raise ValueError("未知动作 %s" % what)

        st["applied_ids"], st["later"], st["blocked"] = applied, later, blocked
        st["dead_ids"], st["blocked_companies"] = dead, bco
        jdlib.save_state(st)
        if need_refill:
            refill(t, pool.load_config(), job["pool"], 1, jid)
        jdlib.save_json(TODAY, t)
        log_event(job, what, reason=reason, note=note, prev=prev if what == "undo" else "")


def check_today():
    """把今天待投、还没探过的链接探一遍：404/410 = 对方下架了，自动标「链接死了」并补一家
    （跟手动点「屏蔽 → 链接死了」一样，可撤销）。只认 404/410，超时、403 这类不确定的不动；
    LinkedIn 未登录也回 404，跳过。补进来的新岗位下一轮接着探。"""
    import check_links
    hit = []
    for _ in range(3):
        with LOCK:
            cfg = pool.load_config()
            t = load_today()
            done = set(t.get("checked") or [])
            todo = [j for j in t["jobs"] if j["id"] not in done and status_of(j["id"], cfg, t) == "pending"]
        if not todo:
            break
        for j in todo:
            url = j["url"] or ""
            code = check_links.probe(url) if url and "linkedin.com" not in url else None
            if code in check_links.DEAD_CODES:
                try:
                    act(j["id"], "blocked", "dead", note="自动体检：%s" % code)
                    hit.append("%s %s" % (j["company"], j["title"]))
                except ValueError:
                    pass
            with LOCK:
                t = load_today()
                t["checked"] = sorted(set(t.get("checked") or []) | {j["id"]})
                jdlib.save_json(TODAY, t)
    if hit:
        jdlib.log("面板体检：%d 条链接已失效，自动换掉：%s" % (len(hit), "；".join(hit)))
    return hit


def link_watch():
    time.sleep(20)                      # 开机等网络起来
    while True:
        try:
            if check_today():
                WIN.refresh_bar()
                WIN.js(WIN.panel_win, "window.onShow && window.onShow()")
        except Exception as e:
            jdlib.log("面板体检出错：%s: %s" % (type(e).__name__, e))
        time.sleep(600)


def set_quota(key, delta):
    """每天推几家：调大当天就补进来；调小从明天起生效（今天已经排好的不删）。"""
    with LOCK:
        cfg = pool.load_config()
        cur = int(cfg.get("daily_quota_" + key, 5))
        n = max(1, min(30, cur + int(delta)))
        st = jdlib.load_state()
        st["quota"][key] = n
        jdlib.save_state(st)
        if n > cur:
            t = load_today()
            refill(t, pool.load_config(), key, n - cur)
            jdlib.save_json(TODAY, t)
        return n


def more(key):
    with LOCK:
        t = load_today()
        refill(t, pool.load_config(), key, 5)
        jdlib.save_json(TODAY, t)


def history_stats():
    ap = {}
    try:
        lines = io.open(HISTORY, encoding="utf-8").read().splitlines()
    except OSError:
        lines = []
    for ln in lines:
        try:
            e = json.loads(ln)
        except ValueError:
            continue
        if e.get("act") == "applied":
            ap[e["id"]] = e["date"]
        elif e.get("act") == "undo" and e.get("prev") == "applied":
            ap.pop(e["id"], None)
    d = datetime.date.today()
    monday = (d - datetime.timedelta(days=d.weekday())).isoformat()
    return {"today": sum(1 for v in ap.values() if v == d.isoformat()),
            "week": sum(1 for v in ap.values() if v >= monday)}


# ───────────────────────── 我的资料 ─────────────────────────

PENDING = {}            # 导入简历：识别出来、等用户点「保存」的那份


def card_path():
    return jdlib.load_profile()["card_path"]


def me_load():
    return profile_card.load(card_path())


def me_state(prof=None):
    path = card_path()
    prof = prof or me_load()
    return {"parts": profile_card.view(prof, jdlib.resolve), "file": path, "found": os.path.exists(path)}


def me_file(d):
    """「我的资料」里登记的文件路径（附件、各段的证明材料）；只认登记过的路径字段，别的不开。"""
    part = d.get("part") or "files"
    f = next((f for f in profile_card.PARTS[part]["fields"] if f["key"] == d["key"] and f.get("path")), None)
    if not f:
        raise ValueError("这一项不是文件")
    h = me_load()["parts"][part]
    if d.get("card") is not None:
        h = h["cards"][int(d["card"])]
    c = h["cells"].get(d["key"]) or {}
    v = c.get(d.get("lang") or "zh") or c.get("zh") or c.get("en")
    if not v:
        raise ValueError("「我的资料」里还没填这个文件")
    return jdlib.resolve(v)


def pick_file(start):
    """编辑资料时点「选择…」：弹系统的选文件框，返回完整路径（取消 = 空）。"""
    if not WIN.panel_win:
        raise ValueError("只有在面板窗口里能用，手动填路径也行")
    import webview
    d = os.path.dirname(jdlib.resolve(start)) if start else ""
    WIN.picking = True                  # 选文件框会抢走焦点，这期间别把面板收起来
    try:
        r = WIN.panel_win.create_file_dialog(webview.FileDialog.OPEN, directory=d if os.path.isdir(d) else "")
    finally:
        WIN.picking = False
    return os.path.normpath(r[0]).replace("\\", "/") if r else ""


def me_post(path, d):
    with LOCK:
        if path == "/api/me/save":
            prof = profile_card.apply_edit(me_load(), d["part"], d["data"])
        elif path == "/api/me/confirm":
            prof = profile_card.confirm_ai(me_load(), d["part"], d.get("field"), d["lang"],
                                           d.get("card"), d.get("extra"))
        elif path == "/api/me/parse":
            prof, st = profile_card.from_import(d.get("text") or "", me_load())
            PENDING["prof"] = prof
            return {"preview": me_state(prof), "stats": st,
                    "todo": {"zh": profile_card.todo(prof, "zh"), "en": profile_card.todo(prof, "en")}}
        elif path == "/api/me/import":
            prof = PENDING.pop("prof", None)
            if not prof:
                raise ValueError("没有待保存的导入结果，重新识别一次")
        else:
            raise ValueError("未知操作 %s" % path)
        profile_card.save(card_path(), prof)
    return None


def cl_info(jid, cfg):
    base = (cfg.get("cover_letters") or {}).get(jid)
    if not base:
        return {"base": None, "md": False, "pdf": False}
    d = jdlib.load_profile()["cover_letter_dir"]
    return {"base": base, "md": os.path.exists(os.path.join(d, base + ".md")),
            "pdf": os.path.exists(os.path.join(d, base + ".pdf"))}


def state():
    with LOCK:
        cfg = pool.load_config()
        t = load_today()
        d = pool.select(cfg, datetime.date.today())
    have = set(j["id"] for j in t["jobs"])
    nolink = nolink_jobs(d)
    remain = {k: (len([r for r in d[k]["ranked"] if str(r.get("id")) not in have]) if d.get(k) else 0)
              for k in ("hk", "cn")}
    jobs = []
    for j in t["jobs"]:
        j = dict(j, status=status_of(j["id"], cfg, t), cl=cl_info(j["id"], cfg),
                 need_cl=(j["pool"] == "hk") or (j["id"] in t.get("need_cl", [])))
        jobs.append(j)
    st = history_stats()
    st["total"] = len(cfg.get("applied_ids") or [])
    st["blocked_today"] = sum(1 for e in (cfg.get("blocked") or {}).values() if e.get("date") == today_str())
    st["later_today"] = len(t.get("later_today", []))
    return {"date": t["date"], "jobs": jobs, "stats": st, "remain": remain, "reasons": REASONS,
            "me": me_state(), "import_prompt": profile_card.import_prompt(),
            "jobs_prompt": import_list.import_prompt(), "nolink": len(nolink),
            "quota": {k: int(cfg.get("daily_quota_" + k, 5)) for k in ("hk", "cn")},
            "bar": {"style": WIN.style, "pinned": WIN.pinned or WIN.copy_pin, "styles": STYLE_NAMES}}


_FRESH = {}


def fresh_count():
    """关注公司今天新挂出来的岗位数（和推送里「🆕」那段同一口径）；半小时算一次。"""
    now = time.time()
    if _FRESH.get("date") == today_str() and now - _FRESH.get("at", 0) < 1800:
        return _FRESH["n"]
    d = pool.select(pool.load_config(), datetime.date.today())
    n = sum(1 for k in ("hk", "cn") if d.get(k) for r in d[k]["alive_rows"]
            if r.get("first_seen") == today_str() and str(r.get("id", "")).startswith(("w-", "s-")))
    _FRESH.update(date=today_str(), at=now, n=n)
    return n


def bar_state():
    """状态栏用的摘要：只读今天的名单，不重新排池子，点一下就能刷新。"""
    with LOCK:
        cfg = pool.load_config()
        t = load_today()
    c = {"hk": {"a": 0, "n": 0}, "cn": {"a": 0, "n": 0}}
    pend = []
    for j in t["jobs"]:
        s = status_of(j["id"], cfg, t)
        if s in ("applied", "pending"):
            c[j["pool"]]["n"] += 1
            c[j["pool"]]["a"] += s == "applied"
        if s == "pending":
            pend.append(j)
    pend.sort(key=lambda j: 9999 if j["left"] is None else j["left"])
    nx = pend[0] if pend else None
    soon = int(cfg.get("urgent_days", 3))
    return dict(c, style=WIN.style, open=WIN.panel_shown, pending=len(pend),
                done=c["hk"]["a"] + c["cn"]["a"], den=c["hk"]["n"] + c["cn"]["n"],
                urgent=sum(1 for j in pend if j["left"] is not None and j["left"] <= soon),
                next=nx and {k: nx[k] for k in ("id", "company", "title", "left", "date", "url", "pool")},
                new=fresh_count(), week=history_stats()["week"])


# ───────────────────────── 补链接 ─────────────────────────

def nolink_jobs(d=None):
    """池子里（过了筛选、还能投的）没有申请链接的岗位，截止早的在前。"""
    d = d or pool.select(pool.load_config(), datetime.date.today())
    out = [{"id": str(r["id"]), "company": r.get("company", ""), "title": r.get("title", ""),
            "deadline": r.get("deadline") or "", "pool": k}
           for k in ("hk", "cn") if d.get(k) for r in d[k]["alive_rows"] if not (r.get("apply_url") or "").strip()]
    return sorted(out, key=lambda x: x["deadline"] or "9999")


def _url_ok(u):
    u = (u or "").strip().strip("<>\"'")
    return u if re.match(r"https?://[^\s]+$", u) else ""


def save_links(pairs):
    """{id: url} → 记进 state.json 的 links，今天名单里的同一家也换上。"""
    with LOCK:
        st = jdlib.load_state()
        st["links"].update(pairs)
        jdlib.save_state(st)
        t = load_today()
        for j in t["jobs"]:
            if j["id"] in pairs:
                j["url"] = pairs[j["id"]]
        jdlib.save_json(TODAY, t)


def links_prompt(jobs):
    rows = "\n".join("%s | %s | %s | %s" % (j["id"], j["company"], j["title"], j["deadline"] or "未写截止日") for j in jobs)
    return """下面是一些校招岗位，请联网查出每个岗位的官方申请页面，只输出 JSON，用 ```json 包起来，不要别的内容。

格式（id 原样照抄）：
```json
[{"id": "岗位 id", "url": "申请页网址", "where": "官网 / 公司招聘系统 / 招聘网站"}]
```
规则：
1. 优先公司官网，或公司自己用的招聘系统（Workday、Greenhouse、Lever、SuccessFactors、Taleo、Avature、北森、Moka、Hotjob 等）上这个岗位的详情页。
2. 找不到这个岗位的单独页面，就给这家公司的校园招聘页面。
3. LinkedIn、JobsDB、CTgoodjobs 这类招聘网站只在官方页面都找不到时才用，where 写「招聘网站」。
4. 实在找不到就把 url 留空 ""。不要猜、不要编网址。

岗位（id | 公司 | 岗位 | 截止日）：
""" + rows


def links_parse(text):
    """AI 回的 JSON → 预览用的清单（逐个打开试一下，打不开的默认不勾）。"""
    import check_links
    text = text or ""
    m = re.search(r"```(?:json)?\s*(\[.*\])\s*```", text, re.S)
    s = m.group(1) if m else text[text.find("["):text.rfind("]") + 1]
    try:
        items = json.loads(s)
    except ValueError as e:
        raise ValueError("AI 回的 JSON 读不了（%s）：让它「只输出 JSON，用 ```json 包起来」再来一次" % e)
    known = {j["id"]: j for j in nolink_jobs()}
    known.update({j["id"]: {"id": j["id"], "company": j["company"], "title": j["title"],
                            "deadline": j.get("deadline") or "", "pool": j["pool"]} for j in load_today()["jobs"]})
    out = []
    for x in items if isinstance(items, list) else []:
        if not isinstance(x, dict) or str(x.get("id")) not in known:
            continue
        out.append(dict(known[str(x["id"])], url=_url_ok(x.get("url")), where=str(x.get("where") or "")))
    if not out:
        raise ValueError("没对上岗位 id：让 AI 把 id 原样照抄")

    def check(r):
        if not r["url"]:
            r["status"], r["pick"] = "没找到", False
            return
        code = check_links.probe(r["url"], timeout=10)
        if code in check_links.DEAD_CODES:
            r["status"], r["pick"] = "打不开（%s）" % code, False
        elif isinstance(code, int) and code < 400:
            r["status"], r["pick"] = "能打开", True
        else:
            r["status"], r["pick"] = "没验证（%s）" % code, True
    import concurrent.futures as cf
    with cf.ThreadPoolExecutor(8) as ex:
        list(ex.map(check, out))
    return out


# ───────────────────────── 求职信 ─────────────────────────

def _safe(s, n):
    return re.sub(r"[^\w]+", "", s or "")[:n]


class _Keep(dict):
    """模板里没给的占位符原样留着，别 KeyError。"""
    def __missing__(self, k):
        return "{" + k + "}"


def cl_draft(job):
    prof = jdlib.load_profile()
    cl_dir = prof["cover_letter_dir"]
    os.makedirs(cl_dir, exist_ok=True)
    en = " ".join(re.findall(r"[A-Za-z][A-Za-z&.\-]*", job["company"])) or job["company"]
    base = "%s_%s" % (_safe(en, 24) or job["id"], _safe(job["title"], 40) or job["id"])
    path = os.path.join(cl_dir, base + ".md")
    if not os.path.exists(path):
        tpl = io.open(prof["cover_letter_template"], encoding="utf-8").read()
        fields = _Keep({k: v for k, v in prof.items() if isinstance(v, str)})
        fields.update(company=job["company"], title=job["title"], company_en=en,
                      url=job["url"] or "（池子里没有链接）",
                      deadline=job.get("deadline") or "招满即止", today=today_str(),
                      resume=_resume_or_blank(job["pool"]))
        with io.open(path, "w", encoding="utf-8") as f:
            f.write(tpl.format_map(fields))
    st = jdlib.load_state()
    st["cover_letters"][job["id"]] = base
    jdlib.save_state(st)
    open_file(path)


def cl_pdf(base):
    cl_dir = jdlib.load_profile()["cover_letter_dir"]
    md = os.path.join(cl_dir, base + ".md")
    if "【待写" in io.open(md, encoding="utf-8").read():
        raise ValueError("还有【待写】没写完，先写完再生成")
    flags = 0x08000000
    for args in (["md2docx.py", base + ".md"], ["docx2pdf.py", base + ".docx"]):
        r = subprocess.run([PY, os.path.join(CL_TOOLS, args[0]), args[1]], cwd=cl_dir,
                           capture_output=True, creationflags=flags, timeout=180)
        if r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout).decode("utf-8", "replace")[-300:])


def resume_of(pool_key):
    """香港岗用英文简历、大陆岗用中文简历：先看「我的资料 → 附件」，没填再看 profile.json 的 resume。"""
    c = me_load()["parts"]["files"]["cells"].get("resume_en" if pool_key == "hk" else "resume_cn") or {}
    path = jdlib.resolve(c["zh"]) if c.get("zh") else (jdlib.load_profile()["resume"] or {}).get(pool_key)
    if not path:
        raise ValueError("还没登记%s简历：去「我的资料 → 附件」里填上路径" % ("英文" if pool_key == "hk" else "中文"))
    return path


def _resume_or_blank(pool_key):
    try:
        return resume_of(pool_key)
    except ValueError:
        return ""


def open_file(path):
    try:
        os.startfile(path)
    except OSError:
        subprocess.Popen(["notepad.exe", path])


def get_clip():
    import win32clipboard
    win32clipboard.OpenClipboard()
    try:
        return win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT) or ""
    except TypeError:                   # 剪贴板里不是文字
        return ""
    finally:
        win32clipboard.CloseClipboard()


def set_clip(text):
    import win32clipboard
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
    finally:
        win32clipboard.CloseClipboard()


# ───────────────────────── HTTP ─────────────────────────

def find_job(jid):
    return next((j for j in load_today()["jobs"] if j["id"] == jid), None)


def handle_post(path, d):
    if path == "/api/act":
        act(str(d["id"]), d["act"], d.get("reason", ""), d.get("note", ""))
    elif path == "/api/more":
        more(d["pool"])
    elif path == "/api/quota":
        set_quota(d["pool"], d["delta"])
    elif path.startswith("/api/me/"):
        r = me_post(path, d)
        if r is not None:
            return r
    elif path == "/api/clip":
        if "text" in d:
            set_clip(d["text"])
        elif d.get("what") == "file":
            set_clip(os.path.normpath(me_file(d)))
        else:
            job = find_job(str(d["id"]))
            prof = jdlib.load_profile()
            if d["what"] == "resume":
                set_clip(os.path.normpath(resume_of(job["pool"])))
            else:
                base = cl_info(job["id"], pool.load_config())["base"]
                set_clip(os.path.normpath(os.path.join(prof["cover_letter_dir"], base + ".pdf")))
        WIN.copy_pin = True             # 复制完要去网页里粘贴：这次先别「点别处就收」
        return {"pinned": True}
    elif path == "/api/open" and d.get("what") == "file":
        open_file(me_file(d))
        return {}
    elif path == "/api/open" and d.get("what") == "link":     # 补链接预览里点网址看一眼
        u = _url_ok(d.get("url"))
        if u:
            webbrowser.open(u)
        return {}
    elif path == "/api/jobs/parse":
        try:
            items = import_list.recognize(d.get("text") or "")
        except ValueError as e:
            raise ValueError("AI 回的 JSON 读不了（%s）：让它「只输出 JSON，用 ```json 包起来」再来一次" % e)
        if not items:
            raise ValueError("没认出岗位。排得太乱的话，点下面「复制 prompt 让 AI 整理」")
        return {"items": import_list.plan(items)}
    elif path == "/api/jobs/import":
        picks = [x for x in d.get("items") or [] if x.get("pick")]
        with LOCK:
            added = import_list.save(picks, "粘贴导入 %s" % datetime.date.today().strftime("%m-%d"))
        WIN.refresh_bar()
        return dict(state(), added=len(added))
    elif path == "/api/link":                   # 卡片上「粘贴链接 / 改链接」：读剪贴板
        url = _url_ok(d.get("url") or get_clip())
        if not url:
            raise ValueError("剪贴板里不是网址：先在浏览器地址栏复制申请页的网址，再点一次")
        save_links({str(d["id"]): url})
    elif path == "/api/links/copy":
        jobs = nolink_jobs()[:40]
        if not jobs:
            raise ValueError("池子里的岗位都有链接了")
        set_clip(links_prompt(jobs))
        WIN.copy_pin = True
        return {"n": len(jobs), "pinned": True}
    elif path == "/api/links/parse":
        return {"items": links_parse(d.get("text") or "")}
    elif path == "/api/links/save":
        pairs = {str(x["id"]): x["url"] for x in d.get("items") or [] if x.get("pick") and _url_ok(x.get("url"))}
        if not pairs:
            raise ValueError("一条都没勾")
        save_links(pairs)
        return dict(state(), saved=len(pairs))
    elif path == "/api/settings/save":
        with LOCK:
            return {"settings": panel_settings.save(d)}
    elif path == "/api/settings/test":
        return {"results": panel_settings.test([c for c in d.get("channels") or []], pool.load_config())}
    elif path == "/api/pickfile":
        return {"path": pick_file(d.get("start") or "")}
    elif path == "/api/open":
        job = find_job(str(d["id"]))
        w = d["what"]
        if w == "url" and job["url"]:
            webbrowser.open(job["url"])
        elif w == "search":                         # 清单里没给链接的：搜官网申请页
            webbrowser.open("https://www.bing.com/search?q=" + urllib.parse.quote(
                "%s %s apply" % (job["company"], job["title"])))
        elif w == "resume":
            open_file(resume_of(job["pool"]))
        elif w in ("cl_md", "cl_pdf"):
            base = cl_info(job["id"], pool.load_config())["base"]
            open_file(os.path.join(jdlib.load_profile()["cover_letter_dir"],
                                   base + (".md" if w == "cl_md" else ".pdf")))
        return {}
    elif path == "/api/cl":
        job = find_job(str(d["id"]))
        if d["op"] == "draft":
            cl_draft(job)
        elif d["op"] == "pdf":
            cl_pdf(cl_info(job["id"], pool.load_config())["base"])
    elif path == "/api/needcl":
        with LOCK:
            t = load_today()
            s = set(t.get("need_cl", []))
            s.add(d["id"]) if d["on"] else s.discard(d["id"])
            t["need_cl"] = sorted(s)
            jdlib.save_json(TODAY, t)
    elif path == "/api/dock":
        op = d["op"]
        if op == "hide":
            WIN.hide_panel()
        elif op == "show":
            WIN.nudge()
        elif op == "toggle":
            WIN.toggle()
        elif op == "pin":
            on = not (WIN.pinned or WIN.copy_pin)
            WIN.pinned, WIN.copy_pin = on, False
        elif op == "style":
            WIN.set_style(d["style"])
        elif op == "quit":
            threading.Thread(target=quit_all, daemon=True).start()
        return state() if op in ("pin", "style") else {}
    WIN.refresh_bar()
    return state()


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body, ctype="application/json; charset=utf-8", code=200):
        b = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        page = {"/": PAGE_PATH, "/bar": BAR_PATH}.get(self.path.split("?")[0])
        if page:
            self._send(io.open(page, encoding="utf-8").read(), "text/html; charset=utf-8")
        elif self.path == "/api/state" and self.headers.get("X-T") == TOKEN:
            self._send(json.dumps(state(), ensure_ascii=False))
        elif self.path == "/api/settings" and self.headers.get("X-T") == TOKEN:
            self._send(json.dumps(panel_settings.view(), ensure_ascii=False))
        elif self.path == "/api/bar" and self.headers.get("X-T") == TOKEN:
            self._send(json.dumps(bar_state(), ensure_ascii=False))
        else:
            self._send("{}", code=404)

    def do_POST(self):
        if self.headers.get("X-T") != TOKEN:
            return self._send('{"error":"bad token"}', code=403)
        n = int(self.headers.get("Content-Length") or 0)
        try:
            d = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
            self._send(json.dumps(handle_post(self.path, d), ensure_ascii=False))
        except Exception as e:
            jdlib.log("面板 %s 出错：%s: %s" % (self.path, type(e).__name__, e))
            self._send(json.dumps({"error": str(e)}, ensure_ascii=False), code=500)


# ───────────────────────── 窗口：状态栏 + 面板 ─────────────────────────
# 两个独立窗口：状态栏常驻（三种外观可换、能拖、记位置）；面板开机就建好藏着，
# 点状态栏直接显示/隐藏（不改窗口大小，所以没有「滑出来」那一下卡顿）。

user32 = ctypes.windll.user32
for _f in (user32.FindWindowW, user32.GetForegroundWindow, user32.MonitorFromWindow):
    _f.restype = ctypes.c_void_p
SWP = 0x0004 | 0x0010                   # NOZORDER | NOACTIVATE（位置和大小一起改）
BAR_TITLE = "今日投递 · 状态栏"
STYLES = {"strip": (560, 30), "card": (300, 104), "pill": (236, 36)}   # 三种外观的窗口大小（CSS px）
STYLE_NAMES = {"strip": "顶部条", "card": "卡片", "pill": "胶囊"}
PANEL_W, PANEL_H = 1000, 720


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


def _rect(h):
    r = wintypes.RECT()
    user32.GetWindowRect(h, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def _work_area(h):
    """窗口所在那块屏幕去掉任务栏后的范围。"""
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(mi)
    user32.GetMonitorInfoW(ctypes.c_void_p(user32.MonitorFromWindow(h, 2)), ctypes.byref(mi))
    w = mi.rcWork
    return w.left, w.top, w.right, w.bottom


def _scale(h):
    try:
        return user32.GetDpiForWindow(h) / 96.0
    except Exception:
        return 1.0


def _round(h, small=False, border=None):
    """Win11 圆角（DWM 合成那层裁，WebView2 也吃这套；SetWindowRgn 不行）。Win10 没有就保持直角。"""
    try:
        dwm = ctypes.windll.dwmapi
        v = ctypes.c_int(3 if small else 2)                  # DWMWCP_ROUNDSMALL / ROUND
        dwm.DwmSetWindowAttribute(ctypes.c_void_p(h), 33, ctypes.byref(v), 4)
        c = ctypes.c_uint(0xFFFFFFFE if border is None else border)   # 描边：不要 / 指定颜色(0x00BBGGRR)
        dwm.DwmSetWindowAttribute(ctypes.c_void_p(h), 34, ctypes.byref(c), 4)
    except Exception:
        pass


def _foreground(h):
    if user32.SetForegroundWindow(h) and user32.GetForegroundWindow() == h:
        return
    # 系统不让后台进程抢焦点时：临时挂到前台窗口的输入队列上再抢一次
    a = user32.GetWindowThreadProcessId(ctypes.c_void_p(user32.GetForegroundWindow()), None)
    b = ctypes.windll.kernel32.GetCurrentThreadId()
    if a and a != b:
        user32.AttachThreadInput(b, a, True)
        user32.SetForegroundWindow(h)
        user32.AttachThreadInput(b, a, False)


def _tool_window(h):
    """不占任务栏按钮、不进 Alt+Tab（托盘里有图标）。"""
    ex = user32.GetWindowLongW(h, -20)
    user32.SetWindowLongW(h, -20, (ex | 0x80) & ~0x40000)    # +TOOLWINDOW −APPWINDOW


class Win:
    def __init__(self):
        self.bar = self.panel = None                  # hwnd
        self.bar_win = self.panel_win = None          # pywebview 窗口
        self.panel_shown = False
        self.activated = False                        # 这次显示后面板拿到过焦点没有
        self.pinned = False                           # 钉住：点别处也不收
        self.picking = False                          # 正在用选文件框
        self.copy_pin = False                         # 这次打开后复制过东西：收起前都不自动收
        self.panel_last = None
        self.nudge_until = 0                          # 提醒弹出来的：到点没碰过就自己收
        self.want_nudge = 0
        self.hidden_for_game = False
        self.bar_last = None
        self.style = (jdlib.load_state().get("bar") or {}).get("style") or "strip"
        if self.style not in STYLES:
            self.style = "strip"

    # ── 状态栏 ──
    def _bar_cfg(self):
        st = jdlib.load_state()
        return st, st.setdefault("bar", {})

    def place_bar(self):
        s = _scale(self.bar)
        w, h = (v * s for v in STYLES[self.style])
        l, t, r, b = _work_area(self.bar)
        pos = ((self._bar_cfg()[1].get("pos") or {}).get(self.style))
        if pos:
            x, y = pos
        elif self.style == "strip":
            x, y = (l + r - w) / 2, t
        elif self.style == "card":
            x, y = r - w - 12 * s, b - h - 12 * s
        else:
            x, y = r - w - 24 * s, t + 120 * s
        vx, vy = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)          # 整个虚拟桌面，防止换了显示器找不到
        vw, vh = user32.GetSystemMetrics(78), user32.GetSystemMetrics(79)
        x, y = max(vx, min(x, vx + vw - w)), max(vy, min(y, vy + vh - h))
        user32.SetWindowPos(self.bar, 0, int(x), int(y), int(w), int(h), SWP)
        _round(self.bar, small=self.style == "strip", border=0x005777D9 if self.style == "card" else None)
        self.bar_last = _rect(self.bar)

    def save_bar_pos(self, rc):
        l, t = rc[0], rc[1]
        if self.style == "strip" and t - _work_area(self.bar)[1] < 12 * _scale(self.bar):   # 顶部条拖到靠近顶边就吸上去
            t = _work_area(self.bar)[1]
            user32.SetWindowPos(self.bar, 0, l, t, 0, 0, SWP | 0x0001)                  # NOSIZE
        st, bc = self._bar_cfg()
        bc.setdefault("pos", {})[self.style] = [l, t]
        jdlib.save_state(st)
        self.bar_last = _rect(self.bar)

    def set_style(self, style):
        if style not in STYLES:
            raise ValueError("没有这种样式：%s" % style)
        self.style = style
        st, bc = self._bar_cfg()
        bc["style"] = style
        jdlib.save_state(st)
        if self.bar:
            self.place_bar()
        self.refresh_bar()

    def reset_bar(self):
        st, bc = self._bar_cfg()
        (bc.get("pos") or {}).pop(self.style, None)
        bc.pop("panel_pos", None)
        jdlib.save_state(st)
        self.place_bar()

    def js(self, win, code):
        def run():
            try:
                win.evaluate_js(code)
            except Exception:
                pass
        if win:
            threading.Thread(target=run, daemon=True).start()

    def refresh_bar(self):
        self.js(self.bar_win, "window.refresh && refresh()")

    # ── 面板 ──
    def place_panel(self):
        s = _scale(self.panel)
        bl, bt, br, bb = _rect(self.bar)
        l, t, r, b = _work_area(self.bar)
        w, h = min(PANEL_W * s, r - l), min(PANEL_H * s, b - t)
        gap = 6 * s
        pos = self._bar_cfg()[1].get("panel_pos")      # 用户拖过面板 = 放在他拖到的地方
        if pos:
            x, y = pos
        else:
            x = br - w if self.style == "card" else (bl + br - w) / 2
            y = bb + gap if (bt + bb) / 2 < (t + b) / 2 else bt - gap - h     # 状态栏在上半屏就往下开，否则往上开
        x, y = max(l, min(x, r - w)), max(t, min(y, b - h))
        user32.SetWindowPos(self.panel, 0, int(x), int(y), int(w), int(h), SWP)
        self.panel_last = _rect(self.panel)

    def show_panel(self, activate=True):
        if not self.panel or self.hidden_for_game:
            return
        if not self.panel_shown:
            self.place_panel()
            user32.ShowWindow(self.panel, 5 if activate else 4)          # SW_SHOW / SW_SHOWNOACTIVATE
            self.panel_shown, self.activated = True, False
            self.js(self.panel_win, "window.onShow && window.onShow()")
            self.js(self.bar_win, "window.barPanel && barPanel(true)")
        if activate:
            self.nudge_until = 0
            _foreground(self.panel)

    def hide_panel(self):
        if self.panel and self.panel_shown:
            user32.ShowWindow(self.panel, 0)                             # SW_HIDE
            self.panel_shown, self.nudge_until, self.copy_pin = False, 0, False
            self.js(self.bar_win, "window.barPanel && barPanel(false)")

    def toggle(self):
        self.hide_panel() if self.panel_shown else self.show_panel(True)

    def nudge(self, secs=12):
        """定时提醒：面板弹出来但不抢焦点；这段时间里没碰它就自己收回去。"""
        if not self.panel:
            self.want_nudge = secs
            return
        self.show_panel(False)
        if not self.activated:
            self.nudge_until = time.time() + secs
        self.js(self.bar_win, "window.pulse && pulse()")

    def cursor_in(self, h):
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        l, t, r, b = _rect(h)
        return l <= pt.x < r and t <= pt.y < b

    def loop(self):
        import gaming_guard
        games = (pool.load_config().get("dock") or {}).get("hide_when_running")
        if games:
            gaming_guard.GAMES = [g.lower() for g in games]
        while not (self.bar and self.panel):
            self.bar = self.bar or user32.FindWindowW(None, BAR_TITLE)
            self.panel = self.panel or user32.FindWindowW(None, TITLE)
            time.sleep(0.1)
        for h in (self.bar, self.panel):
            user32.ShowWindow(h, 0)
            _tool_window(h)
        _round(self.panel)
        self.place_bar()
        user32.ShowWindow(self.bar, 4)                                   # SW_SHOWNOACTIVATE
        if self.want_nudge:
            self.nudge(self.want_nudge)
        last_game = 0
        while True:
            time.sleep(0.05)
            now = time.time()
            if now - last_game > 20:
                last_game = now
                g = gaming_guard.gaming()
                if g and not self.hidden_for_game:
                    self.hide_panel()
                    user32.ShowWindow(self.bar, 0)
                    self.hidden_for_game = True
                elif not g and self.hidden_for_game:
                    user32.ShowWindow(self.bar, 4)
                    self.hidden_for_game = False
            if self.hidden_for_game:
                continue
            if self.panel_shown:
                fg = user32.GetForegroundWindow()
                down = user32.GetAsyncKeyState(0x01) & 0x8000
                if fg == self.panel:
                    self.activated, self.nudge_until = True, 0
                outside = down and not self.cursor_in(self.panel) and not self.cursor_in(self.bar)
                if self.pinned or self.copy_pin or self.picking:
                    pass
                elif outside or (self.activated and fg not in (self.panel, self.bar)):
                    self.hide_panel()                                    # 点了别处 = 收起
                elif self.nudge_until and now > self.nudge_until and not self.cursor_in(self.panel):
                    self.hide_panel()
            if not user32.GetAsyncKeyState(0x01) & 0x8000:               # 松开鼠标后看状态栏 / 面板是不是被拖过
                rc = _rect(self.bar)
                if rc != self.bar_last:
                    self.save_bar_pos(rc)
                if self.panel_shown:
                    rc = _rect(self.panel)
                    if self.panel_last and rc[:2] != self.panel_last[:2]:
                        st, bc = self._bar_cfg()
                        bc["panel_pos"] = [rc[0], rc[1]]
                        jdlib.save_state(st)
                    self.panel_last = rc


WIN = Win()
ICON = None


def quit_all():
    try:
        if ICON:
            ICON.stop()
    except Exception:
        pass
    try:
        os.remove(PORT_FILE)
    except OSError:
        pass
    os._exit(0)


def tray():
    global ICON
    import pystray
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    dr = ImageDraw.Draw(img)
    dr.rounded_rectangle((4, 4, 60, 60), 14, fill=(217, 119, 87, 255))
    dr.rectangle((18, 22, 46, 26), fill=(255, 255, 255, 255))
    dr.rectangle((18, 31, 46, 35), fill=(255, 255, 255, 255))
    dr.rectangle((18, 40, 38, 44), fill=(255, 255, 255, 255))

    def style_item(k):
        return pystray.MenuItem(STYLE_NAMES[k], lambda: WIN.set_style(k),
                                checked=lambda item: WIN.style == k, radio=True)
    ICON = pystray.Icon("jobdock", img, "今日投递", pystray.Menu(
        pystray.MenuItem("打开面板", lambda: WIN.show_panel(True), default=True),
        pystray.MenuItem("状态栏样式", pystray.Menu(*[style_item(k) for k in STYLES])),
        pystray.MenuItem("状态栏和面板放回默认位置", lambda: WIN.reset_bar()),
        pystray.MenuItem("退出", lambda: quit_all())))
    ICON.run_detached()


def all_done():
    s = state()
    return not [j for j in s["jobs"] if j["status"] == "pending"]


def main():
    nudge = "--nudge" in sys.argv
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\JobDigestPanel")
    if ctypes.windll.kernel32.GetLastError() == 183:                 # 已经有一个在跑
        if nudge and not all_done():
            p = jdlib.load_json(PORT_FILE, {})
            try:
                req = urllib.request.Request("http://127.0.0.1:%d/api/dock" % p["port"],
                                             data=b'{"op":"show"}', headers={"X-T": p["token"]})
                urllib.request.urlopen(req, timeout=5).read()
            except Exception as e:
                jdlib.log("面板提醒失败：%s" % e)
        return 0
    _ = mutex

    import webview
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    jdlib.save_json(PORT_FILE, {"port": port, "token": TOKEN})

    base = "http://127.0.0.1:%d" % port
    w, h = STYLES[WIN.style]
    # 先建的是主窗口：关掉状态栏 = 退出；面板被 Alt+F4 只是藏起来
    WIN.bar_win = webview.create_window(
        BAR_TITLE, "%s/bar?t=%s" % (base, TOKEN), width=w, height=h, x=-32000, y=-32000,
        frameless=True, on_top=True, easy_drag=False, background_color="#d97757",
        min_size=(100, 24))             # pywebview 默认最小 200×100，不放开的话状态栏下面会多出一截
    WIN.panel_win = webview.create_window(
        TITLE, "%s/?t=%s" % (base, TOKEN), width=PANEL_W, height=PANEL_H, x=-32000, y=-32000,
        frameless=True, on_top=True, easy_drag=False, background_color="#f5f4ed")

    def on_close():
        WIN.hide_panel()
        return False
    WIN.panel_win.events.closing += on_close
    if nudge and not all_done():
        WIN.want_nudge = 15

    def started():
        tray()
        threading.Thread(target=link_watch, daemon=True).start()
        WIN.loop()

    webview.start(started)
    quit_all()


if __name__ == "__main__":
    sys.exit(main() or 0)
