"""Exercise optional activity forms with local fixtures and intercepted requests."""
import json
import mimetypes
from pathlib import Path
import shutil
import sys
import tempfile
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from tools.ui_http_smoke import _prepare_env

    with tempfile.TemporaryDirectory(prefix="xiuxian-expedition-ui-") as temporary:
        _prepare_env(0, Path(temporary))
        from model import app_expeditions, state, ui

        for identity, username in ((990001, "captain"), (990002, "mate")):
            state.ensure_identity_registered(identity)
            state.update_send_as_profile(identity, username=username, label=username)
        snapshot = ui.get_ui_snapshot()
        snapshot["config_needed"] = False
        results = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, executable_path=shutil.which("chromium"))
            try:
                for width in (1280, 390):
                    errors, external, writes = [], [], []
                    page = browser.new_page(viewport={"width": width, "height": 844})
                    page.on("pageerror", lambda error: errors.append(str(error)))

                    def route_request(route):
                        request = route.request
                        url = urlsplit(request.url)
                        if url.netloc != "console.test":
                            external.append(request.url)
                            route.abort()
                        elif url.path == "/":
                            route.fulfill(body=ui.render_ui_page(), content_type="text/html")
                        elif url.path.startswith("/static/") or url.path == "/favicon.png":
                            asset = ROOT / ("model/web" + url.path if url.path.startswith("/static/") else "favicon.png")
                            route.fulfill(body=asset.read_bytes(), content_type=mimetypes.guess_type(asset)[0] or "application/octet-stream")
                        elif request.method == "POST":
                            payload = request.post_data_json
                            writes.append({"path": url.path, "payload": payload})
                            ok, message = True, "fixture"
                            if url.path == "/api/expedition-config":
                                ok, message = app_expeditions.ui_set_expedition_config(payload)
                                snapshot["replica"]["expeditions"] = app_expeditions.get_expedition_snapshot()
                            route.fulfill(json={"ok": ok, "message": message, "snapshot": snapshot})
                        else:
                            route.fulfill(json={"ok": True, "snapshot": snapshot})

                    page.route("**/*", route_request)
                    page.goto("http://console.test/", wait_until="networkidle")
                    page.locator("[data-open-dungeon]").first.click()
                    page.locator("details", has=page.locator("#expedition-config-form")).evaluate("el => el.open = true")
                    page.locator("[data-hunt-add]").click()
                    page.locator("[data-hunt-add]").click()
                    assert page.locator("[data-hunt-team]").count() == 2
                    page.locator("[data-hunt-team]").last.locator("[data-hunt-remove]").click()
                    assert page.locator("[data-hunt-team]").count() == 1
                    team = page.locator("[data-hunt-team]").last
                    team.locator("[data-hunt-name]").fill(f"窄屏<{width}>")
                    team.locator("[data-hunt-role]").select_option("primary")
                    team.locator("[data-hunt-member]").nth(0).select_option("990001")
                    team.locator("[data-hunt-member]").nth(1).select_option("990002")
                    team.locator("[data-hunt-sea]").nth(1).select_option("暗礁")
                    team.locator("[data-hunt-weekly]").check()
                    page.locator("[data-expedition-member=xuangu][value='990001']").check()
                    page.locator("[data-expedition-member=changfeng][value='990002']").check()
                    # Polling must preserve a dirty form, including its team order.
                    page.evaluate("renderDungeonModal()")
                    assert team.locator("[data-hunt-name]").input_value() == f"窄屏<{width}>"
                    overflow = page.locator("#expedition-config-form").evaluate("el => el.scrollWidth - el.clientWidth")
                    assert overflow <= 2, (width, overflow)
                    page.locator("#expedition-config-form [type=submit]").click()
                    page.wait_for_function("document.querySelector('#expedition-config-form').dataset.dirty !== '1'")
                    saved = next(item["payload"] for item in reversed(writes) if item["path"] == "/api/expedition-config")
                    assert saved["hunt_teams"][-1]["member_identity_ids"] == ["990001", "990002"]
                    assert saved["hunt_teams"][-1]["seas"] == ["外海", "暗礁", "外海"]
                    assert saved["hunt_teams"][-1]["auto_weekly"] is True
                    assert saved["xuangu"]["enabled"] is False and saved["changfeng"]["enabled"] is False
                    assert saved["xuangu"]["participant_identity_ids"] == ["990001"]
                    assert saved["changfeng"]["participant_identity_ids"] == ["990002"]
                    page.evaluate("document.querySelectorAll('.modal-backdrop.show').forEach(el => el.classList.remove('show'))")
                    page.locator("[data-open-storage-bag]").click()
                    page.locator("#storage-bag-transfer-open-btn").click()
                    panel = page.locator("#storage-bag-transfer-modal")
                    panel.locator("[data-storage-transfer-field=groupRole]").select_option("secondary")
                    panel.locator("[data-storage-transfer-field=manualText]").fill("灵石*10")
                    panel.locator("[data-storage-transfer-field=manualText]").press("Tab")
                    with page.expect_response("**/api/storage-bag-transfer-preview"):
                        panel.locator("[data-storage-transfer-preview]").click()
                    transfer = next(item["payload"] for item in reversed(writes) if item["path"] == "/api/storage-bag-transfer-preview")
                    assert transfer["group_role"] == "secondary"
                    assert not errors and not external, (errors, external)
                    results.append({"width": width, "overflow_px": overflow, "errors": errors, "external_requests": len(external)})
                    page.close()
                    state.set_hunt_teams([])
                    snapshot["replica"]["expeditions"] = app_expeditions.get_expedition_snapshot()
            finally:
                browser.close()
        print(json.dumps(results, ensure_ascii=False))


if __name__ == "__main__":
    main()
