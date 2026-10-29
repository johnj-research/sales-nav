"""Optional, passive reader for Sales Navigator search XHR/fetch responses.

This module does NOT call an undocumented endpoint. The browser loads responses
as the user views the search results; we recognize narrowly defined lead objects
in those responses and combine them with visible DOM cards. API formats change;
unknown formats are ignored, never guessed or silently represented as complete.
No session cookies, request headers, CSRF values, or raw JSON are saved.
"""
from __future__ import annotations

import json
import re
from urllib.parse import urlparse

from sn_core import (LEAD_RE, _absolute_image_url, _location_parts, _year,
                     blank_row, clean_sales_url, company_link,
                     domain_from_website, external_website, public_company_url,
                     sales_url_from_urn, text_clean)

MAX_RESPONSE_BYTES = 4_000_000
MAX_NODES = 30_000


def is_search_api_url(url: str) -> bool:
    parsed = urlparse(url)
    if (parsed.scheme != 'https' or parsed.hostname not in
            {'linkedin.com', 'www.linkedin.com'}):
        return False
    path = parsed.path.lower()
    # No writes or custom requests. Listen only to first-party browser responses.
    return ('search' in path or 'search' in parsed.query.lower()) and (
        path.startswith('/voyager/api/') or path.startswith('/sales-api/'))


def _string(value) -> str:
    if isinstance(value, str):
        return text_clean(value)
    if isinstance(value, dict):
        for key in ('text', 'localized', 'name', 'value'):
            nested = value.get(key)
            if isinstance(nested, str):
                return text_clean(nested)
        return ''
    return ''


def _field(obj: dict, *keys: str) -> str:
    for key in keys:
        value = _string(obj.get(key))
        if value:
            return value
    return ''


def _lead_url(obj: dict) -> str:
    for key in ('salesNavLink', 'salesNavigatorUrl', 'leadUrl',
                'profileUrl', 'navigationUrl', 'url'):
        link = clean_sales_url(_field(obj, key))
        if link:
            return link
    for key in ('entityUrn', 'profileUrn', 'leadUrn'):
        url = sales_url_from_urn(_field(obj, key))
        if url:
            return url
    return ''


def _explicit_year(obj: dict, *keys: str) -> str:
    """Extract a stated start date, not an inferred duration or tenure."""
    for key in keys:
        value = obj.get(key)
        if isinstance(value, dict):
            year = value.get('year')
            if isinstance(year, (int, str)) and re.fullmatch(r'(?:19|20)\d{2}', str(year)):
                return str(year)
        elif isinstance(value, (str, int)):
            year = _year(str(value))
            if year:
                return year
    return ''


