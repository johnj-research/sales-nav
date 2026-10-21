"""Pure parsing, validation and CSV utilities for Sales Navigator lead results.
No network access or LinkedIn login occurs in this module.
Only facts labeled in the rendered page are populated.
"""
from __future__ import annotations

import csv
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup, Tag

FIELDS = [
    "Full Name", "First Name", "Last Name", "Job Title",
    "LinkedIn Sales Navigator URL", "Location", "Captured At",
    "Sales Profile ID", "LinkedIn Member ID", "Profile Summary",
    "Current Company", "Company Start Year", "Current Role Start Year",
    "Company Name", "Company Domain", "Company Website",
    "Company Employee Count", "Company Employee Range", "Company Founded Year",
    "Company Industry", "Company Type", "Company Headquarters City",
    "Company Country", "Company Logo URL", "Company LinkedIn ID",
    "Company LinkedIn URL",
]

BASE = 'https://www.linkedin.com'
LEAD_RE = re.compile(r'^/sales/(?:lead|people)/([^/?#]+)', re.I)
COMPANY_SALES_RE = re.compile(r'^/sales/(?:company|account)/[^/?#]+', re.I)
PUBLIC_COMPANY_RE = re.compile(r'^/company/([^/?#]+)', re.I)
YEAR_RE = re.compile(r'\b(?:19|20)\d{2}\b')
STATE_CODES = {'AL','AK','AZ','AR','CA','CO','CT','DE','FL','GA','HI','ID','IL',
               'IN','IA','KS','KY','LA','ME','MD','MA','MI','MN','MS','MO','MT',
               'NE','NV','NH','NJ','NM','NY','NC','ND','OH','OK','OR','PA','RI',
               'SC','SD','TN','TX','UT','VT','VA','WA','WV','WI','WY','DC'}
COUNTRIES = {'india', 'ireland', 'romania', 'finland', 'philippines', 'vietnam',
             'thailand', 'canada', 'australia', 'singapore', 'germany', 'france',
             'spain', 'italy', 'netherlands', 'belgium', 'sweden', 'norway',
             'denmark', 'poland', 'japan', 'china', 'brazil', 'mexico',
             'new zealand', 'south africa', 'switzerland', 'malaysia',
             'united states', 'united states of america', 'usa', 'us',
             'united kingdom', 'uk', 'great britain', 'uae',
             'united arab emirates', 'indonesia', 'portugal', 'austria',
             'israel', 'pakistan', 'bangladesh', 'sri lanka', 'kenya', 'nigeria',
             'czech republic', 'czechia', 'hungary', 'estonia', 'lithuania'}

def blank_row() -> dict[str, str]:
    return {name: '' for name in FIELDS}

def text_clean(text: str | None) -> str:
    return re.sub(r'\s+', ' ', text or '').strip()

def validate_search_url(url: str) -> str:
    parsed = urlparse(url)
    if (parsed.scheme != 'https' or parsed.hostname not in
            {'www.linkedin.com', 'linkedin.com'} or
            parsed.path.rstrip('/') not in {'/sales/search/people', '/sales/search/leads'} or
            parsed.username or parsed.password or parsed.port):
        raise ValueError('Provide an HTTPS LinkedIn Sales Navigator lead-search URL.')
    return url

def normalized_link(href: str | None) -> str:
    if not href:
        return ''
    uri = urlparse(urljoin(BASE, href.strip()))
    if (uri.scheme != 'https' or not uri.hostname or
            (uri.hostname != 'linkedin.com' and
             not uri.hostname.endswith('.linkedin.com')) or
            uri.username or uri.password or uri.port):
        return ''
    return urlunparse(('https', 'www.linkedin.com', uri.path.rstrip('/'), '', '', ''))

def clean_sales_url(href: str | None) -> str:
    url = normalized_link(href)
    return url if LEAD_RE.match(urlparse(url).path) else ''

def company_link(href: str | None) -> str:
    url = normalized_link(href)
    path = urlparse(url).path
    return url if COMPANY_SALES_RE.match(path) or PUBLIC_COMPANY_RE.match(path) else ''

def public_company_url(href: str | None) -> str:
    """Return the stable /company/slug address, without /about/ or tracking."""
    url = company_link(href)
    matched = PUBLIC_COMPANY_RE.match(urlparse(url).path)
    return f'{BASE}/company/{matched.group(1)}' if matched else ''

def company_about_url(href: str) -> str:
    public = public_company_url(href)
    return public + '/about/' if public else href

