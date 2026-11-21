"""Browser engines. Playwright is required; patchright is an optional stealth backend."""

from __future__ import annotations

import importlib
import importlib.util
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

from agentbrowse.snapshot import SnapshotError, capture_page


class EngineError(RuntimeError):
    """The browser failed to start or a page action failed."""


class EngineUnavailable(EngineError):
    """The requested engine cannot be loaded."""


class Engine(Protocol):
    def start(
        self,
        headless: bool,
        proxy: dict | None,
        profile_dir: str | None,
        ignore_https_errors: bool,
    ) -> None: ...

    def goto(self, url: str) -> None: ...

    def snapshot(self) -> dict: ...

    def click(self, sel: str) -> None: ...

    def type(self, sel: str, text: str) -> None: ...

    def screenshot(self, path: str) -> None: ...

    def evaluate(self, js: str): ...

    def cookies(self) -> list: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class BrowserOptions:
    """Options passed through to Playwright (or patchright's matching API)."""

    headless: bool
    proxy: dict | None
    profile_dir: str | None
    ignore_https_errors: bool
    user_agent: str | None = None

    def __post_init__(self) -> None:
        if self.proxy is not None:
            object.__setattr__(self, "proxy", dict(self.proxy))

    def launch_kwargs(self) -> dict:
        """Arguments for `chromium.launch`. HTTPS flags belong on the context."""
        kwargs: dict = {"headless": self.headless}
        if self.proxy:
            kwargs["proxy"] = dict(self.proxy)
        return kwargs

    def context_kwargs(self) -> dict:
        kwargs: dict = {"ignore_https_errors": self.ignore_https_errors}
        if self.user_agent:
            kwargs["user_agent"] = self.user_agent
        return kwargs

    def persistent_kwargs(self) -> dict:
        """Arguments for `chromium.launch_persistent_context` besides the user-data dir."""
        kwargs: dict = {
            "headless": self.headless,
            "ignore_https_errors": self.ignore_https_errors,
        }
        if self.user_agent:
            kwargs["user_agent"] = self.user_agent
        if self.proxy:
            kwargs["proxy"] = dict(self.proxy)
        return kwargs


def require_http_url(url: str) -> str:
    """Phase 1 navigates with GET only, and only to http(s) URLs."""
    if not isinstance(url, str) or not url.strip():
        raise EngineError("URL is empty")
    cleaned = url.strip()
    parts = urlsplit(cleaned)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise EngineError(f"only http(s) GET navigation is supported, got {cleaned!r}")
    return cleaned


