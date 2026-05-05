"""FedScraperProvider unit tests using fixture HTML — no network."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from freezegun import freeze_time

from ls_equity_fund.data.providers.fred_provider import (
    FOMC_CALENDAR_URL,
    FedScraperProvider,
    NetworkError,
)

FIXTURE = Path(__file__).parent.parent.parent / "fixtures" / "fomccalendars_fixture.html"

# Freeze well before the 2026 meetings so the today<=event<=cutoff filter
# admits everything in the 2026 fixture for lookahead_days=1000.
_FROZEN_NOW = "2025-12-15"


@pytest.fixture
def fixture_html() -> str:
    return FIXTURE.read_text()


def test_fixture_exists() -> None:
    assert FIXTURE.exists(), "tests/fixtures/fomccalendars_fixture.html missing"


def test_fomc_calendar_url_constant_is_fed_dot_gov() -> None:
    """Anti-rec guard: live source must be federalreserve.gov, not hardcoded dates."""
    assert FOMC_CALENDAR_URL == ("https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm")


@freeze_time(_FROZEN_NOW)
def test_parse_extracts_2026_meetings(fixture_html: str) -> None:
    provider = FedScraperProvider(session=None)
    events = provider.fetch_macro_events(lookahead_days=1000, html=fixture_html)
    events_2026 = [e for e in events if e["event_date_et"].startswith("2026")]
    assert len(events_2026) == 8, f"expected 8 meetings in 2026 fixture, got {len(events_2026)}"


@freeze_time(_FROZEN_NOW)
def test_parse_extracts_2027_meetings(fixture_html: str) -> None:
    provider = FedScraperProvider(session=None)
    events = provider.fetch_macro_events(lookahead_days=1000, html=fixture_html)
    events_2027 = [e for e in events if e["event_date_et"].startswith("2027")]
    assert len(events_2027) == 2


@freeze_time(_FROZEN_NOW)
def test_meeting_date_is_decision_day(fixture_html: str) -> None:
    """Pick the LAST day of the day-range (the announcement / decision day)."""
    provider = FedScraperProvider(session=None)
    events = provider.fetch_macro_events(lookahead_days=1000, html=fixture_html)
    march = next(
        e
        for e in events
        if e["description"] == "FOMC Meeting (March)" and e["event_date_et"].startswith("2026")
    )
    assert march["event_date_et"] == "2026-03-18"  # day-range "17-18" → 18


@freeze_time(_FROZEN_NOW)
def test_april_may_resolves_to_may_decision_day(fixture_html: str) -> None:
    """Multi-month label uses second month + last day of range."""
    provider = FedScraperProvider(session=None)
    events = provider.fetch_macro_events(lookahead_days=1000, html=fixture_html)
    apr_may = next(e for e in events if "April/May" in e["description"])
    # parts[-1]='may' → month=5; days[-1]=29 → day=29
    assert apr_may["event_date_et"] == "2026-05-29"


@freeze_time(_FROZEN_NOW)
def test_event_id_is_deterministic(fixture_html: str) -> None:
    provider = FedScraperProvider(session=None)
    events_a = provider.fetch_macro_events(lookahead_days=1000, html=fixture_html)
    events_b = provider.fetch_macro_events(lookahead_days=1000, html=fixture_html)
    assert {e["event_id"] for e in events_a} == {e["event_id"] for e in events_b}


@freeze_time(_FROZEN_NOW)
def test_event_id_is_short_sha1(fixture_html: str) -> None:
    """event_id contract: 16-char hex (sha1 truncated)."""
    provider = FedScraperProvider(session=None)
    events = provider.fetch_macro_events(lookahead_days=1000, html=fixture_html)
    for e in events:
        assert len(e["event_id"]) == 16
        int(e["event_id"], 16)  # parses as hex


@freeze_time(_FROZEN_NOW)
def test_events_are_sorted_ascending(fixture_html: str) -> None:
    provider = FedScraperProvider(session=None)
    events = provider.fetch_macro_events(lookahead_days=1000, html=fixture_html)
    dates = [e["event_date_et"] for e in events]
    assert dates == sorted(dates)


@freeze_time(_FROZEN_NOW)
def test_events_have_required_fields(fixture_html: str) -> None:
    provider = FedScraperProvider(session=None)
    events = provider.fetch_macro_events(lookahead_days=1000, html=fixture_html)
    required = {
        "event_id",
        "event_type",
        "event_date_et",
        "event_date_local",
        "description",
        "source",
    }
    for e in events:
        assert required.issubset(e.keys()), f"missing fields in {e}"
        assert e["event_type"] == "FOMC"
        assert e["source"] == "federalreserve.gov"


@freeze_time("2026-06-01")
def test_lookahead_days_filter(fixture_html: str) -> None:
    """Events beyond lookahead are excluded."""
    provider = FedScraperProvider(session=None)
    # 30 days from 2026-06-01: only the June 16-17 meeting (→ 2026-06-17) qualifies
    events = provider.fetch_macro_events(lookahead_days=30, html=fixture_html)
    for e in events:
        d = datetime.fromisoformat(e["event_date_et"]).date()
        assert (d - date(2026, 6, 1)).days <= 30
        assert d >= date(2026, 6, 1)
    assert len(events) == 1
    assert events[0]["event_date_et"] == "2026-06-17"


@freeze_time(_FROZEN_NOW)
def test_past_events_filtered_out(fixture_html: str) -> None:
    """Events before today are excluded."""
    # Freeze AFTER all 2026 fixtures — only 2027 events should survive.
    with freeze_time("2026-12-31"):
        provider = FedScraperProvider(session=None)
        events = provider.fetch_macro_events(lookahead_days=400, html=fixture_html)
        for e in events:
            assert e["event_date_et"] >= "2026-12-31"


def test_network_failure_raises_network_error() -> None:
    fake_session = MagicMock()
    fake_session.get.side_effect = ConnectionError("DNS down")
    provider = FedScraperProvider(session=fake_session)
    with pytest.raises(NetworkError):
        provider.fetch_macro_events()


def test_404_raises_network_error() -> None:
    fake_session = MagicMock()
    resp = MagicMock()
    resp.status_code = 404
    fake_session.get.return_value = resp
    provider = FedScraperProvider(session=fake_session)
    with pytest.raises(NetworkError):
        provider.fetch_macro_events()


def test_500_raises_network_error() -> None:
    fake_session = MagicMock()
    resp = MagicMock()
    resp.status_code = 500
    fake_session.get.return_value = resp
    provider = FedScraperProvider(session=fake_session)
    with pytest.raises(NetworkError):
        provider.fetch_macro_events()


def test_no_session_no_html_raises_network_error() -> None:
    """Provider with no session and no pre-fetched HTML must raise — never silently produce []."""
    provider = FedScraperProvider(session=None)
    # Force session=None even after constructor's import attempt
    provider.session = None
    with pytest.raises(NetworkError):
        provider.fetch_macro_events()


def test_malformed_html_raises_network_error() -> None:
    """Catastrophic parse failure (non-string passed) wraps as NetworkError."""
    provider = FedScraperProvider(session=None)
    # Pass a non-string-decodable garbage object — bs4 fails internally,
    # _parse re-raises, fetch_macro_events wraps as NetworkError.
    with pytest.raises(NetworkError):
        provider.fetch_macro_events(html=object())  # type: ignore[arg-type]


def test_user_agent_set_on_session() -> None:
    """Polite scraping: User-Agent header includes our identifier."""
    fake_session = MagicMock()
    # MagicMock's `headers.update` is itself a MagicMock — record the call
    # without trying to mutate a plain dict.
    provider = FedScraperProvider(session=fake_session)
    assert provider.session is fake_session
    fake_session.headers.update.assert_called_once()
    (headers_arg,) = fake_session.headers.update.call_args.args
    assert "User-Agent" in headers_arg
    assert "Meridian Capital Partners" in headers_arg["User-Agent"]


def test_no_hardcoded_dates_in_source() -> None:
    """ANTI-RECOMMENDATION GUARD (CLAUDE.md): no hardcoded FOMC dates in source.

    Scans fred_provider.py for date-like literals (YYYY-MM-DD) and explicit
    year strings. The only acceptable date-like references are inline
    docstring examples or test fixtures — but the SOURCE file must contain
    none.
    """
    import re as _re

    src = Path(__file__).parent.parent.parent.parent / (
        "src/ls_equity_fund/data/providers/fred_provider.py"
    )
    content = src.read_text()
    # YYYY-MM-DD pattern
    iso_dates = _re.findall(r"\b(20\d{2}-\d{2}-\d{2})\b", content)
    assert iso_dates == [], (
        f"hardcoded ISO dates found in fred_provider.py: {iso_dates} — "
        "FOMC dates must come from the live fed.gov scrape, never from source."
    )
    # Explicit single-quoted year strings like '2025' / '2026' / '2027'
    quoted_years = _re.findall(r"['\"]20[2-9]\d['\"]", content)
    assert quoted_years == [], f"hardcoded year string literals found: {quoted_years}"
    # Month names that would indicate hardcoded calendars
    for month in [
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ]:
        # Allow month references in docstring/comments — only flag if
        # combined with a numeric date pattern that looks like a calendar entry.
        # Stricter: confirm no direct "Month DD, YYYY" or "Month DD-DD" assignment.
        pattern = rf"['\"]?{month}\s+\d+(?:[-,]\s*\d+)?(?:,\s*20\d{{2}})?['\"]?"
        # The list literal `_MONTH_MAP` mentions month names as DICT KEYS only —
        # those are short lowercase strings without surrounding numbers, so the
        # pattern above (which requires a digit after the month) will not match
        # them. That keeps this guard tight.
        for match in _re.finditer(pattern, content):
            text = match.group(0)
            # Anything that pairs a month with a number is a hardcoded date.
            if _re.search(r"\d", text):
                pytest.fail(f"hardcoded calendar entry found in fred_provider.py: {text!r}")
