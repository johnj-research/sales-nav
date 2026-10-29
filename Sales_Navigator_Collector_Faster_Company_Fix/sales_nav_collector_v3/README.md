# Sales Navigator lead collector v3.3 (expanded field extraction)

A **read-only** Python/Playwright utility that visits an authenticated Sales Navigator people-search page, walks up to a requested number of results pages, collects lead/company data visible in the rendered browser, deduplicates people, and writes a single CSV with the **26 fields in the required order**. It also creates a **separate manual-review template** for up to 10 people. The review file is **not** a second data export sheet; the primary output is one CSV.

**Status:** 58 offline parsing, pagination, virtualization, enrichment, API-response and account-selection redirect tests pass, including expanded firmographic and employment-date fixtures. This code has **not** been verified against a live authenticated Sales Navigator account; LinkedIn can change its layout. Complete the live three-page + ten-person check before treating it as production-ready. Requested fields that are absent from the viewed pages/browser-loaded data remain empty. **90% company-name + industry coverage is measured, not guaranteed.**

## Faster collection and company enrichment (October 27, 2026)

The default detail reader now polls every 350 ms and stops after three unchanged
observations once meaningful data is present. A stable company fixture uses
1.05 seconds of polling instead of 4.8 seconds at the previous default pause.
Initial detail settling is also reduced from 800 ms to 350 ms. This is an offline
wait comparison, not a measured end-to-end LinkedIn speedup. Network navigation,
profile count, and LinkedIn rendering still determine the total time.

If company fields arrive late on your connection, add `--thorough-details` to
restore the longer waits. Fast mode can miss fields that arrive after its stable
window. Empty skeletons retain the full retry budget. Profile visits remain on.
`--skip-profiles` is an existing optional faster mode, but it can leave profile
summary and employment dates blank; company enrichment still runs.

When a Sales Navigator company page lacks its public Page link, the collector
now attempts the existing exact-name public company search, cached per distinct
company name. It no longer assumes that finding a Sales account URL completes
company discovery. This live search still depends on LinkedIn's current markup.

The reference CSV now includes the exact name **T-Mobile**, verified against
https://www.linkedin.com/company/t-mobile/ on October 27, 2026. It supplies website,
domain, size range, industry, type, Bellevue HQ, United States, and public Page
URL. It is not applied to differently named subsidiaries or other employers.
The public Page's primary location supports the country. The size range is
10,001+; this is not an exact employee count. The annual report states an
approximate count as of December 31, 2025, so no exact count is inserted. A numeric
LinkedIn company ID is also left blank unless explicitly established.

For the uploaded 125-row CSV, reference repair fills these company details on
121 exact T-Mobile rows without opening a browser. The other four rows are not
assigned T-Mobile details. Original person data and existing nonblank values are
preserved. All 65 offline tests pass; a live authenticated run remains unverified.

Normal run (fast detail reading is now the default):

```powershell
python sn_collect.py --url "YOUR_FULL_SALES_NAVIGATOR_SEARCH_URL" --pages 3 --output data/leads.csv
```

Slower fallback when company details appear late:

```powershell
python sn_collect.py --url "YOUR_FULL_SALES_NAVIGATOR_SEARCH_URL" --pages 3 --thorough-details --output data/leads.csv
```

## CSV final-column fix (October 27, 2026)

Fixed three gaps affecting company enrichment: captured public company URLs are
now used even without an internal company-link field; detail reading waits for
late fields instead of stopping after five generic fields; and a published
structured country can fill a missing country even when the city is already known.
Structured Country objects with a name are supported too. All 61 offline tests pass.
Live authenticated Sales Navigator behavior has not been verified.

Run a **fresh collection** to obtain newly captured values:

```powershell
python sn_collect.py --url "YOUR_FULL_SALES_NAVIGATOR_SEARCH_URL" --pages 3 --output data/leads_fixed.csv --pause-ms 1400
```

Keep profile visits enabled. The final six columns are Company Type, Company
Headquarters City, Company Country, Company Logo URL, Company LinkedIn ID, and
Company LinkedIn URL. Values that LinkedIn does not expose still remain blank.
The existing CSV repair command cannot recover missing browser data by itself.
Detail visits may take a few seconds longer to allow delayed content to appear.

## v3.3: Extraction improvements for previously blank columns

This release improves how the original 26-column collector obtains the
**Company Start Year**, **Current Role Start Year**, and the 12 **Company ...**
enrichment columns. These improvements apply to **new browser collections**:

- Person start years: parse explicit `YYYY – Present` date ranges in the **current
  employer's experience block**, including multiple positions grouped beneath
  one company, in addition to explicitly labeled start-date fields. Browser-loaded
  search responses with explicit current-company/current-position start dates
  may also provide these years. This does *not* subtract rounded tenure durations
  from today's date or assign years from former employers.
- Company About pages: support additional label/value layouts, website redirect
  links, explicit headcount, size range, founding year, industry, type, and
  headquarters city/country. When genuinely present, public `Organization`
  JSON-LD, logo markup and page-owned numeric company identifiers can supply
  otherwise blank fields. The collector can also use explicit company fields
  in *already browser-loaded* lead-search responses.
