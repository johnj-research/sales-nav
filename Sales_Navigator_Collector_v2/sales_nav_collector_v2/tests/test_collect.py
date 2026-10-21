"""Offline behavior tests: no requests to LinkedIn are made."""
from copy import deepcopy

import pytest

from sn_collect import collect_pages, has_access_challenge, advance_search_results


def search_html(names):
    return '<html><body><ul>' + ''.join(
        f'''<li><a href="/sales/lead/{slug}"><span data-anonymize="person-name">{name}</span></a>
        <span data-anonymize="title">Partner</span>
        <a href="/sales/company/42"><span data-anonymize="company-name">Alpha Advisory</span></a>
        <span data-anonymize="location">Dublin, Ireland</span></li>'''
        for slug, name in names
    ) + '</ul></body></html>'


COMPANY = '''<html><h1>Alpha Advisory</h1><dl>
  <dt>Industry</dt><dd>Financial Services</dd>
  <dt>Website</dt><dd><a href="https://alpha.example">alpha.example</a></dd>
  <dt>Founded</dt><dd>2019</dd></dl></html>'''
PROFILE = '''<html><dl><dt>About</dt><dd>Advises business owners.</dd></dl></html>'''


def test_three_pages_deduplicated_and_company_cached():
    pages = [search_html([('A','Ana Costa'), ('B','Ben Jones')]),
             search_html([('B','Ben Jones'), ('C','Cleo M')]),
             search_html([('D','Dan N'), ('E','Eve Z')])]
    idx = 0
    visits = []
    def current():
        return pages[idx]
    def next_page():
        nonlocal idx
        if idx + 1 >= len(pages):
            return False
        idx += 1
        return True
    def fetch(url):
        visits.append(url)
        return COMPANY if '/sales/company/' in url else PROFILE

    rows, stats = collect_pages(current, next_page, fetch, 3)
    assert stats.pages_processed == 3
    assert len(rows) == 5
    assert len([r for r in rows if r['Sales Profile ID'] == 'B']) == 1
    assert visits.count('https://www.linkedin.com/sales/company/42') == 1
    assert len([v for v in visits if '/sales/lead/' in v]) == 5
    assert all(row['Company Industry'] == 'Financial Services' for row in rows)
    assert all(row['Captured At'] for row in rows)
    assert all(row['Company Name'] == 'Alpha Advisory' for row in rows)


def test_end_when_next_not_available():
    rows, stats = collect_pages(lambda: search_html([('A','Ana Costa')]),
                                lambda: False, lambda url: COMPANY, 100,
                                include_profiles=False)
    assert len(rows) == 1
    assert stats.pages_processed == 1


def test_no_results_fails_clearly():
    with pytest.raises(RuntimeError, match='No lead results'):
        collect_pages(lambda: '<html><body>No results</body></html>',
                      lambda: False, lambda url: '', 3)


def test_blocked_page_fails_without_retry_or_bypass():
    assert has_access_challenge('<html><title>Sign in | LinkedIn</title><body>Sign in</body></html>')
    assert has_access_challenge('<html><body>Security verification: verify you are human</body></html>')
    with pytest.raises(RuntimeError, match='login or verification'):
        collect_pages(lambda: '<html><title>Sign in | LinkedIn</title></html>',
                      lambda: False, lambda url: '', 3)


def test_detail_unavailable_leaves_empty_cells():
    def bad_detail(url):
        raise ValueError('Detail unavailable')
    rows, stats = collect_pages(lambda: search_html([('A','Ana Costa')]),
                                lambda: False, bad_detail, 1)
    assert rows[0]['Full Name'] == 'Ana Costa'
    assert rows[0]['Company Industry'] == ''
    assert rows[0]['Profile Summary'] == ''
    assert stats.detail_errors >= 1


def test_repeated_page_stops_without_infinite_loop():
    rows, stats = collect_pages(lambda: search_html([('A','Ana Costa')]),
                                lambda: True, lambda url: COMPANY, 100,
                                include_profiles=False)
    assert len(rows) == 1
    assert stats.pages_processed == 1