def domain_from_website(url: str) -> str:
    candidate = urlparse(url)
    hostname = (candidate.hostname or '').lower().removeprefix('www.')
    if (candidate.scheme not in {'https', 'http'} or not hostname or
            hostname in {'linkedin.com', 'www.linkedin.com'} or '.' not in hostname):
        return ''
    return hostname

def _text(node: Tag | None) -> str:
    return text_clean(node.get_text(' ', strip=True)) if node else ''

def _nearby_card(a: Tag) -> Tag:
    """Prefer a single result element; never swallow the entire results list."""
    for ancestor in a.parents:
        if not isinstance(ancestor, Tag) or ancestor.name in {'ul', 'body', 'html'}:
            break
        if ancestor.name == 'li':
            return ancestor
    candidate = a.parent if isinstance(a.parent, Tag) else a
    for ancestor in a.parents:
        if not isinstance(ancestor, Tag) or ancestor.name in {'body', 'html'}:
            break
        if ancestor.name not in {'div', 'article', 'section'}:
            continue
        candidate = ancestor
        lead_count = sum(bool(clean_sales_url(link.get('href'))) for link in ancestor.find_all('a', href=True))
        named = ancestor.select_one('[data-anonymize="company-name"]')
        titled = ancestor.select_one('[data-anonymize="title"]')
        if (named or titled) and lead_count <= 2:
            return ancestor
    return candidate

def _anonymized(card: Tag, kind: str) -> str:
    node = card.select_one(f'[data-anonymize="{kind}"]')
    return _text(node)

def _split_name(full_name: str) -> tuple[str, str]:
    parts = full_name.split(maxsplit=1)
    return (parts[0] if parts else '', parts[1] if len(parts) > 1 else '')

def parse_results(html: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, 'html.parser')
    results = []
    seen = set()
    for link in soup.find_all('a', href=True):
        lead_url = clean_sales_url(link.get('href'))
        if not lead_url or lead_url in seen:
            continue
        card = _nearby_card(link)
        name = _anonymized(card, 'person-name') or _text(link)
        if not name or len(name) > 120:
            continue
        seen.add(lead_url)
        row = blank_row()
        row['Full Name'] = name
        row['First Name'], row['Last Name'] = _split_name(name)
        row['LinkedIn Sales Navigator URL'] = lead_url
        row['Sales Profile ID'] = LEAD_RE.match(urlparse(lead_url).path).group(1)
        row['Job Title'] = _anonymized(card, 'title')
        row['Location'] = _anonymized(card, 'location')
        # Use an explicitly rendered industry label if the result card has one.
        row['Company Industry'] = (_anonymized(card, 'company-industry') or
                                   _anonymized(card, 'industry'))
        company_name = _anonymized(card, 'company-name')
        company_url = ''
        for anchor in card.find_all('a', href=True):
            candidate = company_link(anchor.get('href'))
            if candidate:
                company_url = candidate
                if not company_name:
                    company_name = _text(anchor)
                break
        row['Current Company'] = row['Company Name'] = company_name
        if PUBLIC_COMPANY_RE.match(urlparse(company_url).path):
            row['Company LinkedIn URL'] = company_url
            slug = PUBLIC_COMPANY_RE.match(urlparse(company_url).path).group(1)
            if slug.isdigit():
                row['Company LinkedIn ID'] = slug
        row['_company_sales_url'] = company_url
        results.append(row)
    return results

def _labeled_values(soup: BeautifulSoup) -> dict[str, tuple[str, str]]:
    """Map dt/dd, table th/td, and compact definition-style blocks."""
    def link_in(node: Tag) -> str:
        a = node if node.name == 'a' else node.find('a', href=True)
        return a.get('href', '') if a else ''

    values = {}
    for key in soup.find_all(['dt', 'th']):
        val = key.find_next_sibling(['dd', 'td'])
        if not val:
            continue
        label = _text(key).lower().rstrip(':')
        values.setdefault(label, (_text(val), link_in(val)))
    # Public LinkedIn company About pages sometimes render a list of
    # labels followed by sibling content rather than semantic <dl> tags.
    valid_labels = {'website', 'industry', 'company size', 'headquarters',
                    'type', 'founded', 'employee count', 'headcount',
                    'about', 'current role started', 'joined current company',
                    'linkedin member id', 'profile summary'}
    for el in soup.find_all(['dt', 'h2', 'h3', 'span', 'p', 'div', 'strong']):
        label = _text(el).lower().rstrip(':')
        if label not in valid_labels or label in values:
            continue
        next_el = el.find_next_sibling()
        if next_el and next_el.name in {'dd', 'div', 'p', 'span', 'a'}:
            val = _text(next_el)
            if val and val.lower() != label and len(val) < 500:
                link = link_in(next_el)
                values[label] = (val, link)
    return values

