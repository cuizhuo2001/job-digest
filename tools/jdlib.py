# -*- coding: utf-8 -*-
"""
job-digest · 公共层：路径、日志、JSON 读写、运行状态、个人资料、推送。

三类文件分开放，别混：
    config.json   规则（人改：池子在哪、每天几家、排除哪些词、推送渠道）
    state.json    运行状态（程序写：已投 / 稍后 / 屏蔽 / 死链 / 求职信）
    profile.json  个人资料（简历路径、速查卡、求职信模板）
    secrets.json  推送密钥（也可以用环境变量，见 secrets()）
这四个都不进 git，仓库里只有 *.example.json。
"""
import io
import os
import sys
import hmac
import json
import time
import base64
import hashlib
import datetime
import urllib.parse
import urllib.request

if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CFG_PATH = os.path.join(ROOT, "config.json")
STATE_PATH = os.path.join(ROOT, "state.json")
PROFILE_PATH = os.path.join(ROOT, "profile.json")
SECRETS_PATH = os.path.join(ROOT, "secrets.json")
LOG = os.path.join(ROOT, "push.log")
HISTORY = os.path.join(ROOT, "history.jsonl")
TG_LIMIT = 3900

# 程序会写的键都在 state.json；pool.load_config() 读的时候合并进 cfg
STATE_DEFAULTS = {"applied_ids": [], "dead_ids": [], "later": {}, "blocked": {},
                  "blocked_companies": [], "cover_letters": {}}


def log(msg):
    line = "%s  %s" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line)
    try:
        with io.open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def load_json(path, default):
    try:
        with io.open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False, indent=1))
    os.replace(tmp, path)


def resolve(p):
    """相对路径按仓库根目录算；支持 ~。"""
    if not p:
        return p
    p = os.path.expanduser(p)
    return p if os.path.isabs(p) else os.path.normpath(os.path.join(ROOT, p))


def load_state():
    st = load_json(STATE_PATH, {})
    for k, v in STATE_DEFAULTS.items():
        if k not in st:
            st[k] = json.loads(json.dumps(v))
    return st


def save_state(st):
    save_json(STATE_PATH, st)


# ───────────────────────── 个人资料 ─────────────────────────

# 速查卡里默认露出来的字段（section 关键词, 字段名）；其余进「更多」。
# 跟 templates/填表速查卡.example.md 的写法对齐，profile.json 的 card_fields 可覆盖
CARD_FIELDS = {
    "hk": [["身份", "全名 English Full Name"], ["身份", "姓氏 Last Name / 姓"], ["身份", "名 First / Given Name"],
           ["联系", "邮箱（主）"], ["联系", "手机（香港）"], ["硕士", "学校（英）"], ["硕士", "专业（英）"],
           ["硕士", "毕业时间"], ["语言", "雅思总分"], ["签证", "标准英文答法"]],
    "cn": [["身份", "姓名（中文）"], ["联系", "邮箱（主）"], ["联系", "手机（大陆）"], ["硕士", "学校（中）"],
           ["硕士", "专业（中）"], ["硕士", "毕业时间"], ["本科", "学校（中）"], ["本科", "专业"],
           ["语言", "六级 CET-6"], ["大陆", "政治面貌"]],
}


def load_profile():
    """每次现读，改了 profile.json 不用重启面板。缺省值都指向 templates/ 里的示例。"""
    p = load_json(PROFILE_PATH, {})
    out = dict(p)
    out["resume"] = {k: resolve(v) for k, v in (p.get("resume") or {}).items()}
    out["card_path"] = resolve(p.get("card_path") or "templates/填表速查卡.example.md")
    out["cover_letter_dir"] = resolve(p.get("cover_letter_dir") or "cover_letters")
    out["cover_letter_template"] = resolve(p.get("cover_letter_template")
                                           or "templates/cover_letter.example.md")
    out["card_fields"] = p.get("card_fields") or CARD_FIELDS
    return out


# ───────────────────────── 推送 ─────────────────────────

