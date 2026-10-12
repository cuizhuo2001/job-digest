#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
job-digest · 今日投递面板（贴屏幕顶边常驻，QQ 式）

pywebview（WebView2 内核）开一个无边框置顶窗口，页面在 apply_popup.html，
后端是只监听 127.0.0.1 的小服务（带随机 token，防别的网页乱 POST）。

- 贴边：默认贴顶边居中；鼠标碰顶部细边/把手停 0.2s 滑下，离开 0.6s 缩回；
  拖离顶边 = 普通窗口，拖回顶边 = 重新吸附
- 四态：待投 / 已投(applied_ids) / 稍后(later，明天排最前) / 屏蔽(blocked，带原因)
  稍后、屏蔽当场从同一个池子补一家；「再来 5 家」按栏补
- 统计：每次操作追加到 history.jsonl；已投/稍后/屏蔽记在 state.json
- 个人资料（简历、速查卡、求职信模板）读 profile.json，见 profile.example.json
- 打游戏（gaming_guard）时整个窗口藏起来
- 只支持 Windows（贴边靠 Win32 API，剪贴板靠 pywin32）

    pythonw apply_popup.py          常驻（开机自启用这个，静默贴边）
    pythonw apply_popup.py --nudge  已在跑就让它滑下来提醒；没在跑就起一个并提醒
