# -*- coding: utf-8 -*-
"""
job-digest · 面板「设置」页的后端

把小白最容易写错的几样从 config.json / secrets.json 里拎出来做成勾选框：
方向、大陆城市、推送渠道（含密钥和「发一条测试」）、学校邮件导入、要不要推实习。

- 改 config.json 前先备份成 config.json.bak；没在设置页里出现的键原样保留（包括你手写的自定义方向）
- 密钥只写进 secrets.json；读出来给页面时只说「填没填」，不把值传出去
"""
import io
import os
import json
import shutil
from collections import OrderedDict

import jdlib

CITY_CHOICES = ["香港", "深圳", "广州", "上海", "北京", "杭州", "南京", "苏州", "成都", "武汉", "西安", "天津",
                "重庆", "厦门", "长沙", "青岛"]

# 推送渠道 → 要填的密钥（键, 中文名, 是不是密码类）
CHANNEL_FIELDS = OrderedDict([
    ("wechat", {"label": "微信（Server酱）", "help": "sct.ftqq.com 微信扫码登录，复制 SendKey",
                "fields": [("serverchan_key", "SendKey", True)]}),
    ("pushplus", {"label": "微信（PushPlus）", "help": "pushplus.plus 微信扫码登录，复制 token",
                  "fields": [("pushplus_token", "token", True)]}),
    ("email", {"label": "邮件", "help": "QQ 邮箱填 smtp.qq.com；密码填邮箱设置里生成的「授权码」",
               "fields": [("email_smtp", "SMTP 服务器", False), ("email_user", "邮箱地址", False),
                          ("email_pass", "授权码", True), ("email_to", "收件人（不填 = 发给自己）", False),
                          ("email_port", "端口（不填 = 465）", False)]}),
    ("feishu", {"label": "飞书", "help": "群设置 → 群机器人 → 自定义机器人，复制 webhook；开了签名校验就填密钥",
                "fields": [("feishu_webhook", "webhook 地址", True), ("feishu_secret", "签名密钥（可不填）", True)]}),
    ("telegram", {"label": "Telegram", "help": "@BotFather 建机器人拿 token；内地要填代理",
                  "fields": [("telegram_token", "token", True), ("telegram_chat", "chat id", False),
                             ("telegram_proxy", "代理（可不填）", False)]}),
])


def _load_raw():
    with io.open(jdlib.CFG_PATH, encoding="utf-8") as f:
        return json.load(f, object_pairs_hook=OrderedDict)


def _presets():
    path = jdlib.resolve("presets/商科.json")
    p = jdlib.load_json(path, {})
    out = []
    for k, v in p.items():
        if k.startswith("_"):
            continue
        hint = "、".join((v.get("title") or [])[:4]) if isinstance(v, dict) else ""
        out.append({"name": k, "hint": hint})
    return out


def _dir_name(d):
    return d if isinstance(d, str) else (d or {}).get("name", "")


def _mask(v):
    v = str(v or "")
    return "" if not v else ("••••" + v[-4:] if len(v) > 8 else "••••")


def view():
    cfg = _load_raw()
    sec = jdlib.load_json(jdlib.SECRETS_PATH, {})
    presets = _presets()
    pnames = set(p["name"] for p in presets)
    sel = [_dir_name(d) for d in cfg.get("directions") or []]
    custom = [n for n in sel if n and n not in pnames]
    cities = list(cfg.get("allowed_cities") or [])
    src = (cfg.get("sources") or {}).get("mail_import") or {}
    channels = list((cfg.get("notify") or {}).get("channels") or [])
    if "serverchan" in channels:                     # 同一个渠道的两个名字，页面上只显示 wechat
        channels = ["wechat" if c == "serverchan" else c for c in channels]
    return {
        "presets": presets, "selected": sel, "custom": custom,
        "cities": cities, "city_choices": CITY_CHOICES + [c for c in cities if c not in CITY_CHOICES],
        "channels": channels,
        "channel_defs": [{"key": k, "label": v["label"], "help": v["help"],
                          "fields": [{"key": f, "label": lab, "secret": s, "set": bool(sec.get(f)),
                                      "masked": _mask(sec.get(f)) if s else str(sec.get(f) or "")}
                                     for f, lab, s in v["fields"]]}
                         for k, v in CHANNEL_FIELDS.items()],
        "cc_connect": bool((cfg.get("notify") or {}).get("cc_connect_config")),
        "mail": {"enabled": bool(src.get("enabled")), "senders": list(src.get("senders") or [])},
        "no_intern": "实习" in (cfg.get("exclude_type_hk") or []) and "实习" in (cfg.get("exclude_type") or []),
        "skip_cantonese": bool(cfg.get("skip_need_cantonese")),
        "skip_hkpr": bool(cfg.get("skip_need_hkpr")),
    }


