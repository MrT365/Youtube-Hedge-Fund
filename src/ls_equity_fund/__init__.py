"""ls_equity_fund - Meridian Capital Partners single-operator L/S equity hedge fund system.

Layer packages (per ARCHITECTURE.md §3, CONTEXT D-22):
  - data: L1 market/fundamental/SEC data ingestion
  - factors: L2 8-factor scoring engine
  - analysis: L3 Claude qualitative analysis
  - portfolio: L4 portfolio construction (MVO + conviction-tilt)
  - risk: L5 risk model + pre-trade veto + circuit breakers
  - execution: L6 IBKR routing + PaperBroker stub
  - reporting: L7 daily letter + tear sheet
  - dashboard: Streamlit localhost:8502

Phase 0 establishes the package skeleton + three swap-in seam ABCs.
"""

__version__ = "0.1.0"
