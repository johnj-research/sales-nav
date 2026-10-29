"""Read-only Sales Navigator lead search collector.

Operates in a visible, user-signed-in Playwright Chromium session. Does not
interact with leads, send messages, or bypass challenges. Optionally reads
first-party API responses already loaded by the search tab (no API replay).
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from playwright.sync_api import Error as PlaywrightError

from sn_browser import gather_search_page
from sn_navigation import open_search_with_manual_access, navigate_linkedin
from sn_voyager import BrowserSearchResponses
from sn_company_lookup import company_search_url, parse_company_search_results
from sn_enrich import (apply_company_reference, company_key,
                       enrich_existing_csv, load_company_references)
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
    api_only_leads: int = 0
    companies_discovered_by_search: int = 0
    companies_enriched_from_references: int = 0


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
    read_current_html: Callable[[], str | list[dict[str, str]]],
    move_next: Callable[[], bool],
    fetch_detail_html: Callable[[str], str],
    page_limit: int,
    *,
    include_profiles: bool = True,
    resolve_company_url: Callable[[str], str] | None = None,
    company_references: dict[str, dict[str, str]] | None = None,
    log: Callable[[str], None] = print,
) -> tuple[list[dict[str, str]], CollectionStats]:
    """Collect and enrich from a browser adapter; easy to test offline.

    read_current_html: current rendered HTML or merged per-page lead rows.
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
    # One public search per distinct company name, NOT per person.
    company_resolution_cache: dict[str, str] = {}
    company_references = company_references or {}
    company_fields = tuple(field for field in FIELDS if field.startswith('Company '))
    profile_fields = ('LinkedIn Member ID', 'Profile Summary',
                      'Company Start Year', 'Current Role Start Year')

    for n in range(1, page_limit + 1):
        source = read_current_html()
        if isinstance(source, str):
            if has_access_challenge(source):
                raise RuntimeError('Sales Navigator login or verification page shown. '
                                   'Complete it manually and restart; automation will not bypass it.')
            page_rows = parse_results(source)
        else:
            page_rows = source
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
                        profile = parse_profile(
                            profile_html, current_company=row['Company Name'],
                            current_job_title=row.get('Job Title', ''),
                        )
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

            # The original collector stopped here when search cards showed a
            # company name but no clickable company link (as in this export).
            # Exact-name reference rows and verified browser searches can now
            # provide the URL; no similar-name company is guessed.
            company_url = (row.get('_company_sales_url') or
                           row.get('_company_public_url') or
                           public_company_url(row.get('Company LinkedIn URL', '')))
            name_key = company_key(row.get('Company Name', ''))
            reference = company_references.get(name_key, {})
            if not company_url and reference.get('Company LinkedIn URL'):
                company_url = reference['Company LinkedIn URL']
                row['_company_sales_url'] = company_url
            if not company_url and name_key and resolve_company_url:
                if name_key not in company_resolution_cache:
                    try:
                        company_resolution_cache[name_key] = resolve_company_url(
                            row['Company Name']) or ''
                        if company_resolution_cache[name_key]:
                            stats.companies_discovered_by_search += 1
                    except Exception as exc:
                        company_resolution_cache[name_key] = ''
                        log(f'  Company lookup failed for {row["Company Name"]}: {exc}')
                company_url = company_resolution_cache[name_key]
                if company_url:
                    row['_company_sales_url'] = company_url
            if not company_url:
                stats.company_links_missing += 1
                if apply_company_reference(row, company_references):
                    stats.companies_enriched_from_references += 1
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
            # A Sales account URL alone does not guarantee a public Page link.
            # Try the same exact-name resolver used when the initial link is absent.
            public_url = public_url or reference.get('Company LinkedIn URL', '')
            if not public_url and name_key and resolve_company_url:
                if name_key not in company_resolution_cache:
                    try:
                        company_resolution_cache[name_key] = resolve_company_url(row['Company Name']) or ''
                        if company_resolution_cache[name_key]:
                            stats.companies_discovered_by_search += 1
                    except Exception as exc:
                        company_resolution_cache[name_key] = ''
                        log(f'  Public company lookup failed for {row["Company Name"]}: {exc}')
                public_url = public_company_url(company_resolution_cache[name_key])
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
            if apply_company_reference(row, company_references):
                stats.companies_enriched_from_references += 1
            all_rows.append(row)

        if n < page_limit and not move_next():
            log('No enabled Next page, or page did not change; stopping.')
            break

    result = deduplicate(all_rows)
    stats.companies_without_industry = sum(not r['Company Industry'] for r in result)
    return result, stats