- Render timing: detail-page reading retries briefly after initial navigation
  when a React SPA page is still loading. The terminal now prints **blank counts
  for each of the 14 requested fields** so you can identify the remaining gaps.

**Run a fresh collection** (PowerShell in the extracted directory):

```powershell
python sn_collect.py --url "YOUR_FULL_SALES_NAVIGATOR_SEARCH_URL" --pages 3 --output data/leads_v33.csv --pause-ms 1400
```

Do **not** use `--skip-profiles` when you want employment start years. You must
rerun the browser collector for previously absent profile and company values:
`--enrich-csv` can only fill fields supplied by the local reviewed company
reference file, not missing personal profile years. The included
`company_reference.csv` only contains a reviewed record for Reliance Industries
Limited. Other companies need a resolvable page or an appropriate verified
company reference.

**Data availability is still a real limit.** An exact employee count is **not**
the same as a company-size range or the count of LinkedIn-associated members.
A text company slug or a Sales Navigator account number is **not** necessarily
the numeric LinkedIn company ID. A city does **not** establish a country. Some
profile pages display only rounded tenure durations instead of exact start
dates; without an explicit date, the year stays blank. None of the above fields
can be guaranteed for every company or person.

## v3.2: Fixing the blank company columns (October 22, 2026)

**Observed in the supplied export:** 125 rows and 26 headers, but **16 columns
were entirely empty**. The search parser recorded the company name (118 rows
were exactly `Reliance Industries Limited`) but couldn't find company Page links,
so `collect_pages()` skipped all company enrichment for those rows.

**Changes:**

- The live collector tries to find the company Page in three ways: an explicit
  link on the search card or person profile; an **exact-name reviewed** URL in
  `company_reference.csv`; or an **exact and unambiguous on-screen LinkedIn
  company-search result**, once per distinct company name. If there is no
  reliable match, it leaves the value blank rather than assigning the wrong
  company's data. Live public company search depends on the current LinkedIn
  interface and has **not** been validated on a signed-in account.
- The collector now fills missing firmographic fields from a reviewed company
  reference file even if the LinkedIn About page is unavailable. Existing
  captured fields take precedence and are not overwritten.
- A browser-free `--enrich-csv` mode repairs an already-exported CSV. It does
  **not** requery personal profiles or hallucinate employment dates.

### Repair an existing CSV (no LinkedIn login or Playwright browser required)

Open PowerShell in the extracted `sales_nav_collector_v3` folder and run:

```powershell
python sn_collect.py --enrich-csv "C:\path\to\sales_nav_leads.csv" --output "C:\path\to\sales_nav_leads_enriched.csv"
```

The included `company_reference.csv` has an **exact company-name** reference for
`Reliance Industries Limited`: website/domain `ril.com`, LinkedIn industry
`Oil and Gas`, size range `10,001+`, type `Public Company`, LinkedIn Page
`https://www.linkedin.com/company/reliance`, headquarters city `Navi Mumbai`,
country `India`, and incorporation year `1973`.

