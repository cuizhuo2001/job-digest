"""导入岗位清单（AI 回的 JSON）→ 分池、查重、存进去；刷新数据源的锁。"""
import os
import time
import datetime

from conftest import days, row, write_rows

AI_REPLY = """好的，这是整理好的清单：
```json
[
 {"company": "汇丰 HSBC", "title": "Graduate Programme 2027", "deadline": "%s", "url": "https://x.example/1", "type": "校招"},
 {"company": "某券商", "title": "暑期实习生", "deadline": "", "url": "", "type": "实习", "city": "深圳"},
 {"company": "甲银行", "title": "Management Trainee", "deadline": "2020-01-01", "url": ""},
 {"title": "没有公司名的不要"}
]
```""" % days(20)


def test_from_json_and_plan(proj, cfg):
    import import_list
    write_rows(proj, "data/test_hk.csv", [row("hkc-1", "汇丰 HSBC", "Graduate Programme 2027", days(20))])
    items = import_list.recognize(AI_REPLY)
    assert [x["company"] for x in items] == ["汇丰 HSBC", "某券商", "甲银行"]
    assert items[1]["type"] == "实习"
    p = {x["company"]: x for x in import_list.plan(items, cfg)}
    assert p["汇丰 HSBC"]["why"] == "已在池子里" and p["汇丰 HSBC"]["dup"]["id"] == "hkc-1"
    assert p["某券商"]["pool"] == "cn" and p["某券商"]["pick"] is False      # 实习：按规则默认不勾
    assert p["甲银行"]["why"] == "已过截止日"


def test_save_numbers_continue(proj, cfg):
    import import_list
    items = [{"company": "A", "title": "MT", "deadline": "", "url": "", "type": "校招", "pick": True, "pool": "hk"}]
    a = import_list.save(items, "测试", prefix="m")
    b = import_list.save(items, "测试", prefix="m")
    head = "m-hk-%s-" % datetime.date.today().strftime("%Y%m%d")
    assert a[0]["id"] == head + "1" and b[0]["id"] == head + "2"
    s = import_list.save(items, "学校邮件", prefix="s")
    assert s[0]["id"].startswith("s-hk-") and os.path.exists(os.path.join(proj, "data/school_hk.csv"))


def test_refresh_lock(proj, cfg):
    import refresh
    a = refresh._lock()
    assert a and refresh._lock() is None                  # 别的进程在刷：这边直接用现有数据
    old = time.time() - 3600
    os.utime(a, (old, old))                               # 锁放了一小时没删（进程被杀）：当它失效
    b = refresh._lock()
    assert b
    os.remove(b)


def test_due_any_only_enabled_sources(proj, cfg):
    import refresh
    assert refresh.due_any(cfg) is False                  # 测试配置里全关了
    c = dict(cfg, sources=dict(cfg["sources"], hk_watch={"enabled": True}))
    assert refresh.due_any(c) is True                     # 开了、又从没刷过