def _get(values: dict, *labels: str) -> tuple[str, str]:
    for label in labels:
        val = values.get(label.lower())
        if val and val[0]:
            return val
    return '', ''

def _year(text: str) -> str:
    match = YEAR_RE.search(text)
    return match.group(0) if match else ''

def _location_parts(value: str) -> tuple[str, str]:
    parts = [part.strip() for part in value.split(',') if part.strip()]
    if not parts:
        return '', ''
    city = parts[0]
    candidate = parts[-1]
    if len(parts) >= 3:
        return city, '' if candidate.upper() in STATE_CODES else candidate
    if len(parts) == 2 and candidate.lower() in COUNTRIES:
        return city, candidate
    return city, ''

def _public_company_from_page(soup: BeautifulSoup, page_url: str = '',
                              company_name: str = '') -> str:
    """Use the page's own company URL, canonical metadata or a matching link.

    A generic /company/ link is not sufficient: suggested companies and footer
    links may refer to other businesses.
    """
    direct = public_company_url(page_url)
    if direct:
        return direct
    canonical = soup.find('link', rel=lambda rel: bool(rel) and 'canonical' in rel)
    if canonical:
        candidate = public_company_url(canonical.get('href'))
        if candidate:
            return candidate
    og = soup.find('meta', attrs={'property': 'og:url'})
    if og:
        candidate = public_company_url(og.get('content'))
        if candidate:
            return candidate
    norm_name = text_clean(company_name).casefold()
    for link in soup.find_all('a', href=True):
        candidate = public_company_url(link.get('href'))
        if not candidate:
            continue
        text = _text(link).casefold()
        if text in {'view linkedin page', 'linkedin company page',
                    'view company on linkedin', 'visit linkedin page'}:
            return candidate
        if norm_name and text == norm_name:
            return candidate
    return ''

def parse_company(html: str, page_url: str = '') -> dict[str, str]:
    soup = BeautifulSoup(html, 'html.parser')
    fields = blank_row()
    labels = _labeled_values(soup)
    h1 = soup.find('h1')
    if h1:
        fields['Company Name'] = _text(h1)
    website, href = _get(labels, 'website')
    candidate = href or website
    if candidate and not urlparse(candidate).scheme and '.' in candidate:
        candidate = 'https://' + candidate
    if domain_from_website(candidate):
        fields['Company Website'] = candidate
        fields['Company Domain'] = domain_from_website(candidate)
    fields['Company Industry'] = (_get(labels, 'industry')[0] or
                                  _anonymized(soup, 'company-industry') or
                                  _anonymized(soup, 'industry'))
    fields['Company Type'] = _get(labels, 'type')[0]
    fields['Company Founded Year'] = _year(_get(labels, 'founded')[0])
    employees = _get(labels, 'company size')[0]
    range_match = re.search(r'(\d[\d,]*\s*(?:-|–|—|to)\s*\d[\d,]*\+?|\d[\d,]*\s*\+)', employees, re.I)
    if range_match:
        fields['Company Employee Range'] = re.sub(r'\s*[-–—]\s*', '-', range_match.group(1)).replace(' ', '')
    fields['Company Employee Count'] = _get(labels, 'employee count', 'headcount')[0]
    if fields['Company Employee Count'] and not re.fullmatch(r'\d[\d,]*', fields['Company Employee Count']):
        fields['Company Employee Count'] = ''
    city, country = _location_parts(_get(labels, 'headquarters')[0])
    fields['Company Headquarters City'], fields['Company Country'] = city, country
    for img in soup.find_all('img', src=True):
        if 'logo' in (img.get('alt') or '').lower() and img['src'].startswith('https://'):
            fields['Company Logo URL'] = img['src']
            break
    public = _public_company_from_page(soup, page_url, fields['Company Name'])
    fields['Company LinkedIn URL'] = public
    fields['_company_public_url'] = public
    if public:
        slug = PUBLIC_COMPANY_RE.match(urlparse(public).path).group(1)
        if slug.isdigit():
            fields['Company LinkedIn ID'] = slug
    return fields