"""
import io, os, re, sys, json, time, ctypes, secrets, datetime, threading, subprocess, webbrowser
import urllib.request
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import jdlib  # noqa: E402
import pool  # noqa: E402
import profile_card  # noqa: E402

HISTORY = jdlib.HISTORY
TODAY = os.path.join(jdlib.ROOT, "popup_today.json")
PORT_FILE = os.path.join(jdlib.ROOT, "popup_port.json")
PAGE_PATH = os.path.join(HERE, "apply_popup.html")
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


def me_file(key):
    """附件 part 里登记的文件路径（只认这里登记过的，别的路径不开）。"""
    c = me_load()["parts"]["files"]["cells"].get(key) or {}
    if not c.get("zh"):
        raise ValueError("「我的资料 → 附件」里还没填这个文件")
    return jdlib.resolve(c["zh"])


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
            "quota": {k: int(cfg.get("daily_quota_" + k, 5)) for k in ("hk", "cn")}}


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
            set_clip(os.path.normpath(me_file(d["key"])))
        else:
            job = find_job(str(d["id"]))
            prof = jdlib.load_profile()
            if d["what"] == "resume":
                set_clip(os.path.normpath(resume_of(job["pool"])))
            else:
                base = cl_info(job["id"], pool.load_config())["base"]
                set_clip(os.path.normpath(os.path.join(prof["cover_letter_dir"], base + ".pdf")))
        return {}
    elif path == "/api/open" and d.get("what") == "file":
        open_file(me_file(d["key"]))
        return {}
    elif path == "/api/open":
        job = find_job(str(d["id"]))
        w = d["what"]
        if w == "url" and job["url"]:
            webbrowser.open(job["url"])
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
    elif path == "/api/hold":
        DOCK.hold = bool(d["on"])
        return {}
    elif path == "/api/dock":
        if d["op"] == "hide":
            DOCK.slide(False)
        elif d["op"] == "show":
            DOCK.nudge()
        elif d["op"] == "quit":
            threading.Thread(target=quit_all, daemon=True).start()
        return {}
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
        if self.path.split("?")[0] == "/":
            self._send(io.open(PAGE_PATH, encoding="utf-8").read(), "text/html; charset=utf-8")
        elif self.path == "/api/state" and self.headers.get("X-T") == TOKEN:
            self._send(json.dumps(state(), ensure_ascii=False))
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


# ───────────────────────── 贴边窗口 ─────────────────────────

user32 = ctypes.windll.user32
SWP_SIZE = 0x0004 | 0x0010              # NOZORDER | NOACTIVATE（位置和大小一起改）


class Dock:
    # 缩起时窗口直接缩成这么大（CSS px），只装得下「今日投递 x/x」小标签。
    # ⚠️ 别用 SetWindowRgn 裁：WebView2 用 GPU 直接上屏，会绕过 region，露出整条面板背景
    TAB_H, TAB_W = 28, 300

    def __init__(self):
        self.hwnd = None
        self.window = None
        self.mode = "docked"            # docked / float
        self.shown = True
        self.full = None                # 展开时的窗口大小 (w, h)
        self.hold = False               # 页面里正在打字（屏蔽备注）时别缩
        self.sticky_until = 0           # 提醒模式：滑下来后这段时间内不自动缩
        self.hidden_for_game = False

    def rect(self):
        r = wintypes.RECT()
        user32.GetWindowRect(self.hwnd, ctypes.byref(r))
        return r.left, r.top, r.right, r.bottom

    def scale(self):
        try:
            return user32.GetDpiForWindow(self.hwnd) / 96.0
        except Exception:
            return 1.0

    def _set(self, x, y, w, h):
        user32.SetWindowPos(self.hwnd, 0, int(x), int(y), int(w), int(h), SWP_SIZE)

    def slide(self, show):
        if not self.hwnd or self.mode != "docked" or show == self.shown:
            return
        l, t, r, b = self.rect()
        mid = (l + r) / 2
        if show:
            w, h = self.full
            self._set(mid - w / 2, 0, w, h)
        else:
            self.full = (r - l, b - t)      # 记住展开时的大小（用户可能拖过）
            s = self.scale()
            self._set(mid - self.TAB_W * s / 2, 0, self.TAB_W * s, self.TAB_H * s)
        self.shown = show
        if show and self.window:
            try:
                self.window.evaluate_js("window.onShow && window.onShow()")
            except Exception:
                pass

    def nudge(self, secs=8):
        self.sticky_until = time.time() + secs
        if self.mode == "docked":
            self.slide(True)

    def place(self):
        w, h = self.full
        sw = user32.GetSystemMetrics(0)
        self._set((sw - w) / 2, 0, w, h)
        self.mode, self.shown = "docked", True

    def redock(self):
        if not self.shown:
            self.slide(True)
        l, t, r, b = self.rect()
        self.full = (r - l, b - t)
        self.place()
        self.sticky_until = time.time() + 3

    def in_trigger(self, cx, cy):
        """缩起时窗口本身就是那块小标签：鼠标在窗口里 = 碰到了。"""
        l, t, r, b = self.rect()
        return l <= cx < r and t <= cy < b + 2

    def loop(self):
        import gaming_guard
        games = (pool.load_config().get("dock") or {}).get("hide_when_running")
        if games:
            gaming_guard.GAMES = [g.lower() for g in games]
        while not self.hwnd:
            self.hwnd = user32.FindWindowW(None, TITLE)
            time.sleep(0.1)
        l, t, r, b = self.rect()
        self.full = (r - l, b - t)
        self.place()
        hover_t = leave_t = None
        last_game = 0
        pt = wintypes.POINT()
        while True:
            time.sleep(0.04)
            now = time.time()
            if now - last_game > 20:
                last_game = now
                g = gaming_guard.gaming()
                if g and not self.hidden_for_game:
                    user32.ShowWindow(self.hwnd, 0)                  # SW_HIDE
                    self.hidden_for_game = True
                elif not g and self.hidden_for_game:
                    user32.ShowWindow(self.hwnd, 4)                  # SW_SHOWNOACTIVATE
                    self.hidden_for_game = False
            if self.hidden_for_game:
                continue
            user32.GetCursorPos(ctypes.byref(pt))
            cx, cy = pt.x, pt.y
            l, t, r, b = self.rect()
            if user32.GetAsyncKeyState(0x01) & 0x8000:              # 左键按着：可能在拖
                if self.mode == "docked" and self.shown and t > 8:
                    self.mode = "float"
                hover_t = leave_t = None
                continue
            if self.mode == "float":
                if t <= 8:                                           # 拖回顶边松手 = 重新吸附
                    self._move(l, 0)
                    self.mode, self.shown = "docked", True
                continue
            if self.shown:
                inside = l <= cx < r and t <= cy < b
                if inside or self.hold or now < self.sticky_until:
                    leave_t = None
                else:
                    leave_t = leave_t or now
                    if now - leave_t > 0.6:
                        self.slide(False)
                        leave_t = None
            else:
                if self.in_trigger(cx, cy):
                    hover_t = hover_t or now
                    if now - hover_t > 0.2:
                        self.slide(True)
                        hover_t = None
                else:
                    hover_t = None


DOCK = Dock()
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
    ICON = pystray.Icon("jobdock", img, "今日投递", pystray.Menu(
        pystray.MenuItem("展开", lambda: DOCK.nudge(3), default=True),
        pystray.MenuItem("吸附回顶边", lambda: DOCK.redock()),
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

    DOCK.window = webview.create_window(
        TITLE, "http://127.0.0.1:%d/?t=%s" % (port, TOKEN), width=1000, height=720,
        frameless=True, on_top=True, easy_drag=False, background_color="#f5f4ed",
        min_size=(Dock.TAB_W, Dock.TAB_H))      # pywebview 默认最小 200×100，不放开的话缩起时标签下面会多出一截
    if nudge and not all_done():
        DOCK.sticky_until = time.time() + 15

    def started():
        tray()
        DOCK.loop()

    webview.start(started)
    quit_all()


if __name__ == "__main__":
    sys.exit(main() or 0)
