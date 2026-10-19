"""面板「设置」：保存时别把用户手写的配置弄丢；密钥只进 secrets.json、不回传页面。"""
import os
import json

import pytest

from conftest import write_json


def load(proj, name):
    return json.load(open(os.path.join(proj, name), encoding="utf-8"))


def test_save_keeps_custom_and_unknown_keys(proj, cfg):
    import panel_settings
    raw = load(proj, "config.json")
    raw["directions"] = ["管培", {"name": "我的方向", "title": ["测试"], "quota_cn": 1}]
    raw["我的备注"] = "别动我"
    write_json(proj, "config.json", raw)
    panel_settings.save({"directions": ["投行", "我的方向"], "cities": ["香港", "深圳", "珠海"],
                         "channels": ["wechat"], "secrets": {"serverchan_key": "SCTsecret5678"},
                         "no_intern": False, "skip_cantonese": True, "skip_hkpr": False})
    c = load(proj, "config.json")
    assert c["directions"] == ["投行", {"name": "我的方向", "title": ["测试"], "quota_cn": 1}]
    assert c["我的备注"] == "别动我" and c["allowed_cities"] == ["香港", "深圳", "珠海"]
    assert c["notify"]["channels"] == ["wechat"]
    assert "实习" not in c["exclude_type_hk"] and "实习" not in c.get("exclude_type", [])
    assert c["skip_need_cantonese"] is True and "skip_need_hkpr" not in c
    assert os.path.exists(os.path.join(proj, "config.json.bak"))
    assert load(proj, "secrets.json") == {"serverchan_key": "SCTsecret5678"}
    assert "SCTsecret5678" not in json.dumps(load(proj, "config.json"))


def test_view_masks_secrets_and_reads_switches(proj, cfg):
    import panel_settings
    write_json(proj, "secrets.json", {"serverchan_key": "SCTsecret5678"})
    raw = load(proj, "config.json")
    raw["skip_need_hkpr"] = True
    write_json(proj, "config.json", raw)
    v = panel_settings.view()
    s = json.dumps(v, ensure_ascii=False)
    assert "SCTsecret5678" not in s and "5678" in s
    assert v["skip_hkpr"] is True and v["skip_cantonese"] is False


def test_turning_switch_off_removes_key(proj, cfg):
    import panel_settings
    panel_settings.save({"skip_cantonese": True})
    panel_settings.save({"skip_cantonese": False})
    assert "skip_need_cantonese" not in load(proj, "config.json")


def test_empty_directions_rejected(proj, cfg):
    import panel_settings
    with pytest.raises(ValueError):
        panel_settings.save({"directions": []})
