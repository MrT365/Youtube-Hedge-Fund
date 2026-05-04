"""create_phase1_tables

Phase 1 (L1 — Data Infrastructure) migration. Materializes the 13 tables every
Phase 1 ingestion module needs, in a single revision so Wave 2 plans (02..08)
can run in parallel without shared-file conflicts.

Per CONTEXT D-01: raw SQL only via ``op.execute()``. The ``create_table`` ORM
helper, ``MetaData`` and ``Table`` declarations are deliberately not used here.

Per CONTEXT D-04: this migration is the source of truth for the Phase 1 schema.
Any DAO / ingestion code reads its column shape from here.

Schema bindings (success-criterion / threat traceability):
  - universe.{first_seen_date, delisted_date, inclusion_window} → CP1 / SC1
  - insider_transactions.transaction_code (NOT NULL, CHECK)        → CP3 / SC3
  - fundamentals.as_of_ingest_date (in PK, append-only)             → D2 mitigation
  - filings_metadata.filepath + content_hash                        → DATA-05 (bodies on disk)
  - institutional_holdings.{period_end, filed_date} (distinct)      → D4 (45-day lag preserved)
  - refresh_state per (provider, feed_type, ticker)                 → DATA-12 cursor

Foreign keys are intentionally OMITTED for v1 — referential integrity is
enforced by ingest code (matches Phase 0 pattern). Adding FKs later requires
a new migration; downstream tests assert the absence and shape today.

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-04 12:00:00 UTC
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the 13 Phase 1 tables (raw SQL only, D-01)."""

    # ----- 1. universe (DATA-01, DATA-13; binds CP1 / SC1) -----
    # PIT-aware: first_seen_date + delisted_date + inclusion_window enable
    # downstream survivorship-bias-free queries (Plan 01-02 owns ingest).
    op.execute(
        """
        CREATE TABLE universe (
            ticker            TEXT PRIMARY KEY,
            company_name      TEXT,
            exchange          TEXT,
            primary_listing   TEXT,
            sector            TEXT,
            industry          TEXT,
            sub_industry      TEXT,
            first_seen_date   TEXT NOT NULL,
            delisted_date     TEXT,
            inclusion_window  TEXT NOT NULL,
            last_updated      INTEGER NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX idx_universe_sector ON universe(sector)")
    op.execute("CREATE INDEX idx_universe_first_seen ON universe(first_seen_date)")
    op.execute("CREATE INDEX idx_universe_delisted ON universe(delisted_date)")

    # ----- 2. benchmarks (DATA-02; benchmark + sector ETF + macro) -----
    op.execute(
        """
        CREATE TABLE benchmarks (
            ticker         TEXT PRIMARY KEY,
            category       TEXT NOT NULL CHECK (category IN ('benchmark', 'sector_etf', 'macro')),
            description    TEXT,
            last_updated   INTEGER NOT NULL
        )
        """
    )

    # ----- 3. daily_prices (DATA-03) -----
    op.execute(
        """
        CREATE TABLE daily_prices (
            ticker      TEXT NOT NULL,
            date        TEXT NOT NULL,
            open        REAL,
            high        REAL,
            low         REAL,
            close       REAL,
            adj_close   REAL,
            volume      INTEGER,
            PRIMARY KEY (ticker, date)
        )
        """
    )
    op.execute("CREATE INDEX idx_prices_date ON daily_prices(date)")
    op.execute("CREATE INDEX idx_prices_ticker_date ON daily_prices(ticker, date)")

    # ----- 4. fundamentals (DATA-04; D2 mitigation: append-only, as_of_ingest_date in PK) -----
    op.execute(
        """
        CREATE TABLE fundamentals (
            ticker               TEXT NOT NULL,
            period_end           TEXT NOT NULL,
            period_type          TEXT NOT NULL CHECK (period_type IN ('annual', 'quarterly')),
            as_of_ingest_date    TEXT NOT NULL,
            revenue              REAL,
            gross_profit         REAL,
            operating_income     REAL,
            net_income           REAL,
            eps_basic            REAL,
            eps_diluted          REAL,
            total_assets         REAL,
            total_liabilities    REAL,
            total_equity         REAL,
            current_assets       REAL,
            current_liabilities  REAL,
            accounts_receivable  REAL,
            inventory            REAL,
            long_term_debt       REAL,
            cash_and_equivalents REAL,
            cfo                  REAL,
            cfi                  REAL,
            cff                  REAL,
            capex                REAL,
            free_cash_flow       REAL,
            dividends_paid       REAL,
            buybacks             REAL,
            shares_outstanding   REAL,
            rd_expense           REAL,
            ebit                 REAL,
            retained_earnings    REAL,
            working_capital      REAL,
            accruals             REAL,
            PRIMARY KEY (ticker, period_end, period_type, as_of_ingest_date)
        )
        """
    )
    op.execute("CREATE INDEX idx_fund_ticker ON fundamentals(ticker)")
    op.execute("CREATE INDEX idx_fund_ticker_period ON fundamentals(ticker, period_end)")

    # ----- 5. fundamental_ratios (DATA-04; 24 derived ratios, recomputed daily) -----
    op.execute(
        """
        CREATE TABLE fundamental_ratios (
            ticker                  TEXT NOT NULL,
            asof_date               TEXT NOT NULL,
            roe                     REAL,
            roa                     REAL,
            gross_margin            REAL,
            operating_margin        REAL,
            net_margin              REAL,
            revenue_growth_yoy      REAL,
            revenue_growth_qoq      REAL,
            earnings_growth_yoy     REAL,
            earnings_growth_qoq     REAL,
            debt_to_equity          REAL,
            fcf_yield               REAL,
            current_ratio           REAL,
            ar_to_revenue           REAL,
            cfo_to_ni               REAL,
            accruals_ratio          REAL,
            retained_earnings_ratio REAL,
            working_capital_ratio   REAL,
            total_liabilities_ratio REAL,
            ebit_margin             REAL,
            rd_intensity            REAL,
            shares_out              REAL,
            dividend_yield          REAL,
            buyback_yield           REAL,
            asset_turnover          REAL,
            PRIMARY KEY (ticker, asof_date)
        )
        """
    )
    op.execute("CREATE INDEX idx_ratios_date ON fundamental_ratios(asof_date)")

    # ----- 6. filings_metadata (DATA-05; bodies on disk under cache/filings/) -----
    op.execute(
        """
        CREATE TABLE filings_metadata (
            accession_number  TEXT PRIMARY KEY,
            ticker            TEXT NOT NULL,
            cik               TEXT NOT NULL,
            form_type         TEXT NOT NULL,
            filed_date        TEXT NOT NULL,
            period_of_report  TEXT,
            filepath          TEXT NOT NULL,
            content_hash      TEXT,
            fetched_at        INTEGER NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX idx_filings_ticker_form ON filings_metadata(ticker, form_type)")
    op.execute("CREATE INDEX idx_filings_filed_date ON filings_metadata(filed_date)")

    # ----- 7. insider_transactions (DATA-06; binds CP3 / SC3 — transaction_code first-class) -----
    # transaction_code values per Form 4 spec (StockTitan ref in CLAUDE.md sources):
    #   P=open-market purchase, S=open-market sale, A=grant/award, M=option exercise,
    #   F=tax-withholding, G=gift, D=disposition. Anything else is a parse error
    #   and MUST be rejected at the schema layer (CP3 binding).
    op.execute(
        """
        CREATE TABLE insider_transactions (
            accession_number     TEXT NOT NULL,
            line_no              INTEGER NOT NULL,
            ticker               TEXT NOT NULL,
            insider_name         TEXT,
            insider_title        TEXT,
            is_officer           INTEGER NOT NULL DEFAULT 0,
            is_director          INTEGER NOT NULL DEFAULT 0,
            is_ten_percent_owner INTEGER NOT NULL DEFAULT 0,
            transaction_code TEXT NOT NULL CHECK (transaction_code IN ('P', 'S', 'A', 'M', 'F', 'G', 'D')),
            transaction_type     TEXT,
            shares               REAL,
            price_per_share      REAL,
            total_value          REAL,
            transaction_date     TEXT NOT NULL,
            filed_date           TEXT NOT NULL,
            ownership_type       TEXT,
            PRIMARY KEY (accession_number, line_no)
        )
        """
    )
    op.execute("CREATE INDEX idx_insider_ticker_date ON insider_transactions(ticker, transaction_date)")
    op.execute("CREATE INDEX idx_insider_code ON insider_transactions(transaction_code)")

    # ----- 8. institutional_holdings (DATA-07; 13F — period_end vs filed_date distinct, D4 binding) -----
    op.execute(
        """
        CREATE TABLE institutional_holdings (
            cik             TEXT NOT NULL,
            fund_name       TEXT NOT NULL,
            ticker          TEXT NOT NULL,
            period_end      TEXT NOT NULL,
            filed_date      TEXT NOT NULL,
            shares          REAL,
            value_usd       REAL,
            change_shares   REAL,
            is_new_position INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (cik, ticker, period_end)
        )
        """
    )
    op.execute("CREATE INDEX idx_inst_ticker_period ON institutional_holdings(ticker, period_end)")
    op.execute("CREATE INDEX idx_inst_fund ON institutional_holdings(fund_name)")

    # ----- 9. short_interest (DATA-08) -----
    op.execute(
        """
        CREATE TABLE short_interest (
            ticker                 TEXT NOT NULL,
            snapshot_date          TEXT NOT NULL,
            shares_short           REAL,
            short_ratio            REAL,
            short_percent_of_float REAL,
            PRIMARY KEY (ticker, snapshot_date)
        )
        """
    )
    op.execute("CREATE INDEX idx_short_ticker_date ON short_interest(ticker, snapshot_date)")

    # ----- 10. analyst_estimates (DATA-09) -----
    op.execute(
        """
        CREATE TABLE analyst_estimates (
            ticker          TEXT NOT NULL,
            snapshot_date   TEXT NOT NULL,
            eps_fy1         REAL,
            eps_fy2         REAL,
            rev_fy1         REAL,
            rev_fy2         REAL,
            target_price    REAL,
            n_analysts      INTEGER,
            PRIMARY KEY (ticker, snapshot_date)
        )
        """
    )
    op.execute("CREATE INDEX idx_est_ticker_date ON analyst_estimates(ticker, snapshot_date)")

    # ----- 11. earnings_calendar (DATA-10) -----
    op.execute(
        """
        CREATE TABLE earnings_calendar (
            ticker          TEXT NOT NULL,
            expected_date   TEXT NOT NULL,
            time_of_day     TEXT,
            fiscal_period   TEXT,
            refreshed_at    INTEGER NOT NULL,
            PRIMARY KEY (ticker, expected_date)
        )
        """
    )
    op.execute("CREATE INDEX idx_earn_date ON earnings_calendar(expected_date)")

    # ----- 12. macro_calendar (DATA-11; FOMC + cached fallback) -----
    op.execute(
        """
        CREATE TABLE macro_calendar (
            event_id         TEXT PRIMARY KEY,
            event_type       TEXT NOT NULL,
            event_date_et    TEXT NOT NULL,
            event_date_local TEXT,
            description      TEXT,
            source           TEXT NOT NULL,
            fetched_at       INTEGER NOT NULL,
            last_refreshed   INTEGER NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX idx_macro_date ON macro_calendar(event_date_et)")
    op.execute("CREATE INDEX idx_macro_type ON macro_calendar(event_type)")

    # ----- 13. refresh_state (DATA-12; per-provider/feed/ticker incremental cursor) -----
    op.execute(
        """
        CREATE TABLE refresh_state (
            provider          TEXT NOT NULL,
            feed_type         TEXT NOT NULL,
            ticker            TEXT NOT NULL,
            last_value_text   TEXT,
            last_value_int    INTEGER,
            last_refreshed    INTEGER NOT NULL,
            status            TEXT NOT NULL CHECK (status IN ('OK', 'FAILED', 'SKIPPED')),
            last_error        TEXT,
            PRIMARY KEY (provider, feed_type, ticker)
        )
        """
    )
    op.execute("CREATE INDEX idx_refresh_status ON refresh_state(status)")


def downgrade() -> None:
    """Drop all 13 Phase 1 tables and their indexes (reverse creation order)."""

    # 13. refresh_state
    op.execute("DROP INDEX IF EXISTS idx_refresh_status")
    op.execute("DROP TABLE IF EXISTS refresh_state")

    # 12. macro_calendar
    op.execute("DROP INDEX IF EXISTS idx_macro_type")
    op.execute("DROP INDEX IF EXISTS idx_macro_date")
    op.execute("DROP TABLE IF EXISTS macro_calendar")

    # 11. earnings_calendar
    op.execute("DROP INDEX IF EXISTS idx_earn_date")
    op.execute("DROP TABLE IF EXISTS earnings_calendar")

    # 10. analyst_estimates
    op.execute("DROP INDEX IF EXISTS idx_est_ticker_date")
    op.execute("DROP TABLE IF EXISTS analyst_estimates")

    # 9. short_interest
    op.execute("DROP INDEX IF EXISTS idx_short_ticker_date")
    op.execute("DROP TABLE IF EXISTS short_interest")

    # 8. institutional_holdings
    op.execute("DROP INDEX IF EXISTS idx_inst_fund")
    op.execute("DROP INDEX IF EXISTS idx_inst_ticker_period")
    op.execute("DROP TABLE IF EXISTS institutional_holdings")

    # 7. insider_transactions
    op.execute("DROP INDEX IF EXISTS idx_insider_code")
    op.execute("DROP INDEX IF EXISTS idx_insider_ticker_date")
    op.execute("DROP TABLE IF EXISTS insider_transactions")

    # 6. filings_metadata
    op.execute("DROP INDEX IF EXISTS idx_filings_filed_date")
    op.execute("DROP INDEX IF EXISTS idx_filings_ticker_form")
    op.execute("DROP TABLE IF EXISTS filings_metadata")

    # 5. fundamental_ratios
    op.execute("DROP INDEX IF EXISTS idx_ratios_date")
    op.execute("DROP TABLE IF EXISTS fundamental_ratios")

    # 4. fundamentals
    op.execute("DROP INDEX IF EXISTS idx_fund_ticker_period")
    op.execute("DROP INDEX IF EXISTS idx_fund_ticker")
    op.execute("DROP TABLE IF EXISTS fundamentals")

    # 3. daily_prices
    op.execute("DROP INDEX IF EXISTS idx_prices_ticker_date")
    op.execute("DROP INDEX IF EXISTS idx_prices_date")
    op.execute("DROP TABLE IF EXISTS daily_prices")

    # 2. benchmarks
    op.execute("DROP TABLE IF EXISTS benchmarks")

    # 1. universe
    op.execute("DROP INDEX IF EXISTS idx_universe_delisted")
    op.execute("DROP INDEX IF EXISTS idx_universe_first_seen")
    op.execute("DROP INDEX IF EXISTS idx_universe_sector")
    op.execute("DROP TABLE IF EXISTS universe")
