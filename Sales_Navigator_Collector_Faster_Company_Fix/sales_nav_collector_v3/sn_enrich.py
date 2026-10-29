"""Conservative offline company enrichment from user-reviewed company references.

References match the exact normalized company name. They never overwrite nonblank
lead or company data, and no person-level values are guessed.
"""
from __future__ import annotations

import csv
from pathlib import Path
from urllib.parse import urlparse

from sn_core import FIELDS, domain_from_website, public_company_url, text_clean, write_csv

COMPANY_FIELDS = tuple(f for f in FIELDS if f.startswith('Company ') and f != 'Company Name')


def company_key(name: str) -> str:
    """Case/whitespace normalization ONLY: subsidiaries stay separate."""
    return text_clean(name).casefold()


def load_company_references(filename: str | Path | None) -> dict[str, dict[str, str]]:
    if not filename:
        return {}
    file = Path(filename)
    if not file.exists():
        raise FileNotFoundError(f'Company reference CSV does not exist: {file}')
    result: dict[str, dict[str, str]] = {}
    with file.open('r', encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or 'Company Name' not in reader.fieldnames:
            raise ValueError('Company reference CSV must contain a Company Name header.')
        for number, source in enumerate(reader, 2):
            key = company_key(source.get('Company Name') or '')
            if not key:
                raise ValueError(f'Company reference CSV row {number}: missing Company Name.')
            if key in result:
                raise ValueError(f'Company reference CSV has a duplicate company: {source["Company Name"]}.')
            row = {field: text_clean(source.get(field) or '') for field in COMPANY_FIELDS}
            if row['Company LinkedIn URL']:
                parsed = public_company_url(row['Company LinkedIn URL'])
                if not parsed:
                    raise ValueError(f'Company reference row {number}: invalid LinkedIn company URL.')
                row['Company LinkedIn URL'] = parsed
            if row['Company Website']:
                parsed = urlparse(row['Company Website'])
                if parsed.scheme not in ('http', 'https') or not domain_from_website(row['Company Website']):
                    raise ValueError(f'Company reference row {number}: invalid company website URL.')
                row['Company Domain'] = row['Company Domain'] or domain_from_website(row['Company Website'])
            if row['Company Founded Year'] and not (row['Company Founded Year'].isdigit() and
                                                    len(row['Company Founded Year']) == 4):
                raise ValueError(f'Company reference row {number}: founded year should have four digits.')
            result[key] = row
    return result


def apply_company_reference(row: dict[str, str], refs: dict[str, dict[str, str]]) -> bool:
    reference = refs.get(company_key(row.get('Company Name') or row.get('Current Company') or ''))
    if not reference:
        return False
    changed = False
    for field in COMPANY_FIELDS:
        if reference.get(field) and not row.get(field):
            row[field] = reference[field]
            changed = True
    return changed


def enrich_existing_csv(source_path: str | Path, output_path: str | Path,
                        refs: dict[str, dict[str, str]]) -> tuple[int, int]:
    with Path(source_path).open('r', encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or any(field not in reader.fieldnames for field in FIELDS):
            raise ValueError('Existing CSV must have all 26 original lead headers.')
        rows = list(reader)
    enriched = sum(apply_company_reference(row, refs) for row in rows)
    write_csv(rows, output_path)
    return len(rows), enriched
