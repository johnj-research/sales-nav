"""Pure parsing, validation and CSV utilities for Sales Navigator lead results.

No network access or LinkedIn login occurs in this module.
Only facts labeled in the rendered page are populated.
"""
from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse, urlunparse

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
SALES_URN_RE = re.compile(r'^urn:li:fs_salesProfile:\(([^()]+)\)$', re.I)
YEAR_RE = re.compile(r'\b(?:19|20)\d{2}\b')
DATE_SPAN_RE = re.compile(
    r'\b((?:(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|'
    r'Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|'
    r'Dec(?:ember)?)\s+)?(?:19|20)\d{2})\s*'
    r'(?:-|–|—|to)\s*'
    r'(Present|Current|Now|(?:(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|'
    r'Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|'
    r'Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+)?(?:19|20)\d{2})\b',
    re.I,
)
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


def sales_url_from_urn(value: str | None) -> str:
    """Derive a lead link from a complete, explicitly supplied Sales URN."""
    match = SALES_URN_RE.fullmatch(value or '')
    if not match:
        return ''
    pieces = [piece.strip() for piece in match.group(1).split(',')]
    if len(pieces) != 3 or not all(re.fullmatch(r'[\w.=-]{1,180}', p) for p in pieces):
        return ''
    return clean_sales_url('/sales/lead/' + ','.join(pieces))


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


def external_website(value: str) -> str:
    """Accept an explicit external site, including LinkedIn's website redirect.

    Never follow these links; inspect only the page's already-displayed href.
    """
    value = text_clean(value)
    if value and not urlparse(value).scheme and '.' in value:
        value = 'https://' + value
    parsed = urlparse(value)
    if parsed.hostname in ('www.linkedin.com', 'linkedin.com') and (
            parsed.path.startswith('/redir/') or parsed.path.startswith('/redirect')):
        url_params = parse_qs(parsed.query)
        value = next(iter(url_params.get('url', []) or url_params.get('target', [])), '')
    return value if domain_from_website(value) else ''


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
    # Some Sales Navigator layouts render a virtualized <li> with only a
    # salesProfile URN attribute; the card itself may have no <a href>.
    # Convert this explicit identity into a local anchor for uniform parsing.
    for node in soup.find_all(attrs={'data-scroll-into-view': True}):
        url = sales_url_from_urn(node.get('data-scroll-into-view'))
        if url and node.select_one('[data-anonymize="person-name"]'):
            synthetic = soup.new_tag('a', href=url)
            node.insert(0, synthetic)
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
                    'employees', 'company website', 'organization type',
                    'year founded', 'founded in', 'headquarters location',
                    'headquarters city', 'headquarters country', 'country',
                    'industry type', 'company headcount', 'company location',
                    'about', 'current role started', 'joined current company',
                    'linkedin member id', 'profile summary',
                    'started at current company', 'current company start date',
                    'current position start date', 'time in current company',
                    'time in current role'}
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
                continue
        # Many React views wrap a label in a span and put the value in the
        # adjacent *parent* span/div rather than beside the label itself.
        parent = el.parent
        if not isinstance(parent, Tag) or parent.name not in {'div', 'span', 'p', 'li'}:
            continue
        if _text(parent).lower().rstrip(':') != label:
            continue
        next_parent = parent.find_next_sibling(['div', 'span', 'p', 'a'])
        if next_parent:
            val = _text(next_parent)
            if val and val.lower() != label and len(val) < 500:
                values[label] = (val, link_in(next_parent))
    return values


def _json_ld_organization(soup: BeautifulSoup, expected_name: str = '') -> dict:
    """Read explicitly published Organization JSON-LD, not arbitrary app state."""
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            obj = json.loads(script.string or script.get_text())
        except (ValueError, TypeError):
            continue
        stack = [obj]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
                continue
            if not isinstance(node, dict):
                continue
            types = node.get('@type', [])
            types = [types] if isinstance(types, str) else types
            if isinstance(types, list) and any(
                isinstance(t, str) and t.lower() in {'organization', 'corporation'}
                for t in types
            ):
                name = text_clean(node.get('name')) if isinstance(node.get('name'), str) else ''
                if not expected_name or name.casefold() == text_clean(expected_name).casefold():
                    return node
            stack.extend(v for k, v in node.items() if k in ('@graph', 'mainEntity'))
    return {}


