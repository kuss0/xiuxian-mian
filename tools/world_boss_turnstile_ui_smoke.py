"""Offline browser checks for Boss configuration; no live UI or game requests."""

import json
from pathlib import Path
import shutil

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]


def main():
    automation = {
        "world_boss_auto_enabled": True,
        "world_boss_turnstile_enabled": False,
        "world_boss_auto_account_limit": 1,
        "world_boss_auto_excluded_identity_ids": [22, 33],
        "world_boss_candidates": [
            {"identity_id":11,"label":"Online","configured_auto_enabled":True,"login":{"ready":True}},
            {"identity_id":22,"label":"Offline","configured_auto_enabled":False,"login":{"ready":False}},
        ],
    }
    requests = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=shutil.which("chromium"))
        try:
            for width, height in [(1280, 900), (390, 844)]:
                page = browser.new_page(viewport={"width":width,"height":height})

                def route_handler(route):
                    if route.request.url.endswith("/api/world-boss-miniapp-config"):
                        payload = route.request.post_data_json
                        requests.append(payload)
                        automation["world_boss_turnstile_enabled"] = payload["turnstile_enabled"]
                    route.fulfill(json={"ok":True,"miniapp":{"automation":automation}})

                page.route("http://offline.invalid/**", route_handler)
                page.goto("http://offline.invalid/")
                page.set_content('<html><head></head><body><div id="miniapp-home-body"></div></body></html>')
                for style in ("app.css", "ui_fixes.css", "ui_skin.css"):
                    page.add_style_tag(path=str(ROOT / "model/web/static/css" / style))
                page.add_script_tag(path=str(ROOT / "model/web/static/js/miniapp_ui.js"))
                page.evaluate("refreshMiniAppHome()")
                toggle = page.locator('[data-world-boss-turnstile-enabled]')
                toggle.check()
                assert not page.locator('[data-world-boss-candidate="22"]').is_checked()
                page.locator('[data-world-boss-config-save]').click()
                page.wait_for_function("!document.querySelector('[data-world-boss-config-save]').disabled")
                assert requests[-1]["turnstile_enabled"] is True
                assert set(map(int, requests[-1]["excluded_identity_ids"])) == {22, 33}
                assert toggle.is_checked()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.locator('[data-world-boss-auto]').screenshot(path=f"/tmp/xiuxian-boss-ui-{width}.png")
                page.close()
        finally:
            browser.close()
    print(json.dumps({"ok":True,"viewports":2,"save_requests":len(requests),"live_requests":0}))


if __name__ == "__main__":
    main()
