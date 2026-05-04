# Research Summary — Meridian Capital Partners (`ls_equity_fund`)

**Synthesized:** 2026-05-04
**Sources:** STACK.md · FEATURES.md · ARCHITECTURE.md · PITFALLS.md
**Synthesizer confidence:** HIGH (all four research files are HIGH-confidence with live-verified sources)

---

## Executive Summary

Meridian Capital Partners is a single-operator long/short US equity factor-model system — universe → 8-factor sector-percentile scoring → Claude qualitative overlay → MVO/conviction-tilt construction → absolute-veto risk layer → IBKR paper execution → institutional Streamlit dashboard. The research confirms this is a credible, achievable solo quant system. The spec covers approximately 85% of institutional table-stakes out of the box; the eight gaps (G1–G8) are all cheap to close and must be surfaced as explicit requirements. The differentiator stack — Claude qualitative overlay with prompt caching, Form 4 P/S/A/M/F decoding, 13F crowding flag, MCTR-aware Barra risk model, dual-mode LP letter, JARVIS chat — is meaningfully above what solo quant systems typically ship and should be preserved as-specified.

The architecture is a layered batch pipeline with SQLite as the integration hub. The seven conceptual layers collapse into one Python package, one database, one CLI tree. The critical architectural decision is the L4↔L5 build-order resolution: conviction-tilt ships before the Barra risk model; MVO plugs in after L5 is live as a strategy swap-in behind the `Optimizer` interface. This is not a workaround — it is the spec's mandated non-convergence fallback, and the seam is already designed for it. Every subsequent phase operates against a working system rather than a half-assembled one.

The dominant risk category is **silent correctness failure**, not loud crashes: survivor-only universe inflating backtest returns, restated yfinance fundamentals contaminating quality factors, Form 4 award codes miscounted as buy signals, and uncounted `cache_creation_input_tokens` silently blowing the $25 cost ceiling. These are all fixable at the layer they enter, but only if they are named explicitly in the requirements so testing addresses them. The five critical pitfalls — survivorship bias, prompt-cache invalidation + cache-write token cost, Form 4 misclassification, MVO covariance instability, and veto-bypass via closing-trade mislabeling — must bind specific acceptance criteria in the phases that own them.

---

## Stack at a Glance

All mandated by spec; versions verified live on PyPI 2026-05-04.

| Component | Pinned Version | Critical Note |
|-----------|---------------|---------------|
| Python | `3.11.x` (floor) | Required by scipy 1.17, numpy 2, pandas 2.2 |
| uv | `0.11.8` | Replaces pip/pyenv/poetry; sleep-aware installs |
| yfinance | `0.2.6x` — pin specific known-good | Turbulent 2025-2026; do NOT float; requires `curl_cffi` transport |
| edgartools | `>=5.30,<6` | Covers all form types (10-K/10-Q/8-K/Form4/13F); built-in 10 req/s rate limiting |
| **ib_async** | `2.1.0` | **NOT `ib_insync`** — author deceased 2024, library frozen 2023-07 |
| anthropic | `>=0.97` | `cache_control: {"type":"ephemeral"}` requires system as content-block list, not a plain string |
| scipy | `>=1.16,<1.18` | SLSQP API stable |
| numpy | `>=2.0,<2.5` | Pin sub-2.5 for ecosystem stability |
| **pandas** | `>=2.2,<3.0` | **Hard upper bound — pandas 3.0 (Jan 2026) breaks copy-on-write, str dtype, inplace; will corrupt factor pipeline** |
| statsmodels | `>=0.14.6` | numpy-2 compatible; OLS/WLS for Barra cross-sectional regressions |
| streamlit | `>=1.57,<2` | `@st.cache_data` (DataFrames) vs `@st.cache_resource` (connections, clients) — distinction is critical |
| pydantic + pydantic-settings | `2.13.x` | Typed config validation at boot; nested env vars via `env_nested_delimiter` |
| structlog | `25.5.0` | Structured JSON audit trail; `bind_contextvars(run_id=...)` for per-run correlation |
| sqlite3 | stdlib | No ORM; plain SQL + parameterized queries; Alembic for migrations only |
| launchd | OS-native | `StartCalendarInterval` + `WakeSystem=true`; **not cron** (cron misses during sleep) |
| pytest + freezegun + responses | `9.0.3` / `1.5.5` / `0.26.0` | `freezegun` required for time-based factors (30/60/90d revisions, 90d insider flow) |

