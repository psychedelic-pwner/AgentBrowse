import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

_CHROMIUM: bool | None = None


def chromium_installed() -> bool:
    global _CHROMIUM
    if _CHROMIUM is not None:
        return _CHROMIUM
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        _CHROMIUM = False
        return False
    try:
        with sync_playwright() as playwright:
            executable = Path(playwright.chromium.executable_path)
        _CHROMIUM = executable.is_file()
    except Exception:
        _CHROMIUM = False
    return _CHROMIUM


@pytest.fixture
def chromium_ready():
    if not chromium_installed():
        pytest.skip("playwright chromium is not installed")
