<!-- this tool detects challenges and hands them to a human; it does not defeat them. -->

# agentbrowse

A browser an AI agent can drive, with a slot for Burp in the middle. Phase 1 wraps Chromium (Playwright) and an optional stealth backend (patchright) behind one CLI: GET a URL, snapshot the page, keep a profile.

This tool detects challenges and hands them to a human; it does not defeat them.

## Install

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/playwright install chromium
```

Stealth engine, optional:

```bash
.venv/bin/pip install patchright && .venv/bin/patchright install chromium
```

## Use

```bash
.venv/bin/python -m agentbrowse open https://example.com/ --json
.venv/bin/python -m agentbrowse open https://target.example/ --headed --proxy http://127.0.0.1:8080 --insecure --profile burp
.venv/bin/python -m agentbrowse snap --json
.venv/bin/python -m agentbrowse profiles list
.venv/bin/python -m agentbrowse profiles delete burp --force
.venv/bin/python -m agentbrowse open https://target.example/ --challenge-report
.venv/bin/python -m agentbrowse open https://target.example/ --headed --pause-for-human
.venv/bin/python -m agentbrowse open https://target.example/ --geo US --proxy-pool proxies.yaml
.venv/bin/python -m agentbrowse open https://target.example/ --flare-solverr http://127.0.0.1:8191
.venv/bin/python -m agentbrowse mcp --proxy http://127.0.0.1:8080 --insecure --profile burp
```

`--insecure` sets Playwright `ignore_https_errors` so Burp's MITM certificate is accepted. `--proxy` takes `http://127.0.0.1:8080`, `127.0.0.1:8080`, or `socks5://user:pass@host:1080`.

`--challenge-report` prints the detection and nothing else. A detected challenge that is not cleared exits **4**. In a headless session the message is `run again with --headed --pause-for-human`. `--pause-for-human` only waits; it does not solve the interstitial. `--geo` reads a user-supplied YAML or JSON pool (`[{country, url}]`) and does not ship any proxies. `--flare-solverr` points at a FlareSolverr you already run. If that process is down, `open` continues.

Profiles live in `~/.agentbrowse/profiles/<name>` (override the root with `AGENTBROWSE_HOME`). Each command is one-shot: `open` launches Chromium, GETs the URL, prints a snapshot, and closes. `snap` relaunches that profile and GETs the saved URL. The state file stores profile name, URL (userinfo stripped), headless, and the insecure flag. It does not store proxy credentials or cookies.

Cookie export/import uses Playwright storage state (`export_cookies` / `import_cookies`).

## Use with AI agents

`agentbrowse mcp` is a long-running MCP server. Transport is newline-delimited JSON-RPC 2.0 on stdio: one JSON object per line. stdout is reserved for protocol messages. The startup line `agentbrowse MCP server on stdio` goes to stderr. The browser starts on the first `browser_open`.

```json
{
  "mcpServers": {
    "agentbrowse": {
      "command": "agentbrowse",
      "args": ["mcp", "--profile", "burp", "--proxy", "http://127.0.0.1:8080", "--insecure"]
    }
  }
}
```

Add `--headed` when the window should be visible. Flags match `open`: `--headed`, `--proxy SPEC`, `--profile NAME`, `--insecure`.

| Tool | Arguments | Returns |
| --- | --- | --- |
| `browser_open` | `url` | snapshot (`url`, `title`, `challenge`, `text`, `elements`, `console_errors`) |
| `browser_snapshot` | | the same snapshot |
| `browser_click` | `selector` | `ok`, `url`, `title` |
| `browser_type` | `selector`, `text`, optional `submit` | `ok`, `url` |
| `browser_screenshot` | `path` | `ok`, `path` |
| `browser_evaluate` | `expression` | `ok`, `result` |
| `browser_cookies` | | `cookies`: `name`, `domain`, `path`, `expires` |
| `browser_close` | | `ok` |

`browser_cookies` is redacted on purpose. Values are credentials and are never returned. `browser_evaluate` runs in the page; an expression containing `require(`, `process.`, or `import(` is refused.

## Limits

- The one-shot `open` command is GET navigation. The MCP server can click, type, screenshot, and evaluate in the page. `submit` on `browser_type` presses Enter.
- `challenge` on a snapshot is `null` on a clean page, or `{kind, confidence, evidence, suggested_action}` when an interstitial is detected.
- Exit code 4 means a challenge was detected and not cleared. The tool does not solve captchas.
- Default engine is `chromium`. `create_engine("stealth")` uses patchright and raises `EngineUnavailable` with an install hint if it is missing.

## Tests

```bash
.venv/bin/python -m pytest -q
```

Tests bind `127.0.0.1` only. The browser test skips if Chromium was not installed.

## Credits

See [CONTRIBUTORS.md](CONTRIBUTORS.md).
