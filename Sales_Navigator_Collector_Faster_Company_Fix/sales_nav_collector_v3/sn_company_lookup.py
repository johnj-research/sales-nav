"""Read-only public LinkedIn company search via normal rendered browser pages.

Only exact, unambiguous company-name matches are accepted. Browser navigates
as a user would; no internal API calls, bulk name guessing or session export.
"""
from __future__ import annotations

from urllib.parse import quote
from bs4 import BeautifulSoup, Tag
from sn_core import BASE, public_company_url, text_clean
from sn_enrich import company_key


def company_search_url(name: str) -> str:
    return f'{BASE}/search/results/companies/?keywords={quote(text_clean(name))}'


def parse_company_search_results(html: str, expected_name: str) -> str:
    """Only accept one distinct company URL with an *exact* displayed name.

    Skip generic links, related businesses, and fuzzy matches. If login is
    required or multiple companies have the same name, return empty string.
    """
    expected = company_key(expected_name)
    if not expected:
        return ''
    soup = BeautifulSoup(html, 'html.parser')
    matches = set()
    for anchor in soup.find_all('a', href=True):
        url = public_company_url(anchor.get('href'))
        if not url:
            continue
        texts = [text_clean(anchor.get_text(' ', strip=True))]
        for element in anchor.find_all(['span', 'h3', 'h4', 'strong'], recursive=True):
            texts.append(text_clean(element.get_text(' ', strip=True)))
        if any(company_key(item) == expected for item in texts):
            matches.add(url)
    return next(iter(matches)) if len(matches) == 1 else ''
