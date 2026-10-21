"""Read-only Sales Navigator lead search collector.

Operates in a visible, user-signed-in Playwright Chromium session. Does not
interact with leads, send messages, bypass challenges, or use internal APIs.
Data are extracted from rendered HTML, so absent/undocumented fields stay blank.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qsl, urlencode, urlparse

from bs4 import BeautifulSoup

from sn_core import (
    FIELDS, clean_sales_url, company_about_url, deduplicate, inspect_csv,
    make_review_sample, now_utc, parse_company, parse_profile, parse_results,
    public_company_url, validate_search_url, write_csv,
)

@dataclass
class CollectionStats:
    pages_processed: int = 0
    raw_leads: int = 0
    details_visited: int = 0
    detail_errors: int = 0
    company_links_missing: int = 0
    company_pages_visited: int = 0
    public_about_pages_visited: int = 0
    companies_without_industry: int = 0

@dataclass(frozen=True)
class ExportSummary:
    path: Path
    total_pages: int
    total_people: int
    new_people: int
    legacy_imported: bool

def _search_identity(url: str) -> tuple[str, int]:
    """Hash stable search parameters; ignore login session and page counters."""
    parsed = urlparse(validate_search_url(url))
    items = parse_qsl(parsed.query, keep_blank_values=True)
    ignored = {'sessionid', 'page', 'pagenumber'}
    normalized = sorted((key, value) for key, value in items
                        if key.lower() not in ignored)
    signature = hashlib.sha256(
        (parsed.path.rstrip('/') + '?' + urlencode(normalized)).encode('utf-8')
    ).hexdigest()
    start_page = 1
    for key, value in items:
        if key.lower() in {'page', 'pagenumber'} and value.isdigit() and int(value) > 0:
            start_page = int(value)
            break
    return signature, start_page

def _read_existing_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline='', encoding='utf-8-sig') as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != FIELDS:
            raise ValueError(f'Existing CSV has unexpected headers: {path}. '
                             'Original file was not changed.')
        return list(reader)

def _legacy_backup_path(path: Path) -> Path:
    candidate = path.with_name(path.stem + '_legacy.bak')
    number = 2
    while candidate.exists():
        candidate = path.with_name(f'{path.stem}_legacy_{number}.bak')
        number += 1
    return candidate

def export_accumulated(
    new_rows: list[dict[str, str]],
    output: str | Path,
    search_url: str,
    pages_processed: int,
) -> ExportSummary:
    """Merge runs and retain one current 26-column CSV with total-page count.

    Metadata lives in an adjacent JSON file. For the same search, page numbers
    seen in prior runs are unioned, so 1 page followed by 3 pages counts as 3.
    For separate searches, their page counts add up. This tracks pages visited,
    not whether every lead on each page was successfully captured.
    """
    if pages_processed < 1:
        raise ValueError('pages_processed must be positive; no CSV was written.')
    if not new_rows:
        raise ValueError('No new rows; existing CSV was not changed.')

    base = Path(output)
    state_path = base.with_name(base.stem + '_state.json')
    search_id, start_page = _search_identity(search_url)
    state = {'version': 1, 'current_csv': None, 'pages': {}}

    if state_path.exists():
        with state_path.open(encoding='utf-8') as stream:
            state = json.load(stream)
        if (state.get('version') != 1 or not isinstance(state.get('pages'), dict)
                or not isinstance(state.get('current_csv'), str)):
            raise ValueError(f'Invalid state file: {state_path}. Existing CSV was not changed.')
        previous_name = state['current_csv']
        if (Path(previous_name).name != previous_name or
                not re.fullmatch(re.escape(base.stem) + r'_\d+_pages\.csv', previous_name)):
            raise ValueError('Unexpected previous CSV filename in state; refusing to overwrite.')
        previous_path = base.with_name(previous_name)
        if not previous_path.is_file():
            raise FileNotFoundError(f'Tracked CSV is missing: {previous_path}. '
                                    'Restore it before continuing to avoid data loss.')
        previous_rows = _read_existing_csv(previous_path)
        legacy_imported = False
    elif base.is_file():
        # Older V2 created a fixed name; import its rows and keep a .bak copy.
        previous_path = base
        previous_rows = _read_existing_csv(previous_path)
        legacy_imported = True
    else:
        previous_path = None
        previous_rows = []
        legacy_imported = False

    combined = deduplicate(previous_rows + new_rows)
    if not combined:
        raise ValueError('No identifiable leads; existing CSV was not changed.')

    page_history = {}
    for key, pages in state['pages'].items():
        if (not isinstance(key, str) or not isinstance(pages, list) or
                any(not isinstance(p, int) or isinstance(p, bool) or p < 1 for p in pages)):
            raise ValueError('Invalid page history state. Existing CSV was not changed.')
        page_history[key] = set(pages)
    page_history.setdefault(search_id, set()).update(
        range(start_page, start_page + pages_processed)
    )
    total_pages = sum(len(page_set) for page_set in page_history.values())
    current_path = base.with_name(f'{base.stem}_{total_pages}_pages.csv')
    # Guard against an unrelated file with the same requested destination.
    if current_path.exists() and current_path != previous_path:
        raise FileExistsError(f'Cannot replace pre-existing file: {current_path}. '
                              'Choose another --output base name.')

    write_csv(combined, current_path)
    next_state = {
        'version': 1,
        'current_csv': current_path.name,
        'pages': {key: sorted(pages) for key, pages in page_history.items()},
    }
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_tmp = state_path.with_name(state_path.name + '.tmp')
    with state_tmp.open('w', encoding='utf-8') as stream:
        json.dump(next_state, stream, indent=2)
    state_tmp.replace(state_path)

    if previous_path is not None and previous_path != current_path:
        if legacy_imported:
            previous_path.rename(_legacy_backup_path(previous_path))
        else:
            previous_path.unlink()

    return ExportSummary(
        path=current_path,
        total_pages=total_pages,
        total_people=len(combined),
        new_people=len(combined) - len(deduplicate(previous_rows)),
        legacy_imported=legacy_imported,
    )

def has_access_challenge(html: str) -> bool:
    """Recognize common full-page login/challenge views; not normal page nav."""
    soup = BeautifulSoup(html, 'html.parser')
    title = soup.title.get_text(' ', strip=True).lower() if soup.title else ''
    body = soup.get_text(' ', strip=True).lower()[:4000]
    if any(token in title for token in ('sign in', 'log in', 'security verification',
                                        'captcha', 'checkpoint')):
        return True
    if soup.select_one('form[action*="/login"], form[action*="/uas/login"]'):
        return True
    return any(token in body for token in (
        'verify you are human', 'security verification: ',
        'complete this captcha', 'please complete a security check',
    ))


def _fill_missing(row: dict[str, str], enrichment: dict[str, str], fields: tuple[str, ...]) -> None:
    for field in fields:
        if not row.get(field) and enrichment.get(field):
            row[field] = enrichment[field]

def collect_pages(
    read_current_html: Callable[[], str],
    move_next: Callable[[], bool],
    fetch_detail_html: Callable[[str], str],
    page_limit: int,
    *,
    include_profiles: bool = True,
    log: Callable[[str], None] = print,
) -> tuple[list[dict[str, str]], CollectionStats]:
    """Collect and enrich from a browser adapter; easy to test offline.

    read_current_html: current search page's rendered HTML.
    move_next: click 'Next' and return True only if page advances.
    fetch_detail_html: visit an allowed lead/company URL and return HTML.
    """
    if page_limit < 1:
        raise ValueError('Pages must be at least 1.')

    stats = CollectionStats()
    all_rows: list[dict[str, str]] = []
    seen_profiles: set[str] = set()
    seen_pages: set[tuple[str, ...]] = set()
    company_cache: dict[str, dict[str, str]] = {}
    company_fields = tuple(field for field in FIELDS if field.startswith('Company '))
    profile_fields = ('LinkedIn Member ID', 'Profile Summary',
                      'Company Start Year', 'Current Role Start Year')

    for n in range(1, page_limit + 1):
        html = read_current_html()
        if has_access_challenge(html):
            raise RuntimeError('Sales Navigator login or verification page shown. '
                               'Complete it manually and restart; automation will not bypass it.')
        page_rows = parse_results(html)
        signature = tuple(sorted(row['LinkedIn Sales Navigator URL'] for row in page_rows))
        if signature in seen_pages:
            log('Repeated search page encountered; stopping.')
            break
        if not page_rows:
            if n == 1:
                raise RuntimeError('No lead results found. Check the search URL, login status, '
                                   'and whether your Sales Navigator layout is supported.')
            log('Search page has no lead results; stopping.')
            break
        seen_pages.add(signature)
        stats.pages_processed += 1
        stats.raw_leads += len(page_rows)
        log(f'Page {n}/{page_limit}: found {len(page_rows)} lead links.')

        for row in page_rows:
            lead_url = row['LinkedIn Sales Navigator URL']
            if lead_url not in seen_profiles:
                seen_profiles.add(lead_url)
                row['Captured At'] = now_utc()
                if include_profiles:
                    try:
                        profile_html = fetch_detail_html(lead_url)
                        if has_access_challenge(profile_html):
                            raise RuntimeError('Login or verification on profile')
                        profile = parse_profile(profile_html, current_company=row['Company Name'])
                        _fill_missing(row, profile, profile_fields + ('Company LinkedIn URL',))
                        for key in ('_company_sales_url', '_company_public_url'):
                            if not row.get(key) and profile.get(key):
                                row[key] = profile[key]
                        stats.details_visited += 1
                    except Exception as exc:
                        stats.detail_errors += 1
                        log(f'  Profile details unavailable for {lead_url}: {exc}')
            else:
                row['Captured At'] = now_utc()

            company_url = row.get('_company_sales_url', '')
            if not company_url:
                stats.company_links_missing += 1
                all_rows.append(row)
                continue

            # Always use /company/<slug>/about/ for a public LinkedIn Page.
            # Sales Navigator account pages are opened first; their public
            # company-page link may provide more labeled business details.
            detail_url = company_about_url(company_url)
            if detail_url not in company_cache:
                try:
                    company_html = fetch_detail_html(detail_url)
                    if has_access_challenge(company_html):
                        raise RuntimeError('Login or verification on company page')
                    company_cache[detail_url] = parse_company(company_html, detail_url)
                    stats.details_visited += 1
                    stats.company_pages_visited += 1
                    if public_company_url(detail_url):
                        stats.public_about_pages_visited += 1
                except Exception as exc:
                    company_cache[detail_url] = {}
                    stats.detail_errors += 1
                    log(f'  Company details unavailable for {detail_url}: {exc}')
            company = company_cache[detail_url]
            _fill_missing(row, company, company_fields)

            # When a Sales account page points to its public LinkedIn Page,
            # read the About page once per company for richer firmographics.
            public_url = (row.get('_company_public_url') or
                          company.get('_company_public_url') or
                          public_company_url(company_url))
            if public_url and not public_company_url(detail_url):
                about_url = company_about_url(public_url)
                if about_url not in company_cache:
                    try:
                        public_html = fetch_detail_html(about_url)
                        if has_access_challenge(public_html):
                            raise RuntimeError('Login or verification on public About page')
                        company_cache[about_url] = parse_company(public_html, about_url)
                        stats.details_visited += 1
                        stats.company_pages_visited += 1
                        stats.public_about_pages_visited += 1
                    except Exception as exc:
                        company_cache[about_url] = {}
                        stats.detail_errors += 1
                        log(f'  Public company About unavailable for {about_url}: {exc}')
                _fill_missing(row, company_cache[about_url], company_fields)
            all_rows.append(row)

        if n < page_limit and not move_next():
            log('No enabled Next page, or page did not change; stopping.')
            break

    result = deduplicate(all_rows)
    stats.companies_without_industry = sum(not r['Company Industry'] for r in result)
    return result, stats

# Sales Navigator may render only a few search-result cards at a time.
# Scroll the results PANEL, not whichever element the mouse happens to hover.
_RESET_RESULTS_SCROLL_JS = r"""() => {
  const first = document.querySelector(
    'a[href*="/sales/lead/"], a[href*="/sales/people/"]');
  for (let node = first && first.parentElement;
       node && node !== document.body;
       node = node.parentElement) {
    const overflowY = getComputedStyle(node).overflowY;
    if (node.scrollHeight > node.clientHeight + 50 &&
        /auto|scroll|overlay/.test(overflowY)) {
      node.scrollTop = 0;
      return true;
    }
  }
  if (document.scrollingElement) document.scrollingElement.scrollTop = 0;
  return false;
}"""

_SCROLL_RESULTS_JS = r"""() => {
  const links = [...document.querySelectorAll(
    'a[href*="/sales/lead/"], a[href*="/sales/people/"]')];
  const last = links[links.length - 1];
  for (let node = last && last.parentElement;
       node && node !== document.body;
       node = node.parentElement) {
    const overflowY = getComputedStyle(node).overflowY;
    if (node.scrollHeight > node.clientHeight + 50 &&
        /auto|scroll|overlay/.test(overflowY)) {
      node.scrollTop += Math.max(450, Math.floor(node.clientHeight * 0.8));
      return node.scrollTop + node.clientHeight >= node.scrollHeight - 5;
    }
  }
  const root = document.scrollingElement || document.documentElement;
  window.scrollBy(0, Math.max(450, Math.floor(window.innerHeight * 0.8)));
  return root.scrollTop + root.clientHeight >= root.scrollHeight - 5;
}"""

def capture_search_page(page, pause_ms: int, expected: int = 25) -> str:
    """Accumulate virtualized lead cards for one results page.

    The 25-lead target is a soft target: last pages and some searches have
    fewer. Stop on a stable bottom-of-list or after bounded attempts.
    """
    page.evaluate(_RESET_RESULTS_SCROLL_JS)
    page.wait_for_timeout(max(pause_ms, 500))
    snapshots: list[str] = []
    seen: set[str] = set()
    unchanged = 0
    for attempt in range(40):
        html = page.content()
        snapshots.append(html)
        before = len(seen)
        seen.update(r['LinkedIn Sales Navigator URL'] for r in parse_results(html))
        unchanged = unchanged + 1 if len(seen) == before else 0
        if len(seen) >= expected:
            break
        # Preserve existing manual-login flow when no leads are present.
        if not seen and attempt >= 7:
            break
        at_bottom = page.evaluate(_SCROLL_RESULTS_JS) if seen else False
        page.wait_for_timeout(max(pause_ms, 500))
        if at_bottom and unchanged >= 4:
            break

    print(f'Search-page capture: {len(seen)} unique leads observed '
          f'(target {expected}).')
    return '\n'.join(snapshots)

NEXT_SELECTORS = (
    'button[aria-label="Next"]',
    'button[aria-label="Next page"]',
    'button[aria-label*="next page" i]',
    'button[class*="pagination"][class*="next"]',
    '.artdeco-pagination__button--next',
    'a[rel="next"]',
)

def advance_search_results(page, previous_urls: set[str]) -> bool:
    """Click only a search-pagination Next control, then confirm changed leads."""
    next_button = None
    for selector in NEXT_SELECTORS:
        matches = page.locator(selector)
        for index in range(min(matches.count(), 3)):
            candidate = matches.nth(index)
            if candidate.is_visible():
                if not candidate.is_enabled():
                    return False
                next_button = candidate
                break
        if next_button is not None:
            break
    if next_button is None:
        # Small fallback for plain-text pagination buttons.
        matches = page.locator('button').filter(has_text=re.compile(r'^\s*Next\s*$', re.I))
        for index in range(min(matches.count(), 3)):
            candidate = matches.nth(index)
            if candidate.is_visible():
                if not candidate.is_enabled():
                    return False
                next_button = candidate
                break
    if next_button is None:
        return False

    next_button.click(timeout=10000)
    # A changing URL alone does not guarantee new results; verify visible lead IDs.
    for _ in range(20):
        page.wait_for_timeout(750)
        current = {r['LinkedIn Sales Navigator URL'] for r in parse_results(page.content())}
        if current and current != previous_urls:
            return True
    return False

def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description='Collect Sales Navigator lead search results into a 26-column CSV.')
    p.add_argument('--url', help='Full Sales Navigator lead search URL (prompted if missing).')
    p.add_argument('--pages', type=int, help='Maximum search result pages (prompted if missing).')
    p.add_argument('--output', default='data/sales_nav_leads.csv', help='CSV output path.')
    p.add_argument('--profile-dir', default='.sales_navigator_profile',
                   help='Local persistent Chromium profile (contains login session).')
    p.add_argument('--skip-profiles', action='store_true',
                   help='Skip profile detail pages (faster; summary/role dates likely blank).')
    p.add_argument('--pause-ms', type=int, default=1100,
                   help='Pause between pages, in milliseconds (default: 1100).')
    return p

def run_browser(search_url: str, page_limit: int, output: str, profile_dir: str,
                skip_profiles: bool, pause_ms: int) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        # Reuses a dedicated profile. No session cookies or credentials are exported.
        browser = playwright.chromium.launch_persistent_context(
            user_data_dir=str(Path(profile_dir).resolve()), headless=False,
            viewport={'width': 1440, 'height': 950},
        )
        try:
            search_page = browser.pages[0] if browser.pages else browser.new_page()
            detail_page = browser.new_page()
            search_page.goto(search_url, wait_until='domcontentloaded', timeout=60000)
            search_page.wait_for_timeout(1600)

            def get_search_html() -> str:
                return capture_search_page(search_page, pause_ms=pause_ms, expected=25)

            if not parse_results(get_search_html()):
                print('\nIf this browser is asking you to sign in, sign in manually.')
                print('Wait until you can see the Sales Navigator search results.')
                input('Then press Enter here to continue... ')
                search_page.goto(search_url, wait_until='domcontentloaded', timeout=60000)

            def fetch_detail(url: str) -> str:
                parsed = urlparse(url)
                if parsed.scheme != 'https' or parsed.hostname != 'www.linkedin.com' or not (
                    parsed.path.startswith('/sales/lead/') or
                    parsed.path.startswith('/sales/people/') or
                    parsed.path.startswith('/sales/company/') or
                    parsed.path.startswith('/sales/account/') or
                    parsed.path.startswith('/company/')
                ):
                    raise ValueError('Unsafe or unsupported detail URL')
                detail_page.goto(url, wait_until='domcontentloaded', timeout=60000)
                detail_page.wait_for_timeout(max(pause_ms, 500))
                destination = urlparse(detail_page.url)
                if (destination.scheme != 'https' or not destination.hostname or
                        (destination.hostname != 'linkedin.com' and
                         not destination.hostname.endswith('.linkedin.com'))):
                    raise RuntimeError('Detail page redirected away from LinkedIn.')
                return detail_page.content()

            def move_next() -> bool:
                old_urls = {r['LinkedIn Sales Navigator URL']
                            for r in parse_results(search_page.content())}
                return advance_search_results(search_page, old_urls)

            rows, stats = collect_pages(get_search_html, move_next, fetch_detail,
                                        page_limit, include_profiles=not skip_profiles)
            export = export_accumulated(rows, output, search_url, stats.pages_processed)
            actual_output = export.path
            review_path = str(actual_output.with_name(actual_output.stem + '_review.csv'))
            make_review_sample(actual_output, review_path, n=10)
            report = inspect_csv(actual_output)
            print(f'\nSaved {export.total_people} unique people '
                  f'({export.new_people} newly added) to: {actual_output.resolve()}')
            print(f'Pages visited: {stats.pages_processed} this run; '
                  f'{export.total_pages} distinct tracked search pages across runs.')
            if export.legacy_imported:
                print('Imported the old unnumbered CSV and kept a *_legacy.bak backup. '
                      'Previous untracked pages cannot be counted.')
            print(f'10-person manual-review sample: {Path(review_path).resolve()}')
            print(f'Details visited: {stats.details_visited}; detail errors: {stats.detail_errors}')
            print(f'Company pages opened: {stats.company_pages_visited} '
                  f'(public About pages: {stats.public_about_pages_visited})')
            print(f'Rows without a discoverable company URL: {stats.company_links_missing}; '
                  f'rows without industry: {stats.companies_without_industry}')
            print(f'Company + industry coverage: {report["company_and_industry_coverage"]:.1%} '
                  f'(90% target: {"PASS" if report["quality_target_met"] else "NOT MET"})')
            print('Manual validation of at least 10 profiles is still required.')
        finally:
            browser.close()

def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        search_url = validate_search_url((args.url or input('Sales Navigator search URL: ')).strip())
        page_limit = args.pages if args.pages is not None else int(input('Number of pages: ').strip())
        if page_limit < 1 or page_limit > 100:
            raise ValueError('Choose between 1 and 100 pages.')
        if args.pause_ms < 500:
            raise ValueError('Pause must be at least 500 milliseconds.')
        run_browser(search_url, page_limit, args.output, args.profile_dir,
                    args.skip_profiles, args.pause_ms)
        return 0
    except (ValueError, RuntimeError, OSError, KeyboardInterrupt) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1

if __name__ == '__main__':
    sys.exit(main())