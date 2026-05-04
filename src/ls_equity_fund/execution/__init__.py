"""L6 - Execution layer (Phase 8+).

Public façade: execute_rebalance(rebalance, dry_run)
Phase 0 ships:
  - Broker seam (execution/base.py)
  - PaperBroker concrete (execution/paper_broker.py) - deterministic-fill contract
"""
from ls_equity_fund.execution.base import Broker

__all__ = ["Broker"]
