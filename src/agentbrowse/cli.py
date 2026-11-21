"""Command line for one-shot GET navigation, snapshots, and profiles.

Challenge handling stops at detection and a human handoff. This CLI does not
solve captchas, farm tokens, or call a captcha-solving API.
"""

from __future__ import annotations

import argparse
import json
import sys

from agentbrowse.challenge import Challenge
from agentbrowse.engine import (
    BrowserOptions,
    EngineError,
    EngineUnavailable,
    create_engine,
    require_http_url,
)
from agentbrowse.flare import FlareClient, FlareResult
from agentbrowse.geoproxy import GeoProxyUnavailable, load_pool, pick
from agentbrowse.handoff import DEFAULT_HANDOFF_TIMEOUT_S, plan_handoff, wait_for_clear
from agentbrowse.mcp_server import McpServer
from agentbrowse.proxy import ProxyError, parse_proxy
from agentbrowse.session import (
    ProfileError,
    SessionError,
    delete_profile,
    ensure_profile,
    list_profiles,
    read_state,
    write_state,
)
from agentbrowse.snapshot import SnapshotError, format_snapshot

_MISSING = object()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentbrowse",
        description="Browser for AI agents, with a slot for Burp. Detects challenges; does not defeat them.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    open_cmd = sub.add_parser("open", help="GET a URL and print a snapshot")
    open_cmd.add_argument("url", help="http(s) URL to open")
    mode = open_cmd.add_mutually_exclusive_group()
    mode.add_argument("--headless", action="store_true", help="Run headless (default)")
    mode.add_argument("--headed", action="store_true", help="Show the browser window")
    open_cmd.add_argument("--proxy", metavar="SPEC", help="http://host:port, host:port, or socks5://user:pass@host:port")
    open_cmd.add_argument("--profile", metavar="NAME", help="Profile name (default: default)")
    open_cmd.add_argument(
        "--insecure",
        action="store_true",
        help="Ignore HTTPS errors (needed for Burp's MITM CA)",
    )
    open_cmd.add_argument("--json", action="store_true", help="Print the snapshot as JSON")
    open_cmd.add_argument(
        "--pause-for-human",
        action="store_true",
        help="Headed only: wait while a person clears a detected challenge",
    )
    open_cmd.add_argument(
        "--flare-solverr",
        metavar="URL",
        help="Optional local FlareSolverr base URL. If it is down, open continues",
    )
    open_cmd.add_argument("--geo", metavar="CC", help="Country code to select from --proxy-pool")
    open_cmd.add_argument(
        "--proxy-pool",
        metavar="FILE",
        help="User-supplied YAML or JSON proxy pool ({country, url})",
    )
    open_cmd.add_argument(
        "--challenge-report",
        action="store_true",
        help="Print challenge detection only; do not wait for a person",
    )
    open_cmd.set_defaults(func=cmd_open)

    snap_cmd = sub.add_parser("snap", help="Reopen the last profile session and print a snapshot")
    snap_cmd.add_argument("--json", action="store_true", help="Print the snapshot as JSON")
    snap_cmd.set_defaults(func=cmd_snap)

    profiles = sub.add_parser("profiles", help="Manage browser profiles")
    profile_sub = profiles.add_subparsers(dest="profiles_command", required=True)
    list_cmd = profile_sub.add_parser("list", help="List profiles")
    list_cmd.set_defaults(func=cmd_profiles_list)
    delete_cmd = profile_sub.add_parser("delete", help="Delete a profile")
    delete_cmd.add_argument("name", help="Profile name")
    delete_cmd.add_argument("--force", action="store_true", help="Delete without a prompt")
    delete_cmd.set_defaults(func=cmd_profiles_delete)

    mcp_cmd = sub.add_parser("mcp", help="Run an MCP server on stdio for AI agents")
    mcp_cmd.add_argument("--headed", action="store_true", help="Show the browser window")
    mcp_cmd.add_argument(
        "--proxy",
        metavar="SPEC",
        help="http://host:port, host:port, or socks5://user:pass@host:port",
    )
    mcp_cmd.add_argument("--profile", metavar="NAME", help="Profile name (default: default)")
    mcp_cmd.add_argument(
        "--insecure",
        action="store_true",
        help="Ignore HTTPS errors (needed for Burp's MITM CA)",
    )
    mcp_cmd.set_defaults(func=cmd_mcp)
    return parser


