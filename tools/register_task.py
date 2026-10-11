#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
job-digest · 注册 / 刷新 Windows 任务计划（三个任务）

    JobDigest-Push    每天 schedule.push（默认 10:00）推简报
    JobDigest-Panel   登录后 30 秒起常驻面板（贴顶边）
    JobDigest-Nudge   每天 schedule.nudge（默认 10:40、16:00）让面板滑下来提醒

Python 路径、当前用户、仓库位置都是运行时取的，换电脑直接跑就行。
时间在 config.json 的 schedule 里改，改完重跑一次本脚本。

用法：
    python tools/register_task.py            注册（已存在则覆盖）
    python tools/register_task.py --show     看三个任务现在的状态
    python tools/register_task.py --run      立刻手动推一次简报
    python tools/register_task.py --remove   删掉三个任务
"""
import io
import os
import sys
import datetime
import subprocess
from xml.sax.saxutils import escape

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import jdlib  # noqa: E402

BIN = os.path.dirname(sys.executable)
PY = os.path.join(BIN, "python.exe")
PYW = os.path.join(BIN, "pythonw.exe")

TEMPLATE = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Author>%(user)s</Author><Description>%(desc)s</Description></RegistrationInfo>
  <Principals><Principal id="Author"><UserId>%(sid)s</UserId><LogonType>InteractiveToken</LogonType></Principal></Principals>
  <Settings>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <RestartOnFailure><Count>3</Count><Interval>PT10M</Interval></RestartOnFailure>
    <StartWhenAvailable>true</StartWhenAvailable>
    <ExecutionTimeLimit>%(limit)s</ExecutionTimeLimit>
  </Settings>
  <Triggers>%(trigger)s</Triggers>
  <Actions Context="Author"><Exec><Command>%(cmd)s</Command><Arguments>%(args)s</Arguments><WorkingDirectory>%(cwd)s</WorkingDirectory></Exec></Actions>
</Task>"""


def daily(hhmm):
    return ("<CalendarTrigger><StartBoundary>%sT%s:00</StartBoundary>"
            "<ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay></CalendarTrigger>"
            % ((datetime.date.today() + datetime.timedelta(days=1)).isoformat(), hhmm))
    # 起点放明天：放今天的话，已经过去的时间点可能被「错过就补跑」立刻触发一次


def current_sid():
    out = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True).stdout
    return out.decode("mbcs", "replace").strip().split(",")[-1].strip('"')


def tasks():
    sch = jdlib.load_json(jdlib.CFG_PATH, {}).get("schedule") or {}
    push_at = sch.get("push", "10:00")
    nudge_at = sch.get("nudge", ["10:40", "16:00"])
    user = "%s\\%s" % (os.environ.get("USERDOMAIN", "."), os.environ.get("USERNAME", ""))
    logon = "<LogonTrigger><Delay>PT30S</Delay><UserId>%s</UserId></LogonTrigger>" % escape(user)
    popup = os.path.join(HERE, "apply_popup.py")
    # (名字, 说明, 触发器, 程序, 参数, 时限)；面板常驻不能有时限（PT0S）
    return [
        ("JobDigest-Push", "今日投递 · 每日简报", daily(push_at), PY,
         '"%s"' % os.path.join(HERE, "push.py"), "PT20M"),
        ("JobDigest-Panel", "今日投递 · 面板（登录后常驻，贴顶边）", logon, PYW, '"%s"' % popup, "PT0S"),
        ("JobDigest-Nudge", "今日投递 · 面板提醒", "".join(daily(t) for t in nudge_at), PYW,
         '"%s" --nudge' % popup, "PT0S"),
    ]


def schtasks(*args):
    r = subprocess.run(["schtasks"] + list(args), capture_output=True)
    return r.returncode, (r.stdout + r.stderr).decode("mbcs", "replace").strip()


def main():
    names = [t[0] for t in tasks()]
    if "--show" in sys.argv:
        for n in names:
            print(schtasks("/Query", "/TN", n, "/FO", "LIST")[1], "\n")
        return 0
    if "--run" in sys.argv:
        print(schtasks("/Run", "/TN", "JobDigest-Push")[1])
        return 0
    if "--remove" in sys.argv:
        for n in names:
            print(n, schtasks("/Delete", "/TN", n, "/F")[1])
        return 0

    if not os.path.exists(PYW):
        print("找不到 %s —— 请用 python.exe（不是 py 启动器或虚拟环境的 shim）运行本脚本" % PYW)
        return 2
    sid, user = current_sid(), os.environ.get("USERNAME", "")
    bad = 0
    for name, desc, trig, cmd, args, limit in tasks():
        xml = TEMPLATE % dict(user=escape(user), desc=escape(desc), sid=sid, trigger=trig,
                              cmd=escape(cmd), args=escape(args), cwd=escape(jdlib.ROOT), limit=limit)
        p = os.path.join(os.environ.get("TEMP", jdlib.ROOT), name + ".xml")
        with io.open(p, "w", encoding="utf-16") as f:      # schtasks 要 UTF-16（带 BOM）
            f.write(xml)
        code, out = schtasks("/Create", "/TN", name, "/XML", p, "/F")
        print(name, out)
        bad += code != 0
    return 2 if bad else 0


if __name__ == "__main__":
    sys.exit(main() or 0)