**Hard anti-recommendations:** `ib_insync` (unmaintained), `pandas>=3.0` (breaking), `requests-cache` (broken with yfinance curl_cffi), any ORM beyond Alembic migrations, `celery`/`rq`/APScheduler, Docker, sklearn/lightgbm (no supervised ML in v1).

---

## Confirmed Spec Coverage

Features the spec already covers well — do not relitigate these in requirements.

| Feature | Coverage | Verdict |
|---------|----------|---------|
| Sector-neutral GICS percentile rank (join key for all downstream) | Full | Strong |
| Beta-neutral construction + rolling 60d beta | Full | Strong |
| 8-check absolute pre-trade veto (no override flag) | Full | Strong — closing-trade exemption is correctly the only carve-out |
| Circuit breakers (-1.5%/d, -2.5%/d, -4%/wk, -8% DD) | Full | Strong |
| MVO + conviction-tilt as required pair | Full | Strong — fallback is mandatory, not optional |
| Anthropic prompt caching + analysis cache + $25 cost ceiling | Full | Load-bearing — not an optimization; required from day 1 |
| Form 4 P/S/A/M/F decoding | Full | Strong differentiator |
| 13F multi-fund-opening flag | Full | Strong differentiator |
| IBKR-native borrow check (not Alpaca) | Full | Correct broker-of-record |
| Dry-run / execute separation | Full | Strong |
| Full audit trail (orders, vetoes, breakers) | Full (cross-cutting) | Strong |
| Dual-mode LP/internal letter | Full | Genuine differentiator |
| JARVIS chat over JSON snapshot | Full | Novel for solo quant systems |
| Barra-style 120d risk model with MCTR feeding MVO covariance | Full | Strong differentiator |
| Configurable data provider interface (paid feed swap-in) | Full | Correct seam design |
| Paper→live IBKR separation (mode: paper / mode: live) | Full | Requires MERIDIAN_LIVE_OK=1 env guard as defense-in-depth |

---

## Gaps to Promote into Requirements

These 8 gaps from FEATURES research must become first-class requirements. P1 gaps belong in REQUIREMENTS.md as active requirements. P2 gaps are explicitly deferred to v1.x.

