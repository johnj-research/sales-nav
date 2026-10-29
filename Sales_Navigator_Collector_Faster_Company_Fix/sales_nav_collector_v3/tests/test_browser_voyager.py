"""Regression tests for virtualized 4-card results and API search fallback."""
import json

from sn_browser import gather_search_page, merge_page_rows
from sn_collect import collect_pages
from sn_voyager import (BrowserSearchResponses, is_search_api_url,
                        parse_search_response)


def page_html(numbers):
    return '<ul>' + ''.join(
        f'<li><a href="/sales/lead/ID{i},NAME_SEARCH,t{i}">'
        f'<span data-anonymize="person-name">Person {i}</span></a>'
        f'<span data-anonymize="title">Leader {i}</span></li>'
        for i in numbers
    ) + '</ul>'


class VirtualizedPage:
    """Only four of 25 result cards exist in the DOM at any time."""
    def __init__(self, count=25):
        self.count = count
        self.position = 0
        self.scrolls = 0

    def content(self):
        return page_html(range(self.position + 1, min(self.count + 1, self.position + 5)))

    def evaluate(self, script):
        assert 'scrollTop' in script
        self.scrolls += 1
        before = self.position
        self.position = min(self.count - 4, self.position + 3)
        return {'moved': before != self.position,
                'atBottom': self.position >= self.count - 4}

    def wait_for_timeout(self, ms):
        pass


def test_scroll_virtualization_finds_all_25_not_4():
    browser = VirtualizedPage()
    rows = gather_search_page(browser, pause_ms=350)
    assert len(rows) == 25
    assert rows[0]['Full Name'] == 'Person 1'
    assert rows[-1]['Full Name'] == 'Person 25'
    assert browser.scrolls >= 8


def test_scroll_limit_bounds_broken_page():
    browser = VirtualizedPage(2000)
    rows = gather_search_page(browser, pause_ms=350, max_scroll_steps=2, log=lambda _: None)
    assert len(rows) >= 4
    assert browser.scrolls == 2


def test_browser_rows_work_with_original_collector_pipeline():
    browser = VirtualizedPage()
    rows, stats = collect_pages(
        lambda: gather_search_page(browser, pause_ms=350),
        lambda: False, lambda url: '', 1, include_profiles=False,
    )
    assert stats.pages_processed == 1
    assert stats.raw_leads == len(rows) == 25


def test_realistic_sales_search_api_current_positions():
    obj = {'elements': [
        {'entityUrn': f'urn:li:fs_salesProfile:(ID{i},NAME_SEARCH,abc{i})',
         'fullName': f'Person {i}', 'geoRegion': 'London, UK',
         'currentPositions': [{'title': 'Director', 'companyName': 'Example Co'}]}
        for i in range(1, 26)
    ]}
    rows = parse_search_response(obj)
    assert len(rows) == 25
    assert rows[6]['Full Name'] == 'Person 7'
    assert rows[6]['Company Name'] == 'Example Co'
    assert rows[6]['Job Title'] == 'Director'
    assert rows[6]['Location'] == 'London, UK'
    assert rows[6]['LinkedIn Sales Navigator URL'].endswith('/ID7,NAME_SEARCH,abc7')


def test_api_does_not_use_past_employers_as_current():
    obj = {'elements': [{'fullName': 'Ali K',
                         'entityUrn': 'urn:li:fs_salesProfile:(ABC,NAME_SEARCH,token)',
                         'pastPositions': [{'title': 'CEO', 'companyName': 'Old Co'}]}]}
    parsed = parse_search_response(obj)
    assert len(parsed) == 1
    assert parsed[0]['Current Company'] == ''
    assert parsed[0]['Job Title'] == ''


def test_api_requires_name_and_sales_identity():
    response = {'data': {'search': {'elements': [
        {'name': 'Has no URL', 'title': 'CTO'},
        {'entityUrn': 'urn:li:fs_salesProfile:(token,NAME_SEARCH,xyz)'},
        {'entityUrn': 'urn:li:fs_miniProfile:123', 'name': 'Unrelated Person'},
        {'fullName': 'Valid Person', 'leadUrl': '/sales/lead/valid'},
    ]}}}
    results = parse_search_response(response)
    assert len(results) == 1
    assert results[0]['Full Name'] == 'Valid Person'


def test_api_merges_with_dom_on_stable_sales_id():
    dom = parse_search_response({'elements': [
        {'leadUrl': '/sales/lead/abc,NAME_SEARCH,t1', 'fullName': 'Jane X'}]})
    from_html = {'Sales Profile ID': 'abc,OTHER_SEARCH,t2',
                 'LinkedIn Sales Navigator URL': 'https://www.linkedin.com/sales/lead/abc,OTHER_SEARCH,t2',
                 'Full Name': 'Jane X', 'Job Title': 'CFO'}
    joined = merge_page_rows([from_html], dom)
    assert len(joined) == 1
    assert joined[0]['Job Title'] == 'CFO'


