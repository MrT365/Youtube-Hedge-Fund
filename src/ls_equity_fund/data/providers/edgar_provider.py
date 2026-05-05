"""EDGAR provider — edgartools fetches; lxml parses (DATA-05/06/07).

Per CLAUDE.md: ``edgartools`` (5.30.x) handles EDGAR's 10 req/sec rate-limit
and the User-Agent header natively. We delegate fetching + filing-list to it.

Per the plan-level decision in 01-06: Form 4 + 13F XML parsing is done with
``lxml`` XPath against the public schemas. ``edgartools``' built-in Form 4
dataclass is version-drifty across releases — going straight to lxml gives
deterministic, version-pinned behavior. The ``parse_form4`` / ``parse_13f``
methods retain a ``try edgartools / except → lxml`` shape so future upgrades
can flip the default without touching callers.

Per CONTEXT D-21: ``sec_user_agent`` lives on ``Secrets`` (loaded from .env),
not ``config.yaml``. The constructor REFUSES to operate without an email
address in the User-Agent — EDGAR returns 403 otherwise (T-01-17 mitigation).

Per PITFALLS.md CP3: every Form 4 transaction code (P/S/A/M/F/G/D) is
distinct and meaningful. The parser persists each row's code as the literal
letter; the schema CHECK constraint at the DB layer (migration 0002) rejects
anything outside the seven-letter set.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date
from pathlib import Path
from typing import Any, cast

import structlog

from ls_equity_fund.data.providers.base import FilingsProvider

log = structlog.get_logger(__name__)

# Form 4 transaction codes per StockTitan + SEC Form 4 spec.
# CP3 binding: every code MUST be parsed. Phase 2 scoring filters on P/S only.
#   P = open-market purchase (true buy signal)
#   S = open-market sale (true sell signal)
#   A = grant/award (compensation, NOT directional)
#   M = exercise of derivative (NOT directional)
#   F = payment of tax-withholding (NOT directional)
#   G = bona fide gift (NOT directional)
#   D = disposition non-open-market (NOT directional)
VALID_TRANSACTION_CODES: frozenset[str] = frozenset({"P", "S", "A", "M", "F", "G", "D"})

# CEO/CFO title detector — hits "Chief Executive Officer", "Chief Financial Officer",
# bare "CEO" / "CFO" (word-boundary anchored), case-insensitive. Doesn't match
# "Director", "VP Engineering", or other non-CEO/CFO officer titles.
CEO_CFO_TITLE_RE = re.compile(r"(?i)(chief\s+(executive|financial)\s+officer|\b(?:CEO|CFO)\b)")


class EdgarProvider(FilingsProvider):
    """EDGAR FilingsProvider — edgartools fetches, lxml parses Form 4 / 13F.

    Init MUST be called with a valid SEC User-Agent (must contain '@'). The
    UA is set globally via ``edgar.set_identity()`` at construction so any
    edgartools call from any thread inherits it. A second instantiation with
    a different UA overrides; this is fine for v1's single-process daily run.
    """

    def __init__(self, *, sec_user_agent: str) -> None:
        if not sec_user_agent or "@" not in sec_user_agent:
            raise ValueError(
                "sec_user_agent must contain a contact email per EDGAR policy "
                "(T-01-17 mitigation — EDGAR returns 403 without identity)"
            )
        self._sec_user_agent = sec_user_agent
        # edgartools sets identity globally — call once at init. Soft-fail on
        # ImportError so unit tests that only exercise lxml parsing still run.
        try:
            from edgar import set_identity

            set_identity(sec_user_agent)
        except ImportError:
            log.warning("edgartools_unavailable_lxml_fallback_only")

    # ---------- FilingsProvider interface ----------

    def fetch_filings(
        self,
        ticker: str,
        forms: list[str],
        since: date | None = None,
        cache_dir: Path | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch filings via edgartools and persist raw bodies under ``cache_dir``.

        Returns metadata rows ready for ``filings_metadata`` insert. Bodies are
        written under ``cache_dir/{ticker}/{accession}.{ext}`` where ext is
        ``.xml`` for Form 4 / 13F and ``.txt`` otherwise. ``content_hash`` is
        SHA-256 of the body file (T-01-19 mitigation — tamper detection).

        Idempotent on repeated calls: existing files are not re-fetched.
        """
        try:
            from edgar import Company
        except ImportError:
            log.error("edgartools_not_installed_cannot_fetch")
            return []

        if cache_dir is None:
            cache_dir = Path("cache/filings")
        ticker_dir = cache_dir / ticker
        ticker_dir.mkdir(parents=True, exist_ok=True)

        results: list[dict[str, Any]] = []
        try:
            company = Company(ticker)
        except Exception as exc:
            log.error("edgar_company_lookup_failed", ticker=ticker, error=str(exc))
            return results

        try:
            filings = company.get_filings(form=forms)
            if since is not None:
                filings = filings.filter(filing_date=f"{since.isoformat()}:")
        except Exception as exc:
            log.error("edgar_filings_fetch_failed", ticker=ticker, error=str(exc))
            return results

        for filing in filings:
            try:
                form_type = str(filing.form)
                accession = str(filing.accession_no).replace("-", "")
                ext = "xml" if form_type in ("4", "13F-HR") else "txt"
                filepath = ticker_dir / f"{accession}.{ext}"
                if not filepath.exists():
                    body = (filing.text() if ext == "txt" else filing.xml()) or ""
                    filepath.write_text(body, encoding="utf-8")
                content_hash = hashlib.sha256(filepath.read_bytes()).hexdigest()
                period = (
                    filing.period_of_report.isoformat()
                    if hasattr(filing.period_of_report, "isoformat")
                    else (str(filing.period_of_report) if filing.period_of_report else None)
                )
                filed_date = (
                    filing.filing_date.isoformat()
                    if hasattr(filing.filing_date, "isoformat")
                    else str(filing.filing_date)
                )
                results.append(
                    {
                        "accession_number": accession,
                        "ticker": ticker,
                        "cik": str(company.cik).zfill(10),
                        "form_type": form_type,
                        "filed_date": filed_date,
                        "period_of_report": period,
                        "filepath": str(filepath),
                        "content_hash": content_hash,
                    }
                )
            except Exception as exc:
                log.warning(
                    "edgar_filing_persist_failed",
                    ticker=ticker,
                    error=str(exc),
                )
                continue
        return results

    def parse_form4(self, accession_number: str, raw_xml_path: Path) -> list[dict[str, Any]]:
        """Parse Form 4 XML; lxml is the canonical path.

        Returns one dict per ``<nonDerivativeTransaction>`` with all spec
        fields. Rows whose ``transactionCode`` is outside the 7-letter
        VALID_TRANSACTION_CODES set are dropped (with a warning) — the
        DB CHECK constraint would reject them anyway.
        """
        try:
            return self._parse_form4_lxml(accession_number, raw_xml_path)
        except Exception as exc:
            log.error(
                "form4_parse_failed",
                accession=accession_number,
                path=str(raw_xml_path),
                error=str(exc),
            )
            return []

    def parse_13f(self, accession_number: str, raw_path: Path) -> list[dict[str, Any]]:
        """Parse 13F INFORMATION TABLE; lxml is the canonical path.

        Returns one dict per ``<infoTable>`` with ticker / shares / value /
        cusip. ``period_end`` and ``filed_date`` are populated by the caller
        (``data/institutional.py``) from ``filings_metadata`` to preserve the
        45-day filing lag (D4 binding).
        """
        try:
            return self._parse_13f_lxml(accession_number, raw_path)
        except Exception as exc:
            log.error(
                "thirteen_f_parse_failed",
                accession=accession_number,
                path=str(raw_path),
                error=str(exc),
            )
            return []

    # ---------- Internal lxml parsers ----------

    def _parse_form4_lxml(self, accession_number: str, raw_xml_path: Path) -> list[dict[str, Any]]:
        from lxml import etree

        tree = etree.parse(str(raw_xml_path))
        root = tree.getroot()

        ticker = (root.findtext(".//issuer/issuerTradingSymbol") or "").strip().upper()

        # Reporting owner relationship (1st reporter; multi-reporter Form 4
        # is uncommon — v1 takes the first).
        owner = root.find(".//reportingOwner")
        if owner is not None:
            insider_name = (owner.findtext(".//rptOwnerName") or "").strip()
            relationship = owner.find(".//reportingOwnerRelationship")
            if relationship is not None:
                is_director = _flag(relationship, "isDirector")
                is_officer = _flag(relationship, "isOfficer")
                is_ten = _flag(relationship, "isTenPercentOwner")
                insider_title = (relationship.findtext("officerTitle") or "").strip()
            else:
                is_director = is_officer = is_ten = 0
                insider_title = ""
        else:
            insider_name = ""
            insider_title = ""
            is_director = is_officer = is_ten = 0

        results: list[dict[str, Any]] = []
        line_no = 0
        for tx in root.findall(".//nonDerivativeTransaction"):
            line_no += 1
            code = (tx.findtext(".//transactionCoding/transactionCode") or "").strip().upper()
            if code not in VALID_TRANSACTION_CODES:
                log.warning(
                    "form4_unknown_transaction_code",
                    accession=accession_number,
                    code=code,
                    line_no=line_no,
                )
                continue
            shares = _float(tx.findtext(".//transactionAmounts/transactionShares/value"))
            price = _float(tx.findtext(".//transactionAmounts/transactionPricePerShare/value"))
            tx_date = (tx.findtext(".//transactionDate/value") or "").strip()
            ad_code = (
                (tx.findtext(".//transactionAmounts/transactionAcquiredDisposedCode/value") or "")
                .strip()
                .upper()
            )
            tx_type = "ACQUIRED" if ad_code == "A" else "DISPOSED" if ad_code == "D" else ""
            ownership_type = (
                tx.findtext(".//ownershipNature/directOrIndirectOwnership/value") or ""
            ).strip()
            total_value = shares * price if (shares is not None and price is not None) else None

            results.append(
                {
                    "accession_number": accession_number,
                    "line_no": line_no,
                    "ticker": ticker,
                    "insider_name": insider_name,
                    "insider_title": insider_title,
                    "is_officer": is_officer,
                    "is_director": is_director,
                    "is_ten_percent_owner": is_ten,
                    "transaction_code": code,
                    "transaction_type": tx_type,
                    "shares": shares,
                    "price_per_share": price,
                    "total_value": total_value,
                    "transaction_date": tx_date,
                    "filed_date": "",  # caller populates from filings_metadata
                    "ownership_type": ownership_type,
                }
            )
        return results

    def _parse_13f_lxml(self, accession_number: str, raw_path: Path) -> list[dict[str, Any]]:
        from lxml import etree

        tree = etree.parse(str(raw_path))
        root = tree.getroot()

        # 13F INFORMATION TABLE namespace (post-2013 schema). Older filings
        # may omit the namespace — fall back to namespaceless XPath.
        ns = {"n": "http://www.sec.gov/edgar/document/thirteenf/informationtable"}
        info_tables = root.findall(".//n:infoTable", namespaces=ns)
        if not info_tables:
            info_tables = root.findall(".//infoTable")

        results: list[dict[str, Any]] = []
        for info in info_tables:
            # Try namespaced first, then namespaceless.
            ticker_text = (
                _xpath_text(info, "n:nameOfIssuer", ns)
                or _xpath_text(info, "nameOfIssuer", None)
                or ""
            )
            shares_text = _xpath_text(info, "n:shrsOrPrnAmt/n:sshPrnamt", ns) or _xpath_text(
                info, "shrsOrPrnAmt/sshPrnamt", None
            )
            value_text = _xpath_text(info, "n:value", ns) or _xpath_text(info, "value", None)
            cusip_text = _xpath_text(info, "n:cusip", ns) or _xpath_text(info, "cusip", None)

            results.append(
                {
                    "ticker": (ticker_text or "").upper().strip(),
                    "shares": _float(shares_text),
                    "value_usd": _float(value_text),
                    "cusip": (cusip_text or "").strip(),
                }
            )
        return results


# ---------- Helpers ----------


def _flag(elem: Any, tag: str) -> int:
    if elem is None:
        return 0
    v = (elem.findtext(tag) or "").strip()
    return 1 if v in {"1", "true", "True", "TRUE"} else 0


def _float(value: Any) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(str(value).replace(",", ""))
    except (ValueError, TypeError):
        return None


def _xpath_text(elem: Any, path: str, ns: dict[str, str] | None) -> str | None:
    """findtext that tolerates None namespace map."""
    if elem is None:
        return None
    if ns:
        return cast("str | None", elem.findtext(path, namespaces=ns))
    return cast("str | None", elem.findtext(path))


__all__ = ["CEO_CFO_TITLE_RE", "VALID_TRANSACTION_CODES", "EdgarProvider"]