| # | Gap | Layer | Priority | Cost | Bind to Pitfall | Why It Cannot Slip |
|---|-----|-------|----------|------|-----------------|-------------------|
| G1 | Hard earnings-blackout absolute veto for new entries (N=3 trading days) | L5 — add as 9th veto check | P1 | S | D6 (earnings date quality) | Spec only halves position size; does not block new initiations. Uncompensated event risk on new entries. Closing trades remain exempt per existing veto pattern. |
| G2 | PIT universe table: first_seen_date, delisted_date, inclusion_window per ticker | L1 | P1 | S | D1 (survivorship bias — Critical) | Adding now is one schema column; retrofitting is impossible because deletions destroy history. Without this, any future backtest overstates Sharpe by 1-4%/yr and the paper→live decision is based on fiction. |
| G3 | Named tear-sheet metric set explicitly enumerated in L7 | L7 | P1 | S | — | "Institutional tear sheet" is vague. Pin to Pyfolio-equivalent: Sharpe, Sortino, Calmar, max-DD, hit rate, profit factor, beta, alpha, R2, skew, kurtosis, tail ratio. Provides objective definition-of-done. |
| G4 | Paper→live promotion gate with named numeric criteria (promotion_gate.yaml) | Cross-cutting | P1 | S | Disc1 (Critical — going live without ceremony) | Out of Scope mentions "explicit gating ceremony" but never defines gates. Minimum: 60+ trading days paper, realized Sharpe >= 0.8, slippage realized within 50% of modeled, zero unexplained circuit-breaker triggers in trailing 30d. Without numeric criteria, the ceremony is feel-based. |
| G5 | Backtest/walk-forward harness over L1's 3y OHLCV with PIT universe (G2) | New post-spine layer | P2 | L | D1, D2 | Defer to v1.x once paper spine is live. The 3y OHLCV data is already ingested; the harness is software. Required before any Sharpe claim from history is defensible. |
| G6 | Factor IC / staleness monitor (rolling 6m IC per factor, auto-flag on degradation) | L7 | P2 | M | F3, F4 | Defer to v1.x. Required to detect alpha decay before P&L does. Needs 90+ days of persisted factor-at-entry scores before it produces signal. |
| G7 | Optimizer non-convergence audit log entry | L4 — extend audit trail | P1 | XS | O2, O3 (Critical — stale weights) | Each MVO→conviction-tilt fallback must write a structured audit row with reason (singular covariance, iteration limit, etc.) so deteriorating model conditions are visible before they become daily occurrences. |
| G8 | Borrow-rate / hard-to-borrow cost capture from IBKR per short position | L6 | P1 | S | I3, O4 | IBKR publishes borrow rates per ticker; HTB names cost 5-50%/yr. Without this, the L4 transaction-cost model's short side is fictional. Also feeds the "refuse new short if rate > 25%/yr" rule that prevents force-close events. |

G1, G2, G3, G4, G7, G8 are P1 — active requirements for v1.
G5 and G6 are P2 — explicitly deferred to v1.x after paper spine is validated.

---

## Architecture Decisions That Bind the Roadmap

These are structural seams the roadmap must respect; they are not suggestions.

### Decision 1: Single package, SQLite hub, facade-per-layer

One src/ls_equity_fund/ package. Layers communicate via SQLite tables (bulk data) and Pydantic models (control objects). No layer imports another layer's internals — only the public __init__.py facade. Every roadmap phase must produce a runnable CLI command and a passing test suite for its layer before the next phase starts.

### Decision 2: Three provider seams define the swap-in points

- data/providers/base.py → MarketDataProvider ABC → YFinanceProvider ships Phase 1; PolygonProvider stub ships Phase 0
- portfolio/optimizers/base.py → Optimizer ABC → conviction-tilt ships Phase 5; MVO ships Phase 7 as plug-in
- execution/broker/base.py → Broker ABC → PaperBroker ships Phase 0; IBKRBroker via ib_async ships Phase 8

Hardcoding any provider inside layer logic defeats the spec's swap-in mandate.

### Decision 3: L4/L5 cycle resolution — conviction-tilt first, MVO after L5

This is the most important build-order decision. MVO requires a working covariance matrix from L5. L5 needs 120 days of returns data. On day 1 there is no covariance. Resolution:

- Phase 5: ConvictionTiltOptimizer — zero L5 dependency, always works
- Phase 6: L5 Barra risk model → covariance available
- Phase 7: MVOOptimizer as plug-in; conviction-tilt remains non-convergence fallback per spec
- config.yaml starts at optimizer: conviction; operator flips to optimizer: mvo after Phase 7

This is not technical debt. Conviction-tilt is the spec-mandated fallback. MVO is an enhancement. The Optimizer seam was designed for this exact pattern.

### Decision 4: Execution is operator-initiated, not automated

launchd runs daily-refresh which chains L1→L2→L3→L4(whatif)→L5(veto)→L7. L6 execution is not in the daily-refresh chain. The operator reviews the dashboard and runs `cli run-execution --execute` explicitly. This means execution bugs can never be triggered by the scheduler.

### Decision 5: Dashboard is read-only over SQLite — no service layer

Streamlit reads from the same SQLite via dashboard/queries.py. No API server, no separate process. JARVIS chat is the only Anthropic call from the dashboard — per explicit user action (button press), with caching, behind a 30s timeout. Auto-refresh is conditional on market hours. Streamlit never computes factor scores or calls Anthropic on page load — compute happens in the CLI, Streamlit reads results.

