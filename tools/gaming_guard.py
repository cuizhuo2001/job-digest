# -*- coding: utf-8 -*-
"""游戏时别打扰：有这些进程在跑就算「在打游戏」。

面板用 gaming() 决定要不要整个藏起来；名单可在 config.json 的 dock.hide_when_running 里改。
"""
import subprocess, time

GAMES = ["league of legends.exe", "leagueclient.exe", "leagueclientux.exe",
         "valorant-win64-shipping.exe", "valorant.exe", "riotclientservices.exe"]


def gaming():
    try:
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True,
                             creationflags=0x08000000).stdout.decode("gbk", "replace").lower()
    except Exception:
        return False
    return any('"%s"' % g in out for g in GAMES if g != "riotclientservices.exe")


def wait_until_free(max_wait=10800, poll=60):
    t0 = time.time()
    while gaming() and time.time() - t0 < max_wait:
        time.sleep(poll)
