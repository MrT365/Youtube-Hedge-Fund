"""Federal Reserve macro-calendar scraper (DATA-11).

Implements MacroProvider against fed.gov HTML. Anti-recommendation rule
(CLAUDE.md): NEVER hardcode FOMC dates. The Fed publishes the schedule
in HTML at FOMC_CALENDAR_URL; this scraper parses the year-grouped tables.

Plan-level decisions (see 01-08-PLAN.md):
  - bs4 over edgartools/Selenium — page is static HTML; no JS rendering needed.
  - On HTTP / parse failure: raise NetworkError. Caller (refresh_macro_calendar)
    handles fallback to cached rows + staleness warning.
  - event_id = sha1("FOMC|<event_date_et>")[:16] — deterministic for UPSERT.
  - meeting "decision day" = LAST day of the published day-range
    (e.g. "16-17" → day 17). Multi-month entries ("April/May") use the
    second month name (the actual decision month).
"""
from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta
from typing import Any

import structlog

log = structlog.get_logger(__name__)

FOMC_CALENDAR_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"

# User-Agent: identify ourselves with a contact (mirrors EDGAR convention).
_DEFAULT_USER_AGENT = (
    "Meridian Capital Partners macro-calendar-bot (one operator; weekly fetch)"
)

_MONTH_MAP: dict[str, int] = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}


class NetworkError(RuntimeError):
    """Raised when fetch_macro_events fails to retrieve / parse upstream."""


class FedScraperProvider:
    """MacroProvider impl using BeautifulSoup4 against fed.gov HTML."""

    def __init__(
        self,
        *,
        session: Any = None,
        user_agent: str = _DEFAULT_USER_AGENT,
    ) -> None:
        if session is None:
            try:
                import requests

                session = requests.Session()
            except ImportError:
                session = None
        if session is not None:
            try:
                session.headers.update({"User-Agent": user_agent})
            except Exception:
                # Some test mocks don't support headers attribute — non-fatal.
                pass
        self.session = session

    def fetch_macro_events(
        self,
        lookahead_days: int = 365,
        *,
        url: str = FOMC_CALENDAR_URL,
        html: str | None = None,
    ) -> list[dict[str, Any]]:
        """Scrape FOMC meeting dates within `lookahead_days` of today.

        Args:
            lookahead_days: ignore events further out than this.
            url: override URL (production = FOMC_CALENDAR_URL).
            html: pre-fetched HTML (for tests).

        Raises:
            NetworkError: on HTTP failure or parse failure.
        """
        if html is None:
            try:
                if self.session is None:
                    raise NetworkError(
                        "FOMC calendar fetch failed: no HTTP session available"
                    )
                resp = self.session.get(url, timeout=30)
                if resp.status_code != 200:
                    raise NetworkError(
                        f"FOMC calendar fetch failed: status={resp.status_code}"
                    )
                html = resp.text
            except NetworkError:
                raise
            except Exception as e:
                raise NetworkError(f"FOMC calendar fetch failed: {e}") from e

        try:
            events = self._parse(html)
        except Exception as e:
            raise NetworkError(f"FOMC calendar parse failed: {e}") from e

        today = date.today()
        cutoff = today + timedelta(days=lookahead_days)
        filtered = [
            e for e in events if today <= _to_date(e["event_date_et"]) <= cutoff
        ]
        return sorted(filtered, key=lambda e: e["event_date_et"])

    @staticmethod
    def _parse(html: str) -> list[dict[str, Any]]:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")

        events: list[dict[str, Any]] = []
        # Each year is in its own panel-default block; heading text "2026 FOMC Meetings"
        for panel in soup.select(".panel-default"):
            heading = panel.select_one(".panel-heading")
            if heading is None:
                continue
            heading_text = heading.get_text(strip=True)
            year_match = re.search(r"\b(20\d{2})\b", heading_text)
            if not year_match:
                continue
            year = int(year_match.group(1))

            for row in panel.select("tbody tr"):
                cells = row.find_all("td")
                if len(cells) < 2:
                    continue
                month_label = cells[0].get_text(strip=True)
                day_range = cells[1].get_text(strip=True)
                event_date = _resolve_meeting_date(year, month_label, day_range)
                if event_date is None:
                    continue
                event_date_et = event_date.isoformat()
                event_date_local = _to_local(event_date)
                event_id = hashlib.sha1(
                    f"FOMC|{event_date_et}".encode()
                ).hexdigest()[:16]
                events.append({
                    "event_id": event_id,
                    "event_type": "FOMC",
                    "event_date_et": event_date_et,
                    "event_date_local": event_date_local,
                    "description": f"FOMC Meeting ({month_label})",
                    "source": "federalreserve.gov",
                })
        return events


# ---------- helpers ----------

def _resolve_meeting_date(
    year: int, month_label: str, day_range: str
) -> date | None:
    """Pick the SECOND day of the meeting (the "decision day") as event_date_et.

    Multi-month labels like "April/May" use the SECOND token (the decision
    month). Day-range like "16-17" picks the last day. Returns None if the
    label / range can't be parsed (defensive — partial Fed page changes
    shouldn't crash the whole parser).
    """
    parts = [p.strip().lower() for p in month_label.split("/")]
    month_name = parts[-1]  # last is meeting end / decision month
    month = _MONTH_MAP.get(month_name)
    if month is None:
        return None

    days = re.findall(r"\d+", day_range)
    if not days:
        return None
    day = int(days[-1])

    try:
        return date(year, month, day)
    except ValueError:
        return None


def _to_date(s: str) -> date:
    return datetime.fromisoformat(s).date()


def _to_local(d: date) -> str:
    """Same wall-clock day translated to operator's local TZ — for FOMC blackout.

    FOMC announcements are mid-afternoon ET; for an operator-local CALENDAR
    DAY view (which is what the blackout veto needs), the same date is
    correct in any US-or-Western-EU timezone. Non-US operators get a
    follow-up plan; v1 ships ET-equivalent. Recorded as a separate column
    so a future timezone-aware refactor can update without schema change.
    """
    return d.isoformat()


__all__ = ["FOMC_CALENDAR_URL", "FedScraperProvider", "NetworkError"]
