"""Offline contracts for the tabs a launch leaves behind. No browser is opened.

A launch used to show two tabs: the browser's own startup tab plus the one the run drives. These
tests pin the two halves of the fix without a browser -- which tab is recognised as the startup tab,
and which blank tabs may be closed once a page is up.

Run: ``uv run pytest tests/test_browser_tabs.py``
"""

import pytest

from jev_ultrafast import browser


def targets(*pages, kinds=None):
    """The ``Target.getTargets`` answer for a browser showing exactly these pages."""
    infos = [
        {"targetId": target, "type": "page", "url": url}
        for target, url in pages
    ]
    if kinds:
        infos += [{"targetId": target, "type": kind, "url": url} for target, kind, url in kinds]
    return {"targetInfos": infos}


def cdp_stub(monkeypatch, answers, calls=None):
    """Serve prepared answers in order, recording every call."""

    def cdp(method, **params):
        if calls is not None:
            calls.append((method, params))
        if method == "Target.getTargets":
            return answers[-1] if len(answers) == 1 else answers.pop(0)
        return {}

    monkeypatch.setattr(browser, "cdp", cdp)
    return calls if calls is not None else []


@pytest.mark.parametrize(
    "url",
    ["about:blank", "about:blank#x", "chrome://newtab/", "chrome://newtab", "about:newtab"],
)
def test_a_tab_with_nothing_on_it_is_blank_whatever_the_browser_calls_it(url):
    assert browser.blank_page(url) is True


@pytest.mark.parametrize("url", ["", None, "https://example.test/", "about:srcdoc"])
def test_a_tab_showing_a_page_is_not_blank(url):
    assert browser.blank_page(url) is False


def test_the_single_blank_tab_a_launch_opens_is_the_tab_to_drive(monkeypatch):
    cdp_stub(monkeypatch, [targets(("startup", "about:blank"))])
    assert browser.startup_target() == "startup"


def test_a_browser_showing_a_page_is_left_alone(monkeypatch):
    # Nothing to adopt: taking this tab would navigate away from what its owner is reading.
    cdp_stub(monkeypatch, [targets(("theirs", "https://example.test/"), ("mine", "about:blank"))])
    assert browser.startup_target() is None


def test_a_second_blank_tab_means_the_startup_tab_is_already_spoken_for(monkeypatch):
    # The connection layer has created the run's own tab, so the startup tab is a spare, not a home.
    cdp_stub(monkeypatch, [targets(("startup", "about:blank"), ("driven", "about:blank"))])
    assert browser.startup_target() is None


def test_a_browser_that_shows_nothing_at_all_is_waited_for_then_given_a_tab(monkeypatch):
    cdp_stub(monkeypatch, [targets()])
    monkeypatch.setattr(browser.time, "sleep", lambda seconds: None)
    assert browser.startup_target(deadline=0.0) is None


def test_the_startup_tab_is_used_once_it_appears(monkeypatch):
    cdp_stub(monkeypatch, [targets(), targets(("startup", "about:blank"))])
    monkeypatch.setattr(browser.time, "sleep", lambda seconds: None)
    assert browser.startup_target() == "startup"


def test_the_blank_spare_is_closed_once_the_driven_tab_shows_a_page(monkeypatch):
    calls = []
    cdp_stub(
        monkeypatch,
        [targets(("startup", "about:blank"), ("driven", "file:///page.html"))],
        calls,
    )
    browser.close_spare_blank_tabs("driven")
    assert [params["targetId"] for method, params in calls if method == "Target.closeTarget"] == ["startup"]


def test_a_page_the_run_does_not_drive_is_never_closed(monkeypatch):
    # The browser may hold real pages, including someone else's: only blank spares are fair game.
    calls = []
    cdp_stub(
        monkeypatch,
        [targets(("theirs", "https://example.test/"), ("startup", "about:blank"), ("driven", "file:///page.html"))],
        calls,
    )
    browser.close_spare_blank_tabs("driven")
    assert [params["targetId"] for method, params in calls if method == "Target.closeTarget"] == ["startup"]


def test_nothing_is_closed_when_blank_tabs_are_all_the_browser_has(monkeypatch):
    # A driven tab can still be blank: closing the spare would then close the browser.
    calls = []
    cdp_stub(monkeypatch, [targets(("startup", "about:blank"), ("driven", "about:blank"))], calls)
    browser.close_spare_blank_tabs("driven")
    assert [c for c in calls if c[0] == "Target.closeTarget"] == []


def test_a_browser_on_its_own_new_tab_page_keeps_that_tab(monkeypatch):
    calls = []
    cdp_stub(monkeypatch, [targets(("only", "chrome://newtab/"))], calls)
    browser.close_spare_blank_tabs("driven")
    assert [c for c in calls if c[0] == "Target.closeTarget"] == []


def test_a_tab_that_is_already_gone_does_not_stop_the_other_spares(monkeypatch):
    calls = []
    answers = [targets(("gone", "about:blank"), ("also", "about:blank"), ("driven", "file:///page.html"))]

    def cdp(method, **params):
        calls.append((method, params))
        if method == "Target.getTargets":
            return answers[0]
        if method == "Target.closeTarget" and params["targetId"] == "gone":
            raise RuntimeError("No target with given id found")
        return {}

    monkeypatch.setattr(browser, "cdp", cdp)
    browser.close_spare_blank_tabs("driven")
    closed = [params["targetId"] for method, params in calls if method == "Target.closeTarget"]
    assert closed == ["gone", "also"]


def test_a_browser_that_cannot_be_read_is_left_exactly_as_it_is(monkeypatch):
    def cdp(method, **params):
        raise RuntimeError("daemon is gone")

    monkeypatch.setattr(browser, "cdp", cdp)
    browser.close_spare_blank_tabs("driven")  # no raise: cleanup never breaks the run it follows
