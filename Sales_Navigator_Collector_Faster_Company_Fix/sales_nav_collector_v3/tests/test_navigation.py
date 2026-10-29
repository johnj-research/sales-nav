"""Offline regression tests for contract-chooser navigation interruptions."""

import pytest
from playwright.sync_api import Error as PlaywrightError

from sn_navigation import (
    is_contract_chooser, is_login_page, is_search_page,
    navigate_linkedin, open_search_with_manual_access, search_has_results,
)


SEARCH = 'https://www.linkedin.com/sales/search/people?query=my-filters'
CHOOSER = 'https://www.linkedin.com/sales/contract-chooser?accountId=123'
HOME = 'https://www.linkedin.com/sales/home'
RESULTS = '''<a href="/sales/lead/abc,NAME_SEARCH,t1">
    <span data-anonymize="person-name">Jane Doe</span></a>'''


class Capture:
    rows = []
    def __init__(self):
        self.clears = 0
    def clear_page(self):
        self.clears += 1
        self.rows = []


class FakePage:
    def __init__(self, *, goto_outcomes):
        self.url = 'about:blank'
        self.goto_outcomes = list(goto_outcomes)
        self.goto_calls = []
        self.pause_calls = 0

    def goto(self, url, **kwargs):
        self.goto_calls.append((url, kwargs))
        outcome = self.goto_outcomes.pop(0)
        if outcome == 'chooser':
            self.url = CHOOSER
            raise PlaywrightError(f'Page.goto: Navigation to "{url}" is interrupted by another navigation to "{CHOOSER}"')
        if outcome == 'login':
            self.url = 'https://www.linkedin.com/login'
            raise PlaywrightError(f'Page.goto: Navigation to "{url}" is interrupted by another navigation to "https://www.linkedin.com/login"')
        if outcome == 'search':
            self.url = SEARCH
        if outcome == 'unknown':
            self.url = 'https://example.com/evil'
            raise PlaywrightError('Page.goto: Navigation to "x" is interrupted by another navigation to "https://example.com/evil"')
        if outcome == 'timeout':
            raise PlaywrightError('Page.goto: Timeout 60000ms exceeded.')

    def content(self):
        return RESULTS if is_search_page(self.url) else '<title>Choose a contract</title>'

    def wait_for_timeout(self, ms):
        self.pause_calls += 1


def test_contract_chooser_detected_exactly():
    assert is_contract_chooser(CHOOSER)
    assert not is_contract_chooser(HOME)
    assert is_login_page('https://www.linkedin.com/checkpoint/challenge')
    assert is_search_page(SEARCH)
    assert not is_search_page(CHOOSER)
    assert not is_search_page('https://evil.com/sales/search/people')


def test_normal_search_does_not_prompt():
    page = FakePage(goto_outcomes=['search'])
    open_search_with_manual_access(page, SEARCH, Capture(),
                                   prompt=lambda _: pytest.fail('prompted on working search'))
    assert len(page.goto_calls) == 1
    assert page.goto_calls[0][1]['wait_until'] == 'commit'


def test_interrupted_redirect_to_contract_chooser_then_manual_access():
    page = FakePage(goto_outcomes=['chooser', 'search'])
    prompts = []
    def prompt(_):
        prompts.append(True)
        page.url = HOME  # User selects licensed account and lands on homepage.
        return ''
    messages = []
    open_search_with_manual_access(page, SEARCH, Capture(), prompt=prompt,
                                   log=messages.append)
    assert len(prompts) == 1
    assert len(page.goto_calls) == 2
    assert all(call[0] == SEARCH for call in page.goto_calls)
    assert any('contract chooser' in msg.lower() for msg in messages)


def test_manual_access_can_open_results_without_second_navigation():
    page = FakePage(goto_outcomes=['login'])
    def prompt(_):
        page.url = SEARCH  # Sign-in redirected directly back to results.
        return ''
    open_search_with_manual_access(page, SEARCH, Capture(), prompt=prompt)
    assert len(page.goto_calls) == 1


def test_chooser_is_not_ready_even_with_stale_api_rows():
    page = FakePage(goto_outcomes=[])
    page.url = CHOOSER
    cap = Capture()
    cap.rows = [{'Full Name': 'Stale Lead'}]
    assert not search_has_results(page, cap)


def test_repeated_chooser_has_actionable_error_instead_of_stacktrace():
    page = FakePage(goto_outcomes=['chooser', 'chooser'])
    with pytest.raises(RuntimeError, match='Select the correct Sales Navigator account'):
        open_search_with_manual_access(page, SEARCH, Capture(), manual_attempts=1,
                                       prompt=lambda _: '', log=lambda _: None)
    assert len(page.goto_calls) == 2


def test_unrelated_playwright_errors_are_not_swallowed():
    page = FakePage(goto_outcomes=['timeout'])
    with pytest.raises(PlaywrightError, match='Timeout'):
        navigate_linkedin(page, SEARCH)


def test_redirect_off_linkedin_is_rejected():
    page = FakePage(goto_outcomes=['unknown'])
    with pytest.raises(RuntimeError, match='away from LinkedIn'):
        navigate_linkedin(page, SEARCH)