### Decision 6: Phase 0's PaperBroker is load-bearing

PaperBroker (in-memory, deterministic fills at last price) ships in Phase 0. It lets the entire L4→L5→L6 chain be exercised end-to-end before any IBKR connection exists. Without it, every downstream phase that touches execution is blocked on broker setup.

### Decision 7: Alembic for migrations

Schema will evolve (G2 universe PIT columns, new audit columns, new factors). Alembic batch_alter_table handles SQLite's limited ALTER. Every schema change is a new revision; alembic downgrade works. scripts/init_db.py wraps alembic upgrade head so a fresh clone is one command.

---

## Top-5 Critical Pitfalls Cross-Referenced to Phases

These bind specific phase acceptance criteria. A phase is not complete until its critical pitfall prevention is implemented and tested.

### CP1. Survivorship + look-ahead bias (D1 + D2) — Phase 1 (L1)

Risk: Universe sourced from today's S&P 500 excludes delisted names. yfinance returns restated fundamentals, not point-in-time. Both contaminate every downstream factor and any future backtest.

Phase 1 acceptance criteria must include:
- universe table has first_seen_date, delisted_date, inclusion_window columns (G2)
- Every fundamental row has as_of_filing_date stored at ingest, not just period_end
- Code comment in data/providers/yfinance_provider.py: "yfinance fundamentals are live-forward only; never use for backtesting"
- Test: universe table contains PIT columns; a test ticker marked delisted is flagged not deleted

### CP2. Anthropic prompt-cache invalidation + cache-write token cost (C1 + C2) — Phase 4 (L3)

Risk: A single whitespace change in a system prompt busts the cache across all 4 analyzers x 40 tickers. cache_creation_input_tokens is billed at 1.25x input rate — not counting it means the $25 ceiling never trips while the bill runs.

Cross-finding collapse: STACK prompt-caching mandate + FEATURES verdict that L3 infrastructure is more important than LLM choice + PITFALLS C1 + C2 all converge on one mandate:

Phase 4 must ship cache infrastructure + cost-tracker + cache-aware integration tests before any analyzer ships. The analyzers are secondary deliverables within that phase.

Phase 4 acceptance criteria must include:
- System prompts in versioned files (analysis/prompts/v1/); edits go to a new version directory
- Cost tracker sums: input_tokens + (cache_creation_input_tokens x 1.25) + (cache_read_input_tokens x 0.10) + (output_tokens x output_rate)
- Unit test: cost tracker validated against a real Anthropic API call, confirmed against Anthropic dashboard line items
- Soft warning at $20; hard abort at $25
- Integration test: 4 analyzers x 5 synthetic tickers; second run cache hit rate > 90%; total cost stays under $5

### CP3. Form 4 transaction-code misclassification (D3) — Phase 1 (L1) + Phase 2 (L2)

Risk: Treating F/A/M/G codes as buy signals inverts the insider factor. The 3x CEO/CFO weighting amplifies the wrong direction. Near-zero Spearman correlation with subsequent returns is the symptom — it looks like a working factor until attribution is done.

Phase 1 acceptance criteria:
- insider_transactions.transaction_code is a first-class column (P/S/A/M/F/G/D), not an aggregate
- Parser test: known CFO 10b5-1 sale → S; RSU vesting → A; tax withhold → F; none counted as buy

Phase 2 acceptance criteria:
- Factor computes net flow from P-only minus S-only; A/M/F/G/D contribute zero to directional signal (still logged for audit)
- Cluster-buy detection counts distinct insiders with code=P only
- Unit test: synthetic Form 4 data with all code types → only P/S contribute to signal

### CP4. MVO covariance instability (O1 + O3) — Phase 7 (L4 MVO)

Risk: Sample covariance on N=500 x T=120 is rank-deficient (rank <= 119). MVO concentrates in lowest-variance noise eigenvectors. Code that silently uses stale weights as fallback means the system appears to run while the book drifts unmanaged.

