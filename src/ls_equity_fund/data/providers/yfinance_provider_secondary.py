"""yfinance secondary feeds — short interest, estimates, earnings calendar.

Fills YFinanceProvider stubs for ShortInterestProvider, EstimatesProvider.
Per CLAUDE.md: yfinance 0.2.65 + curl_cffi session is the only supported
transport.

Per PITFALLS.md D6: yfinance earnings dates are noisy (timezone shifts,
dropped dates). This impl records what yfinance reports; downstream
earnings-blackout (Phase 5) applies a 5-day buffer to absorb noise.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

log = structlog.get_logger(__name__)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    reraise=True,
)
def get_short_interest_impl(
    session: Any, ticker: str, asof: date
) -> dict[str, Any] | None:
    """yfinance Ticker.info short fields.

    Returns None when the upstream `info` dict is empty/unavailable.
    """
    import yfinance as yf

    yt = yf.Ticker(ticker, session=session) if session else yf.Ticker(ticker)
    info = yt.info or {}
    if not info:
        return None
    return {
        "shares_short": _f(info.get("sharesShort")),
        "short_ratio": _f(info.get("shortRatio")),
        "short_percent_of_float": _f(info.get("shortPercentOfFloat")),
    }


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    reraise=True,
)
def get_estimates_impl(
    session: Any, ticker: str, asof: date
) -> dict[str, Any] | None:
    """yfinance analyst targets + earnings/revenue estimates.

    Defensive: each attribute is read inside a try/except so a single
    yfinance shape change does not zero out the entire snapshot.
    """
    import yfinance as yf

    yt = yf.Ticker(ticker, session=session) if session else yf.Ticker(ticker)
    info = yt.info or {}
    if not info:
        return None

    target = None
    try:
        targets = yt.analyst_price_targets
        if isinstance(targets, dict):
            target = _f(targets.get("mean") or targets.get("targetMeanPrice"))
        elif targets is not None and not getattr(targets, "empty", True):
            target = _f(targets.iloc[0].get("targetMeanPrice"))
    except Exception as e:
        log.warning("yf_price_targets_failed", ticker=ticker, error=str(e))
        target = _f(info.get("targetMeanPrice"))

    eps_fy1 = eps_fy2 = rev_fy1 = rev_fy2 = None
    try:
        eps_df = yt.earnings_estimate
        if eps_df is not None and not getattr(eps_df, "empty", True):
            for label, target_var in (("0y", "eps_fy1"), ("+1y", "eps_fy2")):
                if label in eps_df.index:
                    val = _f(eps_df.loc[label].get("avg"))
                    if target_var == "eps_fy1":
                        eps_fy1 = val
                    else:
                        eps_fy2 = val
    except Exception as e:
        log.warning("yf_earnings_estimate_failed", ticker=ticker, error=str(e))

    try:
        rev_df = yt.revenue_estimate
        if rev_df is not None and not getattr(rev_df, "empty", True):
            for label, target_var in (("0y", "rev_fy1"), ("+1y", "rev_fy2")):
                if label in rev_df.index:
                    val = _f(rev_df.loc[label].get("avg"))
                    if target_var == "rev_fy1":
                        rev_fy1 = val
                    else:
                        rev_fy2 = val
    except Exception as e:
        log.warning("yf_revenue_estimate_failed", ticker=ticker, error=str(e))

    return {
        "eps_fy1": eps_fy1,
        "eps_fy2": eps_fy2,
        "rev_fy1": rev_fy1,
        "rev_fy2": rev_fy2,
        "target_price": target,
        "n_analysts": _i(info.get("numberOfAnalystOpinions")),
    }


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    reraise=True,
)
def get_next_earnings_dates_impl(
    session: Any, ticker: str, lookahead_days: int = 30
) -> list[dict[str, Any]]:
    """Upcoming earnings within ``lookahead_days``.

    Empty list = no scheduled earnings inside the window. Each entry is
    ``{expected_date: 'YYYY-MM-DD', time_of_day: 'BMO'|'AMC'|'MID'|'',
       fiscal_period: ''}``. ``fiscal_period`` is left blank — yfinance does
    not expose this reliably across versions; downstream factors use the
    date alone (PITFALLS D6 — 5-day buffer absorbs noise).
    """
    import yfinance as yf

    yt = yf.Ticker(ticker, session=session) if session else yf.Ticker(ticker)
    try:
        df = yt.earnings_dates
    except Exception as e:
        log.warning("yf_earnings_dates_failed", ticker=ticker, error=str(e))
        return []
    if df is None or getattr(df, "empty", True):
        return []

    today = datetime.utcnow().date()
    cutoff = today + timedelta(days=lookahead_days)
    out: list[dict[str, Any]] = []
    for idx, _row in df.iterrows():
        try:
            ts = idx.to_pydatetime() if hasattr(idx, "to_pydatetime") else idx
            ev_date = ts.date() if hasattr(ts, "date") else ts
        except Exception:
            continue
        if ev_date < today or ev_date > cutoff:
            continue
        time_of_day = ""
        try:
            hour = ts.hour
            if hour < 12:
                time_of_day = "BMO"  # before market open
            elif hour >= 16:
                time_of_day = "AMC"  # after market close
            else:
                time_of_day = "MID"
        except Exception:
            pass
        out.append(
            {
                "expected_date": ev_date.isoformat(),
                "time_of_day": time_of_day,
                "fiscal_period": "",
            }
        )
    return out


def _f(v: Any) -> float | None:
    """Coerce to float, returning None for None/non-numeric/NaN values."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    # NaN check without importing math (NaN != NaN).
    return None if f != f else f


def _i(v: Any) -> int | None:
    """Coerce to int, returning None for None/non-numeric values."""
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


__all__ = [
    "get_estimates_impl",
    "get_next_earnings_dates_impl",
    "get_short_interest_impl",
]