def _absolute_image_url(candidate: str) -> str:
    parsed = urlparse(candidate)
    return candidate if (parsed.scheme == 'https' and parsed.hostname and
                         not parsed.username and not parsed.password and not parsed.port) else ''


def _company_logo(soup: BeautifulSoup, org: dict, public_page: bool) -> str:
    logo = org.get('logo', '')
    if isinstance(logo, dict):
        logo = logo.get('url') or logo.get('contentUrl', '')
    if isinstance(logo, str) and _absolute_image_url(logo):
        return logo
    for img in soup.find_all('img'):
        hints = ' '.join(str(img.get(attr, '')) for attr in (
            'alt', 'class', 'data-test-id', 'data-anonymize'))
        if ('logo' not in hints.casefold() or
                any(token in hints.casefold() for token in ('suggested', 'recommended', 'similar'))):
            continue
        for attr in ('src', 'data-delayed-url', 'data-src'):
            candidate = _absolute_image_url(img.get(attr, ''))
            if candidate:
                return candidate
    # On public LinkedIn company pages the social preview usually identifies
    # the company's logo; do not do this on Sales account/search pages.
    if public_page:
        for attrs in ({'property': 'og:image'}, {'name': 'twitter:image'}):
            meta = soup.find('meta', attrs=attrs)
            if meta and _absolute_image_url(meta.get('content', '')):
                return meta['content']
    return ''


def _company_numeric_id(soup: BeautifulSoup, org: dict) -> str:
    """Require a page-owned numeric company identifier, not a search/account ID."""
    identifier = org.get('identifier')
    if isinstance(identifier, dict):
        id_kind = str(identifier.get('propertyID') or identifier.get('name') or '').casefold()
        value = str(identifier.get('value') or '')
        if 'linkedin' in id_kind and re.fullmatch(r'\d+', value):
            return value
        match = re.fullmatch(r'urn:li:(?:fsd_company|fs_normalized_company):(\d+)', value)
        if match:
            return match.group(1)
    # A naked Organization.identifier may be a tax number, registration ID,
    # or DUNS number; it is not necessarily a LinkedIn company ID.
    # Avoid descendant IDs here: company pages can contain recommendations.
    h1 = soup.find('h1')
    for node in (soup.find('main'), h1, h1.parent if h1 else None):
        if not isinstance(node, Tag):
            continue
        urn = node.get('data-entity-urn', '')
        match = re.fullmatch(r'urn:li:(?:fsd_company|fs_normalized_company):(\d+)', urn)
        if match:
            return match.group(1)
        val = node.get('data-company-id', '')
        if re.fullmatch(r'\d+', str(val)):
            return str(val)
    return ''


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
                    'view company on linkedin', 'visit linkedin page',
                    'view on linkedin', 'view company page',
                    'visit company page'}:
            return candidate
        if norm_name and text == norm_name:
            return candidate
    return ''


