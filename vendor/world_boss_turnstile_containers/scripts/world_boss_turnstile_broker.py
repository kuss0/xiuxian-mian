#!/usr/bin/env python3
"""世界 Boss Turnstile token broker。"""

from __future__ import annotations

import argparse
import json
import os
import platform
import random
import shutil
import subprocess
import threading
import time
import uuid
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from queue import Full, Queue
from typing import Any
from urllib.parse import quote, urlparse, urlunparse

try:
    from DrissionPage import Chromium, ChromiumOptions
except Exception:  # pragma: no cover - Camoufox 专用镜像不安装 Chromium 依赖
    Chromium = None  # type: ignore[assignment]
    ChromiumOptions = None  # type: ignore[assignment]

try:
    from camoufox import DefaultAddons
    from camoufox.sync_api import Camoufox
except Exception:  # pragma: no cover - Chromium 专用镜像不安装 Camoufox
    Camoufox = None  # type: ignore[assignment,misc]
    DefaultAddons = None  # type: ignore[assignment,misc]

try:
    from pyvirtualdisplay import Display
except Exception:  # pragma: no cover - 由 broker 镜像提供
    Display = None  # type: ignore[assignment,misc]


DEFAULT_PAGE_URL = "https://asc.aiopenai.app/miniapp/xianxia-world-boss"
DEFAULT_SITE_KEY = "0x4AAAAAAEmIsCuTGsikqRH9"
DEFAULT_ACTION = "qyz_world_boss_begin"
DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_WORKERS = 2
DEFAULT_MAX_QUEUE = 8
DEFAULT_PROFILE_ROOT = "/var/lib/turnstile/profiles"
ALLOWED_HOST = "asc.aiopenai.app"


