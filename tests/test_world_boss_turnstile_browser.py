from types import SimpleNamespace

import pytest

from tools.world_boss_turnstile_browser import corrected_worker, viewport_origin


@pytest.mark.parametrize("metrics", [None, {}, {"x":0,"y":None},
    {"x":0,"y":float('nan'),"width":900,"height":849},
    {"x":0,"y":51,"width":0,"height":849},
    {"x":-1,"y":51,"width":900,"height":849}])
def test_unknown_geometry_never_clicks(metrics):
    assert viewport_origin(metrics) is None


def test_click_uses_viewport_not_screen_origin():
    origin = viewport_origin({"x":0,"y":51,"width":900,"height":849})
    worker = corrected_worker(object)()
    tab = SimpleNamespace(
        _boss_viewport_origin=origin,
        _attr=lambda *_: "1",
        read_widget_rect=lambda: {"left":20,"top":20,"width":300,"height":65},
    )
    rect = worker._read_widget_rect(tab)
    assert rect["screenX"] + rect["left"] + 30 == 40
    assert rect["screenY"] + rect["top"] + rect["height"] / 2 == 103.5
    clicks = []
    worker._click_rect = lambda r: clicks.append(r) or True
    assert worker._click_widget(tab, rect)
    assert clicks == [rect]


@pytest.mark.parametrize("ready", ["", "0"])
def test_wait_for_interactive_callback(ready):
    worker = corrected_worker(object)()
    tab = SimpleNamespace(_boss_viewport_origin={"screenX":0,"screenY":51},
                          _attr=lambda *_: ready)
    assert worker._read_widget_rect(tab) is None


def test_new_widget_resets_geometry():
    worker = corrected_worker(object)()
    scripts = []
    tab = SimpleNamespace(_boss_viewport_origin={"screenX":0,"screenY":51})
    tab.run_js = lambda script: scripts.append(script) or '{}'
    worker._install_widget(tab, "test-key", "test-action")
    assert tab._boss_viewport_origin is None
    assert worker._read_widget_rect(tab) is None
    assert "before-interactive-callback" in scripts[1]
    assert "retry:'never'" in scripts[1]
