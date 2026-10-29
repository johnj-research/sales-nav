"""Read-only result-list scrolling and per-page DOM/API record assembly."""
from __future__ import annotations

from sn_core import parse_results

# Each pass scrolls the nearest scrollable ancestor of the lead cards. Sales Nav
# frequently uses a nested scroll panel rather than the document viewport.
SCROLL_RESULTS = r"""() => {
  const leads = Array.from(document.querySelectorAll(
    'a[href*="/sales/lead/"], a[href*="/sales/people/"], [data-scroll-into-view*="salesProfile"]'
  ));
  const scrollables = [];
  for (const lead of leads.slice(0, 8)) {
    for (let el = lead.parentElement; el && el !== document.body; el = el.parentElement) {
      const style = getComputedStyle(el);
      if (el.scrollHeight > el.clientHeight + 12 &&
          /auto|scroll|overlay/.test(style.overflowY)) {
        scrollables.push(el);
      }
    }
  }
  // Choose the inner scrolling list, never the wide overall search panel.
  const target = scrollables.length ? scrollables[0] : document.scrollingElement;
  if (!target) return { moved: false, atBottom: true, y: 0, height: 0 };
  const before = target.scrollTop;
  const step = Math.max(350, Math.floor(target.clientHeight * .72));
  target.scrollTop = Math.min(target.scrollHeight, before + step);
  if (target === document.scrollingElement && target.scrollTop === before) {
    window.scrollBy(0, step);
  }
  const after = target.scrollTop;
  return {
    moved: after > before + 1,
    atBottom: after + target.clientHeight >= target.scrollHeight - 12,
    y: after,
    height: target.scrollHeight
  };
}"""


def merge_page_rows(*groups: list[dict[str, str]]) -> list[dict[str, str]]:
    """Merge repeat card sightings, including cards replaced by virtualization.

    A sales-profile's first identifier component can be shared between a
    browser URL and an API URN with differing search context suffixes.
    """
    merged: dict[str, dict[str, str]] = {}
    for group in groups:
        for row in group:
            profile_id = row.get('Sales Profile ID', '').split(',', 1)[0]
            key = profile_id or row.get('LinkedIn Sales Navigator URL', '')
            if not key or not row.get('Full Name'):
                continue
            if key not in merged:
                merged[key] = dict(row)
            else:
                target = merged[key]
                for field, value in row.items():
                    if value and not target.get(field):
                        target[field] = value
    return list(merged.values())


def gather_search_page(page, api_capture=None, *, pause_ms: int = 700,
                       max_scroll_steps: int = 70,
                       log=print) -> list[dict[str, str]]:
    """Extract all available cards while scrolling the actual results panel.

    Handles virtualization by retaining each unique lead immediately. A quiet
    bottom must persist across several passes to allow slow lazy-loading.
    A bounded pass count prevents infinite scrolling if the page is broken.
    """
    merged: list[dict[str, str]] = []
    quiet_at_bottom = 0
    for step in range(max_scroll_steps + 1):
        dom = parse_results(page.content())
        api = api_capture.rows if api_capture and api_capture.enabled else []
        previous = len(merged)
        merged = merge_page_rows(merged, dom, api)
        if step == max_scroll_steps:
            log(f'  Maximum {max_scroll_steps} scrolls reached; found {len(merged)} people.')
            break
        scroll = page.evaluate(SCROLL_RESULTS)
        # The page may render a new batch asynchronously after the scroll.
        page.wait_for_timeout(max(pause_ms, 350))
        if scroll.get('atBottom') or not scroll.get('moved'):
            quiet_at_bottom = quiet_at_bottom + 1 if len(merged) == previous else 0
        else:
            quiet_at_bottom = 0
        if quiet_at_bottom >= 5:
            # Capture final asynchronous result update before returning.
            merged = merge_page_rows(
                merged, parse_results(page.content()),
                api_capture.rows if api_capture and api_capture.enabled else [],
            )
            break
    if len(merged) <= 4:
        log(f'  Only {len(merged)} profiles found on this page. If Sales Navigator '
            'shows more, the results panel or API layout may need updating.')
    return merged