def save(d):
    """d = {directions: [名字], cities: [..], channels: [..], secrets: {键: 值（只传改了的）}, mail: {..}, no_intern}"""
    path = jdlib.CFG_PATH
    cfg = _load_raw()
    shutil.copyfile(path, path + ".bak")

    if "directions" in d:
        old = {_dir_name(x): x for x in cfg.get("directions") or []}
        names = [n for n in d["directions"] if n]
        if not names:
            raise ValueError("至少选一个方向（不想按方向筛就去 config.json 里把 directions 删掉）")
        cfg["directions"] = [old.get(n, n) for n in names]     # 原来写成对象的（自定义 / 改过名额的）原样保留

    if "cities" in d:
        cities = [c.strip() for c in d["cities"] if c and c.strip()]
        if not cities:
            raise ValueError("至少选一个城市")
        cfg["allowed_cities"] = list(OrderedDict.fromkeys(cities))

    if "channels" in d:
        chans = [c for c in d["channels"] if c in CHANNEL_FIELDS]
        notify = cfg.get("notify") or OrderedDict()
        notify["channels"] = chans or ["console"]
        cfg["notify"] = notify

    if "mail" in d:
        src = cfg.get("sources") or OrderedDict()
        m = src.get("mail_import") or OrderedDict()
        m["enabled"] = bool(d["mail"].get("enabled"))
        m["senders"] = [s.strip() for s in d["mail"].get("senders") or [] if s.strip()]
        src["mail_import"] = m
        cfg["sources"] = src

    if "no_intern" in d:
        for k in ("exclude_type_hk", "exclude_type"):
            cur = [x for x in cfg.get(k) or [] if x != "实习"]
            cfg[k] = cur + ["实习"] if d["no_intern"] else cur

    for key, ck in (("skip_cantonese", "skip_need_cantonese"), ("skip_hkpr", "skip_need_hkpr")):
        if key in d:
            if d[key]:
                cfg[ck] = True
            else:
                cfg.pop(ck, None)

    io.open(path + ".tmp", "w", encoding="utf-8").write(json.dumps(cfg, ensure_ascii=False, indent=1) + "\n")
    os.replace(path + ".tmp", path)

    new = {k: str(v).strip() for k, v in (d.get("secrets") or {}).items()
           if k in {f for c in CHANNEL_FIELDS.values() for f, _, _ in c["fields"]} and str(v).strip()}
    if new:
        sec = jdlib.load_json(jdlib.SECRETS_PATH, OrderedDict())
        sec.update(new)
        jdlib.save_json(jdlib.SECRETS_PATH, sec)
    return view()


def test(channels, cfg):
    """每个渠道单独发一条测试，逐个报结果（不像正式推送那样「一个成功就算成功」）。"""
    import datetime
    text = "今日投递 · 测试消息\n收到这条就说明「%s」能用了 👍\n（%s）" % (
        "、".join(channels), datetime.datetime.now().strftime("%m-%d %H:%M"))
    out = {}
    for ch in channels:
        fn = jdlib.CHANNELS.get(ch)
        try:
            if not fn:
                raise RuntimeError("不认识的渠道")
            fn(text.replace("「%s」" % "、".join(channels), "「%s」" % CHANNEL_FIELDS.get(ch, {}).get("label", ch)), cfg, False)
            out[ch] = "ok"
        except Exception as e:
            out[ch] = "%s: %s" % (type(e).__name__, str(e)[:160])
    return out