NEXT_SELECTORS = (
    'button[aria-label="Next"]',
    'button[aria-label="Next page"]',
    'button[aria-label*="next" i]',
    'a[aria-label*="next page" i]',
    '[data-test-pagination-next-btn]',
    'button[data-test-id*="pagination-next" i]',
    'button[class*="pagination"][class*="next"]',
    '.artdeco-pagination__button--next',
    'a[rel="next"]',
)


def advance_search_results(page, previous_urls: set[str],
                           read_urls: Callable[[], set[str]] | None = None) -> bool:
    """Click only a search-pagination Next control, then confirm changed leads."""
    next_button = None
    disabled_control_seen = False
    for selector in NEXT_SELECTORS:
        matches = page.locator(selector)
        for index in range(min(matches.count(), 3)):
            candidate = matches.nth(index)
            if candidate.is_visible():
                if not candidate.is_enabled():
                    disabled_control_seen = True
                    continue
                next_button = candidate
                break
        if next_button is not None:
            break
    if next_button is None and disabled_control_seen:
        return False
    if next_button is None:
        # Small fallback for plain-text pagination buttons.
        matches = page.locator('button').filter(has_text=re.compile(r'^\s*Next\s*$', re.I))
        for index in range(min(matches.count(), 3)):
            candidate = matches.nth(index)
            if candidate.is_visible():
                if not candidate.is_enabled():
                    continue
                next_button = candidate
                break
    if next_button is None:
        return False

    before_url = page.url
    next_button.click(timeout=10000)
    # Confirm new results, including IDs supplied through browser-loaded APIs.
    # An altered URL alone isn't sufficient proof that cards updated.
    for _ in range(24):
        page.wait_for_timeout(600)
        current = (read_urls() if read_urls is not None else
                   {r['LinkedIn Sales Navigator URL'] for r in parse_results(page.content())})
        if current and (current - previous_urls):
            return True
        if current and current != previous_urls and page.url != before_url:
            return True
    return False


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description='Collect Sales Navigator lead search results into a 26-column CSV.')
    p.add_argument('--url', help='Full Sales Navigator lead search URL (prompted if missing).')
    p.add_argument('--enrich-csv', metavar='FILE',
                   help='Repair an existing 26-column CSV using --company-map; no browser needed.')
    p.add_argument('--company-map',
                   default=str(Path(__file__).with_name('company_reference.csv')),
                   help='Reviewed company details CSV for exact company-name matches.')
    p.add_argument('--no-company-search', action='store_true',
                   help='Disable fallback public LinkedIn company search in the signed-in browser.')
    p.add_argument('--pages', type=int, help='Maximum search result pages (prompted if missing).')
    p.add_argument('--output', default='data/sales_nav_leads.csv', help='CSV output path.')
    p.add_argument('--profile-dir', default='.sales_navigator_profile',
                   help='Local persistent Chromium profile (contains login session).')
    p.add_argument('--thorough-details', action='store_true',
                   help='Use longer detail waits for slow-loading pages (slower).')
    p.add_argument('--skip-profiles', action='store_true',
                   help='Skip profile detail pages (faster; summary/role dates likely blank).')
    p.add_argument('--pause-ms', type=int, default=800,
                   help='Pause between scroll passes and detail views, milliseconds (default: 800).')
    p.add_argument('--max-scroll-steps', type=int, default=70,
                   help='Maximum result-list scroll passes per page (default: 70).')
    p.add_argument('--no-voyager', action='store_true',
                   help='Disable passive parsing of search responses; DOM-only collection.')
    return p



