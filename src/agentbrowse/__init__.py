"""Browser automation for agents, with a slot for an intercepting proxy."""

from agentbrowse.challenge import Challenge, detect_html
from agentbrowse.engine import (
    BrowserOptions,
    Engine,
    EngineError,
    EngineUnavailable,
    PatchrightEngine,
    PlaywrightEngine,
    create_engine,
    require_http_url,
)
from agentbrowse.flare import FlareClient, FlareResult
from agentbrowse.geoproxy import GeoProxyUnavailable, load_pool, pick
from agentbrowse.handoff import plan_handoff, wait_for_clear
from agentbrowse.proxy import ProxyError, parse_proxy
from agentbrowse.session import (
    ProfileError,
    SessionError,
    delete_profile,
    ensure_profile,
    export_cookies,
    import_cookies,
    list_profiles,
)
from agentbrowse.snapshot import DEFAULT_TEXT_LIMIT, SnapshotError, build_snapshot

__version__ = "0.1.0"

__all__ = [
    "BrowserOptions",
    "Challenge",
    "DEFAULT_TEXT_LIMIT",
    "Engine",
    "EngineError",
    "EngineUnavailable",
    "FlareClient",
    "FlareResult",
    "GeoProxyUnavailable",
    "PatchrightEngine",
    "PlaywrightEngine",
    "ProfileError",
    "ProxyError",
    "SessionError",
    "SnapshotError",
    "build_snapshot",
    "create_engine",
    "delete_profile",
    "detect_html",
    "ensure_profile",
    "export_cookies",
    "import_cookies",
    "list_profiles",
    "load_pool",
    "parse_proxy",
    "pick",
    "plan_handoff",
    "require_http_url",
    "wait_for_clear",
    "__version__",
]
