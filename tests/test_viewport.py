"""Offline contracts for the viewport an owned tab is laid out in. No browser is opened.

A run used to pin every tab it owned to 1120x780 whatever the window was, so a page filled one
corner of the window it had been opened in and the rest of the window showed the browser's own
background. These tests pin the decision instead: a browser on screen keeps the size of its window, a
headless browser -- which has no window to take a size from -- gets one a page fits in, and
``JEV_VIEWPORT`` overrides both.

Run: ``uv run pytest tests/test_viewport.py``
"""

import pytest

from jev_ultrafast import browser


def without_pinned_size(monkeypatch):
    monkeypatch.delenv(browser.VIEWPORT_ENV, raising=False)


@pytest.mark.parametrize("value", ["1280x800", "1280X800", " 1280 x 800 ", "1280*800"])
def test_jev_viewport_pins_the_size_it_names(monkeypatch, value):
    monkeypatch.setenv(browser.VIEWPORT_ENV, value)
    assert browser.pinned_viewport() == (1280, 800)


def test_nothing_pinned_leaves_the_size_to_the_window(monkeypatch):
    without_pinned_size(monkeypatch)
    assert browser.pinned_viewport() is None


@pytest.mark.parametrize("value", ["1280", "1280x", "x800", "1280x800x2", "big", "1280 by 800", "1280x-800", "²x³"])
def test_a_jev_viewport_that_is_not_a_size_says_so(monkeypatch, value):
    monkeypatch.setenv(browser.VIEWPORT_ENV, value)
    with pytest.raises(ValueError, match="WIDTHxHEIGHT"):
        browser.pinned_viewport()


@pytest.mark.parametrize("value", ["0x800", "1280x0", "0x0"])
def test_a_jev_viewport_that_is_not_positive_says_so(monkeypatch, value):
    monkeypatch.setenv(browser.VIEWPORT_ENV, value)
    with pytest.raises(ValueError, match="positive"):
        browser.pinned_viewport()


def test_a_browser_that_says_it_is_headless_is_one():
    agent = {"userAgent": "Mozilla/5.0 HeadlessChrome/155.0.0.0 Safari/537.36"}
    assert browser.names_itself_headless(agent) is True


def test_a_browser_on_screen_does_not_say_it():
    agent = {"userAgent": "Mozilla/5.0 (Macintosh) Chrome/155.0.0.0 Safari/537.36"}
    assert browser.names_itself_headless(agent) is False
    assert browser.names_itself_headless(None) is False


def test_the_display_a_headless_browser_starts_with_gives_it_away():
    # --user-agent replaces the product string, so a spoofed headless launch says nothing there; the
    # 800x600 virtual screen it reports is what is left.
    assert browser.shows_headless_display([800, 600]) is True
    assert browser.shows_headless_display([1920, 1080]) is False
    assert browser.shows_headless_display(None) is False


def headless_probe(monkeypatch, version, display):
    """A Browser whose browser-level answer and page answer are the two under test."""
    instance = browser.Browser.__new__(browser.Browser)
    monkeypatch.setattr(browser, "cdp", lambda method, **params: version)
    instance.evaluate = lambda expression: display
    return instance


def test_a_browser_that_hides_the_word_is_still_headless_by_its_display(monkeypatch):
    agent = {"userAgent": "Mozilla/5.0 (Macintosh) Chrome/155.0.0.0 Safari/537.36"}
    assert headless_probe(monkeypatch, agent, [800, 600]).headless_browser() is True


def test_a_browser_on_a_real_screen_is_not_headless(monkeypatch):
    agent = {"userAgent": "Mozilla/5.0 (Macintosh) Chrome/155.0.0.0 Safari/537.36"}
    assert headless_probe(monkeypatch, agent, [1920, 1080]).headless_browser() is False


def test_a_browser_that_cannot_say_keeps_the_size_it_has(monkeypatch):
    def cdp(method, **params):
        raise RuntimeError("daemon is gone")

    instance = browser.Browser.__new__(browser.Browser)
    monkeypatch.setattr(browser, "cdp", cdp)
    instance.evaluate = lambda expression: pytest.fail("a browser that cannot say is not asked twice")
    assert instance.headless_browser() is False


def recording_cdp(monkeypatch):
    """Every CDP call the run makes, in order."""
    calls = []

    def cdp(method, **params):
        calls.append((method, params))
        return {}

    monkeypatch.setattr(browser, "cdp", cdp)
    return calls


def browser_that_owns(owned, viewport):
    """A Browser that opened nothing: only the viewport decision is under test."""
    instance = browser.Browser.__new__(browser.Browser)
    instance.owned = owned
    instance.viewport = viewport
    instance.session = "session"
    return instance


def test_an_owned_tab_is_pinned_to_the_runs_viewport(monkeypatch):
    calls = recording_cdp(monkeypatch)
    browser_that_owns(owned=True, viewport=(1280, 800)).pin_viewport()
    assert calls == [
        (
            "Emulation.setDeviceMetricsOverride",
            {"session_id": "session", "width": 1280, "height": 800, "deviceScaleFactor": 1, "mobile": False},
        )
    ]


def test_an_owned_tab_in_a_window_keeps_that_window_s_size(monkeypatch):
    # The whole page belongs in the window someone opened, so nothing is emulated over it.
    calls = recording_cdp(monkeypatch)
    browser_that_owns(owned=True, viewport=None).pin_viewport()
    assert calls == [("Emulation.clearDeviceMetricsOverride", {"session_id": "session"})]


def test_a_tab_the_run_does_not_own_keeps_its_own_size(monkeypatch):
    # Someone's own tab, and the window a launch put on screen: an earlier owned run's emulated
    # viewport is cleared off it rather than replaced, pinned size or not.
    calls = recording_cdp(monkeypatch)
    browser_that_owns(owned=False, viewport=(1280, 800)).pin_viewport()
    assert calls == [("Emulation.clearDeviceMetricsOverride", {"session_id": "session"})]


def test_a_viewport_that_cannot_be_read_fails_before_any_tab_is_touched(monkeypatch):
    # A bad size used to be read after the run had already created or attached to a tab, which left
    # that tab -- and any emulated viewport on it -- behind.
    calls = recording_cdp(monkeypatch)
    monkeypatch.setattr(browser, "ensure_daemon", lambda: None)
    monkeypatch.setenv(browser.VIEWPORT_ENV, "big")
    with pytest.raises(ValueError, match="WIDTHxHEIGHT"):
        browser.Browser("https://example.test/")
    assert calls == []
