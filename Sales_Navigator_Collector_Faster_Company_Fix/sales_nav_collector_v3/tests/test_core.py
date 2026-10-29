import csv
from pathlib import Path

import pytest

from sn_core import (
    FIELDS, clean_sales_url, validate_search_url, parse_results,
    parse_company, parse_profile, deduplicate, write_csv,
    inspect_csv, make_review_sample, domain_from_website,
)


SEARCH_HTML = '''
<html><body><ul class="search-results">
<li class="result-item">
  <a href="/sales/lead/ABC123,NAME?trk=search"><span data-anonymize="person-name">Ana Costa</span></a>
  <span data-anonymize="title">Founder &amp; CEO</span>
  <a href="/sales/company/9821"><span data-anonymize="company-name">Harbor Advisory</span></a>
  <span data-anonymize="location">Dublin, Ireland</span>
</li>
<li class="result-item">
  <a href="/sales/lead/XYZ890"><span data-anonymize="person-name">Jo Lee</span></a>
  <span data-anonymize="title">Director</span>
  <a href="/sales/company/555"><span data-anonymize="company-name">Acme Advisors</span></a>
  <span data-anonymize="location">Cork, Ireland</span>
</li>
</ul></body></html>'''


COMPANY_HTML = '''
<html><body><h1>Harbor Advisory</h1><dl>
<dt>Website</dt><dd><a href="https://www.harboradvisory.example/about">harboradvisory.example</a></dd>
<dt>Industry</dt><dd>Financial Services</dd>
<dt>Company size</dt><dd>11-50 employees</dd>
<dt>Founded</dt><dd>2018</dd>
<dt>Type</dt><dd>Privately Held</dd>
<dt>Headquarters</dt><dd>Dublin, Ireland</dd>
</dl><img alt="Harbor Advisory logo" src="https://example.org/logo.png"/></body></html>'''


PROFILE_HTML = '''<html><body><h1>Ana Costa</h1><dl>
<dt>About</dt><dd>Advises emerging companies.</dd>
<dt>Current role started</dt><dd>January 2022</dd>
<dt>Joined current company</dt><dd>2018</dd>
<dt>LinkedIn Member ID</dt><dd>123456</dd>
</dl></body></html>'''


def test_exact_ordered_26_columns():
    assert len(FIELDS) == 26
    assert len(set(FIELDS)) == 26
    assert FIELDS[4] == 'LinkedIn Sales Navigator URL'
    assert FIELDS[-1] == 'Company LinkedIn URL'


def test_search_results_parsing_and_no_false_company_id():
    rows = parse_results(SEARCH_HTML)
    assert len(rows) == 2
    first = rows[0]
    assert first['Full Name'] == 'Ana Costa'
    assert first['First Name'] == 'Ana'
    assert first['Last Name'] == 'Costa'
    assert first['Job Title'] == 'Founder & CEO'
    assert first['Current Company'] == 'Harbor Advisory'
    assert first['Company Name'] == 'Harbor Advisory'
    assert first['Sales Profile ID'] == 'ABC123,NAME'
    assert first['Company LinkedIn URL'] == ''  # Sales account is not a /company/ URL
    assert first['_company_sales_url'] == 'https://www.linkedin.com/sales/company/9821'


def test_company_detail_only_from_labeled_data():
    fields = parse_company(COMPANY_HTML, 'https://www.linkedin.com/sales/company/9821')
    assert fields['Company Industry'] == 'Financial Services'
    assert fields['Company Website'] == 'https://www.harboradvisory.example/about'
    assert fields['Company Domain'] == 'harboradvisory.example'
    assert fields['Company Employee Range'] == '11-50'
    assert fields['Company Employee Count'] == ''
    assert fields['Company Founded Year'] == '2018'
    assert fields['Company Headquarters City'] == 'Dublin'
    assert fields['Company Country'] == 'Ireland'
    assert fields['Company Type'] == 'Privately Held'
    assert fields['Company Logo URL'] == 'https://example.org/logo.png'


def test_profile_detail_and_missing_fields():
    fields = parse_profile(PROFILE_HTML)
    assert fields['Profile Summary'] == 'Advises emerging companies.'
    assert fields['Current Role Start Year'] == '2022'
    assert fields['Company Start Year'] == '2018'
    assert fields['LinkedIn Member ID'] == '123456'
    assert fields['Company Employee Count'] == ''


def test_dedupe_by_id_or_url_across_pages_and_no_same_name_merge():
    row1 = {'Full Name': 'Ana Costa', 'Sales Profile ID': 'A', 'Job Title': 'CEO'}
    row2 = {'Full Name': 'Ana Costa', 'Sales Profile ID': 'A', 'Company Industry': 'Finance'}
    row3 = {'Full Name': 'Ana Costa', 'Sales Profile ID': 'B', 'Company Industry': 'Retail'}
    merged = deduplicate([row1, row2, row3])
    assert len(merged) == 2
    assert merged[0]['Job Title'] == 'CEO'
    assert merged[0]['Company Industry'] == 'Finance'
    assert merged[1]['Company Industry'] == 'Retail'
    assert all(set(r) == set(FIELDS) for r in merged)


def test_validate_url_rejects_other_hosts_and_pages():
    assert validate_search_url('https://www.linkedin.com/sales/search/people?query=x')
    for url in ['https://evil.example/sales/search/people',
                'https://www.linkedin.com.evil.example/sales/search/people',
                'https://linkedin.com/in/person', 'file:///etc/passwd']:
        with pytest.raises(ValueError):
            validate_search_url(url)