class FakeResponse:
    def __init__(self, url, data, status=200, content_type='application/json'):
        self.url = url
        self.status = status
        self.headers = {'content-type': content_type}
        self._body = json.dumps(data).encode()
    def body(self): return self._body


def test_api_observes_only_relevant_first_party_search_calls():
    assert is_search_api_url('https://www.linkedin.com/sales-api/salesApiLeadSearch?q=searchQuery')
    assert is_search_api_url('https://www.linkedin.com/voyager/api/graphql?variables=(search:people)')
    assert not is_search_api_url('https://evil.example/sales-api/salesApiLeadSearch')
    assert not is_search_api_url('https://www.linkedin.com/sales-api/salesApiMessages')
    capture = BrowserSearchResponses()
    data = {'elements': [{'fullName': 'Lead A', 'leadUrl': '/sales/lead/a'}]}
    capture.handle_response(FakeResponse('https://www.linkedin.com/sales-api/salesApiLeadSearch', data))
    capture.handle_response(FakeResponse('https://evil.example/sales-api/salesApiLeadSearch', data))
    assert capture.responses_seen == 1
    assert capture.responses_with_leads == 1
    assert len(capture.rows) == 1
    capture.clear_page()
    assert capture.rows == []


def test_api_disabled_never_reads_network_bodies():
    cap = BrowserSearchResponses(enabled=False)
    cap.handle_response(FakeResponse('https://www.linkedin.com/sales-api/salesApiLeadSearch',
                                     {'elements': [{'fullName': 'X', 'leadUrl': '/sales/lead/x'}]}))
    assert cap.responses_seen == 0
    assert not cap.rows


def test_three_virtualized_pages_collect_75_unique_people():
    current_page = 0
    virtual_page = VirtualizedPage(25)

    def read():
        rows = gather_search_page(virtual_page, pause_ms=350, log=lambda _: None)
        for row in rows:
            number = int(row['Full Name'].split()[-1]) + current_page * 25
            row['Full Name'] = f'Person {number}'
            row['LinkedIn Sales Navigator URL'] = f'https://www.linkedin.com/sales/lead/P{number}'
            row['Sales Profile ID'] = f'P{number}'
        return rows

    def move():
        nonlocal current_page, virtual_page
        current_page += 1
        virtual_page = VirtualizedPage(25)
        return True

    rows, stats = collect_pages(read, move, lambda url: '', 3, include_profiles=False)
    assert len(rows) == 75
    assert stats.pages_processed == 3
    assert rows[0]['Sales Profile ID'] == 'P1'
    assert rows[-1]['Sales Profile ID'] == 'P75'


def test_api_supplies_all_25_even_when_dom_has_four():
    cap = BrowserSearchResponses()
    api_rows = {'elements': [{'entityUrn': f'urn:li:fs_salesProfile:(ID{i},NAME_SEARCH,t{i})',
                              'fullName': f'Person {i}',
                              'currentPositions': [{'title': 'Manager'}]}
                             for i in range(1, 26)]}
    cap.handle_response(FakeResponse('https://www.linkedin.com/sales-api/salesApiLeadSearch', api_rows))
    page = VirtualizedPage(4)
    rows = gather_search_page(page, cap, pause_ms=350, log=lambda _: None)
    assert len(rows) == 25
    assert all(r['Job Title'] for r in rows)


def test_passive_search_response_reads_explicit_firmographics_and_start_years():
    payload = {'elements': [{
        'leadUrl': '/sales/lead/real', 'fullName': 'Taylor Example',
        'currentCompanyStartYear': 2018,
        'currentPositions': [{'title': 'Vice President',
                              'startDate': {'month': 4, 'year': 2023}}],
        'currentCompany': {
            'name': 'Alpha Advisory', 'industryName': 'Consulting',
            'websiteUrl': 'https://alpha.example',
            'companySize': '51-200 employees', 'foundedYear': '2010',
            'companyType': 'Privately Held',
            'headquarters': 'Mumbai, India',
            'logoUrl': 'https://cdn.example/alpha-logo.png',
            'companyUrl': 'https://www.linkedin.com/company/alpha-advisory',
            'entityUrn': 'urn:li:fsd_company:8675309',
        }
    }]}
    row, = parse_search_response(payload)
    assert row['Company Start Year'] == '2018'
    assert row['Current Role Start Year'] == '2023'
    assert row['Company Name'] == 'Alpha Advisory'
    assert row['Company Domain'] == 'alpha.example'
    assert row['Company Website'] == 'https://alpha.example'
    assert row['Company Industry'] == 'Consulting'
    assert row['Company Employee Range'] == '51-200'
    assert row['Company Founded Year'] == '2010'
    assert row['Company Type'] == 'Privately Held'
    assert row['Company Country'] == 'India'
    assert row['Company Logo URL'] == 'https://cdn.example/alpha-logo.png'
    assert row['Company LinkedIn URL'] == 'https://www.linkedin.com/company/alpha-advisory'
    assert row['Company LinkedIn ID'] == '8675309'