Sources: [Reliance LinkedIn company page](https://www.linkedin.com/company/reliance)
and [Reliance's corporate sustainability filing](https://www.ril.com/sites/default/files/2024-08/BRSR202324.pdf).
The reference is **not** applied to similarly named companies or subsidiaries
unless you explicitly add their verified names and details. Reference CSV rows
can include `Source URL` and `Secondary Source URL` for provenance; the
collector uses only the matching `Company ...` fields in the 26-column export.

### Collect fresh results with company discovery

```powershell
python sn_collect.py --url "YOUR_FULL_SALES_NAVIGATOR_SEARCH_URL" --pages 3 --output data/leads.csv
```

The included company reference loads automatically. The collector also tries
read-only rendered LinkedIn company searches for names not already resolved.
This may not work when LinkedIn redirects to a login wall or changes its UI.
To disable optional company searches use `--no-company-search`. To use your
own reviewed reference CSV use `--company-map "C:\path\to\companies.csv"`.
Use `--skip-profiles` only if you don't need person-level details.

**Columns that still can be blank:** Member ID, person About/Summary,
company/role start years, numeric employee count, logo URL, numeric company ID,
and any other field not reliably exposed in the browser or reviewed references.
A LinkedIn company *employee-size range* is not the same as the exact number
of employees. It is neither safe nor accurate to fill person-level values from
generic company data. The sample CSV repair has **94.4% Company Name + Industry
coverage** (118/125), not 100% completion of all 26 columns.

Run the 48 offline regression tests with `python -m pytest -q`. These include
company reference matching, cached live company lookup behavior, and an offline
repair check. The live account/page behavior remains unverified.

---

## Windows setup (PowerShell)

Requires Python 3.10+ and your own Sales Navigator access.

```powershell
cd sales_nav_collector_v3
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

1. A visible Chromium browser launches and remembers your login inside `.sales_navigator_profile`. If LinkedIn redirects to its **Sales Navigator contract/account chooser**, select the account you are entitled to use. If asked, **log in or complete verification manually**. Press Enter in the terminal after Sales Navigator opens; the collector will retry the *original filtered search URL* if needed. Do not enter your credentials into the terminal.
2. The program scrolls the **actual results container**, preserving profiles as virtualized cards appear (rather than relying on the first four DOM cards). By default it also passively observes lead-search JSON responses the browser itself loads from `/sales-api/` or `/voyager/api/`, merging their recognized lead records with the DOM results. **It does not make independent API requests, replay endpoints, copy session cookies, or bypass verification.** It then visits each unique lead profile. It tries to identify the current employer's LinkedIn company URL from either the search result or an explicitly matching link on the lead profile. It visits unique Sales Navigator account pages and, when a matching public LinkedIn company page is linked, its **About** page as well. It then clicks search pagination **Next** until the limit or the last page.
3. It writes `data/sales_nav_leads.csv` (or your chosen output file) and `data/sales_nav_leads_review.csv` with up to 10 rows to compare on screen.
4. An existing CSV is replaced **only after** at least one lead was found and processing reached the export step. The CSV uses UTF-8 with BOM for Excel compatibility.

**New in v3:** `--max-scroll-steps 110` increases the number of attempts for long/lazy result lists; `--pause-ms 1300` helps slow-loading pages; `--no-voyager` disables the passive API response reader and uses the DOM only. The collector stops after several quiet passes at the bottom and logs the number of leads found on every page. There is no forced 4- or 8-card limit. Search pagination also recognizes more Next-button variants and checks that new leads actually appeared.

**Voyager / Sales API details:** On newer Sales Navigator clients the browser may use `/sales-api/salesApiLeadSearch`, while some LinkedIn experiences use `/voyager/api/` search responses. These are **undocumented internal services, not an official supported public Sales Navigator search API**. The collector does not call them directly; it only parses successful JSON search responses already received in the signed-in browser. It understands explicitly identified lead links/URNs, names, `currentPositions`, title, company and region. Unsupported response shapes are ignored and ordinary rendered-card extraction continues. The end-of-run API counters show if useful search responses were observed. API access/shape, completeness and reliability are not guaranteed. For authorized enterprise integrations, use supported LinkedIn partner interfaces rather than relying on internals.

Optional: `--skip-profiles` makes it faster but commonly leaves `Profile Summary`, `Company Start Year`, and `Current Role Start Year` empty. `--profile-dir sales_navigator_browser_profile` reuses the dedicated browser profile from older scripts, if Chrome is not already running with that profile. `--pause-ms 1800` increases the pause between requests. Use `python sn_collect.py --help` for all options.

## Fix for `Page.goto: Navigation ... is interrupted by another navigation`

This commonly occurs if LinkedIn redirects a search request to `/sales/contract-chooser` before the initial page finishes loading. It is an **account selection / access navigation**, not a four-card parsing bug. Version 3.1+ catches only the relevant Playwright navigation-interruption error, lets you complete LinkedIn's selection manually, and then retries the original search. It never attempts to select an account automatically.

If you see the chooser, **click the Sales Navigator account you are licensed to use**, wait for Sales Navigator to open, then press Enter in the PowerShell window. If the page keeps redirecting to the chooser, open Sales Navigator manually in the same dedicated Chromium browser and check that your subscription and account access are correct. If your session search URL has expired, copy a new link from a working people search. Do **not** bypass LinkedIn sign-in, licensing or security screens.

For an initial check, try:

```powershell
python sn_collect.py --url "YOUR_FULL_SALES_NAVIGATOR_SEARCH_URL" --pages 1 --pause-ms 1200 --max-scroll-steps 100
```

To avoid losing your login, keep using the same `--profile-dir` and do not run two collector instances against one browser profile at the same time. Version 3.2 does not promise live access: if your account cannot open the search in LinkedIn's UI, the collector cannot open it either.

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

- Uses on-screen navigation in a persistent visible Chromium browser. It **listens to existing browser-loaded internal JSON search responses but does not initiate API requests**; it does not bypass CAPTCHAs or authentication. If login/verification is required, complete it manually. Respect platform rules and applicable privacy/data-use requirements.
- A page truly containing four results will still export only four. If Sales Navigator visibly shows more than the script finds, inspect the results scrolling container and whether the current UI is still supported. Test with `--pages 3` and examine printed per-page counts.
- Some lead/company pages do not expose all 26 values. The browser markup can change without notice. Selectors use visible `data-anonymize` elements and labeled company/profile details; therefore the live 90% completeness target may need further, UI-specific improvements.
- The browser profile contains sensitive session information. **Never share or commit** `.sales_navigator_profile` or `sales_navigator_browser_profile`. CSV exports and browser snapshots can contain personal information. The provided `.gitignore` excludes these. Uploaded older scripts contained a plaintext API key: **rotate it if it was real**, and do not copy those scripts into a repository.
- The extractor does not upload data anywhere or publish to GitHub. No API key is required for this Playwright version.
