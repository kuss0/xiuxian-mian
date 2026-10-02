"""Project-owned browser corrections; the imported broker remains unchanged."""

import importlib.util
import json
import math
from pathlib import Path
import sys


def viewport_origin(metrics):
    """Firefox exposes the content area's screen origin, excluding browser chrome."""
    if not isinstance(metrics, dict):
        return None
    try:
        x = float(metrics["x"])
        y = float(metrics["y"])
        width = float(metrics["width"])
        height = float(metrics["height"])
    except (KeyError, TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in (x, y, width, height)):
        return None
    if x < 0 or y < 0 or width < 340 or height < 140:
        return None
    return {"screenX": x, "screenY": y}


def corrected_worker(base):
    class WorldBossBrowserWorker(base):
        def _install_widget(self, tab, site_key, action):
            # Read chrome geometry before loading the cross-origin frame. Never
            # assume page coordinates equal desktop coordinates (51px on this host).
            metrics = tab.run_js(
                "return JSON.stringify({x:window.mozInnerScreenX, "
                "y:window.mozInnerScreenY, width:innerWidth, height:innerHeight});"
            )
            try:
                metrics = json.loads(metrics) if isinstance(metrics, str) else metrics
            except ValueError:
                metrics = None
            tab._boss_viewport_origin = viewport_origin(metrics)
            tab.run_js(_WIDGET_SCRIPT % (json.dumps(site_key), json.dumps(action)))

        @staticmethod
        def _read_widget_rect(tab):
            origin = getattr(tab, "_boss_viewport_origin", None)
            if not origin:
                return None
            # A created container or loaded api.js is not a clickable challenge.
            # Wait for Turnstile's own interactive callback before the single click.
            if tab._attr("#__qyz_turnstile_broker_widget", "data-qyz-interactive") != "1":
                return None
            rect = tab.read_widget_rect()
            if not rect or rect.get("width", 0) < 300 or rect.get("height", 0) < 65:
                return None
            # Explicit normal widget: 300x65; checkbox centre is 20px from its left.
            # The upstream desktop click helper adds 30px, hence the 10px adjustment.
            return {**rect, **origin, "left": rect["left"] - 10.0, "height": 65.0}

        def _click_widget(self, tab, rect):
            return self._click_rect(rect)

    return WorldBossBrowserWorker


_WIDGET_SCRIPT = r"""
(() => {
  const sitekey = %s;
  const action = %s;
  const host = document.createElement('div');
  host.id = '__qyz_turnstile_broker_widget';
  host.style.cssText = 'position:fixed;left:20px;top:20px;width:300px;height:65px';
  document.body.appendChild(host);
  window.__qyzTurnstileBroker = {token:'', error:'', widgetId:null};
  const fail = code => {
    window.__qyzTurnstileBroker.error = String(code);
    host.dataset.qyzError = String(code);
  };
  const render = () => {
    try {
      host.dataset.qyzApi = 'ready';
      const id = window.turnstile.render(host, {
        sitekey, action, size:'normal', theme:'dark', appearance:'always',
        execution:'render', retry:'never', 'refresh-expired':'never',
        'refresh-timeout':'never',
        callback: token => {
          window.__qyzTurnstileBroker.token = String(token || '');
          host.dataset.qyzToken = String(token || '');
        },
        'before-interactive-callback': () => { host.dataset.qyzInteractive = '1'; },
        'after-interactive-callback': () => { host.dataset.qyzInteractive = '0'; },
        'error-callback': fail,
        'expired-callback': () => fail('expired'),
        'timeout-callback': () => fail('interactive_timeout')
      });
      window.__qyzTurnstileBroker.widgetId = id;
      host.dataset.qyzWidgetAssigned = '1';
    } catch (_) { fail('render_failed'); }
  };
  if (window.turnstile) { render(); return; }
  const script = document.createElement('script');
  script.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';
  script.async = false;
  script.onload = render;
  script.onerror = () => fail('script_failed');
  document.head.appendChild(script);
})();
"""


def main():
    path = Path("/app/upstream_world_boss_turnstile_broker.py")
    spec = importlib.util.spec_from_file_location("world_boss_broker_upstream", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.CamoufoxWorker = corrected_worker(module.CamoufoxWorker)
    module.main()


if __name__ == "__main__":
    main()
