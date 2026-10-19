"""关注公司查岗：适配器解析（假网页，不联网）、某家查失败 / 只查一家时别丢别家的岗位。"""
import os
import sys
import json

import pytest

from conftest import write_json

SEARCH = """
<span id="tile-search-results-label">Showing 1 to 3 of 3 Jobs</span>
<a href="/emergingtalent/job/Central-Investment-Banking-Graduate-Hong/1371/" class="jobTitle-link">Investment Banking - Graduate</a>
<a href="/emergingtalent/job/Central-Hang-Seng-Management-Trainee/1372/" class="jobTitle-link">Hang Seng Management Trainee (MT) Programme</a>
<a href="/emergingtalent/job/Central-Markets-Internship/1373/" class="jobTitle-link">Markets - Internship</a>
"""
DETAIL = "<p><b>Closing Date:</b> Mon Nov 30, 2026</p>"


def test_csbhtml_parses_brands_and_closing_date(proj, monkeypatch):
    import watch_hk
    pages = []

    def fake(url):
        pages.append(url)
        return SEARCH if "/search/" in url else DETAIL
    monkeypatch.setattr(watch_hk, "_get_html", fake)
    c = {"name": "汇丰 HSBC", "host": "apply.careers.hsbc.com", "path": "/emergingtalent",
         "locationsearch": "Hong Kong", "brands": {"Hang Seng": "恒生银行 Hang Seng"}}
    jobs = {j["key"]: j for j in watch_hk.csbhtml(c, [])}
    assert set(jobs) == {"1371", "1372", "1373"}
    assert jobs["1372"]["company"] == "恒生银行 Hang Seng" and "company" not in jobs["1371"]
    assert jobs["1371"]["deadline"] == "2026-11-30" and jobs["1371"]["type"] == "校招"
    assert jobs["1373"]["type"] == "实习" and jobs["1373"]["deadline"] == ""          # 实习不读详情页
    assert sum("/search/" in u for u in pages) == 1                                    # 一页就全了，不多翻


def test_moka_cities(proj):
    import watch_cn
    assert watch_cn._cities("Beijing, Shanghai, Shenzhen") == ["北京", "上海", "深圳"]
    assert watch_cn._cities("Shanghai Guangzhou, Guangdong Beijing") == ["北京", "上海", "广州"]
    assert watch_cn._cities("Hong Kong") == ["香港"] and watch_cn._cities("HK & Macau") == ["澳门"] and watch_cn._cities("Remote") == []


def test_moka_skips_quietly_without_playwright(proj, monkeypatch):
    import watch_cn
    import watch_hk
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    with pytest.raises(watch_hk.Skip):
        watch_cn.moka({"org": "pwc", "site": "1"}, [])


def _watchlist(proj, names):
    write_json(proj, "wl_test.json", {"companies": [{"name": n, "system": n, "industry": ""} for n in names]})


def _job(key, title="Graduate Programme 2027"):
    return {"key": key, "title": title, "type": "校招", "posted": "", "deadline": "", "locs": ["Hong Kong"],
            "url": "https://x.example/" + key}


def test_failed_or_skipped_company_keeps_old_rows(proj, cfg, monkeypatch):
    import pool
    import watch_hk
    _watchlist(proj, ["A", "B", "C"])
    c = dict(cfg, sources={"hk_watch": {"watchlist": "wl_test.json", "out": "data/hk_watch.csv"}})
    ok = {"A": lambda c, kw, want: [_job("a1")], "B": lambda c, kw, want: [_job("b1")],
          "C": lambda c, kw, want: [_job("c1")]}
    monkeypatch.setattr(watch_hk, "systems", lambda: ok)
    watch_hk.run(c)
    first = {r["id"]: r for r in pool.load_pool(os.path.join(proj, "data/hk_watch.csv"))}
    assert len(first) == 3

    def boom(c, kw, want):
        raise RuntimeError("网站挂了")

    def skip(c, kw, want):
        raise watch_hk.Skip("没装 Playwright")
    monkeypatch.setattr(watch_hk, "systems", lambda: dict(ok, B=boom, C=skip))
    msg, fails = watch_hk.run(c)
    rows = {r["id"]: r for r in pool.load_pool(os.path.join(proj, "data/hk_watch.csv"))}
    assert set(rows) == set(first)                                   # B 挂了、C 跳过：上次的岗位都还在
    assert len(fails) == 1 and "B" in fails[0] and "Playwright" in msg
    assert all(rows[k]["first_seen"] == first[k]["first_seen"] for k in rows)   # 不会被当成「新」再推


def test_only_flag_keeps_other_companies(proj, cfg, monkeypatch):
    import pool
    import watch_hk
    _watchlist(proj, ["A", "B"])
    c = dict(cfg, sources={"hk_watch": {"watchlist": "wl_test.json", "out": "data/hk_watch.csv"}})
    monkeypatch.setattr(watch_hk, "systems", lambda: {"A": lambda c, kw, want: [_job("a1")],
                                                      "B": lambda c, kw, want: [_job("b1")]})
    watch_hk.run(c)
    monkeypatch.setattr(watch_hk, "systems", lambda: {"A": lambda c, kw, want: [_job("a1"), _job("a2", "MT 2027")],
                                                      "B": lambda c, kw, want: []})
    watch_hk.run(c, only="A")
    rows = pool.load_pool(os.path.join(proj, "data/hk_watch.csv"))
    assert sorted(r["watch"] for r in rows) == ["A", "A", "B"]          # 只查 A，B 的岗位原样留着


def test_removed_company_rows_dropped(proj, cfg, monkeypatch):
    import pool
    import watch_hk
    _watchlist(proj, ["A", "B"])
    c = dict(cfg, sources={"hk_watch": {"watchlist": "wl_test.json", "out": "data/hk_watch.csv"}})
    monkeypatch.setattr(watch_hk, "systems", lambda: {"A": lambda c, kw, want: [_job("a1")],
                                                      "B": lambda c, kw, want: [_job("b1")]})
    watch_hk.run(c)
    _watchlist(proj, ["A"])                                             # 从关注名单里删了 B
    watch_hk.run(c)
    assert [r["watch"] for r in pool.load_pool(os.path.join(proj, "data/hk_watch.csv"))] == ["A"]


def test_watchlists_are_valid(proj):
    import watch_cn
    import watch_hk
    known = set(watch_cn.SYSTEMS)
    for f in ("watchlist_hk.json", "watchlist_cn.json"):
        comps = json.load(open(os.path.join(proj, f), encoding="utf-8"))["companies"]
        names = [x["name"] for x in comps]
        assert len(names) == len(set(names)), f + " 里公司重名"
        assert all(x["system"] in known for x in comps), [x for x in comps if x["system"] not in known]