def parse_company(html: str, page_url: str = '') -> dict[str, str]:
    soup = BeautifulSoup(html, 'html.parser')
    fields = blank_row()
    labels = _labeled_values(soup)
    h1 = soup.find('h1')
    org = _json_ld_organization(soup, _text(h1) if h1 else '')
    if h1:
        fields['Company Name'] = _text(h1)
    elif isinstance(org.get('name'), str):
        fields['Company Name'] = text_clean(org['name'])
    website, href = _get(labels, 'website', 'company website')
    anonymized_site = soup.select_one('[data-anonymize="company-website"]')
    if anonymized_site and not website:
        website = _text(anonymized_site)
        href = anonymized_site.get('href', '') or (
            anonymized_site.find('a', href=True).get('href', '')
            if anonymized_site.find('a', href=True) else '')
    candidate = (external_website(href) or external_website(website) or
                 external_website(org.get('url') if isinstance(org.get('url'), str) else ''))
    # An Organization.url may itself be a LinkedIn Page; it is not a company
    # website and should never be written to Domain / Website.
    if candidate:
        fields['Company Website'] = candidate
        fields['Company Domain'] = domain_from_website(candidate)
    fields['Company Industry'] = (_get(labels, 'industry', 'industry type')[0] or
                                  _anonymized(soup, 'company-industry') or
                                  _anonymized(soup, 'industry') or
                                  (org.get('industry') if isinstance(org.get('industry'), str) else ''))
    fields['Company Type'] = (_get(labels, 'type', 'organization type')[0] or
                              _anonymized(soup, 'company-type'))
    fields['Company Founded Year'] = (_year(_get(labels, 'founded', 'year founded', 'founded in')[0]) or
                                      _year(_anonymized(soup, 'company-founded')) or
                                      _year(str(org.get('foundingDate', ''))))
    employees = (_get(labels, 'company size', 'company headcount')[0] or
                 _anonymized(soup, 'company-size'))
    if not employees and isinstance(org.get('numberOfEmployees'), dict):
        qty = org['numberOfEmployees']
        if qty.get('minValue') is not None and qty.get('maxValue') is not None:
            employees = f"{qty['minValue']}-{qty['maxValue']}"
    range_match = re.search(r'(\d[\d,]*\s*(?:-|–|—|to)\s*\d[\d,]*\+?|\d[\d,]*\s*\+)', employees, re.I)
    if range_match:
        fields['Company Employee Range'] = re.sub(r'\s*[-–—]\s*', '-', range_match.group(1)).replace(' ', '')
    headcount = _get(labels, 'employee count', 'headcount', 'employees')[0]
    # "1,234 associated members" or "See all employees" is *not* the
    # actual size of a company; accept only an explicit integer headcount.
    if re.fullmatch(r'\d[\d,]*', headcount):
        fields['Company Employee Count'] = headcount
    elif isinstance(org.get('numberOfEmployees'), dict):
        count = org['numberOfEmployees'].get('value')
        if isinstance(count, (str, int)) and re.fullmatch(r'\d[\d,]*', str(count)):
            fields['Company Employee Count'] = str(count)
    city, country = _location_parts(_get(labels, 'headquarters', 'headquarters location',
                                             'company location')[0])
    if not city:
        city, country = _location_parts(_anonymized(soup, 'company-headquarters'))
    city = city or _get(labels, 'headquarters city')[0]
    country = country or _get(labels, 'headquarters country', 'country')[0]
    if isinstance(org.get('address'), dict):
        address = org['address']
        city = city or text_clean(address.get('addressLocality', ''))
        structured_country = address.get('addressCountry', '')
        if isinstance(structured_country, dict):
            structured_country = structured_country.get('name', '')
        if isinstance(structured_country, str):
            country = country or text_clean(structured_country)
    fields['Company Headquarters City'], fields['Company Country'] = city, country
    public = _public_company_from_page(soup, page_url, fields['Company Name'])
    fields['Company LinkedIn URL'] = public
    fields['_company_public_url'] = public
    fields['Company Logo URL'] = _company_logo(soup, org, bool(public_company_url(page_url)))
    if public:
        slug = PUBLIC_COMPANY_RE.match(urlparse(public).path).group(1)
        if slug.isdigit():
            fields['Company LinkedIn ID'] = slug
        elif public_company_url(page_url):
            fields['Company LinkedIn ID'] = _company_numeric_id(soup, org)
    return fields


