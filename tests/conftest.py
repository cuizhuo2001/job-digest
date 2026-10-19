"""
测试都在一个临时项目目录里跑：把 tools / presets / examples 拷过去，从那里 import。
jdlib 按自己文件所在的位置定项目根目录，所以测试读写的 config.json / state.json / data/ 全是临时的，
不会碰到你自己的配置和投递记录。也不联网：要联网的地方都换成了假的。

    python -m pytest              核心逻辑（Windows / Mac / Linux 都能跑，GitHub 每次推送自动跑）
    python -m pytest -m ui        面板界面（要 Windows + Playwright：pip install playwright && python -m playwright install chromium）
"""
import os
import sys
import json
import shutil
import datetime

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL_MODULES = {f[:-3] for f in os.listdir(os.path.join(REPO, "tools")) if f.endswith(".py")}
RESET = ["state.json", "secrets.json", "history.jsonl", "config.json.bak", "popup_today.json", "push.log",
         "data/jd_flags.json", "data/manual_hk.csv", "data/manual_cn.csv", "data/school_hk.csv", "data/school_cn.csv",
         "data/hk_watch.csv", "data/cn_watch.csv", "data/cn_watch_hk.csv", "data/test_hk.csv", "data/.refreshing"]


@pytest.fixture(scope="session")
def proj(tmp_path_factory):
    tmp = str(tmp_path_factory.mktemp("job-digest"))
    for d in ("tools", "presets", "examples", "templates"):
        shutil.copytree(os.path.join(REPO, d), os.path.join(tmp, d), ignore=shutil.ignore_patterns("__pycache__"))
    for f in ("config.example.json", "profile.example.json", "watchlist_hk.json", "watchlist_cn.json"):
        shutil.copy(os.path.join(REPO, f), os.path.join(tmp, f))
    os.makedirs(os.path.join(tmp, "data"))
    for m in list(sys.modules):
        if m in TOOL_MODULES:
            del sys.modules[m]
    sys.path.insert(0, os.path.join(tmp, "tools"))
    import jdlib
    assert os.path.normcase(jdlib.ROOT) == os.path.normcase(tmp), "没从临时目录导入，测试会碰到真实配置"
    return tmp


@pytest.fixture
def cfg(proj, monkeypatch):
    """每个测试开始前：示例配置 + 只用测试数据、关掉所有数据源和推送；清掉状态、密钥、标签缓存。返回生效的配置。"""
    for f in RESET:
        try:
            os.remove(os.path.join(proj, f))
        except OSError:
            pass
    for k in list(os.environ):
        if k.startswith("JD_"):
            monkeypatch.delenv(k)
    c = json.load(open(os.path.join(proj, "config.example.json"), encoding="utf-8"))
    c["pool_path"] = ["data/manual_cn.csv", "data/school_cn.csv", "data/cn_watch.csv"]
    c["pool_path_hk"] = ["data/test_hk.csv", "data/manual_hk.csv", "data/school_hk.csv", "data/hk_watch.csv"]
    c["sources"] = {k: {"enabled": False} for k in ("cn_import", "hk_watch", "cn_watch", "mail_import")}
    c["notify"] = {"channels": ["console"]}
    c["directions"] = ["管培", "审计税务"]
    write_json(proj, "config.json", c)
    import jd_flags
    jd_flags._MEM.clear()
    import pool
    write_rows(proj, "data/manual_cn.csv", [])          # 大陆池至少得有个文件（空的就行）
    return pool.load_config()


def write_json(proj, name, data):
    with open(os.path.join(proj, name), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def days(n):
    return (datetime.date.today() + datetime.timedelta(days=n)).isoformat()


def row(rid, company, title, deadline="", url="", city="香港", typ="校招", **kw):
    r = {"id": rid, "company": company, "title": title, "city": city, "deadline": deadline,
         "deadline_kind": "fixed" if deadline else "until_full", "deadline_text": deadline or "官网未列截止日",
         "apply_url": url, "type": typ, "industry": "", "tags": "", "score": 60, "first_seen": "", "reasons": ""}
    r.update(kw)
    return r


def write_rows(proj, name, rows):
    import pool
    pool.write_pool(os.path.join(proj, name), rows)