class FakeLocator:
    def __init__(self, enabled):
        self.enabled = enabled
        self.clicked = 0
    def count(self): return 1
    def nth(self, index): return self
    def is_visible(self): return True
    def is_enabled(self): return self.enabled
    def click(self, timeout): self.clicked += 1


class FakePage:
    def __init__(self, enabled):
        self.loc = FakeLocator(enabled)
        self.url = 'https://www.linkedin.com/sales/search/people?page=1'
        self.html = search_html([('A','Ana Costa')])
    def locator(self, selector):
        if selector == 'button[aria-label="Next"]': return self.loc
        return EmptyLocator()
    def content(self): return self.html
    def wait_for_timeout(self, ms):
        if self.loc.clicked:
            self.html = search_html([('B','Ben Jones')])
            self.url = 'https://www.linkedin.com/sales/search/people?page=2'


class EmptyLocator:
    def count(self): return 0


def test_click_next_button_only_when_enabled():
    active = FakePage(True)
    assert advance_search_results(active, {'https://www.linkedin.com/sales/lead/A'}) is True
    assert active.loc.clicked == 1
    disabled = FakePage(False)
    assert advance_search_results(disabled, {'https://www.linkedin.com/sales/lead/A'}) is False
    assert disabled.loc.clicked == 0


def test_missing_result_company_link_is_recovered_from_matching_lead_profile():
    search = '''<html><body><li>
      <a href="/sales/lead/A"><span data-anonymize="person-name">Ana Costa</span></a>
      <span data-anonymize="company-name">Alpha Advisory</span>
    </li></body></html>'''
    profile = '''<html><body><section id="experience">
      <a href="/company/alpha-advisory">Alpha Advisory</a>
      <a href="/company/old-co">Old Co</a>
    </section></body></html>'''
    company = '''<html><body><h1>Alpha Advisory</h1><dl>
      <dt>Website</dt><dd><a href="https://alpha.example">Visit website</a></dd>
      <dt>Industry</dt><dd>Financial Services</dd>
    </dl></body></html>'''
    visited = []
    def fetch(url):
        visited.append(url)
        if '/sales/lead/' in url:
            return profile
        if '/company/alpha-advisory/about' in url:
            return company
        raise AssertionError('Unexpected company visit: ' + url)
    rows, stats = collect_pages(lambda: search, lambda: False, fetch, 1)
    assert len(rows) == 1
    assert rows[0]['Company Industry'] == 'Financial Services'
    assert rows[0]['Company Website'] == 'https://alpha.example'
    assert rows[0]['Company LinkedIn URL'] == 'https://www.linkedin.com/company/alpha-advisory'
    assert 'https://www.linkedin.com/company/alpha-advisory/about/' in visited


def test_sales_account_without_fields_follows_its_explicit_public_company_link():
    search = search_html([('A', 'Ana Costa')])
    account = '''<html><body><h1>Alpha Advisory</h1>
      <a href="/company/alpha-advisory">LinkedIn company page</a></body></html>'''
    about = '''<html><body><h1>Alpha Advisory</h1><dl>
      <dt>Industry</dt><dd>Investment Management</dd>
      <dt>Founded</dt><dd>2017</dd>
    </dl></body></html>'''
    visited = []
    def fetch(url):
        visited.append(url)
        if url.endswith('/sales/company/42'):
            return account
        if url.endswith('/company/alpha-advisory/about/'):
            return about
        if '/sales/lead/' in url:
            return PROFILE
        raise AssertionError('Unexpected URL ' + url)
    rows, stats = collect_pages(lambda: search, lambda: False, fetch, 1)
    assert rows[0]['Company Industry'] == 'Investment Management'
    assert rows[0]['Company Founded Year'] == '2017'
    assert rows[0]['Company LinkedIn URL'] == 'https://www.linkedin.com/company/alpha-advisory'
    assert 'https://www.linkedin.com/company/alpha-advisory/about/' in visited