class _ChromiumEngine:
    api_module = ""
    install_hint = ""

    def __init__(self) -> None:
        self.options: BrowserOptions | None = None
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._console_errors: list[str] = []
        self._last_status: int | None = None

    @classmethod
    def available(cls) -> bool:
        package = cls.api_module.split(".", 1)[0]
        if not package:
            return False
        return importlib.util.find_spec(package) is not None

    def start(
        self,
        headless: bool,
        proxy: dict | None,
        profile_dir: str | None,
        ignore_https_errors: bool,
        user_agent: str | None = None,
    ) -> None:
        if self._playwright is not None or self._page is not None:
            raise EngineError("browser is already started")
        _validate_proxy(proxy)
        self.options = BrowserOptions(
            headless=bool(headless),
            proxy=proxy,
            profile_dir=str(profile_dir) if profile_dir else None,
            ignore_https_errors=bool(ignore_https_errors),
            user_agent=user_agent or None,
        )
        self._launch(self.options)

    def _load_api(self):
        if not self.available():
            raise EngineUnavailable(self.install_hint)
        try:
            return importlib.import_module(self.api_module)
        except Exception as exc:
            raise EngineUnavailable(f"{self.install_hint} ({exc})") from None

    def _launch(self, options: BrowserOptions) -> None:
        api = self._load_api()
        try:
            self._playwright = api.sync_playwright().start()
            browser_type = self._playwright.chromium
            if options.profile_dir:
                self._context = browser_type.launch_persistent_context(
                    options.profile_dir,
                    **options.persistent_kwargs(),
                )
                self._browser = getattr(self._context, "browser", None)
            else:
                self._browser = browser_type.launch(**options.launch_kwargs())
                self._context = self._browser.new_context(**options.context_kwargs())
            self._context.set_default_timeout(15_000)
            self._context.set_default_navigation_timeout(30_000)
            pages = self._context.pages
            self._page = pages[0] if pages else self._context.new_page()
            self._bind_page(self._page)
        except EngineUnavailable:
            self.close()
            raise
        except Exception as exc:
            self.close()
            raise EngineError(f"failed to start browser: {exc}") from None

    def _bind_page(self, page) -> None:
        def on_console(msg) -> None:
            try:
                if getattr(msg, "type", None) == "error":
                    self._console_errors.append(str(msg.text))
            except Exception:
                return

        def on_pageerror(err) -> None:
            self._console_errors.append(str(err))

        page.on("console", on_console)
        page.on("pageerror", on_pageerror)

    def goto(self, url: str) -> None:
        # page.goto issues a GET. Phase 1 has no form-submission helper.
        self._require_page()
        target = require_http_url(url)
        self._last_status = None
        try:
            response = self._page.goto(target, wait_until="domcontentloaded")
        except Exception as exc:
            raise EngineError(f"navigation failed for {target}: {exc}") from None
        self._last_status = getattr(response, "status", None) if response is not None else None

    def snapshot(self) -> dict:
        self._require_page()
        try:
            return capture_page(self._page, list(self._console_errors), status=self._last_status)
        except SnapshotError as exc:
            raise EngineError(str(exc)) from None

    def add_cookies(self, cookies: list) -> None:
        """Apply cookies from a caller-supplied jar. Empty input is a no-op."""
        self._require_page()
        if not cookies:
            return
        if self._context is None:
            raise EngineError("browser is not started")
        try:
            self._context.add_cookies(list(cookies))
        except Exception as exc:
            raise EngineError(f"could not apply cookies: {exc}") from None

    def cookies(self) -> list:
        """Return the context cookie jar, values included.

        Agent-facing callers must drop `value` before the jar leaves the process.
        Cookie values are credentials.
        """
        self._require_page()
        if self._context is None:
            raise EngineError("browser is not started")
        try:
            found = self._context.cookies()
        except Exception as exc:
            raise EngineError(f"could not read cookies: {exc}") from None
        if not isinstance(found, list):
            raise EngineError("could not read cookies")
        return list(found)

    def click(self, sel: str) -> None:
        self._require_page()
        if not isinstance(sel, str) or not sel.strip():
            raise EngineError("selector is empty")
        try:
            self._page.locator(sel).click()
        except Exception as exc:
            raise EngineError(f"click failed for {sel!r}: {exc}") from None

    def type(self, sel: str, text: str) -> None:
        # fill() sets the control value in one shot, which is what agents want.
        self._require_page()
        if not isinstance(sel, str) or not sel.strip():
            raise EngineError("selector is empty")
        if text is None:
            raise EngineError("text is missing")
        try:
            self._page.locator(sel).fill(str(text))
        except Exception as exc:
            raise EngineError(f"type failed for {sel!r}: {exc}") from None

    def press(self, sel: str, key: str) -> None:
        """Press a key on an element. MCP uses Enter when browser_type sets submit."""
        self._require_page()
        if not isinstance(sel, str) or not sel.strip():
            raise EngineError("selector is empty")
        if not isinstance(key, str) or not key:
            raise EngineError("key is empty")
        try:
            self._page.locator(sel).press(key)
        except Exception as exc:
            raise EngineError(f"press failed for {sel!r}: {exc}") from None

    def screenshot(self, path: str) -> None:
        self._require_page()
        if not isinstance(path, str) or not path.strip():
            raise EngineError("screenshot path is empty")
        dest = Path(path)
        try:
            if dest.parent and str(dest.parent) not in ("", "."):
                dest.parent.mkdir(parents=True, exist_ok=True)
            self._page.screenshot(path=str(dest))
        except Exception as exc:
            raise EngineError(f"screenshot failed for {path!r}: {exc}") from None

    def evaluate(self, js: str):
        self._require_page()
        if not isinstance(js, str) or not js.strip():
            raise EngineError("evaluate source is empty")
        try:
            return self._page.evaluate(js)
        except Exception as exc:
            raise EngineError(f"evaluate failed: {exc}") from None

    def close(self) -> None:
        context = self._context
        browser = self._browser
        playwright = self._playwright
        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None
        if context is not None:
            try:
                context.close()
            except Exception:
                pass
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
        if playwright is not None:
            try:
                playwright.stop()
            except Exception:
                pass

    def _require_page(self) -> None:
        if self._page is None:
            raise EngineError("browser is not started")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.close()
        return False


def _validate_proxy(proxy: dict | None) -> None:
    if proxy is None:
        return
    if not isinstance(proxy, dict) or not isinstance(proxy.get("server"), str):
        raise EngineError("proxy must be a mapping with a 'server' string; use parse_proxy()")
    server = proxy["server"]
    if "://" not in server:
        raise EngineError(f"proxy server {server!r} is invalid; use parse_proxy()")


class PlaywrightEngine(_ChromiumEngine):
    api_module = "playwright.sync_api"
    install_hint = (
        "playwright is not installed. "
        "Install it with: pip install playwright && playwright install chromium"
    )


class PatchrightEngine(_ChromiumEngine):
    api_module = "patchright.sync_api"
    install_hint = (
        "stealth engine requires patchright. "
        "Install it with: pip install patchright && patchright install chromium"
    )

    @classmethod
    def available(cls) -> bool:
        return importlib.util.find_spec("patchright") is not None


def create_engine(name: str = "chromium") -> Engine:
    """Return the chromium engine, or the stealth engine when `name` is `stealth`."""
    if name == "chromium":
        return PlaywrightEngine()
    if name == "stealth":
        return PatchrightEngine()
    raise EngineUnavailable(f"unknown engine {name!r}; expected 'chromium' or 'stealth'")
