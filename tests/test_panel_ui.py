"""面板界面（要 Windows + Playwright）：起面板的本地服务，用无头浏览器点一遍。

    python -m pytest -m ui
"""
import os
import sys
import json
import threading

import pytest

from conftest import days, row, write_rows

pytestmark = pytest.mark.ui
if sys.platform != "win32":
    pytest.skip("面板只有 Windows 版", allow_module_level=True)
sync_api = pytest.importorskip("playwright.sync_api", reason="要装 Playwright")


@pytest.fixture
def panel(proj, cfg):
    write_rows(proj, "data/test_hk.csv", [
        row("w-%d" % i, "银行%d" % i, "Management Trainee Programme %d" % i, days(3 + i), "https://x.example/%d" % i)
        for i in range(8)])
    import jd_flags
    jd_flags.remember({"w-0": "Fluent in Cantonese is required.", "w-1": "Cantonese is an advantage."})
    import apply_popup as ap
    from http.server import ThreadingHTTPServer
    ap.open_file = lambda p: None
    ap.set_clip = lambda t: None
    srv = ThreadingHTTPServer(("127.0.0.1", 0), ap.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    errors = []
    with sync_api.sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1000, "height": 720})
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto("http://127.0.0.1:%d/?t=%s" % (srv.server_address[1], ap.TOKEN))
        pg.wait_for_selector(".row")
        yield pg
        b.close()
    srv.shutdown()
    assert not errors, errors


def state(proj):
    return json.load(open(os.path.join(proj, "state.json"), encoding="utf-8"))


def test_cards_show_flags(panel):
    t = panel.inner_text('.row[data-id="w-0"]')
    assert "要粤语" in t
    assert "粤语加分" in panel.inner_text('.row[data-id="w-1"]')
    assert panel.locator('.row[data-id="w-2"] .flag').count() == 0


def test_mark_applied_and_undo(panel, proj):
    panel.click('.row[data-id="w-2"] .hd')
    panel.click('.row.open [data-a="applied"]')
    panel.wait_for_selector('.row.applied[data-id="w-2"]')
    assert "w-2" in state(proj)["applied_ids"]
    panel.click('.row[data-id="w-2"] [data-a="undo"]')
    panel.wait_for_selector('.row.pending[data-id="w-2"]')
    assert "w-2" not in state(proj)["applied_ids"]


def test_settings_switches_save(panel, proj):
    panel.click("#tset")
    panel.wait_for_selector('[data-a="syue"]')
    assert not panel.is_checked('[data-a="syue"]') and not panel.is_checked('[data-a="spr"]')
    panel.click('[data-a="syue"]')
    panel.click('[data-a="ssave"]')
    panel.wait_for_function("()=>document.querySelector('#toast').textContent==='已保存'")
    c = json.load(open(os.path.join(proj, "config.json"), encoding="utf-8"))
    assert c["skip_need_cantonese"] is True and "skip_need_hkpr" not in c