def read_detail_html(page, url: str, pause_ms: int, *, thorough: bool = False,
                     current_company: str = '', current_job_title: str = '') -> str:
    """Allow delayed detail fields to render, including the final CSV columns."""
    is_company = urlparse(url).path.startswith(('/company/', '/sales/company/',
                                               '/sales/account/'))
    meaningful = (tuple(field for field in FIELDS if field.startswith('Company ')
                        and field not in ('Company Name', 'Company Start Year'))
                  if is_company else
                  ('Profile Summary', 'Company Start Year',
                   'Current Role Start Year', 'LinkedIn Member ID'))

    def values_from(html):
        values = (parse_company(html, url) if is_company else
                  parse_profile(html, current_company, current_job_title))
        return tuple(values.get(key, '') for key in meaningful)

    best_html = page.content()
    best_values = values_from(best_html)
    best_score = sum(bool(v) for v in best_values)
    previous_values = best_values
    stable = 0
    # Do not stop at five generic fields: country, logo and ID may arrive later.
    for _ in range(6):
        if has_access_challenge(best_html) or best_score == len(meaningful):
            break
        page.wait_for_timeout(max(450, min(pause_ms, 1200)) if thorough else 350)
        current_html = page.content()
        if has_access_challenge(current_html):
            return current_html
        current_values = values_from(current_html)
        current_score = sum(bool(v) for v in current_values)
        stable = stable + 1 if current_values == previous_values else 0
        previous_values = current_values
        if current_score >= best_score:
            best_html, best_score = current_html, current_score
        # An empty skeleton gets the full retry budget. Populated, unchanged
        # details usually indicate unavailable fields, not a pending render.
        ready = current_score >= (2 if is_company else 1)
        if not thorough and ready and stable >= 3:
            break
    return best_html


