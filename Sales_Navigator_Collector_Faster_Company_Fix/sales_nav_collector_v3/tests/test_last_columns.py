from sn_core import blank_row, parse_company
from sn_collect import collect_pages


def test_existing_public_company_url_is_used_for_enrichment():
    row = blank_row()
    row.update({'Full Name': 'Ana Costa', 'Company Name': 'Alpha',
                'LinkedIn Sales Navigator URL': 'https://www.linkedin.com/sales/lead/A',
                'Company LinkedIn URL': 'https://www.linkedin.com/company/alpha'})
    visits = []
    def fetch(url):
        visits.append(url)
        return '<h1>Alpha</h1><dl><dt>Type</dt><dd>Public Company</dd></dl>'
    rows, _ = collect_pages(lambda: [row], lambda: False, fetch, 1,
                            include_profiles=False)
    assert rows[0]['Company Type'] == 'Public Company'
    assert visits == ['https://www.linkedin.com/company/alpha/about/']


def test_structured_country_fills_missing_country_when_city_already_present():
    html = '''<h1>Alpha</h1><dl><dt>Headquarters</dt><dd>Paris</dd></dl>
    <script type="application/ld+json">{"@type":"Organization","name":"Alpha",
    "address":{"addressLocality":"Paris","addressCountry":{"@type":"Country","name":"France"}}}</script>'''
    company = parse_company(html, 'https://www.linkedin.com/company/alpha/about/')
    assert company['Company Headquarters City'] == 'Paris'
    assert company['Company Country'] == 'France'


def test_detail_read_retains_late_company_fields():
    from sn_collect import read_detail_html
    class Page:
        index = 0
        def content(self):
            base = '<h1>Alpha</h1><dl><dt>Industry</dt><dd>Consulting</dd><dt>Website</dt><dd>https://alpha.example</dd><dt>Type</dt><dd>Public Company</dd><dt>Headquarters</dt><dd>Paris</dd></dl>'
            if self.index >= 2:
                base += '<dl><dt>Country</dt><dd>France</dd></dl><img alt="Alpha logo" src="https://cdn.example/alpha.png"><main data-company-id="123"></main>'
            return base
        def wait_for_timeout(self, ms):
            self.index += 1
    url = 'https://www.linkedin.com/company/alpha/about/'
    result = parse_company(read_detail_html(Page(), url, 500), url)
    assert result['Company Country'] == 'France'
    assert result['Company Logo URL'] == 'https://cdn.example/alpha.png'
    assert result['Company LinkedIn ID'] == '123'


def test_sparse_sales_company_page_falls_back_to_public_company_search():
    row = blank_row()
    row.update({'Full Name': 'Ana', 'Company Name': 'Alpha',
                'LinkedIn Sales Navigator URL': 'https://www.linkedin.com/sales/lead/A',
                '_company_sales_url': 'https://www.linkedin.com/sales/company/99'})
    lookups = []
    def resolve(name):
        lookups.append(name)
        return 'https://www.linkedin.com/company/alpha'
    def fetch(url):
        return ('<h1>Alpha</h1><dl><dt>Website</dt><dd>https://alpha.example</dd></dl>'
                if '/about/' in url else '<h1>Alpha</h1>')
    rows, _ = collect_pages(lambda: [dict(row), dict(row)], lambda: False,
                            fetch, 1, include_profiles=False,
                            resolve_company_url=resolve)
    assert rows[0]['Company Domain'] == 'alpha.example'
    assert lookups == ['Alpha']


def test_fast_details_stop_on_stable_content_but_thorough_keeps_waiting():
    from sn_collect import read_detail_html
    class Page:
        waited = 0
        def content(self):
            return '<h1>Alpha</h1><dl><dt>Industry</dt><dd>Consulting</dd></dl>'
        def wait_for_timeout(self, ms):
            self.waited += ms
    fast, thorough = Page(), Page()
    url = 'https://www.linkedin.com/company/alpha/about/'
    assert read_detail_html(fast, url, 800) == read_detail_html(thorough, url, 800, thorough=True)
    assert fast.waited <= 1200
    assert thorough.waited == 4800


def test_thorough_details_capture_content_arriving_after_fast_window():
    from sn_collect import read_detail_html
    class Page:
        index = 0
        def content(self):
            base = '<h1>Alpha</h1><dl><dt>Industry</dt><dd>Consulting</dd></dl>'
            return base + ('<dl><dt>Type</dt><dd>Public Company</dd></dl>' if self.index >= 5 else '')
        def wait_for_timeout(self, ms):
            self.index += 1
    url = 'https://www.linkedin.com/company/alpha/about/'
    company = parse_company(read_detail_html(Page(), url, 800, thorough=True), url)
    assert company['Company Type'] == 'Public Company'


def test_empty_detail_skeleton_gets_full_fast_retry_budget():
    from sn_collect import read_detail_html
    class Page:
        index = 0
        def content(self):
            return ('<h1>Alpha</h1><dl><dt>Industry</dt><dd>Consulting</dd></dl>'
                    if self.index >= 5 else '<html><body>Loading</body></html>')
        def wait_for_timeout(self, ms):
            self.index += 1
    url = 'https://www.linkedin.com/company/alpha/about/'
    assert parse_company(read_detail_html(Page(), url, 800), url)['Company Industry'] == 'Consulting'
