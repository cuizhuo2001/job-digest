#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
job-digest · 学校就业中心邮件自动导入

每次推送前（refresh.py）看一眼 Outlook 收件箱：发件人在 config.sources.mail_import.senders 里的邮件
（比如城大商学院就业中心 cbcareer@cityu.edu.hk 每周的 e-Newsletter），把正文里的岗位表认出来
（认法见 import_list.py），新的加进 data/school_hk.csv / school_cn.csv。

- 处理过的邮件记在 data/mail_import.json，不会重复导
- 已在池子里的、过了截止日的不加；实习照加，但会被你的规则（exclude_type_hk 等）筛掉
- 跟其他自动来源一样过方向 / 关键词 / 城市筛选（id 是 s- 开头；手动挑的 m- 开头才不过筛选）
- 新导入的会出现在简报「🆕 新出现的岗位」里

需要 Windows + 经典版 Outlook（新版 Outlook 没有 COM 接口）且已登录学校邮箱；用的是 pywin32。

    python tools/import_mail.py --dry     只看会导入什么
    python tools/import_mail.py           导入
"""
import os
import sys
import time
import argparse
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import jdlib  # noqa: E402
import pool  # noqa: E402
import import_list  # noqa: E402

DEFAULT_OUT = "data/mail_import.json"
PR_SMTP = "http://schemas.microsoft.com/mapi/proptags/0x39FE001E"


def opts(cfg):
    o = dict((cfg.get("sources") or {}).get("mail_import") or {})
    o.setdefault("out", DEFAULT_OUT)
    o.setdefault("senders", [])
    o.setdefault("subject_keywords", [])
    o.setdefault("days", 30)
    return o


def outlook_inbox():
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    try:
        app = win32com.client.GetActiveObject("Outlook.Application")
        launched = False
    except Exception:
        app = win32com.client.Dispatch("Outlook.Application")        # 没开就拉起来（十几秒）
        launched = True
    inbox = app.GetNamespace("MAPI").GetDefaultFolder(6)
    if launched:                                    # 冷启动时收件箱会先是空的，等它同步
        for _ in range(36):
            if inbox.Items.Count:
                break
            time.sleep(5)
    return inbox


def sender_of(msg):
    try:
        v = msg.PropertyAccessor.GetProperty(PR_SMTP)
        if v:
            return str(v).strip().lower()
    except Exception:
        pass
    try:
        return str(msg.SenderEmailAddress or "").strip().lower()
    except Exception:
        return ""


def recent_mails(inbox, days):
    items = inbox.Items
    items.Sort("[ReceivedTime]", True)
    cutoff = datetime.datetime.now() - datetime.timedelta(days=days)
    for msg in items:
        try:
            if msg.Class != 43:                     # 只要邮件
                continue
            t = msg.ReceivedTime
            rt = datetime.datetime(t.year, t.month, t.day, t.hour, t.minute)
        except Exception:
            continue
        if rt < cutoff:
            break
        yield msg, rt


def run(cfg, dry=False):
    o = opts(cfg)
    senders = [s.lower() for s in o["senders"]]
    kws = [k.lower() for k in o["subject_keywords"]]
    if not senders:
        return "学校邮件：config 里还没填 sources.mail_import.senders", []
    state_path = jdlib.resolve(o["out"])
    st = jdlib.load_json(state_path, {"done": {}})
    today = datetime.date.today()
    total, mails, problems = [], 0, []
    for msg, rt in recent_mails(outlook_inbox(), int(o["days"])):
        try:
            eid, subj = msg.EntryID, msg.Subject or ""
        except Exception:
            continue
        if eid in st["done"]:
            continue
        frm = sender_of(msg)
        if not any(s in frm for s in senders) or (kws and not any(k in subj.lower() for k in kws)):
            continue
        try:
            body = msg.Body or ""
        except Exception as e:
            problems.append("读不到邮件正文：%s（%s）" % (subj[:30], e))
            continue
        items = import_list.plan(import_list.parse(body, today), cfg)
        for r in items:
            if r["why"].startswith("实习"):
                r["pick"] = True                    # 实习也收进来，交给你的筛选规则决定推不推
        picks = [r for r in items if r["pick"]]
        mails += 1
        note = "学校邮件 %s「%s」" % (rt.strftime("%m-%d"), subj[:40])
        if dry:
            print("📬 %s %s → 认出 %d 条，新的 %d 条" % (rt.strftime("%m-%d"), subj[:50], len(items), len(picks)))
            for r in picks:
                print("     %s %-4s %-10s %-26s %s" % (r["pool"], r["type"], r["deadline"] or "滚动", r["company"][:26], r["title"][:60]))
            continue
        added = import_list.save(picks, note, prefix="s") if picks else []
        total += added
        st["done"][eid] = {"date": rt.strftime("%Y-%m-%d"), "subject": subj[:80], "found": len(items), "added": len(added)}
    if not dry:
        jdlib.save_json(state_path, st)
    return "学校邮件 %d 封新邮件，导入 %d 个岗位" % (mails, len(total)), problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="只看会导入什么，不写文件")
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    msg, problems = run(pool.load_config(), a.dry)
    print(msg)
    for p in problems:
        print("  ✗", p)


if __name__ == "__main__":
    sys.exit(main() or 0)
