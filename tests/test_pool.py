"""排岗位：方向、类型、城市、过期、重复、已投、手动补录 / 学校推荐的特殊规则。"""
import datetime

from conftest import days, row, write_json, write_rows

NOW = datetime.date.today()


def sel(cfg):
    import pool
    return pool.select(cfg, NOW)


def hk_ids(cfg):
    return [r["id"] for r in sel(cfg)["hk"]["alive_rows"]]


def test_directions_type_and_expiry(proj, cfg):
    write_rows(proj, "data/test_hk.csv", [
        row("w-mt", "甲银行", "Management Trainee Programme 2027", days(20)),
        row("w-audit", "乙会计师行", "Audit Associate - Graduate", days(20)),
        row("w-it", "丙公司", "Product Designer", days(20)),                        # 方向对不上
        row("w-intern", "甲银行", "Summer Internship 2027", days(20), typ="实习"),   # 默认不推实习
        row("w-old", "丁银行", "Management Trainee", days(-1)),                    # 已过截止
    ])
    d = sel(cfg)
    assert sorted(r["id"] for r in d["hk"]["alive_rows"]) == ["w-audit", "w-mt"]
    drop = d["stats"]["drop"]
    assert drop["方向不符"] == 1 and drop["岗位类型"] == 1 and drop["已过截止"] == 1


def test_nodir_mentions_soon_closing(proj, cfg):
    write_rows(proj, "data/test_hk.csv", [
        row("w-it", "丙公司", "Product Designer", days(3)),
        row("w-it2", "丙公司", "UX Designer", days(60)),
    ])
    d = sel(cfg)
    assert [r["id"] for r in d["hk"]["nodir"]] == ["w-it"]     # 方向没对上、但快截止的：简报里提一句


def test_dedupe_same_title_and_same_url(proj, cfg):
    write_rows(proj, "data/test_hk.csv", [
        row("w-1", "甲银行", "Management Trainee Programme", days(10), "https://x.example/job/1"),
        row("w-2", "甲银行", "Graduate Programme - Audit", days(10), "https://x.example/job/2"),
    ])
    write_rows(proj, "data/manual_hk.csv", [
        row("m-hk-20261019-1", "甲银行", "management trainee programme", days(10)),            # 同公司同标题
        row("m-hk-20261019-2", "甲银行 Bank", "Audit Graduate", days(10), "https://x.example/job/2/"),  # 同链接
    ])
    ids = hk_ids(cfg)
    assert len(ids) == 2 and sel(cfg)["stats"]["drop"]["重复"] == 2


def test_applied_elsewhere_counts_for_same_job(proj, cfg):
    write_rows(proj, "data/test_hk.csv", [row("w-1", "甲银行", "Management Trainee", days(10), "https://x.example/1")])
    write_rows(proj, "data/manual_hk.csv", [row("m-hk-20261019-1", "甲银行", "Management Trainee", days(10))])
    write_json(proj, "state.json", {"applied_ids": ["m-hk-20261019-1"]})
    import pool
    c = pool.load_config()
    assert hk_ids(c) == []                                        # 手动那条投过了，自动查到的同一个也别再推
    assert sel(c)["stats"]["drop"]["已投"] == 2


def test_manual_bypasses_rules_school_bypasses_directions(proj, cfg):
    write_rows(proj, "data/manual_hk.csv", [row("m-hk-20261019-1", "某科技", "Software Engineer", days(10), typ="实习")])
    write_rows(proj, "data/school_hk.csv", [
        row("s-hk-20261019-1", "某公司", "Product Designer", days(10)),
        row("s-hk-20261019-2", "某公司", "Summer Intern", days(10), typ="实习"),
    ])
    ids = hk_ids(cfg)
    assert "m-hk-20261019-1" in ids                               # 手动补录：类型 / 方向都不管
    assert "s-hk-20261019-1" in ids and "s-hk-20261019-2" not in ids   # 学校推荐：不看方向，类型照筛


def test_links_from_panel_override_pool(proj, cfg):
    write_rows(proj, "data/manual_hk.csv", [row("m-hk-20261019-1", "某公司", "Graduate Programme", days(10))])
    write_json(proj, "state.json", {"links": {"m-hk-20261019-1": "https://apply.example/1"}})
    import pool
    r = sel(pool.load_config())["hk"]["alive_rows"][0]
    assert r["apply_url"] == "https://apply.example/1"


def test_config_auto_created_from_example(proj, cfg):
    import os
    import jdlib
    import pool
    os.remove(jdlib.CFG_PATH)
    c = pool.load_config()                                        # 新用户忘了复制示例配置：照示例建一份
    assert os.path.exists(jdlib.CFG_PATH) and c.get("directions")
