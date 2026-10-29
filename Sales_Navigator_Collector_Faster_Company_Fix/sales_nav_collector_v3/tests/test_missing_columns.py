"""Fixtures for field layouts absent from the original 3.2 collector.

Offline synthetic markup only; no LinkedIn access or private session data.
"""

from sn_collect import collect_pages
from sn_core import parse_company, parse_profile


def test_public_company_about_nested_fields_and_logo_metadata():
    page = '''<html><head>
       <meta property="og:image" content="https://cdn.example/firm-logo.jpg">
       </head><body><main data-entity-urn="urn:li:fsd_company:84620">
       <h1>Example Financial</h1>
       <div class="org-page-details"><div><span>Website</span></div>
         <div><a href="https://www.example-financial.test">Visit website</a></div>
         <div>Industry</div><div>Financial Services</div>
         <div>Company size</div><div>51-200 employees</div>
         <div>Employee count</div><div>143</div>
         <div>Type</div><div>Privately Held</div>
         <div>Founded</div><div>2016</div>
         <div>Headquarters</div><div>Bengaluru, Karnataka, India</div>
       </div></main></body></html>'''
    result = parse_company(page, 'https://www.linkedin.com/company/example-financial/about/')
    assert result['Company Domain'] == 'example-financial.test'
    assert result['Company Website'] == 'https://www.example-financial.test'
    assert result['Company Employee Count'] == '143'
    assert result['Company Employee Range'] == '51-200'
    assert result['Company Founded Year'] == '2016'
    assert result['Company Industry'] == 'Financial Services'
    assert result['Company Type'] == 'Privately Held'
    assert result['Company Headquarters City'] == 'Bengaluru'
    assert result['Company Country'] == 'India'
    assert result['Company Logo URL'] == 'https://cdn.example/firm-logo.jpg'
    assert result['Company LinkedIn ID'] == '84620'
    assert result['Company LinkedIn URL'] == 'https://www.linkedin.com/company/example-financial'


def test_public_structured_organization_metadata_fills_unlabeled_fields():
    page = '''<html><body><script type="application/ld+json">{
       "@context":"https://schema.org", "@type":"Organization",
       "name":"Acme Insights", "url":"https://acme.example",
       "industry":"Data Analytics", "foundingDate":"2014-03-21",
       "logo":{"url":"https://cdn.example/acme.svg"},
       "address":{"@type":"PostalAddress","addressLocality":"Austin",
                  "addressCountry":"United States"},
       "numberOfEmployees":{"@type":"QuantitativeValue","minValue":11,"maxValue":50},
       "identifier":{"@type":"PropertyValue","propertyID":"LinkedIn company ID","value":"78123"}
       }</script></body></html>'''
    parsed = parse_company(page, 'https://www.linkedin.com/company/acme-insights/about/')
    assert parsed['Company Name'] == 'Acme Insights'
    assert parsed['Company Domain'] == 'acme.example'
    assert parsed['Company Industry'] == 'Data Analytics'
    assert parsed['Company Founded Year'] == '2014'
    assert parsed['Company Employee Range'] == '11-50'
    assert parsed['Company Employee Count'] == ''
    assert parsed['Company Headquarters City'] == 'Austin'
    assert parsed['Company Country'] == 'United States'
    assert parsed['Company Logo URL'] == 'https://cdn.example/acme.svg'
    assert parsed['Company LinkedIn ID'] == '78123'


def test_grouped_experience_yields_company_year_and_current_role_year():
    page = '''<html><body><section id="experience"><h2>Experience</h2><ul>
      <li><a href="/company/alpha">Alpha Advisory</a><ul>
       <li><h3>Senior Director</h3><span>Apr 2024 – Present</span></li>
       <li><h3>Manager</h3><span>Feb 2020 – Mar 2024</span></li>
      </ul></li>
      <li><a href="/company/old">Old Employer</a><span>2010 - 2019</span></li>
    </ul></section></body></html>'''
    profile = parse_profile(page, current_company='Alpha Advisory',
                            current_job_title='Senior Director')
    assert profile['Company Start Year'] == '2020'
    assert profile['Current Role Start Year'] == '2024'
    assert profile['Company LinkedIn URL'] == 'https://www.linkedin.com/company/alpha'


def test_current_single_position_and_div_only_experience_cards():
    page = '''<section aria-label="Experience"><h2>Experience</h2>
      <div class="experience-item">
        <a href="/company/alpha">Alpha Advisory</a>
        <h3>Managing Director</h3><span>Jun 2021 - Present</span>
      </div>
      <div class="experience-item">
        <a href="/company/old">Other Company</a>
        <h3>Director</h3><span>2014 - 2020</span>
      </div></section>'''
    fields = parse_profile(page, 'Alpha Advisory', 'Managing Director')
    assert fields['Company Start Year'] == '2021'
    assert fields['Current Role Start Year'] == '2021'


