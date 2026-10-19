"""推送：多个渠道有一个成功就算成功；各渠道请求拼得对不对（不真发，换成假的网络请求）。"""
import json
import base64
import hashlib
import hmac
import datetime

import pytest

from conftest import days, row, write_json, write_rows


@pytest.fixture
def sent(monkeypatch, proj):
    import jdlib
    got = []

    def fake_open(req, proxy=None):
        got.append({"url": req.full_url, "body": req.data})
        return {"code": 0, "data": {}, "StatusCode": 0} if "pushplus" not in req.full_url else {"code": 200}
    monkeypatch.setattr(jdlib, "_open", fake_open)
    return got


def test_one_channel_ok_is_enough(cfg, monkeypatch):
    import jdlib
    monkeypatch.setitem(jdlib.CHANNELS, "wechat", lambda t, c, s: None)
    monkeypatch.setitem(jdlib.CHANNELS, "pushplus", lambda t, c, s: (_ for _ in ()).throw(RuntimeError("token 不对")))
    c = dict(cfg, notify={"channels": ["pushplus", "wechat"]})
    errs = jdlib.notify("今日投递\n正文", c)
    assert len(errs) == 1 and "token 不对" in errs[0]
    with pytest.raises(RuntimeError):
        jdlib.notify("x", dict(cfg, notify={"channels": ["pushplus"]}))


def test_serverchan_turbo_and_v3(proj, cfg, sent):
    import jdlib
    write_json(proj, "secrets.json", {"serverchan_key": "SCT123abc"})
    jdlib._serverchan("💼 投递简报\n第二行", cfg, False)
    assert sent[-1]["url"] == "https://sctapi.ftqq.com/SCT123abc.send"
    assert "title=%F0%9F%92%BC" in sent[-1]["body"].decode()
    write_json(proj, "secrets.json", {"serverchan_key": "sctp4567tabcdef"})
    jdlib._serverchan("x", cfg, False)
    assert sent[-1]["url"] == "https://4567.push.ft07.com/send/sctp4567tabcdef.send"


def test_pushplus_body(proj, cfg, sent):
    import jdlib
    write_json(proj, "secrets.json", {"pushplus_token": "tok"})
    jdlib._pushplus("标题\n内容", cfg, False)
    body = json.loads(sent[-1]["body"])
    assert body == {"token": "tok", "title": "标题", "content": "标题\n内容", "template": "txt"}


def test_feishu_signed(proj, cfg, sent):
    import jdlib
    write_json(proj, "secrets.json", {"feishu_webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/xxx",
                                      "feishu_secret": "sec"})
    jdlib._feishu("hello", cfg, False)
    body = json.loads(sent[-1]["body"])
    want = base64.b64encode(hmac.new(("%s\nsec" % body["timestamp"]).encode(), digestmod=hashlib.sha256).digest()).decode()
    assert body["sign"] == want and body["content"] == {"text": "hello"}


def test_missing_key_says_where_to_get_it(cfg):
    import jdlib
    with pytest.raises(RuntimeError, match="sct.ftqq.com"):
        jdlib._serverchan("x", cfg, False)


def test_env_var_overrides_secrets(proj, cfg, sent, monkeypatch):
    import jdlib
    monkeypatch.setenv("JD_SERVERCHAN_KEY", "SCTenv")
    jdlib._serverchan("x", cfg, False)
    assert sent[-1]["url"].endswith("/SCTenv.send")


def test_message_shows_jd_flags(proj, cfg):
    import jd_flags
    import pool
    import push
    write_rows(proj, "data/test_hk.csv", [
        row("w-1", "甲银行", "Management Trainee Programme", days(5), "https://x.example/1"),
        row("w-2", "乙银行", "Graduate Programme - Audit", days(6), "https://x.example/2"),
    ])
    jd_flags.remember({"w-1": "Fluent in Cantonese is required.", "w-2": "Cantonese is an advantage."})
    d = pool.select(cfg, datetime.date.today())
    msg = push.build_message(d, cfg, datetime.datetime.now(), dry=True)
    assert "投递简报" in msg and "⚠️要粤语" in msg and "· 粤语加分" in msg
