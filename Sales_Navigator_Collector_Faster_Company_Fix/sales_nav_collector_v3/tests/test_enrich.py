"""Regression checks for missing-company discovery and offline CSV repair."""
from __future__ import annotations

import csv
from pathlib import Path

import pytest

from sn_collect import collect_pages
from sn_company_lookup import company_search_url, parse_company_search_results
from sn_core import FIELDS, write_csv, blank_row
from sn_enrich import (apply_company_reference, company_key,
                       enrich_existing_csv, load_company_references)


SEARCH = '''<html><body><li>
  <a href="/sales/lead/A"><span data-anonymize="person-name">Ana A</span></a>
  <span data-anonymize="company-name">Alpha Advisory</span>
</li><li>
  <a href="/sales/lead/B"><span data-anonymize="person-name">Ben B</span></a>
  <span data-anonymize="company-name">Alpha Advisory</span>
</li></body></html>'''
ABOUT = '''<html><body><h1>Alpha Advisory</h1><dl>
<dt>Industry</dt><dd>Financial Services</dd>
<dt>Website</dt><dd><a href="https://alpha.example">alpha.example</a></dd>
</dl></body></html>'''


def test_company_name_search_accepts_only_exact_unambiguous_match():
    page = '''<li><a href="/company/alpha-advisory"><span>Alpha Advisory</span><span>20 employees</span></a></li>
    <li><a href="/company/alpha-advisory-inc">Alpha Advisory Inc</a></li>'''
    assert parse_company_search_results(page, 'Alpha Advisory') == (
        'https://www.linkedin.com/company/alpha-advisory')
    assert parse_company_search_results(page, 'Alpha') == ''
    assert parse_company_search_results('''<a href="/company/a">Alpha Advisory</a>
    <a href="/company/b">Alpha Advisory</a>''', 'Alpha Advisory') == ''
    assert parse_company_search_results('''<a href="https://evil.example/company/alpha">Alpha Advisory</a>''',
                                        'Alpha Advisory') == ''
    assert 'Alpha%20Advisory' in company_search_url('Alpha Advisory')


def test_company_search_recovers_missing_links_and_caches_by_name():
    visits, lookups = [], []
    def fetch(url):
        visits.append(url)
        return ABOUT if '/company/alpha-advisory/about/' in url else ''
    def resolve(name):
        lookups.append(name)
        return 'https://www.linkedin.com/company/alpha-advisory'
    rows, stats = collect_pages(lambda: SEARCH, lambda: False, fetch, 1,
                                include_profiles=False, resolve_company_url=resolve)
    assert len(rows) == 2
    assert lookups == ['Alpha Advisory']
    assert stats.companies_discovered_by_search == 1
    assert stats.company_pages_visited == 1
    assert all(r['Company Industry'] == 'Financial Services' for r in rows)
    assert visits == ['https://www.linkedin.com/company/alpha-advisory/about/']


def test_mapping_repairs_original_rows_with_exact_name_only(tmp_path):
    reference_file = tmp_path / 'companies.csv'
    with reference_file.open('w', encoding='utf-8', newline='') as out:
        writer = csv.DictWriter(out, ['Company Name', 'Company Industry', 'Company LinkedIn URL',
                                      'Company Website', 'Company Founded Year'])
        writer.writeheader()
        writer.writerow({'Company Name': ' Alpha Advisory ', 'Company Industry': 'Finance',
                         'Company LinkedIn URL': 'https://in.linkedin.com/company/alpha-advisory/',
                         'Company Website': 'https://alpha.example', 'Company Founded Year': '2018'})
    refs = load_company_references(reference_file)
    assert company_key('Alpha   Advisory') in refs
    rows = []
    for name in ['Alpha Advisory', 'Alpha Advisors', 'Alpha Advisory']:
        row = blank_row()
        row['Company Name'] = name
        row['Full Name'] = 'Person'
        row['Company Industry'] = 'Original industry' if len(rows) == 2 else ''
        rows.append(row)
    original = tmp_path / 'original.csv'
    target = tmp_path / 'target.csv'
    write_csv(rows, original)
    total, updated = enrich_existing_csv(original, target, refs)
    assert (total, updated) == (3, 2)
    with target.open(encoding='utf-8-sig', newline='') as stream:
        results = list(csv.DictReader(stream))
    assert all(set(r) == set(FIELDS) for r in results)
    assert results[0]['Company Industry'] == 'Finance'
    assert results[0]['Company Domain'] == 'alpha.example'
    assert results[0]['Company LinkedIn URL'] == 'https://www.linkedin.com/company/alpha-advisory'
    assert results[1]['Company Industry'] == ''
    assert results[2]['Company Industry'] == 'Original industry'


def test_matching_company_name_not_enough_without_source():
    row = {'Company Name': 'Example Co', 'Company Industry': ''}
    assert apply_company_reference(row, {}) is False
    assert row['Company Industry'] == ''


def test_untrusted_company_map_link_rejected(tmp_path):
    reference_file = tmp_path / 'unsafe.csv'
    reference_file.write_text('Company Name,Company LinkedIn URL\nExample Co,https://evil.example/company/x\n')
    with pytest.raises(ValueError, match='invalid LinkedIn'):
        load_company_references(reference_file)