def test_domain_ignores_linkedin_and_invalid():
    assert domain_from_website('https://www.example.com/about') == 'example.com'
    assert domain_from_website('javascript:alert(1)') == ''
    assert clean_sales_url('/sales/lead/ABC?q=1') == 'https://www.linkedin.com/sales/lead/ABC'


def test_csv_and_quality_review(tmp_path):
    rows = parse_results(SEARCH_HTML)
    rows[0]['Company Industry'] = 'Financial Services'
    output = tmp_path / 'results.csv'
    write_csv(rows, output)
    with output.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        assert reader.fieldnames == FIELDS
        saved = list(reader)
    assert len(saved) == 2
    assert all(set(row) == set(FIELDS) for row in saved)
    report = inspect_csv(output)
    assert report['rows'] == 2
    assert report['company_and_industry_coverage'] == 0.5
    review = tmp_path / 'review.csv'
    make_review_sample(output, review, 10)
    with review.open(encoding='utf-8-sig', newline='') as stream:
        sample = list(csv.DictReader(stream))
    assert len(sample) == 2
    assert 'Matches Sales Navigator (Y/N)' in sample[0]


def test_data_anonymize_company_industry_when_visible_on_card():
    html = '''<li><a href="/sales/lead/I77"><span data-anonymize="person-name">Mira Shah</span></a>
    <span data-anonymize="title">CEO</span>
    <span data-anonymize="company-name">Navi Partners</span>
    <span data-anonymize="industry">Investment Management</span></li>'''
    records = parse_results(html)
    assert records[0]['Company Industry'] == 'Investment Management'


def test_labeled_company_div_pairs_and_profile_h2():
    html = '''<html><h1>Firm</h1>
      <div>Industry</div><div>Investment Management</div>
      <div>Headquarters</div><div>Manila, Philippines</div>
      <div>Founded</div><div>2017</div></html>'''
    company = parse_company(html)
    assert company['Company Industry'] == 'Investment Management'
    assert company['Company Country'] == 'Philippines'
    assert company['Company Founded Year'] == '2017'
    profile = parse_profile('<html><h2>About</h2><p>Specialist financial adviser.</p></html>')
    assert profile['Profile Summary'] == 'Specialist financial adviser.'


def test_company_profile_link_uses_the_current_company_name_only():
    html = '''<html><body>
      <a href="/company/old-firm">Old Firm</a>
      <section><a href="/company/alpha-advisory">Alpha Advisory</a></section>
    </body></html>'''
    parsed = parse_profile(html, current_company='Alpha Advisory')
    assert parsed['_company_sales_url'] == 'https://www.linkedin.com/company/alpha-advisory'
    assert parsed['Company LinkedIn URL'] == 'https://www.linkedin.com/company/alpha-advisory'
    assert parsed['_company_public_url'] == 'https://www.linkedin.com/company/alpha-advisory'


def test_public_company_about_extracts_nested_website_and_normalizes_profile_url():
    html = '''<html><head><link rel="canonical" href="https://www.linkedin.com/company/alpha-advisory/about/"></head>
    <body><h1>Alpha Advisory</h1>
    <div>Website</div><div><a href="https://alpha.example/">Visit website</a></div>
    <div>Industry</div><div>Financial Services</div>
    <div>Company size</div><div>11-50 employees</div>
    <div>Type</div><div>Privately Held</div>
    <div>Founded</div><div>2019</div>
    <div>Headquarters</div><div>Dublin, Ireland</div>
    <img alt="Alpha Advisory logo" src="https://cdn.example/logo.jpg">
    </body></html>'''
    fields = parse_company(html, 'https://www.linkedin.com/company/alpha-advisory/about/')
    assert fields['Company Website'] == 'https://alpha.example/'
    assert fields['Company Domain'] == 'alpha.example'
    assert fields['Company Industry'] == 'Financial Services'
    assert fields['Company Employee Range'] == '11-50'
    assert fields['Company Founded Year'] == '2019'
    assert fields['Company Headquarters City'] == 'Dublin'
    assert fields['Company Country'] == 'Ireland'
    assert fields['Company Logo URL'] == 'https://cdn.example/logo.jpg'
    assert fields['Company LinkedIn URL'] == 'https://www.linkedin.com/company/alpha-advisory'


def test_sales_company_returns_link_to_public_about_page():
    html = '''<html><body><h1>Alpha Advisory</h1>
    <a href="https://www.linkedin.com/company/alpha-advisory/">View LinkedIn Page</a>
    </body></html>'''
    parsed = parse_company(html, 'https://www.linkedin.com/sales/company/42')
    assert parsed['_company_public_url'] == 'https://www.linkedin.com/company/alpha-advisory'


def test_regional_linkedin_company_pages_are_supported_but_fake_hosts_rejected():
    from sn_core import company_link
    assert company_link('https://in.linkedin.com/company/alpha-advisory/') == (
        'https://www.linkedin.com/company/alpha-advisory')
    assert company_link('https://www.linkedin.com.attacker.example/company/fake') == ''


def test_urn_only_virtual_result_cards_are_supported():
    from sn_core import sales_url_from_urn
    urn = 'urn:li:fs_salesProfile:(ACw123,NAME_SEARCH,xyz1)'
    assert sales_url_from_urn(urn) == 'https://www.linkedin.com/sales/lead/ACw123,NAME_SEARCH,xyz1'
    html = f'''<ul><li data-scroll-into-view="{urn}">
    <span data-anonymize="person-name">Eva Example</span>
    <span data-anonymize="title">Director</span></li></ul>'''
    rows = parse_results(html)
    assert len(rows) == 1
    assert rows[0]['Full Name'] == 'Eva Example'
    assert rows[0]['Job Title'] == 'Director'
    assert not sales_url_from_urn('urn:li:fs_miniProfile:(ACw123)')
