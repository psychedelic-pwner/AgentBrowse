"""Agent-facing page snapshots: text, interactive elements, console errors."""

from __future__ import annotations

import re

from agentbrowse.challenge import Challenge, detect_html

_STATUS_PRESENT = re.compile(r"\b403\b")
_STATUS_429_PRESENT = re.compile(r"\b429\b")

DEFAULT_TEXT_LIMIT = 8000

# `challenge` is filled by detect_html when the HTML looks like an interstitial.
SNAPSHOT_JS = r"""
(opts) => {
  const limit = Math.max(0, Number((opts && opts.textLimit) ?? 8000) || 0);
  const selector = [
    "a[href]",
    "button",
    "input:not([type='hidden'])",
    "select",
    "textarea",
    "[contenteditable='true']",
    "[role='button']",
    "[role='link']",
    "[role='textbox']",
    "[role='checkbox']",
    "[role='radio']",
    "[role='combobox']",
    "[role='switch']",
    "[role='tab']",
    "[role='menuitem']",
    "summary"
  ].join(",");

  function visible(el) {
    if (!el || el.nodeType !== 1) return false;
    const type = (el.getAttribute("type") || "").toLowerCase();
    if (type === "hidden") return false;
    if (el.hasAttribute("hidden")) return false;
    const style = window.getComputedStyle(el);
    if (!style) return false;
    if (style.display === "none" || style.visibility === "hidden") return false;
    if (Number(style.opacity) === 0) return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }

  function roleOf(el) {
    const explicit = (el.getAttribute("role") || "").trim();
    if (explicit) return explicit;
    const tag = el.tagName.toLowerCase();
    if (tag === "a") return "link";
    if (tag === "button" || tag === "summary") return "button";
    if (tag === "textarea") return "textbox";
    if (tag === "select") return "combobox";
    if (el.isContentEditable) return "textbox";
    if (tag === "input") {
      const t = (el.getAttribute("type") || "text").toLowerCase();
      if (t === "button" || t === "submit" || t === "reset" || t === "image") return "button";
      if (t === "checkbox") return "checkbox";
      if (t === "radio") return "radio";
      if (t === "hidden") return "";
      return "textbox";
    }
    return tag;
  }

  function clean(value, cap) {
    return String(value || "").replace(/\s+/g, " ").trim().slice(0, cap);
  }

  function nameOf(el) {
    const aria = el.getAttribute("aria-label");
    if (aria && aria.trim()) return clean(aria, 200);
    const labelledby = el.getAttribute("aria-labelledby");
    if (labelledby) {
      const text = labelledby.split(/\s+/).map((id) => {
        const node = document.getElementById(id);
        return node ? (node.innerText || node.textContent || "") : "";
      }).join(" ");
      const named = clean(text, 200);
      if (named) return named;
    }
    if (el.labels && el.labels.length) {
      const label = el.labels[0];
      const named = clean(label.innerText || label.textContent || "", 200);
      if (named) return named;
    }
    for (const attr of ["placeholder", "alt", "title"]) {
      const value = el.getAttribute(attr);
      if (value && value.trim()) return clean(value, 200);
    }
    if (el.tagName !== "INPUT" && el.tagName !== "TEXTAREA") {
      const named = clean(el.innerText || el.textContent || "", 200);
      if (named) return named;
    }
    return clean(el.getAttribute("name") || "", 200);
  }

  function unique(sel) {
    try {
      return document.querySelectorAll(sel).length === 1;
    } catch (err) {
      return false;
    }
  }

  function selectorOf(el) {
    if (el.id && /^[A-Za-z][\w:-]*$/.test(el.id)) {
      const sel = "#" + el.id;
      if (unique(sel)) return sel;
    }
    const name = el.getAttribute("name");
    const tag = el.tagName.toLowerCase();
    if (name && /^[A-Za-z0-9_.-]+$/.test(name)) {
      const sel = tag + "[name='" + name + "']";
      if (unique(sel)) return sel;
    }
    const parts = [];
    let cur = el;
    while (cur && cur.nodeType === 1 && cur.tagName && cur.tagName.toLowerCase() !== "html") {
      let part = cur.tagName.toLowerCase();
      const parent = cur.parentElement;
      if (parent) {
        const same = Array.from(parent.children).filter((child) => child.tagName === cur.tagName);
        if (same.length > 1) part += ":nth-of-type(" + (same.indexOf(cur) + 1) + ")";
      }
      parts.unshift(part);
      const candidate = parts.join(" > ");
      if (unique(candidate)) return candidate;
      cur = parent;
    }
    return parts.join(" > ") || tag;
  }

  const elements = [];
  const seen = new Set();
  for (const el of document.querySelectorAll(selector)) {
    if (seen.has(el) || !visible(el)) continue;
    seen.add(el);
    const role = roleOf(el);
    const built = selectorOf(el);
    if (!role || !built) continue;
    elements.push({ role: role, name: nameOf(el), selector: built });
    if (elements.length >= 200) break;
  }

  const rawText = document.body ? (document.body.innerText || "") : "";
  return {
    url: String(location.href || ""),
    title: String(document.title || ""),
    text: rawText.length > limit ? rawText.slice(0, limit) : rawText,
    elements: elements
  };
}
"""