def _experience_sections(soup: BeautifulSoup) -> list[Tag]:
    """Locate actual career history, excluding recommended profile cards."""
    candidates = []
    experience_item_css = ('li, article, [data-test-id*="experience-item" i], '
                           '[class*="experience-item" i], '
                           '[data-view-name*="profile-component-entity" i]')
    for el in soup.find_all(['section', 'div']):
        marker = ' '.join(str(el.get(key, '')) for key in ('id', 'class', 'data-test-id', 'aria-label'))
        if re.search(r'\bexperience\b|experience[-_]', marker, re.I) and el.select_one(experience_item_css):
            candidates.append(el)
    for heading in soup.find_all(['h2', 'h3']):
        if _text(heading).casefold() != 'experience':
            continue
        section = heading.find_parent('section')
        if section is None:
            section = heading.parent
            # Sometimes the heading and career list are adjacent siblings.
            for _ in range(3):
                if section is None or section.name == 'body':
                    break
                if section.select_one(experience_item_css):
                    break
                section = section.parent
        if isinstance(section, Tag):
            candidates.append(section)
    # Keep the outer experience company grouping, not an inner "roles" node
    # lacking the company name, and never parse the same group twice.
    return [s for s in candidates if not any(
        s is not other and other in s.parents for other in candidates)]


def _employment_dates(text: str) -> list[tuple[str, bool]]:
    """Parse literal year-to-year / year-to-present spans, never tenure guesses."""
    return [(m.group(1)[-4:], m.group(2).casefold() in {'present', 'current', 'now'})
            for m in DATE_SPAN_RE.finditer(text)]


def _experience_years(soup: BeautifulSoup, current_company: str,
                      current_job_title: str = '') -> tuple[str, str]:
    wanted = text_clean(current_company).casefold()
    if not wanted:
        return '', ''
    title = text_clean(current_job_title).casefold()
    for section in _experience_sections(soup):
        # A top-level <li> is a company block; nested <li>s are individual
        # roles at that company (LinkedIn's grouped experience view).
        jobs = section.find_all('li')
        outer_jobs = [j for j in jobs if not j.find_parent('li') or
                      j.find_parent('li') not in jobs]
        if not outer_jobs:
            outer_jobs = section.find_all('article')
        if not outer_jobs:
            selectors = ('[data-test-id*="experience-item" i], '
                         '[class*="experience-item" i], '
                         '[data-view-name*="profile-component-entity" i]')
            items = section.select(selectors)
            outer_jobs = [j for j in items if not any(parent in items for parent in j.parents)]
        for item in outer_jobs:
            # Require the *displayed* company text or its exact company anchor.
            # A previous employer listed elsewhere in the profile is ignored.
            top_text = ' '.join(item.stripped_strings)
            if not re.search(r'(?<!\w)' + re.escape(wanted) + r'(?!\w)',
                             top_text.casefold()):
                continue
            nested = item.find_all('li')
            if nested:
                role_records = []
                for job in nested:
                    if job.find_parent('li') is not item:
                        continue
                    parts = _employment_dates(_text(job))
                    for year, is_present in parts:
                        role_records.append((year, is_present, _text(job)))
            else:
                role_records = [(year, is_present, top_text)
                                for year, is_present in _employment_dates(top_text)]
            if not role_records:
                continue
            active = [r for r in role_records if r[1]]
            if not active:
                # A former stint at the same company is not necessarily the
                # current position; never attribute those dates by default.
                continue
            company_year = min(r[0] for r in role_records)
            role_year = active[0][0]
            title_matches = [r for r in role_records if title and title in r[2].casefold()]
            if title_matches and any(r[1] for r in title_matches):
                role_year = next(r[0] for r in title_matches if r[1])
            return company_year, role_year
    return '', ''


def parse_profile(html: str, current_company: str = '',
                  current_job_title: str = '') -> dict[str, str]:
    soup = BeautifulSoup(html, 'html.parser')
    labels = _labeled_values(soup)
    row = blank_row()
    row['Profile Summary'] = _get(labels, 'about', 'profile summary')[0]
    row['Company Start Year'] = _year(_get(
        labels, 'joined current company', 'started at current company',
        'current company start date', 'time in current company')[0])
    row['Current Role Start Year'] = _year(_get(
        labels, 'current role started', 'current position start date',
        'time in current role')[0])
    company_year, role_year = _experience_years(soup, current_company, current_job_title)
    row['Company Start Year'] = row['Company Start Year'] or company_year
    row['Current Role Start Year'] = row['Current Role Start Year'] or role_year
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
            anchor_name = (_text(anchor) or text_clean(anchor.get('aria-label'))).casefold()
            if candidate and anchor_name == wanted:
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
