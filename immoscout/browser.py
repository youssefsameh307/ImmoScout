"""Browser session for actions that need a logged-in ImmobilienScout24 account.

The mobile API is read-only, so contacting a landlord goes through the website.
A dedicated, persistent Chrome profile keeps the login and captcha cookies
between runs. It is separate from your normal Chrome profile.

Log in once (a real browser window opens; solve the captcha and sign in):

    immoscout-login
"""
from __future__ import annotations

import os
from pathlib import Path

try:
    from playwright.sync_api import BrowserContext, Playwright, sync_playwright
except ModuleNotFoundError:  # pragma: no cover
    sync_playwright = None  # type: ignore[assignment]

_INSTALL_HINT = (
    "Browser actions need the 'apply' extra. Install it with:\n"
    '    pip install "immoscout[apply]"'
)

LOGIN_URL = "https://www.immobilienscout24.de/geschlossenerbereich/start.html"


def data_dir() -> Path:
    """Where the browser profile and the application log live."""
    base = os.environ.get("IMMOSCOUT_DATA_DIR") or Path(
        os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share")
    ) / "immoscout"
    path = Path(base)
    path.mkdir(parents=True, exist_ok=True)
    return path


def launch(pw: Playwright, *, headless: bool = False) -> BrowserContext:
    """Open the persistent profile in the installed Chrome (less bot-flagged than bundled Chromium)."""
    return pw.chromium.launch_persistent_context(
        str(data_dir() / "browser-profile"),
        channel=os.environ.get("IMMOSCOUT_BROWSER_CHANNEL", "chrome"),
        headless=headless,
        locale="de-DE",
        viewport={"width": 1280, "height": 900},
        args=["--disable-blink-features=AutomationControlled"],
    )


def login() -> None:
    """Open a browser window so the user can solve the captcha and log in; returns when it is closed."""
    if sync_playwright is None:
        raise SystemExit(_INSTALL_HINT)
    with sync_playwright() as pw:
        ctx = launch(pw)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(LOGIN_URL)
        # Google SSO only yields a session cookie that dies with the window; the
        # ImmoScout e-mail/password login persists across launches.
        print(
            "Log in to ImmobilienScout24 in the browser window with e-mail + password\n"
            "(not 'Mit Google anmelden' - that login is lost when the window closes),\n"
            "then close the window."
        )
        page.wait_for_event("close", timeout=0)
        ctx.close()
    print(f"Session saved to {data_dir() / 'browser-profile'}")


def main_login() -> None:
    login()