class SnapshotError(RuntimeError):
    """The page snapshot could not be built."""


def build_snapshot(
    *,
    url: str,
    title: str,
    text: str,
    elements,
    console_errors,
    text_limit: int = DEFAULT_TEXT_LIMIT,
    challenge=None,
    html: str | None = None,
    status: int | None = None,
) -> dict:
    """Shape already-extracted page data into a snapshot, including any challenge."""
    if not isinstance(text_limit, int) or isinstance(text_limit, bool) or text_limit < 0:
        raise SnapshotError("text limit must be a non-negative integer")
    body = text or ""
    if len(body) > text_limit:
        body = body[:text_limit]
    errors = []
    for item in console_errors or []:
        message = str(item).strip()
        if message:
            errors.append(message)
    if challenge is None and html is not None:
        challenge = detect_html(url or "", title or "", _detection_html(html, status))
    return {
        "url": url or "",
        "title": title or "",
        "challenge": _challenge_payload(challenge),
        "text": body,
        "elements": _elements(elements),
        "console_errors": errors,
    }


def capture_page(page, console_errors=None, text_limit: int = DEFAULT_TEXT_LIMIT, status: int | None = None) -> dict:
    """Evaluate the DOM and merge in console errors collected by the engine."""
    if not isinstance(text_limit, int) or isinstance(text_limit, bool) or text_limit < 0:
        raise SnapshotError("text limit must be a non-negative integer")
    try:
        raw = page.evaluate(SNAPSHOT_JS, {"textLimit": text_limit})
    except Exception as exc:
        raise SnapshotError(f"snapshot failed: {exc}") from None
    if not isinstance(raw, dict):
        raise SnapshotError("snapshot script returned an unexpected value")
    errors = list(console_errors or [])
    script_error = raw.get("error")
    if script_error:
        errors.append(f"snapshot: {script_error}")
    return build_snapshot(
        url=str(raw.get("url") or ""),
        title=str(raw.get("title") or ""),
        text=str(raw.get("text") or ""),
        elements=raw.get("elements") or [],
        console_errors=errors,
        text_limit=text_limit,
        html=_page_html(page),
        status=status,
    )


def format_snapshot(snap: dict) -> str:
    """Plain-text rendering of a snapshot for a terminal."""
    lines = [
        f"url: {snap.get('url', '')}",
        f"title: {snap.get('title', '')}",
        f"challenge: {_format_challenge(snap.get('challenge'))}",
        "",
        "text:",
        snap.get("text") or "",
        "",
        "elements:",
    ]
    elements = snap.get("elements") or []
    if not elements:
        lines.append("(none)")
    else:
        for el in elements:
            name = el.get("name") or "(unnamed)"
            lines.append(f"- [{el.get('role', '')}] {name}  {el.get('selector', '')}")
    lines.append("")
    lines.append("console_errors:")
    errors = snap.get("console_errors") or []
    if not errors:
        lines.append("(none)")
    else:
        for err in errors:
            lines.append(f"- {err}")
    return "\n".join(lines)


def _detection_html(html: str, status: int | None) -> str:
    """Let detect_html see the HTTP status. It only accepts strings."""
    body = html or ""
    if status == 403 and not _STATUS_PRESENT.search(body):
        return f"403\n{body}"
    if status == 429 and not _STATUS_429_PRESENT.search(body):
        return f"429\n{body}"
    return body


def _page_html(page) -> str:
    reader = getattr(page, "content", None)
    if not callable(reader):
        return ""
    try:
        html = reader()
    except Exception:
        return ""
    return html if isinstance(html, str) else str(html)


def _challenge_payload(challenge) -> dict | None:
    if challenge is None:
        return None
    if isinstance(challenge, Challenge):
        return challenge.as_dict()
    if isinstance(challenge, dict) and challenge.get("kind"):
        return {
            "kind": challenge.get("kind"),
            "confidence": challenge.get("confidence"),
            "evidence": challenge.get("evidence"),
            "suggested_action": challenge.get("suggested_action"),
        }
    return None


def _format_challenge(challenge) -> str:
    if not challenge:
        return "none"
    if isinstance(challenge, dict):
        kind = challenge.get("kind") or "unknown"
        confidence = challenge.get("confidence") or ""
        return f"{kind} ({confidence})" if confidence else str(kind)
    return str(challenge)


def _elements(raw) -> list[dict]:
    cleaned = []
    if not isinstance(raw, list):
        return cleaned
    for item in raw:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip()
        name = str(item.get("name") or "").strip()
        selector = str(item.get("selector") or "").strip()
        if not role or not selector:
            continue
        cleaned.append({"role": role, "name": name, "selector": selector})
        if len(cleaned) >= 200:
            break
    return cleaned