def parse_search_response(payload) -> list[dict[str, str]]:
    """Return confidently identified lead records from a JSON response.

    Handles `elements`, `included`, nested GraphQL `data` and compact result
    views; ignores objects with no *explicit Sales Navigator lead identity*.
    This is deliberately conservative to avoid treating recommendations,
    unrelated people, or arbitrary company objects as search leads.
    """
    stack = [payload]
    visited = 0
    results = []
    seen = set()
    while stack and visited < MAX_NODES:
        node = stack.pop()
        visited += 1
        if isinstance(node, list):
            stack.extend(reversed(node))
            continue
        if not isinstance(node, dict):
            continue
        url = _lead_url(node)
        name = _field(node, 'fullName', 'name', 'displayName')
        if not name:
            first = _field(node, 'firstName')
            last = _field(node, 'lastName')
            name = text_clean(first + ' ' + last) if first and last else ''
        if url and name and len(name) <= 120 and url not in seen:
            seen.add(url)
            row = blank_row()
            row['LinkedIn Sales Navigator URL'] = url
            row['Sales Profile ID'] = LEAD_RE.match(urlparse(url).path).group(1)
            row['Full Name'] = name
            chunks = name.split(maxsplit=1)
            row['First Name'] = chunks[0]
            row['Last Name'] = chunks[1] if len(chunks) > 1 else ''
            row['Job Title'] = _field(node, 'title', 'occupation', 'headline')
            positions = node.get('currentPositions')
            current_position = positions[0] if isinstance(positions, list) and positions else {}
            if isinstance(current_position, dict):
                row['Job Title'] = row['Job Title'] or _field(current_position, 'title')
                row['Current Role Start Year'] = _explicit_year(
                    current_position, 'startDate', 'startYear', 'startedAt')
            else:
                current_position = {}
            row['Location'] = _field(node, 'location', 'geoRegion', 'geoRegionName')
            company_name = (_field(node, 'companyName', 'currentCompanyName') or
                            _field(current_position, 'companyName'))
            current = node.get('currentCompany')
            if isinstance(current, dict):
                company_name = company_name or _field(current, 'name', 'companyName')
                company_ref = _field(current, 'salesNavLink', 'companyUrl', 'url')
                row['Company Industry'] = _field(current, 'industryName', 'industry')
                row['Company Type'] = _field(current, 'companyType')
                row['Company Founded Year'] = _explicit_year(
                    current, 'foundedYear', 'foundedDate', 'foundingDate')
                website = external_website(_field(current, 'websiteUrl', 'website'))
                if website:
                    row['Company Website'] = website
                    row['Company Domain'] = domain_from_website(website)
                range_text = _field(current, 'companySize', 'employeeRange')
                range_match = re.search(
                    r'(\d[\d,]*\s*(?:-|–|—|to)\s*\d[\d,]*\+?|\d[\d,]*\s*\+)',
                    range_text, re.I)
                if range_match:
                    row['Company Employee Range'] = re.sub(
                        r'\s*[-–—]\s*', '-', range_match.group(1)).replace(' ', '')
                exact_count = _field(current, 'employeeCount', 'exactEmployeeCount')
                if re.fullmatch(r'\d[\d,]*', exact_count):
                    row['Company Employee Count'] = exact_count
                hq = _field(current, 'headquarters', 'headquartersLocation')
                city, country = _location_parts(hq)
                row['Company Headquarters City'] = city
                row['Company Country'] = country
                logo = _field(current, 'logoUrl', 'companyLogoUrl')
                row['Company Logo URL'] = _absolute_image_url(logo)
                own_urn = _field(current, 'entityUrn', 'companyUrn')
                own_id = re.fullmatch(
                    r'urn:li:(?:fsd_company|fs_normalized_company):(\d+)', own_urn)
                if own_id:
                    row['Company LinkedIn ID'] = own_id.group(1)
                public = public_company_url(company_ref)
                if public:
                    row['Company LinkedIn URL'] = public
                row['Company Start Year'] = _explicit_year(
                    node, 'currentCompanyStartDate', 'currentCompanyStartYear')
            else:
                company_ref = ''
                if isinstance(current, str):
                    company_name = company_name or text_clean(current)
            row['Current Company'] = row['Company Name'] = company_name
            row['_company_sales_url'] = company_link(company_ref)
            results.append(row)
        stack.extend(reversed(list(node.values())))
    return results


class BrowserSearchResponses:
    """Accumulator for search responses loaded by the currently viewed tab."""

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.rows: list[dict[str, str]] = []
        self.responses_seen = 0
        self.responses_with_leads = 0
        self.errors = 0

    def clear_page(self) -> None:
        self.rows = []

    def handle_response(self, response) -> None:
        if not self.enabled or not is_search_api_url(response.url):
            return
        try:
            if response.status != 200:
                return
            mime = response.headers.get('content-type', '').lower()
            if 'json' not in mime:
                return
            length = response.headers.get('content-length', '')
            if length.isdigit() and int(length) > MAX_RESPONSE_BYTES:
                return
            body = response.body()
            if len(body) > MAX_RESPONSE_BYTES:
                return
            self.responses_seen += 1
            leads = parse_search_response(json.loads(body))
            if leads:
                self.responses_with_leads += 1
                self.rows.extend(leads)
        except Exception:
            # Search DOM remains the primary data source. Never dump bodies.
            self.errors += 1