class BrokerFailure(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: str = "broker_failed",
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.details = details or {}


@dataclass
class BrokerJob:
    payload: dict[str, Any]
    future: Future
    submitted_at: float = field(default_factory=time.monotonic)


def _json_response(handler: BaseHTTPRequestHandler, status: int, data: dict) -> None:
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def _safe_page_url(value: object, start_param: str, init_data: str = "") -> str:
    raw = str(value or DEFAULT_PAGE_URL).strip()
    parsed = urlparse(raw)
    if parsed.scheme != "https" or parsed.hostname != ALLOWED_HOST:
        raise BrokerFailure("pageUrl 不是允许的世界 Boss 页面", code="invalid_page_url")
    from urllib.parse import parse_qsl, urlencode

    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if start_param:
        query["tgWebAppStartParam"] = start_param
    page = urlunparse(parsed._replace(query=urlencode(query)))
    if not init_data:
        return page
    fragment = "&".join(
        (
            f"tgWebAppData={quote(str(init_data), safe='')}",
            "tgWebAppVersion=7.10",
            "tgWebAppPlatform=tdesktop",
            f"tgWebAppStartParam={quote(str(start_param or ''), safe='')}",
        )
    )
    return f"{page}#{fragment}"


def _js_string(value: str) -> str:
    return json.dumps(str(value or ""), ensure_ascii=False)


class BrowserWorker:
    """一个独立 profile 的 Chromium worker；实例只由一个线程调用。"""

    def __init__(
        self,
        *,
        worker_id: int,
        profile_dir: str,
        display: Any = None,
        proxy: str = "",
        browser_path: str = "",
        gpu_mode: str = "software",
        display_mode: str = "xvfb",
        autostart: bool = True,
    ) -> None:
        self.worker_id = int(worker_id)
        self.profile_dir = str(profile_dir)
        self._browser = None
        self._proxy = str(proxy or "").strip()
        self._browser_path = str(browser_path or "").strip()
        normalized_gpu_mode = str(gpu_mode or "").strip().lower()
        self._gpu_mode = normalized_gpu_mode if normalized_gpu_mode in {
            "software",
            "angle-gl",
            "hardware",
        } else "software"
        normalized_display_mode = str(display_mode or "").strip().lower()
        self._display_mode = normalized_display_mode if normalized_display_mode in {
            "xvfb",
            "xorg",
            "none",
        } else "xvfb"
        self._browser_engine = "chromium"
        self._fingerprint_os = ""
        self._thread_affine = False
        self._dom_wait_seconds = 10.0
        self._gpu_status: dict[str, Any] = {"webgl": None, "vendor": "", "renderer": ""}
        self._last_diagnostic: dict[str, Any] = {}
        self._started_at = 0.0
        self._request_count = 0
        self._display = display
        Path(self.profile_dir).mkdir(parents=True, exist_ok=True)
        if autostart:
            self.start()

    def start(self) -> None:
        if self._browser is None:
            self._start_browser()

    def _clear_profile_singletons(self) -> None:
        """删除本 broker 上次异常退出留下的 Chromium profile 锁。"""
        # 每个 profile 只分配给一个 worker，且 broker 容器不会与其它
        # Chromium 进程共享它；容器重启后这些三个链接可能仍然存在，
        # 会让 DrissionPage 误判为已有浏览器并拒绝连接。
        for name in ("SingletonCookie", "SingletonLock", "SingletonSocket"):
            path = Path(self.profile_dir) / name
            try:
                if path.is_symlink() or path.exists():
                    path.unlink()
            except FileNotFoundError:
                continue
            except OSError:
                # 仅在确实无法清理时让 Chromium 返回原始启动错误。
                continue

    def _start_browser(self) -> None:
        if Chromium is None or ChromiumOptions is None:
            raise BrokerFailure(
                "Chromium 运行依赖未安装",
                code="browser_dependency_missing",
            )
        self._clear_profile_singletons()
        options = ChromiumOptions()
        options.headless(self._display_mode == "none")
        options.set_local_port(9222 + self.worker_id)
        if self._gpu_mode == "angle-gl":
            # Xvfb 的 GLX 路径会让 Chrome 直接禁用 GPU；ANGLE + Mesa
            # 至少提供可用 WebGL，具体是硬件还是 llvmpipe 由 chrome://gpu
            # 的运行时结果判定。
            for argument in (
                "--use-gl=angle",
                "--use-angle=gl",
                "--enable-gpu",
                "--enable-gpu-rasterization",
                "--ignore-gpu-blocklist",
                "--enable-zero-copy",
            ):
                options.set_argument(argument)
        elif self._gpu_mode == "hardware":
            # J4125 映射 /dev/dri 后让 Chrome 走 Mesa/EGL 硬件路径；
            # 不禁用软件回退，避免驱动初始化失败时页面直接空白。
            for argument in (
                "--use-gl=egl",
                "--enable-gpu",
                "--enable-gpu-rasterization",
                "--ignore-gpu-blocklist",
                "--enable-zero-copy",
            ):
                options.set_argument(argument)
        else:
            # Chromium 仍运行在 Xvfb 中，这里使用 SwiftShader 提供稳定的
            # 软件 WebGL，而不是完全关闭 GPU 管线。
            options.set_argument("--use-gl=swiftshader")
            options.set_argument("--enable-webgl")
        options.set_argument("--enable-webgl")
        options.set_argument("--disable-dev-shm-usage")
        options.set_argument("--no-sandbox")
        options.set_argument("--window-size=900,900")
        options.set_argument(f"--window-position={self.worker_id * 920},0")
        options.set_argument("--mute-audio")
        options.set_argument("--no-first-run")
        options.set_argument("--disable-sync")
        options.set_user_data_path(self.profile_dir)
        if self._proxy:
            options.set_proxy(self._proxy)
        path = self._browser_path or self._find_browser_path()
        if path:
            options.set_browser_path(path)
            self._browser_path = path
        try:
            self._browser = Chromium(options)
            self._started_at = time.time()
            print(
                f"Turnstile worker {self.worker_id} browser={self._browser_path or 'default'} "
                f"gpuMode={self._gpu_mode} displayMode={self._display_mode}",
                flush=True,
            )
        except Exception as exc:
            raise BrokerFailure(
                f"Chromium worker {self.worker_id} 启动失败: {type(exc).__name__}",
                code="browser_start_failed",
            ) from exc

    @staticmethod
    def _find_browser_path() -> str:
        for candidate in (
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
        ):
            if os.path.exists(candidate):
                return candidate
        return (
            shutil.which("google-chrome")
            or shutil.which("google-chrome-stable")
            or shutil.which("chromium")
            or shutil.which("chromium-browser")
            or ""
        )

    @property
    def gpu_status(self) -> dict[str, Any]:
        return dict(self._gpu_status)

    @property
    def ready(self) -> bool:
        return self._browser is not None

    @property
    def request_count(self) -> int:
        return self._request_count

    def reset(self) -> None:
        """在 worker 所属线程内重建浏览器，清理异常 challenge 状态。"""
        self.close()
        self.start()

    def close(self) -> None:
        if self._browser is not None:
            try:
                self._browser.quit()
            except Exception:
                try:
                    self._browser.close_tabs()
                except Exception:
                    pass
            self._browser = None

    def solve(
        self,
        *,
        page_url: str,
        site_key: str,
        action: str,
        start_param: str,
        init_data: str,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        if self._browser is None:
            raise BrokerFailure("浏览器未运行", code="browser_unavailable")
        self._request_count += 1
        self._last_diagnostic = {"stage": "new_tab"}
        tab = None
        started = time.monotonic()
        deadline = started + max(5.0, min(90.0, timeout_seconds))
        try:
            tab = self._browser.new_tab()
            self._last_diagnostic = {"stage": "navigate"}
            tab.get(_safe_page_url(page_url, start_param, init_data))
            self._last_diagnostic = {"stage": "wait_document"}
            try:
                tab.wait.doc_loaded(timeout=15)
            except Exception:
                time.sleep(2)
            self._last_diagnostic = {"stage": "read_gpu"}
            self._gpu_status = self._read_gpu_status(tab)
            # Cloudflare/Telegram 页面可能在导航完成后短暂保留空的 DOM。
            # 等待一个可注入的 document，避免 body/head 尚未创建时直接抛 JS 异常。
            self._last_diagnostic = {"stage": "wait_dom"}
            dom_deadline = min(deadline, time.monotonic() + self._dom_wait_seconds)
            while time.monotonic() < dom_deadline:
                if self._dom_ready(tab):
                    break
                time.sleep(0.25)
            self._last_diagnostic = {"stage": "prepare_document"}
            self._prepare_document(tab)
            self._last_diagnostic = {"stage": "install_context"}
            self._clear_sensitive_fragment(tab)
            self._install_telegram_context(tab, init_data)
            self._last_diagnostic = {"stage": "install_widget"}
            self._install_widget(tab, site_key, action)

            clicked = False
            self._last_diagnostic = {"stage": "poll_widget"}
            while time.monotonic() < deadline:
                self._last_diagnostic = {"stage": "poll_read_state"}
                state = self._read_state(tab)
                if state.get("error"):
                    raise BrokerFailure(
                        f"Turnstile callback error: {str(state['error'])[:80]}",
                        code="turnstile_error",
                    )
                if state.get("token"):
                    self._last_diagnostic = {}
                    return {
                        "turnstileToken": str(state["token"]),
                        "turnstileIdempotencyKey": str(uuid.uuid4()),
                        "expiresAt": time.time() + 240,
                        "latencyMs": round((time.monotonic() - started) * 1000.0, 1),
                    }
                if not clicked:
                    self._last_diagnostic = {"stage": "poll_read_rect"}
                    rect = self._read_widget_rect(tab)
                    if rect:
                        self._last_diagnostic = {"stage": "poll_click"}
                        if self._click_widget(tab, rect):
                            self._last_diagnostic = {"stage": "poll_clicked"}
                            clicked = True
                self._last_diagnostic = {"stage": "poll_sleep"}
                time.sleep(0.25)
            self._last_diagnostic = {"stage": "collect_diagnostic"}
            diagnostic = self._collect_diagnostic(tab)
            self._last_diagnostic = {"stage": "timeout", **diagnostic}
            raise BrokerFailure("Turnstile token 获取超时", code="turnstile_timeout")
        except BrokerFailure:
            raise
        except Exception as exc:
            raise BrokerFailure(
                f"浏览器 worker {self.worker_id} 执行失败: {type(exc).__name__}",
                code="browser_error",
            ) from exc
        finally:
            if tab is not None:
                try:
                    tab.close()
                except Exception:
                    pass

    @staticmethod
    def _dom_ready(tab: Any) -> bool:
        try:
            result = tab.run_js(
                "return Boolean(document && document.body && "
                "(document.querySelector('script[src*=\\\"turnstile/v0/api.js\\\"]') "
                "|| document.getElementById('startBtn') "
                "|| document.documentElement.dataset.qyzBrokerDocument === '1'));"
            )
            return bool(result)
        except Exception:
            return False

    @staticmethod
    def _clear_sensitive_fragment(tab: Any) -> None:
        try:
            tab.run_js(
                "try { history.replaceState(null, document.title, "
                "location.pathname + location.search); } catch (_) {}"
            )
        except Exception:
            pass

    @staticmethod
    def _install_telegram_context(tab: Any, init_data: str) -> None:
        if not init_data:
            return
        script = """
        (() => {
          const value = %s;
          try {
            if (window.Telegram && window.Telegram.WebApp) {
              window.Telegram.WebApp.initData = value;
            }
          } catch (err) { void err; }
          window.__qyzBrokerInitData = value;
        })();
        """ % _js_string(init_data)
        try:
            tab.run_js(script)
        except Exception:
            pass

    @staticmethod
    def _install_widget(tab: Any, site_key: str, action: str) -> None:
        script = """
        (() => {
          const sitekey = %s;
          const action = %s;
          window.__qyzTurnstileBroker = {token: "", error: "", widgetId: null};
          const root = document.documentElement;
          if (!root) return;
          let host = document.getElementById("__qyz_turnstile_broker_widget");
          if (!host) {
            host = document.createElement("div");
            host.id = "__qyz_turnstile_broker_widget";
            host.style.cssText = "position:fixed;left:20px;top:20px;z-index:2147483647;width:320px;height:100px;background:#101714;";
            (document.body || root).appendChild(host);
          }
          const render = () => {
            if (!window.turnstile || window.__qyzTurnstileBroker.widgetId !== null) return;
            try {
              host.dataset.qyzApi = "ready";
              window.__qyzTurnstileBroker.widgetId = window.turnstile.render(host, {
                sitekey,
                action,
                theme: "dark",
                appearance: "always",
                execution: "render",
                callback: token => {
                  const value = String(token || "");
                  window.__qyzTurnstileBroker.token = value;
                  host.dataset.qyzToken = value;
                },
                "error-callback": error => {
                  const value = String(error || "error");
                  window.__qyzTurnstileBroker.error = value;
                  host.dataset.qyzError = value;
                },
                "expired-callback": () => {
                  window.__qyzTurnstileBroker.error = "expired";
                  host.dataset.qyzError = "expired";
                }
              });
              host.dataset.qyzWidgetAssigned = "1";
            } catch (err) {
              const value = String(err && err.message || err || "render_failed");
              window.__qyzTurnstileBroker.error = value;
              host.dataset.qyzError = value;
            }
          };
          const waitForApi = (deadline) => {
            if (window.turnstile && typeof window.turnstile.render === "function") {
              try {
                // api.js 已由页面同步加载完成；此时再调用 ready() 会让
                // Turnstile 误判脚本使用了 async/defer，直接拒绝 render。
                render();
              } catch (err) {
                window.__qyzTurnstileBroker.error = String(err && err.message || err || "render_failed");
              }
              return;
            }
            if (Date.now() >= deadline) {
              window.__qyzTurnstileBroker.error = "api_unavailable";
              return;
            }
            window.setTimeout(() => waitForApi(deadline), 100);
          };
          // 页面自身通常已加载 api.js；重复注入会触发 Turnstile 的
          // "Remove async/defer" 保护并永远不给 token。
          const existingScripts = [...document.querySelectorAll(
            'script[src*="challenges.cloudflare.com/turnstile/v0/api.js"]'
          )];
          for (const existingScript of existingScripts) {
            // 某些 Chromium 版本会把外部脚本的 async/defer 状态反映到
            // DOM 属性，即使服务端 HTML 没有这两个 attribute；Turnstile
            // 的 explicit render 会因此直接报错。
            existingScript.removeAttribute("async");
            existingScript.removeAttribute("defer");
            existingScript.async = false;
            existingScript.defer = false;
          }
          if (!window.turnstile && existingScripts.length === 0) {
            const script = document.createElement("script");
            script.src = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
            script.onerror = () => { window.__qyzTurnstileBroker.error = "script_failed"; };
            (document.head || root).appendChild(script);
          }
          waitForApi(Date.now() + 15000);
        })();
        """ % (_js_string(site_key), _js_string(action))
        tab.run_js(script)

    @staticmethod
    def _read_state(tab: Any) -> dict[str, Any]:
        isolated_reader = getattr(tab, "read_turnstile_state", None)
        if callable(isolated_reader):
            try:
                result = isolated_reader()
                return result if isinstance(result, dict) else {}
            except Exception:
                return {}
        result = tab.run_js(
            "return JSON.stringify(window.__qyzTurnstileBroker || {});"
        )
        try:
            parsed = json.loads(result) if isinstance(result, str) else result
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}

    @staticmethod
    def _read_gpu_status(tab: Any) -> dict[str, Any]:
        try:
            result = tab.run_js(
                """
                return (() => {
                  try {
                    const canvas = document.createElement('canvas');
                    const gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
                    if (!gl) return JSON.stringify({webgl: false, vendor: '', renderer: ''});
                    const ext = gl.getExtension('WEBGL_debug_renderer_info');
                    return JSON.stringify({
                      webgl: true,
                      vendor: ext ? String(gl.getParameter(ext.UNMASKED_VENDOR_WEBGL) || '') : '',
                      renderer: ext ? String(gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) || '') : ''
                    });
                  } catch (_) {
                    return JSON.stringify({webgl: false, vendor: '', renderer: ''});
                  }
                })();
                """
            )
            parsed = json.loads(result) if isinstance(result, str) else result
            return parsed if isinstance(parsed, dict) else {"webgl": None, "vendor": "", "renderer": ""}
        except Exception:
            return {"webgl": None, "vendor": "", "renderer": ""}

    @staticmethod
    def _read_diagnostic(tab: Any) -> dict[str, Any]:
        """只保留控件状态，不记录 URL 查询、initData、Cookie 或 token。"""
        isolated_reader = getattr(tab, "read_safe_diagnostic", None)
        if callable(isolated_reader):
            try:
                result = isolated_reader()
                return result if isinstance(result, dict) else {}
            except Exception as exc:
                return {
                    "diagnosticError": type(exc).__name__,
                    "diagnosticMessage": " ".join(str(exc or "").split())[:240],
                }
        try:
            result = tab.run_js(
                """
                return (() => {
                  const state = window.__qyzTurnstileBroker || {};
                  const host = document.getElementById('__qyz_turnstile_broker_widget');
                  const frame = host ? host.querySelector('iframe') : null;
                  const response = document.querySelector(
                    'input[name="cf-turnstile-response"], textarea[name="cf-turnstile-response"]'
                  );
                  let frameHost = '';
                  let framePath = '';
                  try {
                    const parsed = frame && frame.src ? new URL(frame.src) : null;
                    frameHost = parsed ? parsed.hostname : '';
                    framePath = parsed ? parsed.pathname : '';
                  } catch (_) {}
                  return JSON.stringify({
                    locationHost: String(location.host || ''),
                    locationPath: String(location.pathname || '').slice(0, 120),
                    readyState: String(document.readyState || ''),
                    brokerDocument: document.documentElement
                      ? document.documentElement.dataset.qyzBrokerDocument === '1'
                      : false,
                    bodyChildren: document.body ? document.body.childElementCount : 0,
                    turnstileScriptCount: document.querySelectorAll(
                      'script[src*="challenges.cloudflare.com/turnstile"]'
                    ).length,
                    apiType: typeof window.turnstile,
                    widgetAssigned: state.widgetId !== null && state.widgetId !== undefined,
                    callbackError: String(state.error || '').slice(0, 80),
                    hostChildren: host ? host.childElementCount : 0,
                    hostText: host ? String(host.innerText || '').trim().slice(0, 120) : '',
                    iframePresent: Boolean(frame),
                    iframeTitle: frame ? String(frame.title || '').slice(0, 80) : '',
                    iframeHost: frameHost,
                    iframePath: framePath,
                    responseLength: response ? String(response.value || '').length : 0
                  });
                })();
                """
            )
            parsed = json.loads(result) if isinstance(result, str) else result
            return parsed if isinstance(parsed, dict) else {}
        except Exception as exc:
            message = " ".join(str(exc or "").split())[:240]
            return {
                "diagnosticError": type(exc).__name__,
                "diagnosticMessage": message,
            }

    def _collect_diagnostic(self, tab: Any) -> dict[str, Any]:
        result = self._read_diagnostic(tab)
        network_diagnostic = getattr(tab, "safe_network_diagnostic", None)
        if callable(network_diagnostic):
            try:
                result.update(network_diagnostic())
            except Exception:
                pass
        return result

    @staticmethod
    def _read_widget_rect(tab: Any) -> dict[str, float] | None:
        isolated_reader = getattr(tab, "read_widget_rect", None)
        if callable(isolated_reader):
            try:
                return isolated_reader()
            except Exception:
                return None
        result = tab.run_js(
            """
            const el = document.getElementById("__qyz_turnstile_broker_widget");
            if (!el) return "";
            const r = el.getBoundingClientRect();
            if (r.width < 250 || r.height < 50) return "";
            const borderX = ((window.outerWidth || 0) - (window.innerWidth || 0)) / 2;
            const titleBar = ((window.outerHeight || 0) - (window.innerHeight || 0)) - borderX;
            return JSON.stringify({
              left:r.left, top:r.top, width:r.width, height:r.height,
              screenX:(window.screenX || 0) + borderX,
              screenY:(window.screenY || 0) + titleBar
            });
            """
        )
        try:
            parsed = json.loads(result) if isinstance(result, str) else result
            return parsed if isinstance(parsed, dict) else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _click_rect(rect: dict[str, float]) -> bool:
        if platform.system() != "Linux" or not shutil.which("xdotool"):
            if platform.system() != "Windows":
                return False
            # Turnstile rejects synthetic CDP mouse events in a number of
            # browser configurations.  On Windows use the desktop input API
            # so the click is indistinguishable from a local user click.
            try:
                import ctypes

                screen_x = float(rect.get("screenX") or 0)
                screen_y = float(rect.get("screenY") or 0)
                x = int(screen_x + float(rect.get("left") or 0) + 30)
                y = int(screen_y + float(rect.get("top") or 0) + float(rect.get("height") or 0) / 2)
                user32 = ctypes.windll.user32
                user32.SetCursorPos(
                    x + random.randint(-8, 8),
                    y + random.randint(-5, 5),
                )
                time.sleep(random.uniform(0.1, 0.3))
                user32.SetCursorPos(x, y)
                time.sleep(random.uniform(0.1, 0.3))
                user32.mouse_event(0x0002, 0, 0, 0, 0)
                time.sleep(random.uniform(0.05, 0.15))
                user32.mouse_event(0x0004, 0, 0, 0, 0)
                return True
            except (AttributeError, OSError, TypeError, ValueError):
                return False
        screen_x = float(rect.get("screenX") or 0)
        screen_y = float(rect.get("screenY") or 0)
        x = int(screen_x + float(rect.get("left") or 0) + 30)
        y = int(screen_y + float(rect.get("top") or 0) + float(rect.get("height") or 0) / 2)
        try:
            subprocess.run(
                ["xdotool", "mousemove", str(max(0, x + random.randint(-8, 8))), str(max(0, y + random.randint(-5, 5)))],
                check=True,
                timeout=5,
                capture_output=True,
            )
            time.sleep(random.uniform(0.1, 0.3))
            subprocess.run(["xdotool", "mousemove", str(max(0, x)), str(max(0, y))], check=True, timeout=5, capture_output=True)
            subprocess.run(["xdotool", "click", "1"], check=True, timeout=5, capture_output=True)
            return True
        except (OSError, subprocess.SubprocessError):
            return False

    def _click_widget(self, _tab: Any, rect: dict[str, float]) -> bool:
        return self._click_rect(rect)

    def _prepare_document(self, _tab: Any) -> None:
        return


class _CamoufoxWaitAdapter:
    def __init__(self, page: Any) -> None:
        self._page = page

    def doc_loaded(self, timeout: float = 15) -> None:
        self._page.wait_for_load_state(
            "domcontentloaded",
            timeout=max(1, min(3000, int(float(timeout) * 1000))),
        )


class _CamoufoxTabAdapter:
    """把 Playwright Page 收敛为 broker 已使用的最小 tab 接口。"""

    def __init__(self, page: Any) -> None:
        self._page = page
        self.wait = _CamoufoxWaitAdapter(page)
        self._challenge_responses: list[dict[str, Any]] = []
        self._challenge_failures: list[dict[str, Any]] = []
        page.on("response", self._on_response)
        page.on("requestfailed", self._on_request_failed)

    @staticmethod
    def _safe_request_path(url: str) -> tuple[str, str]:
        try:
            parsed = urlparse(str(url or ""))
            return str(parsed.hostname or ""), str(parsed.path or "")[:160]
        except Exception:
            return "", ""

    def _on_response(self, response: Any) -> None:
        host, path = self._safe_request_path(response.url)
        if host != "challenges.cloudflare.com":
            return
        self._challenge_responses.append({
            "status": int(response.status),
            "path": path,
        })
        self._challenge_responses = self._challenge_responses[-8:]

    def _on_request_failed(self, request: Any) -> None:
        host, path = self._safe_request_path(request.url)
        if host != "challenges.cloudflare.com":
            return
        failure = str(request.failure or "")[:120]
        self._challenge_failures.append({"path": path, "failure": failure})
        self._challenge_failures = self._challenge_failures[-8:]

    def get(self, url: str) -> None:
        target = urlparse(url)

        def serve_broker_document(route: Any, request: Any) -> None:
            parsed = urlparse(str(request.url or ""))
            if (
                request.is_navigation_request()
                and request.frame == self._page.main_frame
                and parsed.scheme == target.scheme
                and parsed.hostname == target.hostname
                and parsed.path == target.path
            ):
                route.fulfill(
                    status=200,
                    content_type="text/html; charset=utf-8",
                    headers={"Cache-Control": "no-store"},
                    body=(
                        "<!doctype html><html lang=\"zh-CN\" "
                        "data-qyz-broker-document=\"1\"><head>"
                        "<meta charset=\"utf-8\"><meta name=\"viewport\" "
                        "content=\"width=device-width,initial-scale=1\">"
                        "<title>Turnstile verification</title></head><body></body></html>"
                    ),
                )
                return
            route.continue_()

        self._page.route("**/*", serve_broker_document)
        try:
            # 原页面在 head 中同步加载 Turnstile，Camoufox 下会停在 body
            # 创建之前。导航层提供同源最小文档，保留真实 hostname，同时
            # 让 broker 以 explicit 模式独立加载官方 API。
            self._page.goto(url, wait_until="domcontentloaded", timeout=10000)
        except Exception as exc:
            if type(exc).__name__ != "TimeoutError":
                raise
        finally:
            try:
                self._page.unroute("**/*", serve_broker_document)
            except Exception:
                pass

    @staticmethod
    def _expression(script: str) -> str:
        source = str(script or "").strip()
        if source.startswith("return "):
            source = source[7:].strip()
            if source.endswith(";"):
                source = source[:-1]
            return f"mw:(() => ({source}))()"
        return f"mw:(() => {{\n{source}\n}})()"

    def run_js(self, script: str) -> Any:
        # Camoufox 默认把 Playwright 隔离在沙箱世界；Turnstile 与 Telegram
        # 上下文必须在页面主世界内读取和设置。
        return self._page.evaluate(self._expression(script))

    def _attr(self, selector: str, name: str) -> str:
        try:
            return str(
                self._page.locator(selector).get_attribute(name, timeout=500) or ""
            )
        except Exception:
            return ""

    def read_turnstile_state(self) -> dict[str, Any]:
        token = self._attr("#__qyz_turnstile_broker_widget", "data-qyz-token")
        error = self._attr("#__qyz_turnstile_broker_widget", "data-qyz-error")
        assigned = self._attr(
            "#__qyz_turnstile_broker_widget", "data-qyz-widget-assigned"
        )
        return {"token": token, "error": error, "widgetId": assigned or None}

    def read_widget_rect(self) -> dict[str, float] | None:
        try:
            box = self._page.locator("#__qyz_turnstile_broker_widget").bounding_box(
                timeout=500
            )
        except Exception:
            return None
        if not box:
            return None
        return {
            "left": float(box.get("x") or 0),
            "top": float(box.get("y") or 0),
            "width": float(box.get("width") or 0),
            "height": float(box.get("height") or 0),
        }

    def read_safe_diagnostic(self) -> dict[str, Any]:
        host_selector = "#__qyz_turnstile_broker_widget"
        try:
            host = self._page.locator(host_selector)
            frame = host.locator("iframe")
            body = self._page.locator("body")
            return {
                "locationHost": urlparse(self._page.url).hostname or "",
                "locationPath": urlparse(self._page.url).path[:120],
                "readyState": "",
                "brokerDocument": self._attr("html", "data-qyz-broker-document") == "1",
                "bodyChildren": body.locator(":scope > *").count() if body.count() else 0,
                "turnstileScriptCount": self._page.locator(
                    'script[src*="challenges.cloudflare.com/turnstile"]'
                ).count(),
                "apiType": self._attr(host_selector, "data-qyz-api") or "unknown",
                "widgetAssigned": self._attr(host_selector, "data-qyz-widget-assigned") == "1",
                "callbackError": self._attr(host_selector, "data-qyz-error")[:80],
                "hostChildren": host.locator(":scope > *").count() if host.count() else 0,
                "hostText": (host.inner_text(timeout=500) if host.count() else "")[:120],
                "iframePresent": frame.count() > 0,
                "iframeTitle": (frame.get_attribute("title", timeout=500) or "")[:80]
                if frame.count()
                else "",
                "iframeHost": "",
                "iframePath": "",
                "responseLength": len(self._attr(host_selector, "data-qyz-token")),
            }
        except Exception as exc:
            return {
                "diagnosticError": type(exc).__name__,
                "diagnosticMessage": " ".join(str(exc or "").split())[:240],
            }

    def click_at(self, x: float, y: float) -> None:
        jitter_x = max(0.0, x + random.uniform(-8.0, 8.0))
        jitter_y = max(0.0, y + random.uniform(-5.0, 5.0))
        self._page.mouse.move(jitter_x, jitter_y, steps=random.randint(5, 11))
        time.sleep(random.uniform(0.10, 0.28))
        self._page.mouse.move(x, y, steps=random.randint(2, 5))
        time.sleep(random.uniform(0.08, 0.22))
        self._page.mouse.down()
        time.sleep(random.uniform(0.06, 0.14))
        self._page.mouse.up()

    def safe_network_diagnostic(self) -> dict[str, Any]:
        return {
            "challengeResponses": list(self._challenge_responses),
            "challengeFailures": list(self._challenge_failures),
        }

    def recover_blocked_document(self) -> None:
        self.run_js(
            """
            if (document.readyState === 'loading' && !document.body) {
              for (const script of document.querySelectorAll(
                'script[src*="challenges.cloudflare.com/turnstile"]'
              )) {
                script.remove();
              }
              if (!document.body && document.documentElement) {
                document.documentElement.appendChild(document.createElement('body'));
              }
              window.__qyzCamoufoxDocumentRecovered = true;
            }
            """
        )

    def close(self) -> None:
        self._page.close()


class _CamoufoxBrowserAdapter:
    def __init__(self, context: Any) -> None:
        self._context = context

    def new_tab(self) -> _CamoufoxTabAdapter:
        return _CamoufoxTabAdapter(self._context.new_page())


class CamoufoxWorker(BrowserWorker):
    """使用 Camoufox 的 Windows Firefox 指纹执行同一套 Turnstile 流程。"""

    def __init__(
        self,
        *,
        fingerprint_os: str = "windows",
        fingerprint_locale: str = "zh-CN",
        **kwargs: Any,
    ) -> None:
        self._camoufox_manager: Any = None
        self._requested_fingerprint_os = str(fingerprint_os or "windows")
        self._fingerprint_locale = str(fingerprint_locale or "zh-CN")
        super().__init__(autostart=False, **kwargs)
        self._browser_engine = "camoufox"
        self._fingerprint_os = self._requested_fingerprint_os
        self._thread_affine = True
        self._dom_wait_seconds = 3.0

    @staticmethod
    def _proxy_config(proxy_url: str) -> dict[str, str] | None:
        raw = str(proxy_url or "").strip()
        if not raw:
            return None
        parsed = urlparse(raw)
        if not parsed.scheme or not parsed.hostname:
            raise BrokerFailure("Camoufox proxy 格式无效", code="invalid_proxy")
        host = parsed.hostname
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        server = f"{parsed.scheme}://{host}"
        if parsed.port:
            server += f":{parsed.port}"
        result = {"server": server}
        if parsed.username:
            result["username"] = parsed.username
        if parsed.password:
            result["password"] = parsed.password
        return result

    def _start_browser(self) -> None:
        if Camoufox is None:
            raise BrokerFailure(
                "Camoufox 运行依赖未安装",
                code="browser_dependency_missing",
            )
        Path(self.profile_dir).mkdir(parents=True, exist_ok=True)
        proxy = self._proxy_config(self._proxy)
        options: dict[str, Any] = {
            "headless": False,
            "persistent_context": True,
            "user_data_dir": self.profile_dir,
            "os": self._requested_fingerprint_os,
            "locale": self._fingerprint_locale,
            "timezone_id": "Asia/Shanghai",
            "window": (900, 900),
            "humanize": 1.5,
            "main_world_eval": True,
            "enable_cache": True,
        }
        if DefaultAddons is not None:
            options["exclude_addons"] = [DefaultAddons.UBO]
        if proxy:
            options["proxy"] = proxy
        try:
            self._camoufox_manager = Camoufox(**options)
            context = self._camoufox_manager.__enter__()
            self._browser = _CamoufoxBrowserAdapter(context)
            self._browser_path = "camoufox-managed"
            self._started_at = time.time()
            print(
                f"Turnstile worker {self.worker_id} browser=camoufox "
                f"fingerprintOS={self._fingerprint_os} displayMode={self._display_mode}",
                flush=True,
            )
        except Exception as exc:
            self._camoufox_manager = None
            raise BrokerFailure(
                f"Camoufox worker {self.worker_id} 启动失败: {type(exc).__name__}",
                code="browser_start_failed",
            ) from exc

    def close(self) -> None:
        manager = self._camoufox_manager
        self._browser = None
        self._camoufox_manager = None
        if manager is not None:
            try:
                manager.__exit__(None, None, None)
            except Exception:
                pass

    def _click_widget(self, tab: Any, rect: dict[str, float]) -> bool:
        # Playwright mouse RPC 也可能被 Camoufox 的 challenge iframe 阻塞；
        # Xvfb 中浏览器窗口固定从 (0, 0) 开始，使用桌面级 xdotool 点击。
        click_rect = {
            **rect,
            "screenX": 0.0,
            "screenY": 0.0,
        }
        return self._click_rect(click_rect)

    @staticmethod
    def _read_widget_rect(tab: Any) -> dict[str, float] | None:
        # Camoufox 在 Turnstile iframe 创建后可能让 locator.bounding_box()
        # 永久等待；控件容器由 broker 固定放在 (20, 20) / 320x100。
        return {"left": 20.0, "top": 20.0, "width": 320.0, "height": 100.0}

    def _prepare_document(self, tab: Any) -> None:
        try:
            tab.recover_blocked_document()
        except Exception:
            pass


class BrokerWorkerPool:
    def __init__(
        self,
        *,
        worker_count: int,
        max_queue: int,
        profile_root: str,
        display: Any = None,
        proxy: str = "",
        browser_path: str = "",
        gpu_mode: str = "software",
        display_mode: str = "xvfb",
        browser_engine: str = "chromium",
        fingerprint_os: str = "windows",
        fingerprint_locale: str = "zh-CN",
    ) -> None:
        self._queue: Queue[BrokerJob | None] = Queue(maxsize=max(1, int(max_queue)))
        self._stop = threading.Event()
        self._states: list[dict[str, Any]] = []
        self._workers: list[BrowserWorker] = []
        self._threads: list[threading.Thread] = []
        normalized_engine = str(browser_engine or "chromium").strip().lower()
        self._browser_engine = normalized_engine if normalized_engine in {
            "chromium",
            "camoufox",
        } else "chromium"
        Path(profile_root).mkdir(parents=True, exist_ok=True)
        for worker_id in range(max(1, min(4, int(worker_count)))):
            profile_dir = str(Path(profile_root) / f"worker-{worker_id}")
            common_options = {
                "worker_id": worker_id,
                "profile_dir": profile_dir,
                "display": display,
                "proxy": proxy,
                "browser_path": browser_path,
                "gpu_mode": gpu_mode,
                "display_mode": display_mode,
            }
            if self._browser_engine == "camoufox":
                worker = CamoufoxWorker(
                    **common_options,
                    fingerprint_os=fingerprint_os,
                    fingerprint_locale=fingerprint_locale,
                )
            else:
                worker = BrowserWorker(**common_options)
            self._workers.append(worker)
            self._states.append({"worker": worker_id, "busy": False, "ready": worker.ready})
            thread = threading.Thread(target=self._run, args=(worker_id, worker), name=f"turnstile-worker-{worker_id}", daemon=True)
            thread.start()
            self._threads.append(thread)

    def submit(self, payload: dict[str, Any]) -> Future:
        future: Future = Future()
        try:
            self._queue.put_nowait(BrokerJob(payload=payload, future=future))
        except Full as exc:
            raise BrokerFailure("broker 请求队列已满", code="broker_queue_full") from exc
        return future

    @staticmethod
    def _failure_details(
        worker_id: int,
        worker: BrowserWorker,
        queue_wait_ms: float,
    ) -> dict[str, Any]:
        details: dict[str, Any] = {
            "workerId": worker_id,
            "requestCount": worker.request_count,
            "queueWaitMs": round(max(0.0, queue_wait_ms), 1),
        }
        diagnostic = dict(worker._last_diagnostic)
        if diagnostic:
            details["diagnostic"] = diagnostic
        return details

    def _reset_worker(self, worker_id: int, worker: BrowserWorker) -> None:
        state = self._states[worker_id]
        state["recovering"] = True
        state["ready"] = False
        try:
            worker.reset()
            state["ready"] = worker.ready
            state.pop("error", None)
        except BrokerFailure as exc:
            state["error"] = exc.code
        except Exception as exc:  # pragma: no cover - defensive recovery path
            state["error"] = f"{type(exc).__name__}"
        finally:
            state["recovering"] = False

    def _run(self, worker_id: int, worker: BrowserWorker) -> None:
        try:
            if not worker.ready:
                try:
                    worker.start()
                except BrokerFailure as exc:
                    self._states[worker_id]["error"] = exc.code
                    return
                finally:
                    self._states[worker_id]["ready"] = worker.ready
            while not self._stop.is_set():
                try:
                    job = self._queue.get(timeout=0.5)
                except Exception:
                    continue
                if job is None:
                    self._queue.task_done()
                    return
                queue_wait_ms = max(0.0, time.monotonic() - job.submitted_at) * 1000.0
                self._states[worker_id]["busy"] = True
                self._states[worker_id]["ready"] = worker.ready
                try:
                    if job.future.cancelled():
                        continue
                    result = worker.solve(
                        page_url=str(job.payload["pageUrl"]),
                        site_key=str(job.payload["siteKey"]),
                        action=str(job.payload["action"]),
                        start_param=str(job.payload.get("startParam") or ""),
                        init_data=str(job.payload.get("initData") or ""),
                        timeout_seconds=float(job.payload["timeoutSeconds"]),
                    )
                    result = {
                        **result,
                        "workerId": worker_id,
                        "requestCount": worker.request_count,
                        "queueWaitMs": round(queue_wait_ms, 1),
                    }
                    if not job.future.cancelled():
                        job.future.set_result(result)
                except BrokerFailure as exc:
                    exc.details.update(
                        self._failure_details(worker_id, worker, queue_wait_ms)
                    )
                    if not job.future.cancelled():
                        job.future.set_exception(exc)
                    if exc.code in {
                        "turnstile_timeout",
                        "turnstile_error",
                        "browser_error",
                        "browser_unavailable",
                    }:
                        self._reset_worker(worker_id, worker)
                except Exception as exc:  # pragma: no cover
                    failure = BrokerFailure(
                        f"worker 执行失败: {type(exc).__name__}",
                        code="worker_error",
                        details=self._failure_details(worker_id, worker, queue_wait_ms),
                    )
                    if not job.future.cancelled():
                        job.future.set_exception(failure)
                    self._reset_worker(worker_id, worker)
                finally:
                    self._states[worker_id]["busy"] = False
                    self._states[worker_id]["ready"] = worker.ready
                    self._queue.task_done()
        finally:
            if worker._thread_affine:
                worker.close()

    def health(self) -> dict[str, Any]:
        worker_states = []
        for index, item in enumerate(self._states):
            state = dict(item)
            if index < len(self._workers):
                state["requestCount"] = self._workers[index].request_count
            worker_states.append(state)
        return {
            "ok": bool(self._workers) and all(bool(item.get("ready")) for item in self._states),
            "workersReady": sum(1 for item in self._states if item.get("ready")),
            "workers": len(self._workers),
            "queueDepth": self._queue.qsize(),
            "workerStates": worker_states,
            "browserPath": self._workers[0]._browser_path if self._workers else "",
            "browserEngine": self._browser_engine,
            "gpuMode": self._workers[0]._gpu_mode if self._workers else "",
            "displayMode": self._workers[0]._display_mode if self._workers else "",
            "fingerprintOS": self._workers[0]._fingerprint_os if self._workers else "",
            "gpuStatus": self._workers[0].gpu_status if self._workers else {},
            "lastDiagnostic": dict(self._workers[0]._last_diagnostic) if self._workers else {},
        }

    def close(self) -> None:
        self._stop.set()
        for _ in self._threads:
            try:
                self._queue.put_nowait(None)
            except Full:
                break
        for thread in self._threads:
            thread.join(timeout=3)
        for worker in self._workers:
            if not worker._thread_affine:
                worker.close()


class BrokerHandler(BaseHTTPRequestHandler):
    server_version = "QyzTurnstileBroker/1.0"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            health = self.server.pool.health()  # type: ignore[attr-defined]
            _json_response(self, 200 if health["ok"] else 503, health)
            return
        _json_response(self, 404, {"ok": False, "error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/v1/turnstile/solve":
            _json_response(self, 404, {"ok": False, "error": "not_found"})
            return
        expected = str(getattr(self.server, "broker_secret", "") or "")
        if not expected or self.headers.get("X-Turnstile-Broker-Key", "") != expected:
            _json_response(self, 403, {"ok": False, "error": "forbidden"})
            return
        request_id = ""
        submitted_at = time.monotonic()
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 128 * 1024:
                raise BrokerFailure("请求体大小无效", code="invalid_body")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise BrokerFailure("请求体必须是 JSON 对象", code="invalid_body")
            request_id = str(payload.get("requestId") or "").strip()
            if len(request_id) < 8 or len(request_id) > 96:
                raise BrokerFailure("requestId 无效", code="invalid_request_id")
            site_key = str(payload.get("siteKey") or "").strip()
            action = str(payload.get("action") or "").strip()
            if site_key != str(getattr(self.server, "site_key", DEFAULT_SITE_KEY)):
                raise BrokerFailure("siteKey 不匹配", code="site_key_mismatch")
            if action != str(getattr(self.server, "action", DEFAULT_ACTION)):
                raise BrokerFailure("action 不匹配", code="action_mismatch")
            page_url = _safe_page_url(
                payload.get("pageUrl"),
                str(payload.get("startParam") or ""),
                str(payload.get("initData") or ""),
            )
            timeout_seconds = max(5.0, min(90.0, float(payload.get("timeoutSeconds") or self.server.timeout_seconds)))  # type: ignore[attr-defined]
            future = self.server.pool.submit({  # type: ignore[attr-defined]
                "pageUrl": page_url,
                "siteKey": site_key,
                "action": action,
                "startParam": str(payload.get("startParam") or ""),
                "initData": str(payload.get("initData") or ""),
                "timeoutSeconds": timeout_seconds,
            })
            try:
                result = future.result(timeout=timeout_seconds + 5.0)
            except FutureTimeoutError as exc:
                future.cancel()
                raise BrokerFailure(
                    "Turnstile token 获取超时",
                    code="turnstile_timeout",
                    details={
                        "workerWaitMs": round(
                            max(0.0, time.monotonic() - submitted_at) * 1000.0,
                            1,
                        )
                    },
                ) from exc
            _json_response(self, 200, {"ok": True, "requestId": request_id, **result})
            if getattr(self.server, "once", False):
                threading.Thread(target=self.server.shutdown, daemon=True).start()
        except BrokerFailure as exc:
            status = {
                "invalid_body": 400,
                "invalid_request_id": 400,
                "invalid_page_url": 400,
                "site_key_mismatch": 400,
                "action_mismatch": 400,
                "broker_queue_full": 429,
                "turnstile_timeout": 504,
                "browser_unavailable": 503,
                "browser_start_failed": 503,
                "browser_dependency_missing": 503,
                "browser_error": 503,
                "worker_error": 503,
                "display_failed": 503,
                "invalid_proxy": 400,
            }.get(exc.code, 502)
            response = {
                "ok": False,
                "error": exc.code,
                "message": str(exc)[:160],
            }
            if request_id:
                response["requestId"] = request_id
            response.update(exc.details)
            _json_response(self, status, response)
        except (TypeError, ValueError):
            _json_response(self, 400, {"ok": False, "error": "invalid_body"})
        except Exception as exc:  # pragma: no cover
            _json_response(self, 500, {"ok": False, "error": "internal_error", "message": type(exc).__name__})


def _start_display(display_mode: str) -> Any:
    if str(display_mode or "").strip().lower() in {"none", "xorg"}:
        return None
    if platform.system() != "Linux" or Display is None:
        return None
    try:
        display = Display(visible=0, size=(1920, 1080))
        display.start()
        return display
    except Exception as exc:
        raise BrokerFailure(f"虚拟显示器启动失败: {type(exc).__name__}", code="display_failed") from exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.getenv("QYZ_BROKER_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("QYZ_BROKER_PORT", "8192")))
    parser.add_argument("--once", action="store_true", help="成功处理一个请求后退出")
    parser.add_argument("--proxy", default=os.getenv("QYZ_BROKER_PROXY", ""))
    parser.add_argument("--browser-path", default=os.getenv("QYZ_BROKER_BROWSER_PATH", ""))
    parser.add_argument(
        "--browser-engine",
        choices=("chromium", "camoufox"),
        default=os.getenv("QYZ_BROKER_BROWSER_ENGINE", "chromium").strip().lower() or "chromium",
    )
    parser.add_argument(
        "--gpu-mode",
        choices=("software", "angle-gl", "hardware"),
        default=os.getenv("QYZ_BROKER_GPU_MODE", "software").strip().lower() or "software",
    )
    parser.add_argument(
        "--display-mode",
        choices=("xvfb", "xorg", "none"),
        default=os.getenv("QYZ_BROKER_DISPLAY_MODE", "xvfb").strip().lower() or "xvfb",
    )
    parser.add_argument(
        "--fingerprint-os",
        choices=("windows", "macos", "linux"),
        default=os.getenv("QYZ_BROKER_FINGERPRINT_OS", "windows").strip().lower() or "windows",
    )
    parser.add_argument(
        "--fingerprint-locale",
        default=os.getenv("QYZ_BROKER_FINGERPRINT_LOCALE", "zh-CN").strip() or "zh-CN",
    )
    parser.add_argument("--site-key", default=os.getenv("QYZ_BROKER_SITE_KEY", DEFAULT_SITE_KEY))
    parser.add_argument("--action", default=os.getenv("QYZ_BROKER_ACTION", DEFAULT_ACTION))
    parser.add_argument(
        "--secret",
        default=os.getenv(
            "TURNSTILE_BROKER_SECRET",
            os.getenv("WORLD_BOSS_TURNSTILE_BROKER_SECRET", ""),
        ),
    )
    parser.add_argument("--timeout", type=float, default=float(os.getenv("QYZ_BROKER_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))))
    parser.add_argument("--workers", type=int, default=int(os.getenv("QYZ_BROKER_WORKERS", str(DEFAULT_WORKERS))))
    parser.add_argument("--max-queue", type=int, default=int(os.getenv("QYZ_BROKER_MAX_QUEUE", str(DEFAULT_MAX_QUEUE))))
    parser.add_argument("--profile-root", default=os.getenv("QYZ_BROKER_PROFILE_ROOT", DEFAULT_PROFILE_ROOT))
    parser.add_argument("--allow-unsafe-no-secret", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    secret = str(args.secret or "").strip()
    if not secret and not args.allow_unsafe_no_secret:
        raise SystemExit("TURNSTILE_BROKER_SECRET 未配置；生产环境拒绝无鉴权启动")
    display = _start_display(args.display_mode)
    pool = None
    server = None
    try:
        pool = BrokerWorkerPool(
            worker_count=args.workers,
            max_queue=args.max_queue,
            profile_root=args.profile_root,
            display=display,
            proxy=args.proxy,
            browser_path=args.browser_path,
            gpu_mode=args.gpu_mode,
            display_mode=args.display_mode,
            browser_engine=args.browser_engine,
            fingerprint_os=args.fingerprint_os,
            fingerprint_locale=args.fingerprint_locale,
        )
        server = ThreadingHTTPServer((args.host, args.port), BrokerHandler)
        server.pool = pool  # type: ignore[attr-defined]
        server.once = bool(args.once)  # type: ignore[attr-defined]
        server.broker_secret = secret  # type: ignore[attr-defined]
        server.site_key = str(args.site_key or DEFAULT_SITE_KEY).strip()  # type: ignore[attr-defined]
        server.action = str(args.action or DEFAULT_ACTION).strip()  # type: ignore[attr-defined]
        server.timeout_seconds = max(5.0, min(90.0, float(args.timeout or DEFAULT_TIMEOUT_SECONDS)))  # type: ignore[attr-defined]
        print(
            f"Turnstile broker listening on {args.host}:{args.port} "
            f"workers={len(pool._workers)} browser={args.browser_path or 'auto'} "
            f"browserEngine={args.browser_engine} gpuMode={args.gpu_mode} "
            f"displayMode={args.display_mode} fingerprintOS={args.fingerprint_os}",
            flush=True,
        )
        server.serve_forever(poll_interval=0.5)
    finally:
        if server is not None:
            server.server_close()
        if pool is not None:
            pool.close()
        if display is not None:
            try:
                display.stop()
            except Exception:
                pass


if __name__ == "__main__":
    main()
