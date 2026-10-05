"""Real installed Chrome via Playwright, with a persistent profile so sites
see one ordinary returning user."""

from .settings import PROFILE_DIR


def launch(pw, headless=False):
    ctx = pw.chromium.launch_persistent_context(
        PROFILE_DIR,
        channel="chrome",
        headless=headless,
        locale="en-IN",
        timezone_id="Asia/Kolkata",
        viewport={"width": 1366, "height": 768},
        args=["--disable-blink-features=AutomationControlled"],
        ignore_default_args=["--enable-automation"],
    )
    ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    return ctx, page


def body_text(page):
    try:
        return page.locator("body").inner_text(timeout=3000)
    except Exception:
        return ""