Phase 7 acceptance criteria:
- Ledoit-Wolf shrinkage (sklearn.covariance.LedoitWolf) or Barra factor-model covariance — sample covariance alone is not acceptable
- Ex-ante vol sanity check: if model-implied portfolio vol < 5% annualized, refuse to rebalance and fall back to conviction-tilt
- Fallback semantics: conviction-tilt is the only fallback; never reuse yesterday's weights; if both optimizers fail → halt + red flag file + no orders (G7)
- Test: MVO with intentionally ill-conditioned covariance → fallback fires, G7 audit row written, portfolio uses conviction-tilt output

### CP5. Pre-trade veto bypass via closing-trade mislabeling (R4) — Phase 6 (L5)

Risk: The closing-trade exemption is the only carve-out from the absolute veto. Sloppy classification — a partial reduce labeled "closing," a long-to-short flip labeled "closing" — bypasses the veto. Knight Capital (2012, $440M in 45 minutes) is the canonical example of a single unreviewed code path on the order router.

Phase 6 acceptance criteria:
- Closing-trade definition: all three conditions required: abs(new_position) < abs(old_position) AND sign(new_position) == sign(old_position) AND abs(trade_qty) <= abs(old_position)
- is_closing_trade: bool is an explicit audit field on every order with the rule that produced it
- Unit tests cover: (a) partial reduce → NOT closing, (b) full close → closing, (c) long→short flip → NOT closing, (d) full close + reverse → NOT closing

---

## Cross-Findings Synthesis

### Finding 1: L3 infrastructure is the deliverable, not the LLM

STACK confirms prompt caching is load-bearing (system must be a content-block list, not a plain string). FEATURES confirms the 60/40 quant/Claude blend with graceful 100%-quant fallback is the right resilience pattern. PITFALLS identifies two independent critical failure modes (C1 cache invalidation, C2 cost tracking) that both live in L3 infrastructure, not in the analyzers themselves. Together: Phase 4 ships cache + cost-tracker + cache-aware tests first; analyzers are secondary deliverables within that phase.

### Finding 2: The $25/run ceiling depends on three components, not one

Cost ceiling requires: (a) prompt caching hitting — C1 prevention, (b) cost tracker counting all four token fields including cache_creation_input_tokens at 1.25x — C2 prevention, (c) analysis cache (SQLite, 30-day TTL) returning hits for re-runs — ARCHITECTURE analysis/cache.py. Miss any one and the ceiling is fictional. All three must be in place before any analyzer is enabled in the daily-refresh chain.

### Finding 3: Dashboard must never call Anthropic on page load

ARCHITECTURE: JARVIS is the only Anthropic call from Streamlit, per-question, with caching. PITFALLS S2: 5-min auto-refresh re-renders pages and re-calls Claude — blowing the daily budget from the dashboard. FEATURES: LP letter and weekly commentary are pre-computed in the 17:15 run and stored in SQLite. Requirement: every Anthropic output consumed by the dashboard must be pre-computed by the CLI run and stored in SQLite. Dashboard reads, never computes.

### Finding 4: No contradiction between STACK and PITFALLS on yfinance

STACK recommends pinning a specific yfinance version and isolating behind MarketDataProvider. PITFALLS D1/D2 warn about survivorship bias and restated fundamentals. These are compatible: the interface seam addresses substitutability; the pitfall warnings address what to document and test within the implementation. Confidence: HIGH.

### Finding 5: ARCHITECTURE build order and FEATURES ship-order are consistent

ARCHITECTURE proposes shipping the L7 reporting skeleton in Phase 3 (before L3 Claude analysis) to ship value early. FEATURES ranks both as P1 but does not specify inter-layer ordering. These are consistent — a daily ranked candidate list with sector breakdown is independently valuable and protects against late-pipeline blockers killing the project. Adopt the ARCHITECTURE build order as the roadmap skeleton. No contradiction.

### Finding 6: Potential risk — pandas 3.0 indirect dependency pull

