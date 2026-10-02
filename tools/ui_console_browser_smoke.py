"""Exercise the real console scripts with local fixtures and no live requests."""

import argparse
import base64
from copy import deepcopy
import json
import mimetypes
from pathlib import Path
import shutil
import sys
import tempfile
from urllib.parse import urlsplit
from unittest.mock import patch

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]


def compare_frames(browser, before, after):
    data = [Path(path).read_bytes() for path in (before, after)]
    if data[0] == data[1]:
        return {"pixels": 0, "max_delta": 0}
    page = browser.new_page()
    try:
        return page.evaluate("""async sources => {
            const images=await Promise.all(sources.map(async source=>{
                const image=new Image(); image.src=source; await image.decode(); return image;
            }));
            if(images[0].width!==images[1].width || images[0].height!==images[1].height)
                throw new Error('Screenshot dimensions changed');
            const canvas=document.createElement('canvas');
            canvas.width=images[0].width; canvas.height=images[0].height;
            const ctx=canvas.getContext('2d');
            const rgba=images.map(image=>{ctx.clearRect(0,0,canvas.width,canvas.height);
                ctx.drawImage(image,0,0); return ctx.getImageData(0,0,canvas.width,canvas.height).data;});
            let pixels=0, max_delta=0;
            for(let i=0;i<rgba[0].length;i+=4){
                let delta=0;
                for(let c=0;c<4;c++)delta=Math.max(delta,Math.abs(rgba[0][i+c]-rgba[1][i+c]));
                if(delta)pixels++;
                max_delta=Math.max(max_delta,delta);
            }
            return {pixels,max_delta};
        }""", ["data:image/png;base64," + base64.b64encode(value).decode("ascii") for value in data])
    finally:
        page.close()