def build_browser_options(
    args: argparse.Namespace,
    profile_dir: str | None,
    proxy_spec=_MISSING,
    user_agent: str | None = None,
) -> BrowserOptions:
    """Build the options object for `open`. `--insecure` sets ignore_https_errors."""
    if proxy_spec is _MISSING:
        proxy_spec = getattr(args, "proxy", None)
    proxy = parse_proxy(proxy_spec) if proxy_spec else None
    return BrowserOptions(
        headless=not bool(getattr(args, "headed", False)),
        proxy=proxy,
        profile_dir=profile_dir,
        ignore_https_errors=bool(getattr(args, "insecure", False)),
        user_agent=user_agent or None,
    )


def cmd_open(args: argparse.Namespace) -> int:
    url = require_http_url(args.url)
    proxy_spec = _proxy_spec_for(args)
    if proxy_spec:
        parse_proxy(proxy_spec)
    profile = args.profile or "default"
    profile_dir = str(ensure_profile(profile))
    report_only = bool(getattr(args, "challenge_report", False))
    flare = None if report_only else _maybe_flare(getattr(args, "flare_solverr", None), url)
    user_agent = flare.user_agent if flare is not None and flare.status == "ok" and flare.user_agent else None
    options = build_browser_options(
        args,
        profile_dir=profile_dir,
        proxy_spec=proxy_spec,
        user_agent=user_agent,
    )
    engine = create_engine("chromium")
    snap = None
    code = 0
    try:
        _start_engine(engine, options)
        _apply_flare_cookies(engine, flare)
        engine.goto(url)
        snap = engine.snapshot()
        write_state(
            profile=profile,
            url=str(snap.get("url") or url),
            headless=options.headless,
            ignore_https_errors=options.ignore_https_errors,
        )
        snap, code = _finish_challenge(args, engine, snap, headless=options.headless, allow_pause=True)
    finally:
        engine.close()
    _print_snapshot(snap, json_output=bool(args.json), report_only=report_only)
    return code


def cmd_snap(args: argparse.Namespace) -> int:
    state = read_state()
    profile_dir = str(ensure_profile(state["profile"]))
    options = BrowserOptions(
        headless=bool(state["headless"]),
        proxy=None,
        profile_dir=profile_dir,
        ignore_https_errors=bool(state["ignore_https_errors"]),
    )
    engine = create_engine("chromium")
    try:
        _start_engine(engine, options)
        engine.goto(state["url"])
        snap = engine.snapshot()
        snap, code = _finish_challenge(args, engine, snap, headless=options.headless, allow_pause=False)
    finally:
        engine.close()
    _print_snapshot(snap, json_output=bool(args.json))
    return code


def cmd_profiles_list(_args: argparse.Namespace) -> int:
    for name in list_profiles():
        print(name)
    return 0


def cmd_profiles_delete(args: argparse.Namespace) -> int:
    delete_profile(args.name, force=bool(args.force))
    print(f"deleted profile {args.name}")
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    """Speak MCP on stdio. The browser stays down until the first browser_open."""
    proxy_spec = getattr(args, "proxy", None)
    proxy = parse_proxy(proxy_spec) if proxy_spec else None
    profile = args.profile or "default"
    profile_dir = str(ensure_profile(profile))
    options = BrowserOptions(
        headless=not bool(args.headed),
        proxy=proxy,
        profile_dir=profile_dir,
        ignore_https_errors=bool(args.insecure),
    )
    print("agentbrowse MCP server on stdio", file=sys.stderr, flush=True)
    server = McpServer(
        lambda: create_engine("chromium"),
        sys.stdout,
        sys.stdin,
        options=options,
    )
    server.serve()
    return 0


def _proxy_spec_for(args: argparse.Namespace) -> str | None:
    geo = getattr(args, "geo", None)
    pool_path = getattr(args, "proxy_pool", None)
    direct = getattr(args, "proxy", None)
    if not geo and not pool_path:
        return direct
    if direct:
        raise GeoProxyUnavailable("pass either --proxy or --geo with --proxy-pool")
    if not geo or not pool_path:
        raise GeoProxyUnavailable("pass both --geo CC and --proxy-pool FILE")
    return pick(load_pool(pool_path), geo)