STACK pins pandas>=2.2,<3.0. The pandas 3.0 breaking changes are high-risk for the factor pipeline. If any indirect dependency pulls in pandas 3.0, the pyproject.toml constraint prevents it only if uv respects the upper bound. Resolution: add pandas<3.0 as a hard upper bound in pyproject.toml, and run `uv lock --check` after any dependency update. Confidence: HIGH that the pin is correct; MEDIUM that indirect deps won't force the issue.

---

## Confidence Index

| Research Area | Confidence | Notes |
|---------------|------------|-------|
| Stack (versions, tooling choices) | HIGH | Live PyPI verified 2026-05-04; ib_async migration documented |
| Feature coverage (table-stakes) | HIGH | Cross-referenced against AQR, Barra, Quantopian conventions |
| Feature gaps G1-G8 | HIGH | G1/G2/G4 are standard institutional practice; G5/G6 correctly deferred |
| Architecture (component boundaries, seams) | HIGH | Standard Python packaging; SQLite WAL; seam designs follow spec mandate |
| L4/L5 build-order resolution | MEDIUM-HIGH | Conviction-tilt-first is the clear answer; Phase 5→6→7 sequencing is opinionated |
| Critical pitfalls (all five CP) | HIGH | Verified against primary research: Anthropic docs, Cohen et al., Ledoit-Wolf, Knight Capital postmortem |
| MVO covariance shrinkage method | MEDIUM | Ledoit-Wolf is best-practice; OAS and factor-model paths are both valid; decide in Phase 7 research spike |
| Barra factor model accuracy at 500-name cross-section | MEDIUM | R2 = 0.30-0.50 target is documented; actual performance depends on factor design |
| yfinance reliability at 3000-name universe | MEDIUM | Turbulent history; pin and build behind interface seam; no better free alternative |

Overall: HIGH confidence for all v1 build decisions. MEDIUM for Phase 6 (Barra) and Phase 7 (MVO) implementation details — both warrant a /gsd-research-phase spike before coding starts.

---

## Implications for Roadmap

### Suggested Phase Structure (11 phases, matching ARCHITECTURE build order)

| Phase | Name | Layers | Key Deliverable | Binds Pitfall |
|-------|------|--------|-----------------|---------------|
| 0 | Foundation | Config, DB, CLI skeleton, PaperBroker | Bootable system; all seam interfaces defined; .gitignore validated | Op5, Op4 |
| 1 | Data Infrastructure | L1 | Daily refresh to SQLite; PIT universe columns (G2) | CP1 (D1/D2), CP3 (D3), G2 |
| 2 | Scoring Engine | L2 | run-scoring writes factor_scores and factor_subscores | CP3 (D3 factor side), F1, F4 |
| 3 | Reporting Skeleton + Dashboard | L7 minimal, Streamlit 6 pages | Operator sees daily ranked candidates by sector — value before execution | S1 |
| 4 | Claude Analysis | L3 | 4 analyzers with cache + cost-tracker (infrastructure ships first, analyzers second) | CP2 (C1/C2), C4, C5 |
| 5 | Portfolio Construction (conviction-tilt) | L4 minus MVO | run-portfolio --whatif; conviction-tilt optimizer; first full data→score→analyze→portfolio chain | O2, O3 (fallback defined here) |
| 6 | Risk Model + Veto + Breakers | L5 | Barra factor model, 8-check veto (+ G1 as 9th), circuit breakers; covariance available for Phase 7 | CP5 (R4), R1, R2, R3, G1 |
| 7 | MVO Swap-in | L4 MVO | optimizer: mvo plug-in; Ledoit-Wolf shrinkage; conviction-tilt remains fallback; G7 audit row | CP4 (O1/O3), G7 |
| 8 | IBKR Execution (paper) | L6 | IBKRBroker via ib_async; executor, slippage, borrow check (G8), order manager | I1, I2, I3, I4, G8 |
| 9 | Reporting (full) | L7 complete | Attribution, tear sheet (G3 metric set), letter (dual-mode) | S2, G3 |
| 10 | Dashboard Polish + JARVIS + launchd | Dashboard, scheduling | 5-min auto-refresh (market-hours only), JARVIS chat, launchd plist with WakeSystem=true | S3, Op1, Op2 |
| Post-v1 | Live-readiness review | Cross-cutting | Promotion ceremony with G4 checklist; audit review; paper→live gate | Disc1, Disc3, Disc4 |

