# Sales Navigator lead collector (local prototype)

A **read-only** Python/Playwright utility that visits an authenticated Sales Navigator people-search page, walks up to a requested number of results pages, collects lead/company data visible in the rendered browser, deduplicates people, and writes a single CSV with the **26 fields in the required order**. It also creates a **separate manual-review template** for up to 10 people. The review file is **not** a second data export sheet; the primary output is one CSV.

**Status:** 23 offline parsing and orchestration tests pass, including company-enrichment regressions. This code has **not** been verified against a live authenticated Sales Navigator account; LinkedIn can change its layout. Complete the live three-page + ten-person check before treating it as production-ready. Some requested fields (such as actual employee count, member IDs, employment start years, and company LinkedIn IDs) may not be shown on the pages and are left empty. **90% company-name + industry coverage is measured, not guaranteed.**

## Windows setup (PowerShell)

Requires Python 3.10+ and your own Sales Navigator access.

```powershell
cd sales_nav_collector
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m playwright install chromium
```

If PowerShell prevents activation, use `.venv\Scripts\python.exe` in place of `python` for the commands.

## Run

```powershell
python sn_collect.py
```

Paste your Sales Navigator **people search URL**, then enter the number of pages. Or pass arguments directly:

```powershell
python sn_collect.py --url "https://www.linkedin.com/sales/search/people?query=..." --pages 3 --output data/leads.csv
```

1. A visible Chromium browser launches and remembers your login inside `.sales_navigator_profile`. If asked, **log in manually**. After search results appear, press Enter in the terminal.
2. The program reads the result cards and visits each unique lead profile. It tries to identify the current employer's LinkedIn company URL from either the search result or an explicitly matching link on the lead profile. It visits unique Sales Navigator account pages and, when a matching public LinkedIn company page is linked, its **About** page as well. It then clicks search pagination **Next** until the limit or the last page.
3. It writes `data/sales_nav_leads.csv` (or your chosen output file) and `data/sales_nav_leads_review.csv` with up to 10 rows to compare on screen.
4. An existing CSV is replaced **only after** at least one lead was found and processing reached the export step. The CSV uses UTF-8 with BOM for Excel compatibility.

Optional: `--skip-profiles` makes it faster but commonly leaves `Profile Summary`, `Company Start Year`, and `Current Role Start Year` empty. `--profile-dir sales_navigator_browser_profile` reuses the dedicated browser profile from older scripts, if Chrome is not already running with that profile. `--pause-ms 1800` increases the pause between requests. Use `python sn_collect.py --help` for all options.

## What the fields mean

Exactly the requested 26 headers are emitted in their specified order. A single **row = one person**; company fields appear on the same row. The program uses **Sales Profile ID or Sales Navigator URL** to merge duplicates, not names alone. `Captured At` is an UTC ISO-8601 timestamp. A blank field means the detail was not confidently available from the rendered page. This is expected for exact employee counts and a numeric LinkedIn company ID when those identifiers are not visibly displayed. Employee *range* is **not** substituted for exact employee *count*. A Sales Navigator company/account URL is **not** assumed to be the company's public LinkedIn ID.

**Diagnosing blank company columns:** The terminal summary prints how many company pages were opened, how many public About pages were opened, how many rows lacked a discoverable company URL, and how many exported rows still lack industry. If company pages opened = 0, check whether a company's name links to its Page on the results list or profile. If company pages opened is positive but fields remain blank, the page may render data in a different DOM layout or your account may not see those details. Avoid `--skip-profiles` when testing company discovery. This utility does not blindly resolve companies by name because similar names could produce wrong matches.

**Field distinctions:** "Company Employee Range" (such as 11-50) is often public; "Company Employee Count" is only filled if an exact count is stated. "Company Country" remains empty if only the headquarters city is shown. "Company LinkedIn ID" remains empty if only a text slug is available. Neither of these is guessed.

The parser intentionally does **not** infer employment dates from unrelated text, guess company industries from job titles, or extract private tokens from scripts. A company's website/domain is only recorded if linked and parseable. No contact requests, messages, reactions, edits, account enumeration or other LinkedIn actions are performed.

## Quality checks

Run offline tests:

```powershell
python -m pip install pytest
python -m pytest -q
```

For the actual assignment, run `--pages 3`. Confirm all **26 headers**, number of people, no duplicate Sales URLs/IDs, and the displayed percentage of records with **both Company Name and Company Industry**. Open the generated `_review.csv` in Excel/Sheets and compare **at least 10** sampled records with the Sales Navigator interface; fill `Matches Sales Navigator (Y/N)` and `Review Notes` manually. If coverage is below **90%** or any of the ten records mismatch, the acceptance criteria are **not met**. Adjust selectors against your actual UI before using the results.

## Limits and safety

- Uses ordinary on-screen navigation in a persistent visible Chromium browser; it does not use LinkedIn's internal/undocumented APIs or any CAPTCHA/authentication bypass. If login/verification is required, complete it manually. Respect platform rules and applicable privacy/data-use requirements.
- Some lead/company pages do not expose all 26 values. The browser markup can change without notice. Selectors use visible `data-anonymize` elements and labeled company/profile details; therefore the live 90% completeness target may need further, UI-specific improvements.
- The browser profile contains sensitive session information. **Never share or commit** `.sales_navigator_profile` or `sales_navigator_browser_profile`. CSV exports and browser snapshots can contain personal information. The provided `.gitignore` excludes these. Uploaded older scripts contained a plaintext API key: **rotate it if it was real**, and do not copy those scripts into a repository.
- The extractor does not upload data anywhere or publish to GitHub. No API key is required for this Playwright version.
