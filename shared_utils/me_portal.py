"""ME Franchisee Portal (portal.meintranet.com) automation.

Provides MEPortalSession, a context manager that handles browser lifecycle,
dual-auth login (HTTP + Auth0), and all portal widget interactions (dropdowns,
date pickers, checkboxes, SSRS export).

Requires the [browser] extra: pip install shared-utils[browser]
"""

import os

from playwright.sync_api import Page

from .browser import create_browser, auth0_login
from .secrets_manager import get_secrets

PORTAL_URL = "https://portal.meintranet.com"
REPORTS_URL = f"{PORTAL_URL}/Reports"

DEFAULT_OP_REFS = {
    "username": "op://Private Business/Report Portal - Patrick/username",
    "password": "op://Private Business/Report Portal - Patrick/password",
}

EXPORT_BUTTON_ID = (
    "ctl00_FullWidthPlaceHolder_uxReportViewer_ctl05_ctl04_ctl00_ButtonLink"
)


class MEPortalSession:
    """Context manager for authenticated ME Franchisee Portal sessions.

    Args:
        headless: Run browser without a visible window.
        downloads_path: Directory for browser downloads.
        op_refs: 1Password secret references (used when credentials is None).
        credentials: Pre-resolved {"username": ..., "password": ...} dict.
            When provided, bypasses 1Password entirely. Useful for cloud
            deployments where credentials come from environment variables.
        timeout: Default page timeout in milliseconds.

    Usage::

        # Local dev (1Password with Touch ID):
        with MEPortalSession(headless=False) as session:
            ...

        # Cloud (env var credentials):
        with MEPortalSession(headless=True, credentials={"username": u, "password": p}) as session:
            ...
    """

    def __init__(
        self,
        headless: bool = False,
        downloads_path: str | None = None,
        op_refs: dict[str, str] | None = None,
        credentials: dict[str, str] | None = None,
        timeout: int = 60000,
    ):
        self.headless = headless
        self.downloads_path = downloads_path
        self.op_refs = op_refs or DEFAULT_OP_REFS
        self._credentials = credentials
        self.timeout = timeout
        self._browser_cm = None
        self._browser = None
        self.page: Page | None = None
        self._secrets: dict[str, str] | None = None

    def __enter__(self):
        self._secrets = self._credentials or get_secrets(self.op_refs)
        self._browser_cm = create_browser(
            headless=self.headless,
            downloads_path=self.downloads_path,
            http_credentials={
                "username": self._secrets["username"],
                "password": self._secrets["password"],
            },
            accept_downloads=True,
        )
        self._browser, self.page = self._browser_cm.__enter__()
        self.page.set_default_timeout(self.timeout)
        self._login()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._browser_cm:
            self._browser_cm.__exit__(exc_type, exc_val, exc_tb)
        return False

    # ------------------------------------------------------------------
    # Login
    # ------------------------------------------------------------------

    def _login(self):
        """Navigate to portal and complete Auth0 login."""
        self.page.goto(f"{PORTAL_URL}/")
        self.page.wait_for_load_state("networkidle", timeout=15000)
        self.page.click("#loginLink")
        self.page.wait_for_load_state("networkidle", timeout=20000)
        auth0_login(self.page, self._secrets["username"], self._secrets["password"])
        print("Logged in.")

    # ------------------------------------------------------------------
    # Report navigation
    # ------------------------------------------------------------------

    def open_report(self, report_path: str, category: str | None = None) -> Page:
        """Navigate to Reports page, optionally select a category, and open a report.

        Args:
            report_path: Relative path after the portal base URL
                (e.g. ``"WebForms/Report.aspx?Path=/Meevo%20Operations/..."``)
            category: If given, select this option from the category dropdown
                before opening the report (e.g. ``"Meevo Operations"``).

        Returns:
            The popup ``Page`` for the report viewer.
        """
        self.page.goto(REPORTS_URL)
        self.page.wait_for_load_state("networkidle", timeout=20000)

        if category:
            self.page.select_option("select", label=category)
            self.page.wait_for_timeout(2000)

        with self.page.expect_popup() as popup_info:
            self.page.evaluate(f"window.open('{report_path}', '_blank');")
        rp = popup_info.value
        rp.set_default_timeout(self.timeout)
        rp.wait_for_load_state("networkidle", timeout=self.timeout)
        print("Report page loaded.")
        return rp

    # ------------------------------------------------------------------
    # Widget helpers
    # ------------------------------------------------------------------

    @staticmethod
    def close_all_dropdowns(page: Page):
        """Force-close all open ui-multiselect dropdown menus."""
        page.evaluate(
            '() => document.querySelectorAll(".ui-multiselect-menu")'
            '.forEach(m => m.style.display = "none")'
        )
        page.wait_for_timeout(500)

    @staticmethod
    def select_multiselect_all(page: Page, button_index: int):
        """Open the dropdown at *button_index* and click 'Check All'."""
        page.locator("button.ui-multiselect").nth(button_index).click()
        page.wait_for_timeout(1500)
        page.locator("a.ui-multiselect-all").first.click()
        page.wait_for_timeout(1000)
        MEPortalSession.close_all_dropdowns(page)

    @staticmethod
    def select_multiselect_items(page: Page, button_index: int, items: list[str]):
        """Open the dropdown at *button_index* and click each item by label text."""
        page.locator("button.ui-multiselect").nth(button_index).click()
        page.wait_for_timeout(2000)
        for name in items:
            page.locator(f'li label span:has-text("{name}")').first.click()
            page.wait_for_timeout(500)
        MEPortalSession.close_all_dropdowns(page)

    @staticmethod
    def select_date(page: Page, button_index: int, date_str: str):
        """Open a date dropdown by button index, search for *date_str*, and select it."""
        page.locator("button.ui-multiselect").nth(button_index).click()
        page.wait_for_timeout(2000)
        page.locator(
            '.ui-multiselect-menu:visible input[type="search"]'
        ).first.fill(date_str)
        page.wait_for_timeout(2000)
        page.locator(
            f'.ui-multiselect-menu:visible li label:has-text("{date_str}")'
        ).first.click()
        page.wait_for_timeout(1000)
        MEPortalSession.close_all_dropdowns(page)

    @staticmethod
    def check_checkbox(page: Page, element_id: str):
        """Check a checkbox by its DOM id (no-op if already checked)."""
        page.evaluate(
            f'() => {{ const cb = document.getElementById("{element_id}");'
            f" if (cb && !cb.checked) cb.click(); }}"
        )
        page.wait_for_timeout(300)

    # ------------------------------------------------------------------
    # Report execution & export
    # ------------------------------------------------------------------

    @staticmethod
    def run_report(page: Page, poll_seconds: int = 10, max_polls: int = 30):
        """Click Run Report and poll until the report renders.

        Args:
            page: The report viewer popup page.
            poll_seconds: Seconds between render-check polls.
            max_polls: Maximum number of polls before giving up.
        """
        page.evaluate(
            '() => document.getElementById("ContentPlaceHolder1_uxRunReportButton")'
            '.style.display = "inline-block"'
        )
        page.wait_for_timeout(500)
        page.click("#ContentPlaceHolder1_uxRunReportButton")
        print("Run Report clicked.")

        for i in range(max_polls):
            page.wait_for_timeout(poll_seconds * 1000)
            has_content = page.evaluate(
                "() => document.querySelectorAll('table').length > 10"
            )
            if has_content:
                print(f"Report loaded ({(i + 1) * poll_seconds}s).")
                break
        page.wait_for_timeout(5000)

    @staticmethod
    def export_csv(page: Page, download_dir: str, filename: str = "export.csv") -> str:
        """Click the SSRS Export menu, choose CSV, and save the file.

        Captures the export URL from Playwright's download event, then
        fetches the CSV via an in-page ``fetch()`` call to bypass
        Playwright's download mechanism (which SSRS can cancel).

        Args:
            page: The report viewer popup page.
            download_dir: Directory to save the downloaded CSV.
            filename: Name for the saved file.

        Returns:
            Absolute path to the saved CSV.
        """
        os.makedirs(download_dir, exist_ok=True)
        save_path = os.path.join(download_dir, filename)

        page.click(f"#{EXPORT_BUTTON_ID}")
        page.wait_for_timeout(2000)

        # Trigger the export to capture the download URL, then cancel it.
        with page.expect_download(timeout=120000) as download_info:
            page.locator('a:has-text("CSV (comma delimited)")').first.click()

        download = download_info.value
        export_url = download.url
        download.cancel()
        print(f"Export URL captured: {export_url[:100]}...")

        # Fetch the CSV via the page's JS context (has session cookies).
        csv_text = page.evaluate(
            """async (url) => {
                const resp = await fetch(url);
                if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
                return await resp.text();
            }""",
            export_url,
        )

        with open(save_path, "w", encoding="utf-8") as f:
            f.write(csv_text)

        print(f"CSV saved: {save_path} ({os.path.getsize(save_path):,} bytes)")
        return save_path
