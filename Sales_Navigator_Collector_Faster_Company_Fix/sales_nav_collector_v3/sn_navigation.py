"""Manual-first LinkedIn navigation for authenticated Sales Navigator searches.

A licensed user may be sent to an account/contract selection screen before
Sales Navigator opens. Do not bypass it or treat it as a search result page.
"""
from __future__ import annotations

from typing import Callable
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError

from sn_core import parse_results


def is_linkedin_page(url: str) -> bool:
    parsed = urlparse(url)
    return (parsed.scheme == 'https' and parsed.hostname in
            {'www.linkedin.com', 'linkedin.com'})


def is_search_page(url: str) -> bool:
    parsed = urlparse(url)
    return is_linkedin_page(url) and parsed.path.rstrip('/') in {
        '/sales/search/people', '/sales/search/leads',
    }


def is_contract_chooser(url: str) -> bool:
    return is_linkedin_page(url) and urlparse(url).path.rstrip('/') == '/sales/contract-chooser'


def is_login_page(url: str) -> bool:
    if not is_linkedin_page(url):
        return False
    path = urlparse(url).path.lower()
    return (path.startswith('/login') or path.startswith('/uas/login') or
            path.startswith('/checkpoint') or path.startswith('/sales/login'))


def navigate_linkedin(page, url: str, *, log: Callable[[str], None] = print) -> None:
    """Handle interrupted goto only when LinkedIn took over navigation.

    `wait_until=commit` avoids waiting for unrelated assets on an SPA; it does
    not prevent client-side redirects. Only the specific interruption error is
    recoverable. Never silently ignore timeouts, DNS, SSL, or other failures.
    """
    try:
        page.goto(url, wait_until='commit', timeout=60000)
    except PlaywrightError as exc:
        if 'is interrupted by another navigation to' not in str(exc).lower():
            raise
        # Wait for the competing navigation to settle before inspecting the URL.
        for _ in range(8):
            page.wait_for_timeout(350)
            if is_linkedin_page(page.url):
                break
        if not is_linkedin_page(page.url):
            raise RuntimeError('The browser redirected away from LinkedIn; stopped for safety.') from exc
        log('  LinkedIn redirected during navigation; checking where it landed.')


def search_has_results(page, api_capture) -> bool:
    """Require the *current page* to be a lead search, not a chooser/homepage.

    A stale API result captured during a prior search isn't sufficient to mark
    an unrelated interstitial as ready.
    """
    if not is_search_page(page.url):
        return False
    try:
        return bool(parse_results(page.content()) or api_capture.rows)
    except PlaywrightError:
        # A new document is replacing the old DOM; check again shortly.
        return False


def wait_for_search(page, api_capture, *, pause_ms: int, polls: int = 14) -> bool:
    for _ in range(polls):
        if search_has_results(page, api_capture):
            return True
        if is_contract_chooser(page.url) or is_login_page(page.url):
            return False
        page.wait_for_timeout(max(500, min(1000, pause_ms)))
    return search_has_results(page, api_capture)


def open_search_with_manual_access(
    page, search_url: str, api_capture, *, pause_ms: int = 800,
    prompt: Callable[[str], str] = input,
    log: Callable[[str], None] = print,
    manual_attempts: int = 3,
) -> None:
    """Open search and allow user to complete LinkedIn's access flow.

    Each retry returns to the original user-supplied search URL, preserving its
    filters. Does not attempt to select a license, authenticate, or scrape an
    account chooser on the user's behalf.
    """
    for attempt in range(manual_attempts + 1):
        api_capture.clear_page()
        navigate_linkedin(page, search_url, log=log)
        if wait_for_search(page, api_capture, pause_ms=pause_ms):
            return
        if attempt == manual_attempts:
            break

        if is_contract_chooser(page.url):
            log('\nLinkedIn opened the Sales Navigator account/contract chooser.')
            log('In the Chromium window, select the Sales Navigator account you can access.')
        elif is_login_page(page.url):
            log('\nLinkedIn requires a sign-in or security verification.')
            log('Complete it manually in the Chromium window.')
        else:
            log('\nNo lead search results are visible yet.')
            log('Check the Chromium window for sign-in, account selection, or access errors.')
        log('Wait until Sales Navigator opens before continuing.')
        try:
            prompt('Press Enter after finishing in the browser... ')
        except EOFError as exc:
            raise RuntimeError('Manual LinkedIn access is required; rerun in an interactive terminal.') from exc

        # User may have landed directly on a search result page in the same tab.
        if wait_for_search(page, api_capture, pause_ms=pause_ms, polls=4):
            return
        log('  Reopening your original search URL with its filters...')

    raise RuntimeError(
        'Sales Navigator search results did not become available. '
        'Select the correct Sales Navigator account in the browser, verify '
        'that your subscription can open the search manually, and rerun the '
        'collector. If necessary, copy a fresh lead-search URL.'
    )
