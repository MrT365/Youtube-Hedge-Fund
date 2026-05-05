"""L2 scoring engine public facade."""

from ls_equity_fund.factors.composer import (
    FACTOR_NAMES,
    FACTOR_REGISTRY,
    compute_parent_factor_score,
    register_factor,
)
from ls_equity_fund.factors.persist import write_factor_scores, write_parent_scores
from ls_equity_fund.factors.sector_rank import (
    compute_sector_percentile_rank,
    percentile_rank_within,
)

__all__ = [
    "FACTOR_NAMES",
    "FACTOR_REGISTRY",
    "compute_parent_factor_score",
    "compute_sector_percentile_rank",
    "percentile_rank_within",
    "register_factor",
    "write_factor_scores",
    "write_parent_scores",
]