### Phases that need /gsd-research-phase before execution

- Phase 6 (L5 Barra risk model): Cross-sectional factor model is a serious math project. Covariance shrinkage method, eigenfactor adjustment, residual winsorization, and regression structure (OLS vs WLS) need a dedicated research spike before coding.
- Phase 7 (L4 MVO): SLSQP constraint formulation for joint dollar-neutrality + beta-neutrality + per-sector-neutrality with simultaneously-active equality constraints has known convergence failure modes. Ledoit-Wolf integration with Barra covariance output needs explicit design before implementation.
- Phase 8 (L6 IBKR): Paper-account market-data permissioning (I2), session-drop handling (I4), and borrow-rate capture (G8) have vendor-specific details that change. Research current IBKR API surface and IBC auto-login patterns before Phase 8 starts.

### Phases with well-documented patterns (no deep research needed)

Phase 0 (Foundation), Phase 1 (L1 data), Phase 2 (L2 scoring), Phase 3 (dashboard skeleton), Phase 4 (L3 Claude infrastructure), Phase 5 (conviction-tilt), Phase 9 (L7 reporting), Phase 10 (dashboard polish).

---

## Open Questions

1. Covariance shrinkage: Ledoit-Wolf vs OAS vs raw Barra factor-model covariance (B Sigma_F B'). Recommend Ledoit-Wolf as default; decide in Phase 6 research spike.
2. Anthropic streaming vs non-streaming: Non-streaming is simpler and sufficient for v1. Streaming would improve cost-tracker granularity. Defer to Phase 4 design.
3. JARVIS context window: Fixed system prompt + injected today's portfolio_history + factor_scores top 50 + last 7 runs. No tool-use in v1. Confirm in Phase 10.
4. Live promotion MERIDIAN_LIVE_OK=1 env guard: Implement in Phase 8 (L6) as a hard check that refuses mode: live without the env var. Defense-in-depth against accidental live trading.
5. yfinance exact pin version: Determine by running the full L1 data refresh test suite before Phase 1 closes — pin whatever passes cleanly.
6. cvxpy as MVO fallback: Keep as optional dependency. Activate only if Phase 7 research spike finds SLSQP convergence failure is frequent under the joint-constraint set.

---

## Sources

Aggregated from four research files. Key primary sources:

- Anthropic prompt-caching docs — cache hierarchy, invalidation, token billing fields
- AQR long/short equity design papers — sector neutrality, factor construction conventions
- MSCI Barra USE4 methodology handbook — eigenfactor adjustment, R2 targets, specific-variance inflation
- Cohen, Malloy & Pomorski (2012) "Decoding Inside Information" — only P-code purchases predict; A/M/F are noise
- Ledoit & Wolf (2003) "Honey, I Shrunk the Sample Covariance Matrix" — shrinkage outperforms sample in every scenario
- Khandani & Lo (2007) "What Happened to the Quants in August 2007?" — regime-shift risk to overlapping factor portfolios
- ib_async PyPI + GitHub — ib_insync successor, 2.1.0, 2025-12-08
- edgartools PyPI — 5.30.2, 2026-04-29, covers all spec-required form types
- pandas 3.0 release notes — Jan 21, 2026 breaking changes
- SEC EDGAR rate limit policy — 10 req/s, User-Agent mandatory
- yfinance GitHub issue trail — curl_cffi migration, rate-limit changes 2025-2026
- Quantopian Zipline/Alphalens/Pyfolio — factor IC conventions, tear sheet metric set, PIT data requirements
- Knight Capital Group (2012) postmortem — order-router bypass failure mode; canonical veto-discipline citation

---

*Research synthesis for: Meridian Capital Partners / ls_equity_fund*
*Synthesized: 2026-05-04*
