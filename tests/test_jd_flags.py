"""JD 里的「要粤语 / 要香港永居」：认得准不准、开关筛不筛、手动补录只标不筛。"""
import datetime

import pytest

from conftest import days, row, write_rows

# 都是真实 JD 里的句子（2026-10 卓的香港池）
CASES = [
    ("Proficiency in English and Cantonese, fluency in Putonghua will be an advantage", ["粤语"]),
    ("Proficiency in Cantonese, Mandarin and English is preferred.", ["粤语加分"]),
    ("Be fluent in English , knowledge of Mandarin/Cantonese would be an advantage", ["粤语加分"]),
    ("Mandarin, Cantonese) is preferred as the role will focus on clients", ["粤语加分"]),
    ("Proficiency in English and Chinese (Cantonese & Mandarin)", ["粤语"]),
    ("Cantonese is an advantage for cross-border engagements", ["粤语加分"]),
    ("7.优秀的双语沟通及中英文书写和口语能力，包括粤语及普通话。", ["粤语"]),
    ("能讲流利粤语者优先。", ["粤语加分"]),
    ("Applicants must be Hong Kong permanent residents.", ["永居"]),
    ("Non-permanent residents are welcome to apply.", []),
    ("Candidates must have the right to work in Hong Kong.", []),
    ("Fluent in Cantonese, Business Proficient in Mandarin and English", ["粤语"]),
    ("Fluency in English and Mandarin.", []),
    ("", []),
]


@pytest.mark.parametrize("text,want", CASES)
def test_flags_of(proj, text, want):
    import jd_flags
    assert jd_flags.flags_of(text) == want


def test_hard_beats_soft_in_same_jd(proj):
    import jd_flags
    jd = "Cantonese is a plus.\nMust be fluent in Cantonese and English."
    assert jd_flags.flags_of(jd) == ["粤语"]


def test_remember_labels_and_switches(cfg):
    import jd_flags
    jd_flags.remember({"w-a": "Fluent in Cantonese.", "w-b": "Hong Kong Permanent Resident only.", "w-c": ""})
    assert jd_flags.labels("w-a") == ["要粤语"]
    assert jd_flags.labels("w-b") == ["要香港永居"]
    assert jd_flags.labels("w-c") == [] and jd_flags.load()["w-c"]["ok"] is False   # 读不到 = 不打标签
    assert jd_flags.blocked_by("w-a", cfg) is None                                    # 默认只标不筛
    assert jd_flags.blocked_by("w-a", dict(cfg, skip_need_cantonese=True)) == "要粤语"
    assert jd_flags.blocked_by("w-b", dict(cfg, skip_need_cantonese=True)) is None
    assert jd_flags.blocked_by("w-b", dict(cfg, skip_need_hkpr=True)) == "要香港永居"


def test_pool_filter_respects_switch_and_manual(proj, cfg):
    import jd_flags
    import pool
    write_rows(proj, "data/test_hk.csv", [
        row("w-yue", "某行", "Management Trainee 2027", days(10), "https://a.example/1"),
        row("w-ok", "某行", "Graduate Programme 2027", days(10), "https://a.example/2"),
    ])
    write_rows(proj, "data/manual_hk.csv", [row("m-hk-20261019-1", "某店", "Retail Management Trainee", days(10))])
    jd_flags.remember({"w-yue": "Fluent in Cantonese is required.", "m-hk-20261019-1": "Fluent in Cantonese"})
    now = datetime.date.today()
    ids = lambda c: {r["id"] for r in pool.select(c, now)["hk"]["alive_rows"]}
    assert ids(cfg) == {"w-yue", "w-ok", "m-hk-20261019-1"}
    on = dict(cfg, skip_need_cantonese=True)
    assert ids(on) == {"w-ok", "m-hk-20261019-1"}          # 手动补录的是自己挑的：只标不筛
    assert pool.select(on, now)["stats"]["drop"]["粤语/永居"] == 1


def test_update_reads_only_new_rows(proj, cfg, monkeypatch):
    import jd_flags
    write_rows(proj, "data/test_hk.csv", [
        row("w-1", "某行", "Management Trainee", days(5), "https://a.example/1"),
        row("w-2", "某行", "Graduate Programme", days(5), "https://a.example/2"),
        row("w-3", "某行", "Graduate Programme B", days(5), ""),                 # 没链接的不读
    ])
    calls = []

    def fake(url):
        calls.append(url)
        return "Must be a Hong Kong permanent resident." if url.endswith("/1") else ""
    monkeypatch.setattr(jd_flags, "jd_text", fake)
    assert jd_flags.update(cfg) == (2, 1)
    assert sorted(calls) == ["https://a.example/1", "https://a.example/2"]
    assert jd_flags.labels("w-1") == ["要香港永居"]
    calls.clear()
    jd_flags.update(cfg)                                 # 读过的不再读；读失败的 3 天后才重试
    assert calls == []