def test_employment_years_not_guessed_from_durations_or_former_company():
    page = '''<section id="experience"><h2>Experience</h2><ul>
      <li><a href="/company/alpha">Alpha Advisory</a><span>3 yrs 2 mos</span></li>
      <li><a href="/company/old">Old Firm</a><span>2014 - Present</span></li>
    </ul></section>'''
    fields = parse_profile(page, 'Alpha Advisory')
    assert fields['Company Start Year'] == ''
    assert fields['Current Role Start Year'] == ''
    assert fields['Company LinkedIn URL'] == 'https://www.linkedin.com/company/alpha'


def test_sales_account_number_and_recommended_company_are_not_company_id():
    page = '''<html><main><h1>Alpha Advisory</h1>
      <div><h3>Other companies</h3>
        <a href="/company/similar">Another Company</a>
        <img alt="Suggested company logo" src="https://cdn.example/other.jpg">
        <span data-entity-urn="urn:li:fsd_company:9999">Suggested</span>
      </div>
    </main></html>'''
    company = parse_company(page, 'https://www.linkedin.com/sales/company/51515')
    assert company['Company LinkedIn ID'] == ''
    assert company['Company LinkedIn URL'] == ''
    # A recommended company's logo should not become the target company logo.
    assert company['Company Logo URL'] == ''


def test_end_to_end_collection_exports_enriched_company_and_profile_years():
    search = '''<li><a href="/sales/lead/A">
      <span data-anonymize="person-name">Ana Costa</span></a>
      <span data-anonymize="title">VP</span>
      <a href="/company/alpha">Alpha Advisory</a>
      <span data-anonymize="company-name">Alpha Advisory</span></li>'''
    profile = '''<section id="experience"><ul>
      <li><a href="/company/alpha">Alpha Advisory</a>
        <h3>VP</h3><span>2022 - Present</span></li></ul></section>'''
    company = '''<h1>Alpha Advisory</h1><dl>
      <dt>Website</dt><dd><a href="https://alpha.example">Website</a></dd>
      <dt>Industry</dt><dd>Consulting</dd>
      <dt>Founded</dt><dd>2010</dd>
      <dt>Company size</dt><dd>11-50 employees</dd>
    </dl>'''
    urls = []
    def fetch(url):
        urls.append(url)
        return profile if '/sales/lead/' in url else company
    rows, stats = collect_pages(lambda: search, lambda: False, fetch, 1)
    assert stats.company_pages_visited == 1
    assert len(rows) == 1
    row = rows[0]
    assert row['Current Role Start Year'] == '2022'
    assert row['Company Start Year'] == '2022'
    assert row['Company Domain'] == 'alpha.example'
    assert row['Company Website'] == 'https://alpha.example'
    assert row['Company Industry'] == 'Consulting'
    assert row['Company Employee Range'] == '11-50'
    assert row['Company Founded Year'] == '2010'
    assert row['Company LinkedIn URL'] == 'https://www.linkedin.com/company/alpha'
    assert urls == ['https://www.linkedin.com/sales/lead/A',
                    'https://www.linkedin.com/company/alpha/about/']


def test_linkedin_website_redirect_is_unwrapped_but_social_page_is_not_website():
    page = '''<h1>Example</h1><dl><dt>Website</dt>
       <dd><a href="https://www.linkedin.com/redir/redirect?url=https%3A%2F%2Fexample.test%2Fcontact&amp;urlhash=xyz">Company website</a></dd>
       </dl>'''
    parsed = parse_company(page, 'https://www.linkedin.com/company/example/about/')
    assert parsed['Company Website'] == 'https://example.test/contact'
    assert parsed['Company Domain'] == 'example.test'
    not_website = parse_company('''<script type="application/ld+json">
       {"@type":"Organization","name":"Firm","url":"https://www.linkedin.com/company/firm"}
       </script>''', 'https://www.linkedin.com/company/firm/about/')
    assert not_website['Company Website'] == ''


def test_sales_account_explicit_company_data_anonymize_details():
    page = '''<html><h1>Beta Industries</h1>
     <a data-anonymize="company-website" href="https://beta.example">Site</a>
     <span data-anonymize="company-industry">Manufacturing</span>
     <span data-anonymize="company-type">Public Company</span>
     <span data-anonymize="company-size">1,001-5,000 employees</span>
     <span data-anonymize="company-founded">1999</span>
     <span>Headquarters city</span><span>Chennai</span>
     <span>Country</span><span>India</span>
     <img data-anonymize="company-logo" data-delayed-url="https://cdn.example/beta.png">
     </html>'''
    details = parse_company(page, 'https://www.linkedin.com/sales/company/99')
    assert details['Company Website'] == 'https://beta.example'
    assert details['Company Domain'] == 'beta.example'
    assert details['Company Industry'] == 'Manufacturing'
    assert details['Company Type'] == 'Public Company'
    assert details['Company Employee Range'] == '1,001-5,000'
    assert details['Company Founded Year'] == '1999'
    assert details['Company Headquarters City'] == 'Chennai'
    assert details['Company Country'] == 'India'
    assert details['Company Logo URL'] == 'https://cdn.example/beta.png'
