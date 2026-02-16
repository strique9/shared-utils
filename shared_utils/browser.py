"""Browser automation utilities using Playwright.

Requires the [browser] extra: pip install shared-utils[browser]
After install, run: playwright install chromium
"""

from contextlib import contextmanager

from playwright.sync_api import sync_playwright, Page

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


@contextmanager
def create_browser(
    headless=True,
    user_agent=DEFAULT_USER_AGENT,
    downloads_path=None,
    http_credentials=None,
    accept_downloads=False,
):
    """Launch a Chromium browser with a realistic user agent.

    Yields (browser, page) tuple. Cleans up on exit.

    Args:
        headless: Run browser without a visible window.
        user_agent: User-Agent header string.
        downloads_path: Directory for browser downloads.
        http_credentials: Dict with "username" and "password" for HTTP auth
                          (e.g. IIS/NTLM sites).
        accept_downloads: Whether to accept file downloads automatically.

    Usage:
        with create_browser() as (browser, page):
            page.goto("https://example.com")

        with create_browser(
            http_credentials={"username": "u", "password": "p"},
            accept_downloads=True,
            downloads_path="/tmp/dl",
        ) as (browser, page):
            page.goto("https://protected-site.com")
    """
    launch_kwargs = {"headless": headless}
    if downloads_path:
        launch_kwargs["downloads_path"] = downloads_path

    context_kwargs = {"user_agent": user_agent}
    if http_credentials:
        context_kwargs["http_credentials"] = http_credentials
    if accept_downloads:
        context_kwargs["accept_downloads"] = True

    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_kwargs)
        context = browser.new_context(**context_kwargs)
        page = context.new_page()
        try:
            yield browser, page
        finally:
            browser.close()


def auth0_login(page: Page, username: str, password: str, timeout: int = 8000):
    """Handle Auth0 login form: fill email + password, submit, wait for redirect.

    Expects the page to already be on an Auth0 login URL.
    Returns True if login succeeded (redirected away from auth0).
    """
    page.wait_for_selector(
        'input[name="username"], input[name="email"], input[type="email"], input#username',
        timeout=timeout,
    )

    email_field = page.query_selector(
        'input[name="username"], input[name="email"], input[type="email"], input#username'
    )
    password_field = page.query_selector(
        'input[name="password"], input[type="password"], input#password'
    )

    if not email_field or not password_field:
        raise RuntimeError("Could not find Auth0 email/password fields")

    email_field.fill(username)
    password_field.fill(password)

    submit_btn = page.query_selector(
        'button[type="submit"], input[type="submit"], button[name="action"]'
    )
    if not submit_btn:
        raise RuntimeError("Could not find Auth0 submit button")

    submit_btn.click()
    page.wait_for_url(lambda url: "auth0" not in url, timeout=15000)

    return "auth0" not in page.url