def _maybe_flare(base_url: str | None, url: str) -> FlareResult | None:
    """One local FlareSolverr attempt. Failure is a warning, never an exception."""
    if not base_url:
        return None
    try:
        result = FlareClient(base_url).solve(url)
    except Exception as exc:
        print(f"warning: flaresolverr failed; continuing ({type(exc).__name__})", file=sys.stderr)
        return FlareResult(cookies=[], user_agent="", status="error", raw={"error": type(exc).__name__})
    if result.status != "ok":
        print("warning: flaresolverr unreachable or returned an error; continuing", file=sys.stderr)
    return result


def _start_engine(engine, options: BrowserOptions) -> None:
    # Omit user_agent unless it is set so existing 4-argument doubles keep working.
    if options.user_agent:
        engine.start(
            options.headless,
            options.proxy,
            options.profile_dir,
            options.ignore_https_errors,
            user_agent=options.user_agent,
        )
        return
    engine.start(options.headless, options.proxy, options.profile_dir, options.ignore_https_errors)


def _apply_flare_cookies(engine, flare: FlareResult | None) -> None:
    if flare is None or flare.status != "ok" or not flare.cookies:
        return
    try:
        engine.add_cookies(flare.cookies)
    except Exception:
        print("warning: flaresolverr cookies were not applied; continuing", file=sys.stderr)


def _finish_challenge(args, engine, snap: dict, *, headless: bool, allow_pause: bool) -> tuple[dict, int]:
    """Exit 4 when a challenge is still there. Headless tells the operator to re-run visibly."""
    challenge = snap.get("challenge")
    if not challenge:
        return snap, 0
    report_only = bool(getattr(args, "challenge_report", False))
    pause = (
        allow_pause
        and not report_only
        and not headless
        and bool(getattr(args, "pause_for_human", False))
    )
    if pause:
        print(plan_handoff(_as_challenge(challenge), headless=False), file=sys.stderr)
        print("waiting for a person to clear the challenge", file=sys.stderr)
        box = [snap]

        def poll() -> bool:
            try:
                fresh = engine.snapshot()
            except Exception:
                return False
            box[0] = fresh
            return not fresh.get("challenge")

        if wait_for_clear(poll, DEFAULT_HANDOFF_TIMEOUT_S):
            return box[0], 0
        snap = box[0]
        challenge = snap.get("challenge") or challenge
        print("challenge was not cleared", file=sys.stderr)
        return snap, 4
    print(plan_handoff(_as_challenge(challenge), headless=headless), file=sys.stderr)
    return snap, 4


def _as_challenge(payload) -> Challenge:
    if isinstance(payload, Challenge):
        return payload
    if not isinstance(payload, dict):
        return Challenge(
            kind="challenge",
            confidence="low",
            evidence="",
            suggested_action="run again with --headed --pause-for-human",
        )
    confidence = payload.get("confidence")
    if confidence not in ("low", "medium", "high"):
        confidence = "low"
    return Challenge(
        kind=str(payload.get("kind") or "challenge"),
        confidence=confidence,
        evidence=str(payload.get("evidence") or ""),
        suggested_action=str(payload.get("suggested_action") or "run again with --headed --pause-for-human"),
    )


def _print_snapshot(snap: dict, json_output: bool, report_only: bool = False) -> None:
    challenge = snap.get("challenge")
    if report_only:
        if json_output:
            print(json.dumps({"challenge": challenge}, indent=2, ensure_ascii=False))
            return
        if not challenge:
            print("challenge: none")
            return
        print(f"kind: {challenge.get('kind')}")
        print(f"confidence: {challenge.get('confidence')}")
        print(f"evidence: {challenge.get('evidence')}")
        print(f"suggested_action: {challenge.get('suggested_action')}")
        return
    if json_output:
        print(json.dumps(snap, indent=2, ensure_ascii=False))
        return
    print(format_snapshot(snap))


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        return code if isinstance(code, int) else 2
    try:
        return int(args.func(args))
    except (
        ProxyError,
        ProfileError,
        SessionError,
        EngineError,
        EngineUnavailable,
        SnapshotError,
        GeoProxyUnavailable,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("error: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