def run_browser(search_url: str, page_limit: int, output: str, profile_dir: str,
                skip_profiles: bool, pause_ms: int, max_scroll_steps: int = 70,
                use_voyager: bool = True,
                company_references: dict[str, dict[str, str]] | None = None,
                search_companies: bool = True,
                thorough_details: bool = False) -> None:
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
            api_capture = BrowserSearchResponses(enabled=use_voyager)
            company_search_page = browser.new_page() if search_companies else None
            # Listen before navigation so that the initial search response is captured.
            search_page.on('response', api_capture.handle_response)
            # Account/contract selection can interrupt goto() before DOMContentLoaded.
            # Prompt for manual access, then retry the exact original filtered URL.
            open_search_with_manual_access(
                search_page, search_url, api_capture, pause_ms=pause_ms,
            )

            def get_search_rows() -> list[dict[str, str]]:
                if has_access_challenge(search_page.content()):
                    raise RuntimeError('Sales Navigator login or verification page shown. '
                                       'Complete it manually; the collector will not bypass it.')
                return gather_search_page(
                    search_page, api_capture, pause_ms=pause_ms,
                    max_scroll_steps=max_scroll_steps,
                )

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
                navigate_linkedin(detail_page, url)
                detail_page.wait_for_timeout(max(pause_ms, 500) if thorough_details else 350)
                destination = urlparse(detail_page.url)
                if (destination.scheme != 'https' or not destination.hostname or
                        (destination.hostname != 'linkedin.com' and
                         not destination.hostname.endswith('.linkedin.com'))):
                    raise RuntimeError('Detail page redirected away from LinkedIn.')
                return read_detail_html(detail_page, url, pause_ms,
                                        thorough=thorough_details)

            def move_next() -> bool:
                # Clear stale API records before navigation to the next search page.
                old_urls = {r['LinkedIn Sales Navigator URL']
                            for r in parse_results(search_page.content()) + api_capture.rows}
                api_capture.clear_page()
                def current_page_urls() -> set[str]:
                    return {r['LinkedIn Sales Navigator URL']
                            for r in parse_results(search_page.content()) + api_capture.rows}
                return advance_search_results(search_page, old_urls, current_page_urls)

            def resolve_company(name: str) -> str:
                if company_search_page is None:
                    return ''
                navigate_linkedin(company_search_page, company_search_url(name))
                # A client-side search result list may arrive after DOM commit.
                # Poll the *rendered* page; do not call private LinkedIn APIs.
                for _ in range(8):
                    destination = urlparse(company_search_page.url)
                    if destination.hostname not in {'www.linkedin.com', 'linkedin.com'}:
                        return ''
                    found = parse_company_search_results(company_search_page.content(), name)
                    if found:
                        return found
                    if destination.path.startswith(('/login', '/checkpoint')):
                        return ''
                    company_search_page.wait_for_timeout(max(pause_ms, 650))
                return ''

            rows, stats = collect_pages(
                get_search_rows, move_next, fetch_detail, page_limit,
                include_profiles=not skip_profiles,
                resolve_company_url=resolve_company if search_companies else None,
                company_references=company_references,
            )
            write_csv(rows, output)
            review_path = str(Path(output).with_name(Path(output).stem + '_review.csv'))
            make_review_sample(output, review_path, n=10)
            report = inspect_csv(output)
            print(f'\nSaved {len(rows)} unique people from {stats.pages_processed} pages: {Path(output).resolve()}')
            print(f'10-person manual-review sample: {Path(review_path).resolve()}')
            print(f'Details visited: {stats.details_visited}; detail errors: {stats.detail_errors}')
            print(f'Observed search API responses: {api_capture.responses_seen}; '
                  f'with recognized leads: {api_capture.responses_with_leads}; '
                  f'parse errors: {api_capture.errors}')
            print(f'Company pages opened: {stats.company_pages_visited} '
                  f'(public About pages: {stats.public_about_pages_visited})')
            print(f'Rows enriched from exact-name company references: '
                  f'{stats.companies_enriched_from_references}; '
                  f'companies resolved via on-screen search: {stats.companies_discovered_by_search}')
            print(f'Rows without a discoverable company URL: {stats.company_links_missing}; '
                  f'rows without industry: {stats.companies_without_industry}')
            watched = ('Company Start Year', 'Current Role Start Year',
                       'Company Domain', 'Company Website', 'Company Employee Count',
                       'Company Employee Range', 'Company Founded Year',
                       'Company Industry', 'Company Type', 'Company Headquarters City',
                       'Company Country', 'Company Logo URL', 'Company LinkedIn ID',
                       'Company LinkedIn URL')
            print('Unfilled fields (blank / total exported leads):')
            for field in watched:
                blank = sum(not row.get(field) for row in rows)
                print(f'  {field}: {blank}/{len(rows)}')
            print(f'Company + industry coverage: {report["company_and_industry_coverage"]:.1%} '
                  f'(90% target: {"PASS" if report["quality_target_met"] else "NOT MET"})')
            print('Manual validation of at least 10 profiles is still required.')
        finally:
            browser.close()


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        references = load_company_references(args.company_map)
        if args.enrich_csv:
            if Path(args.enrich_csv).resolve() == Path(args.output).resolve():
                raise ValueError('Choose a different --output to preserve the original CSV.')
            total, enriched = enrich_existing_csv(args.enrich_csv, args.output, references)
            report = inspect_csv(args.output)
            print(f'Saved {total} leads; {enriched} rows received additional company '
                  f'details: {Path(args.output).resolve()}')
            print(f'Company + industry coverage: {report["company_and_industry_coverage"]:.1%}')
            return 0
        search_url = validate_search_url((args.url or input('Sales Navigator search URL: ')).strip())
        page_limit = args.pages if args.pages is not None else int(input('Number of pages: ').strip())
        if page_limit < 1 or page_limit > 100:
            raise ValueError('Choose between 1 and 100 pages.')
        if args.pause_ms < 350:
            raise ValueError('Pause must be at least 350 milliseconds.')
        if not 1 <= args.max_scroll_steps <= 250:
            raise ValueError('Choose between 1 and 250 scroll steps.')
        run_browser(search_url, page_limit, args.output, args.profile_dir,
                    args.skip_profiles, args.pause_ms, args.max_scroll_steps,
                    use_voyager=not args.no_voyager,
                    company_references=references,
                    search_companies=not args.no_company_search,
                    thorough_details=args.thorough_details)
        return 0
    except (ValueError, RuntimeError, OSError, KeyboardInterrupt, PlaywrightError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