def secrets(cfg):
    """环境变量优先，其次 secrets.json；Telegram 还可以借 cc-connect 的配置（可选）。"""
    s = load_json(SECRETS_PATH, {})
    env = os.environ.get
    out = {
        "telegram_token": env("JD_TELEGRAM_TOKEN") or s.get("telegram_token") or "",
        "telegram_chat": env("JD_TELEGRAM_CHAT") or s.get("telegram_chat") or "",
        "telegram_proxy": env("JD_TELEGRAM_PROXY") or s.get("telegram_proxy") or "",
        "feishu_webhook": env("JD_FEISHU_WEBHOOK") or s.get("feishu_webhook") or "",
        "feishu_secret": env("JD_FEISHU_SECRET") or s.get("feishu_secret") or "",
    }
    cc = (cfg.get("notify") or {}).get("cc_connect_config")
    if cc and not out["telegram_token"]:
        import re
        txt = io.open(resolve(cc), encoding="utf-8").read()
        m = re.search(r'token\s*=\s*"([^"]+)"', txt)
        c = re.search(r'allow_from\s*=\s*"([^"]+)"', txt)
        p = re.search(r'^\s*proxy\s*=\s*"([^"]*)"', txt, re.M)
        if m and c:
            out["telegram_token"], out["telegram_chat"] = m.group(1), c.group(1)
            out["telegram_proxy"] = out["telegram_proxy"] or (p.group(1).strip() if p else "")
    return out


def _open(req, proxy=None):
    handlers = [urllib.request.ProxyHandler({"http": proxy, "https": proxy})] if proxy else []
    with urllib.request.build_opener(*handlers).open(req, timeout=30) as r:
        return json.loads(r.read())


def _telegram(text, cfg, silent):
    s = secrets(cfg)
    if not s["telegram_token"] or not s["telegram_chat"]:
        raise RuntimeError("没配 Telegram：设 JD_TELEGRAM_TOKEN / JD_TELEGRAM_CHAT，或写进 secrets.json")
    data = urllib.parse.urlencode({"chat_id": s["telegram_chat"], "text": text,
                                   "disable_web_page_preview": "true",
                                   "disable_notification": "true" if silent else "false"}).encode()
    url = "https://api.telegram.org/bot%s/sendMessage" % s["telegram_token"]
    try:
        res = _open(urllib.request.Request(url, data=data))
    except Exception as e:
        if not s["telegram_proxy"]:
            raise
        log("  Telegram 直连失败（%s），改走代理 %s" % (type(e).__name__, s["telegram_proxy"]))
        res = _open(urllib.request.Request(url, data=data), s["telegram_proxy"])
    if not res.get("ok"):
        raise RuntimeError("Telegram 回绝：%s" % res)


def feishu_sign(secret, ts):
    """飞书自定义机器人「签名校验」：HmacSHA256(key=时间戳+\\n+密钥, msg=空) 再 base64。"""
    key = ("%s\n%s" % (ts, secret)).encode("utf-8")
    return base64.b64encode(hmac.new(key, digestmod=hashlib.sha256).digest()).decode("utf-8")


def _feishu(text, cfg, silent):
    s = secrets(cfg)
    if not s["feishu_webhook"]:
        raise RuntimeError("没配飞书：设 JD_FEISHU_WEBHOOK（群机器人 webhook 地址），或写进 secrets.json")
    body = {"msg_type": "text", "content": {"text": text}}
    if s["feishu_secret"]:
        ts = str(int(time.time()))
        body.update(timestamp=ts, sign=feishu_sign(s["feishu_secret"], ts))
    req = urllib.request.Request(s["feishu_webhook"], data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json; charset=utf-8"})
    res = _open(req)
    if res.get("code", res.get("StatusCode", 0)) != 0:
        raise RuntimeError("飞书回绝：%s" % res)


def _console(text, cfg, silent):
    print("\n" + "=" * 56 + "\n" + text + "\n" + "=" * 56)


CHANNELS = {"telegram": _telegram, "feishu": _feishu, "console": _console}


def notify(text, cfg, silent=False, dry=False):
    """按 config.json 的 notify.channels 逐个发；有一个发成功就算成功，全失败才抛错。"""
    if len(text) > TG_LIMIT:
        text = text[:TG_LIMIT] + "\n…（过长已截断）"
    if dry:
        return _console(text, cfg, silent)
    chans = (cfg.get("notify") or {}).get("channels") or ["console"]
    errors = []
    for ch in chans:
        fn = CHANNELS.get(ch)
        try:
            if not fn:
                raise RuntimeError("不认识的推送渠道 %r（可选：%s）" % (ch, "/".join(CHANNELS)))
            fn(text, cfg, silent)
            log("  ✓ %s" % ch)
        except Exception as e:
            log("  ✗ %s 推送失败：%s: %s" % (ch, type(e).__name__, e))
            errors.append("%s: %s" % (ch, e))
    if len(errors) == len(chans):
        raise RuntimeError("；".join(errors))
    return errors