def run(root, browser, snapshot, miniapp, output):
    from model import ui

    with patch.object(ui, "get_ui_snapshot", return_value=snapshot), \
            patch.object(ui, "UI_TEMPLATE_DIR", str(root / "model/web/pages")), \
            patch.object(ui, "_ui_static_asset_version", return_value="fixture"):
        html = ui.render_ui_page()
    results = []
    for width, height in ((1280, 900), (390, 844)):
        print(f"Checking {root.name} at {width}x{height}", flush=True)
        current = deepcopy(snapshot)
        errors, writes, external = [], [], []
        page = browser.new_page(viewport={"width": width, "height": height})
        page.on("pageerror", lambda error: errors.append(str(error)))

        def route_request(route):
            request = route.request
            url = urlsplit(request.url)
            if url.netloc != "console.test":
                external.append(request.url)
                route.abort()
            elif url.path == "/":
                route.fulfill(body=html, content_type="text/html")
            elif url.path.startswith("/static/") or url.path == "/favicon.png":
                asset = root / ("model/web" + url.path if url.path.startswith("/static/") else "favicon.png")
                route.fulfill(body=asset.read_bytes(), content_type=mimetypes.guess_type(asset)[0] or "application/octet-stream")
            elif request.method == "POST":
                payload = request.post_data_json
                writes.append({"path": url.path, "payload": payload})
                if url.path == "/api/toggle":
                    identity = next(i for i in current["identities"] if i["send_as_id"] == int(payload["send_as_id"]))
                    module = next(m for m in identity["modules"] if m["name"] == payload["module"])
                    module["enabled"] = bool(payload["enabled"])
                route.fulfill(json={"ok": True, "snapshot": current, "message": "Saved"})
            else:
                route.fulfill(json={"ok": True, "snapshot": current, "miniapp": miniapp, "official_schedules": []})

        page.route("**/*", route_request)
        page.goto("http://console.test/", wait_until="networkidle")
        page.wait_for_selector("#miniapp-home-body .miniapp-tabs")
        page.evaluate("document.fonts.ready")
        assert not errors, errors
        frames = {}

        def capture(name):
            path = output / f"{root.name}-{width}-{name}.png"
            page.screenshot(path=str(path), animations="disabled", full_page=True)
            frames[name] = str(path)

        capture("home")
        assert page.locator('[data-yinluo-panel]').count() == 1
        for name, selector, modal in (
            ("settings", "[data-open-basic-config]", "#basic-config-modal"),
            ("queue", "[data-open-pending-queue]", "#pending-queue-modal"),
            ("dungeon", "[data-open-dungeon]", "#dungeon-modal"),
            ("health", "[data-open-runtime-health]", "#runtime-health-modal"),
        ):
            page.locator(selector).first.click()
            page.wait_for_selector(modal + ".show")
            capture(name)
            page.evaluate("document.querySelectorAll('.modal-backdrop.show').forEach(el=>el.classList.remove('show'))")

        page.locator('[data-open-miniapp]').click()
        page.wait_for_selector('#miniapp-home-body .miniapp-tabs')
        page.locator('[data-miniapp-tab="status"]').click()
        capture('miniapp-status')
        page.locator('[data-miniapp-tab="entry"]').click()

        render_count = page.evaluate("""() => {
            let count=0;
            const original=renderPassiveInboxPanel;
            window.renderPassiveInboxPanel=function(){count++; return original();};
            renderAll();
            window.renderPassiveInboxPanel=original;
            return count;
        }""")
        page.locator('.compat-module-section > summary').click()
        toggle = page.locator('[data-toggle-module][data-module="阴罗宗"]').first
        toggle.scroll_into_view_if_needed()
        capture('modules')
        before_writes = len(writes)
        toggle.click()
        page.wait_for_function("!document.querySelector('[data-toggle-module][data-module=\"阴罗宗\"]').disabled")
        page.wait_for_timeout(100)
        assert len(writes) == before_writes + 1, writes
        assert writes[-1]["path"] == "/api/toggle"

        if width < 600:
            page.locator("#identity-select-mobile").select_option("990002")
        else:
            page.locator('[data-select-identity="990002"]').click()
        assert page.evaluate("appState.selectedId") == 990002
        page.wait_for_timeout(150)
        page.evaluate("document.activeElement.blur()")
        page.evaluate("applySnapshot(appState.snapshot,{keepFlash:true})")
        # A response that started before editing must not overwrite that edit.
        page.evaluate("""() => {
            window.savedFetch=window.fetch;
            window.fetch=()=>new Promise(resolve=>window.resolvePoll=resolve);
            window.pendingPoll=refreshState({silent:true,keepFlash:true});
        }""")
        page.locator("[data-open-basic-config]").click()
        field = page.locator('#basic-config-form [name="game_group_id"]')
        field.fill("-100999999")
        page.evaluate("""async () => {
            const stale=JSON.parse(JSON.stringify(appState.snapshot));
            stale.generated_at='STALE';
            resolvePoll({ok:true,status:200,json:async()=>({ok:true,snapshot:stale})});
            await pendingPoll;
            window.fetch=savedFetch;
        }""")
        assert field.input_value() == "-100999999"
        assert page.evaluate("appState.snapshot.generated_at") != "STALE"
        assert not errors, errors
        assert not external, external
        results.append({"width": width, "frames": frames, "passive_renders": render_count,
                        "writes": len(writes), "page_errors": errors, "external_requests": len(external),
                        "scroll_width": page.evaluate("document.documentElement.scrollWidth")})
        page.close()
        print(f"Passed interactions at {width}px", flush=True)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-root", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(ROOT))
    from tools.ui_http_smoke import _prepare_env

    with tempfile.TemporaryDirectory(prefix="xiuxian-browser-") as temporary:
        _prepare_env(0, Path(temporary))
        from model import state, ui

        for identity_id, name, sect in ((990001, "Console <Test>", "阴罗宗"), (990002, "Other", "天星宗")):
            state.ensure_identity_registered(identity_id)
            state.update_send_as_profile(identity_id, username="console_test", label=name, daohao=name,
                                         realm="化神后期", sect_name=sect, enabled=True)
        snapshot = ui.get_ui_snapshot()
        snapshot.update(generated_at="2026-10-02 12:00:00", config_needed=False)
        miniapp = ui.get_miniapp_status_snapshot(send_as_id=990001)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, executable_path=shutil.which("chromium"))
            try:
                result = {"current": run(ROOT, browser, snapshot, miniapp, args.output_dir)}
                assert all(row["passive_renders"] == 1 for row in result["current"])
                if args.baseline_root:
                    result["baseline"] = run(args.baseline_root.resolve(), browser, snapshot, miniapp, args.output_dir)
                    result["frame_differences"] = []
                    for current, baseline in zip(result["current"], result["baseline"]):
                        assert current["scroll_width"] <= baseline["scroll_width"]
                        for name in current["frames"]:
                            delta = compare_frames(browser, baseline["frames"][name], current["frames"][name])
                            # Chromium border antialiasing can vary by 1-2 levels on a few pixels.
                            assert delta["max_delta"] <= 2 and delta["pixels"] <= 64, (current["width"], name, delta)
                            result["frame_differences"].append(dict(width=current["width"], view=name, **delta))
                        print(f"Passed screenshot comparison at {current['width']}px", flush=True)
                    result["visual_match"] = True
            finally:
                browser.close()
        (args.output_dir / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
