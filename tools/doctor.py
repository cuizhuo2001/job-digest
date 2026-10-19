#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
job-digest · 自检：没收到推送、面板打不开、池子空了，先跑这个

    python tools/doctor.py

一项项查：Python 版本 → 依赖 → config / profile 能不能读 → 推送渠道填没填 → 定时任务在不在
→ 上次推送什么时候 → 各数据源多久没刷新 → 池子里还有多少能投 → 面板在不在跑。
每项给 ✓ / ⚠️ / ✗，有问题的附一句怎么修。只读，不改任何文件、不发推送（发测试在面板「设置」里点）。
"""
import os
import sys
import time
import datetime
import subprocess

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

BAD = []


def say(ok, item, detail="", fix=""):
    mark = {True: "✓", False: "✗", None: "⚠️"}[ok]
    print("%s %s%s" % (mark, item, ("：" + detail) if detail else ""))
    if fix and ok is not True:
        print("    → " + fix)
        BAD.append(item)


def main():
    print("今日投递 · 自检\n")
    v = sys.version_info
    say(v >= (3, 9), "Python %d.%d" % (v.major, v.minor), "", "要 3.9 以上，去 python.org 装新版")

    import jdlib
    import pool
    try:
        cfg = pool.load_config()
        say(True, "config.json", "能读")
    except Exception as e:
        say(False, "config.json", "%s: %s" % (type(e).__name__, e),
            "多半是逗号 / 引号写错了；最省事：在面板「设置」里改，或者把 config.json.bak 改回 config.json")
        return finish()

    if os.name == "nt":
        missing = []
        for mod, pkg in (("webview", "pywebview"), ("pystray", "pystray"), ("PIL", "Pillow"), ("win32clipboard", "pywin32")):
            try:
                __import__(mod)
            except Exception:
                missing.append(pkg)
        say(not missing, "面板依赖", "缺 " + "、".join(missing) if missing else "齐了",
            "pip install -r requirements.txt")

    chans = (cfg.get("notify") or {}).get("channels") or ["console"]
    sec = jdlib.secrets(cfg)
    need = {"wechat": ["serverchan_key"], "serverchan": ["serverchan_key"], "pushplus": ["pushplus_token"],
            "email": ["email_smtp", "email_user", "email_pass"], "feishu": ["feishu_webhook"],
            "telegram": ["telegram_token", "telegram_chat"], "console": []}
    lack = ["%s 缺 %s" % (c, "、".join(k for k in need.get(c, []) if not sec.get(k))) for c in chans
            if any(not sec.get(k) for k in need.get(c, []))]
    if chans == ["console"]:
        say(None, "推送渠道", "现在只打印、不推到手机", "面板「设置 → 推送到哪」勾一个（推荐微信），填 key 后点「发测试」")
    else:
        say(not lack, "推送渠道", "、".join(chans) + ("（" + "；".join(lack) + "）" if lack else ""),
            "面板「设置」里把 key 填上")

    st = jdlib.load_state()
    last = st.get("last_push_local")
    if last:
        try:
            age = (datetime.datetime.now() - datetime.datetime.strptime(last, "%Y-%m-%d %H:%M")).total_seconds() / 3600
        except ValueError:
            age = 0
        say(age < 30, "上次推送", last, "超过一天没推了：看 push.log 最后几行，再看下面的定时任务")
    else:
        say(None, "上次推送", "还没推过", "python tools/push.py 发一次真的")

    if os.name == "nt":
        for name in ("JobDigest-Push", "JobDigest-Panel", "JobDigest-Nudge"):
            r = subprocess.run(["schtasks", "/Query", "/TN", name], capture_output=True)
            say(r.returncode == 0, "定时任务 " + name, "在" if r.returncode == 0 else "没有",
                "python tools/register_task.py")

    import refresh
    src = cfg.get("sources") or {}
    for key, label, mod in refresh.SOURCES:
        o = src.get(key) or {}
        if not o.get("enabled", key in refresh.DEFAULT_ON):
            continue
        p = jdlib.resolve(o.get("out") or mod.DEFAULT_OUT)
        if not os.path.exists(p):
            say(None, label, "还没刷新过", "python tools/refresh.py --force")
            continue
        h = (time.time() - os.path.getmtime(p)) / 3600
        say(h < 48, label, "%.0f 小时前刷新" % h, "python tools/refresh.py --force；还不行就看 push.log 里的报错")

    wl = jdlib.load_json(jdlib.resolve("watchlist_cn.json"), {}).get("companies") or []
    if any(c.get("system") == "moka" for c in wl):
        try:
            import playwright.sync_api  # noqa: F401
            say(True, "Playwright", "装了（普华永道、毕马威能自动查）")
        except Exception:
            # 可选功能，不算问题：只提示一句
            say(None, "Playwright（可选）", "没装，普华永道、毕马威会跳过；要查就 pip install playwright，"
                                           "再 python -m playwright install chromium")

    try:
        d = pool.select(cfg, datetime.date.today())
        for k, lab in (("hk", "香港池"), ("cn", "大陆池")):
            if d.get(k):
                n = len(d[k]["alive_rows"])
                say(n >= 10, lab, "还能投 %d 个" % n,
                    "快见底了：面板「设置」多勾几个方向 / 城市，或「导入岗位清单」补学校发的岗位")
    except Exception as e:
        say(False, "排岗位", "%s: %s" % (type(e).__name__, e), "把这行报错发给维护者")

    port = jdlib.load_json(os.path.join(jdlib.ROOT, "popup_port.json"), {})
    alive = False
    if port.get("port"):
        try:
            import urllib.request
            req = urllib.request.Request("http://127.0.0.1:%d/api/bar" % port["port"], headers={"X-T": port.get("token", "")})
            urllib.request.urlopen(req, timeout=3).read()
            alive = True
        except Exception:
            pass
    if os.name == "nt":
        say(alive, "面板", "在跑" if alive else "没在跑",
            "pythonw tools/apply_popup.py；起不来的话先在任务管理器里结束残留的 python / pythonw 进程")
    return finish()


def finish():
    print("\n" + ("都正常。" if not BAD else "有 %d 项要看：%s" % (len(BAD), "、".join(BAD))))
    return 1 if BAD else 0


if __name__ == "__main__":
    sys.exit(main() or 0)
