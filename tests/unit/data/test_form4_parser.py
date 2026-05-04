"""Form 4 parser CP3 / SC3 binding tests — all 7 transaction codes.

The 7-fixture parametrized round-trip is the canonical CP3 binding: each
Form 4 transaction code (P/S/A/M/F/G/D) MUST be parsed and persisted as
the literal letter so downstream scoring (Phase 2) can filter on P-only.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ls_equity_fund.data.providers.edgar_provider import (
    CEO_CFO_TITLE_RE,
    VALID_TRANSACTION_CODES,
    EdgarProvider,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


@pytest.fixture
def provider() -> EdgarProvider:
    return EdgarProvider(
        sec_user_agent="Meridian Capital Partners contact@example.com"
    )


@pytest.mark.parametrize(
    "code,filename",
    [
        ("P", "form4_p_purchase.xml"),
        ("S", "form4_s_sale.xml"),
        ("A", "form4_a_grant.xml"),
        ("M", "form4_m_exercise.xml"),
        ("F", "form4_f_withhold.xml"),
        ("G", "form4_g_gift.xml"),
        ("D", "form4_d_disposition.xml"),
    ],
)
def test_form4_parses_all_seven_transaction_codes(
    provider: EdgarProvider, code: str, filename: str
) -> None:
    """SC3 / CP3 binding — every transaction code distinguishable."""
    fixture = FIXTURES / filename
    assert fixture.exists(), f"fixture missing: {filename}"
    rows = provider.parse_form4(filename, fixture)
    assert len(rows) >= 1, f"no transactions parsed for {filename}"
    assert rows[0]["transaction_code"] == code, (
        f"expected {code}, got {rows[0]['transaction_code']} for {filename}"
    )


def test_all_seven_codes_in_valid_set() -> None:
    assert VALID_TRANSACTION_CODES == frozenset(
        {"P", "S", "A", "M", "F", "G", "D"}
    )


def test_parse_form4_extracts_insider_metadata(provider: EdgarProvider) -> None:
    rows = provider.parse_form4("acc1", FIXTURES / "form4_p_purchase.xml")
    assert rows[0]["ticker"] == "AAPL"
    assert rows[0]["is_officer"] == 1
    assert "Cook" in rows[0]["insider_name"]
    assert "Chief Executive Officer" in rows[0]["insider_title"]
    assert rows[0]["shares"] == 1000.0
    assert rows[0]["price_per_share"] == 185.50
    assert rows[0]["total_value"] == pytest.approx(185500.0)


def test_ceo_cfo_title_regex_detects_titles() -> None:
    assert CEO_CFO_TITLE_RE.search("Chief Executive Officer")
    assert CEO_CFO_TITLE_RE.search("CEO")
    assert CEO_CFO_TITLE_RE.search("Chief Financial Officer")
    assert CEO_CFO_TITLE_RE.search("CFO")
    assert not CEO_CFO_TITLE_RE.search("Director")
    assert not CEO_CFO_TITLE_RE.search("VP Engineering")


def test_unknown_code_is_skipped_with_warning(
    provider: EdgarProvider, tmp_path: Path
) -> None:
    """Schema CHECK constraint enforces the 7 codes — parser skips invalid."""
    bad = tmp_path / "bad.xml"
    bad.write_text(
        """<?xml version='1.0'?>
<ownershipDocument>
  <issuer><issuerTradingSymbol>AAPL</issuerTradingSymbol></issuer>
  <reportingOwner><reportingOwnerId><rptOwnerName>X</rptOwnerName></reportingOwnerId></reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionCoding><transactionCode>Z</transactionCode></transactionCoding>
      <transactionAmounts><transactionShares><value>10</value></transactionShares></transactionAmounts>
      <transactionDate><value>2026-01-01</value></transactionDate>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>
"""
    )
    rows = provider.parse_form4("bad", bad)
    assert rows == []  # invalid code dropped, NOT raised


def test_provider_requires_user_agent_with_email() -> None:
    with pytest.raises(ValueError, match="contact email"):
        EdgarProvider(sec_user_agent="no-email-here")


def test_tracked_funds_default_has_nine_funds() -> None:
    """DATA-07 binding — 9 spec funds default in DataConfig.tracked_funds."""
    from ls_equity_fund.config import DataConfig

    cfg = DataConfig()
    assert len(cfg.tracked_funds) == 9
    names = {f.name for f in cfg.tracked_funds}
    assert "Berkshire Hathaway" in names
    assert "Citadel Advisors" in names
    assert "Pershing Square Capital" in names
    # All CIKs are 10 digits (zero-padded)
    for fund in cfg.tracked_funds:
        assert len(fund.cik) == 10, f"{fund.name} CIK not zero-padded"
        assert fund.cik.isdigit()