def parse_profile(html: str, current_company: str = '') -> dict[str, str]:
    soup = BeautifulSoup(html, 'html.parser')
    labels = _labeled_values(soup)
    row = blank_row()
    row['Profile Summary'] = _get(labels, 'about', 'profile summary')[0]
    row['Company Start Year'] = _year(_get(labels, 'joined current company')[0])
    row['Current Role Start Year'] = _year(_get(labels, 'current role started')[0])
    member_id = _get(labels, 'linkedin member id')[0]
    if member_id.isdigit():
        row['LinkedIn Member ID'] = member_id

    # Recover the company's URL only when the linked company name matches
    # the current employer shown on the search card. Never select a random
    # former employer or a suggested company from the profile page.
    wanted = text_clean(current_company).casefold()
    if wanted:
        for anchor in soup.find_all('a', href=True):
            candidate = company_link(anchor.get('href'))
            if candidate and _text(anchor).casefold() == wanted:
                row['_company_sales_url'] = candidate
                public = public_company_url(candidate)
                if public:
                    row['_company_public_url'] = public
                    row['Company LinkedIn URL'] = public
                break
    return row

def _identity_keys(row: dict[str, str]) -> list[tuple[str, str]]:
    keys = []
    for field in ['Sales Profile ID', 'LinkedIn Member ID', 'LinkedIn Sales Navigator URL']:
        val = row.get(field, '').strip().casefold()
        if val:
            keys.append((field, val))
    return keys

def deduplicate(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Merge using strong identifiers; never merge unrelated identical names."""
    out: list[dict[str, str]] = []
    lookup: dict[tuple[str, str], int] = {}
    for original in rows:
        keys = _identity_keys(original)
        if not keys:
            continue  # Cannot establish person identity safely.
        match_indices = {lookup[key] for key in keys if key in lookup}
        if not match_indices:
            index = len(out)
            out.append(blank_row())
        else:
            index = min(match_indices)
            # Merge any previously separate records that share later identifiers.
            for other in sorted(match_indices - {index}):
                for field in FIELDS:
                    if not out[index][field] and out[other][field]:
                        out[index][field] = out[other][field]
                out[other]['__removed__'] = 'yes'
                for key, value in list(lookup.items()):
                    if value == other:
                        lookup[key] = index
        for field in FIELDS:
            if not out[index][field] and original.get(field):
                out[index][field] = text_clean(str(original[field]))
        for key in keys:
            lookup[key] = index
    return [dict((field, row[field]) for field in FIELDS)
            for row in out if '__removed__' not in row]

def write_csv(rows: list[dict[str, str]], filename: str | Path) -> None:
    path = Path(filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    with tmp.open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS, extrasaction='ignore')
        writer.writeheader()
        for row in rows:
            # Excel treats these prefixes as formula starts; preserve literal text.
            safe = {field: _csv_safe(row.get(field, '')) for field in FIELDS}
            writer.writerow(safe)
    tmp.replace(path)

def _csv_safe(value: str) -> str:
    text = str(value or '')
    return "'" + text if text.lstrip().startswith(('=', '+', '-', '@')) else text

def inspect_csv(filename: str | Path) -> dict:
    with Path(filename).open(newline='', encoding='utf-8-sig') as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != FIELDS:
            raise ValueError('CSV does not contain the exact 26 columns in order.')
        rows = list(reader)
    unique_keys = {tuple(_identity_keys(row)) for row in rows}
    coverage = (sum(bool(row['Company Name'] and row['Company Industry']) for row in rows)
                / len(rows)) if rows else 0.0
    return {'rows': len(rows), 'unique_identities': len(unique_keys),
            'company_and_industry_coverage': coverage, 'quality_target_met': coverage >= .9}

def make_review_sample(csv_path: str | Path, review_path: str | Path, n: int = 10) -> None:
    with Path(csv_path).open(newline='', encoding='utf-8-sig') as stream:
        rows = list(csv.DictReader(stream))
    # Deterministic selection spread across the result, not always first 10.
    if len(rows) > n:
        indices = [(i * (len(rows) - 1)) // (n - 1) for i in range(n)] if n > 1 else [0]
        sample = [rows[i] for i in indices]
    else:
        sample = rows
    path = Path(review_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS + ['Matches Sales Navigator (Y/N)', 'Review Notes'])
        writer.writeheader()
        for row in sample:
            writer.writerow({**row, 'Matches Sales Navigator (Y/N)': '', 'Review Notes': ''})

def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')