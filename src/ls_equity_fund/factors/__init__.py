"""L2 scoring engine public facade.

Importing this package wires every factor into FACTOR_REGISTRY via the
``@register_factor`` decorator. The orchestrator (cli/scoring_cmd.py) iterates
the registry to compute scores; tests can inspect FACTOR_REGISTRY to confirm
all 9 names are populated (8 base + 1 combined).
"""

# Side-effect imports: each module's @register_factor decorator runs at import
# time, populating FACTOR_REGISTRY. Order does not matter for registration —
# the orchestrator handles base-vs-composite ordering via BASE_FACTORS /
# COMPOSITE_FACTORS.
from ls_equity_fund.factors import (  # noqa: F401  (side-effect imports)
    combined_score,
    growth,
    insider,
    institutional,
    momentum,
    quality,
    revisions,
    short_interest,
    value,
)
from ls_equity_fund.factors.composer import (
    BASE_FACTORS,
    COMPOSITE_FACTORS,
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
    "BASE_FACTORS",
    "COMPOSITE_FACTORS",
    "FACTOR_NAMES",
    "FACTOR_REGISTRY",
    "compute_parent_factor_score",
    "compute_sector_percentile_rank",
    "percentile_rank_within",
    "register_factor",
    "write_factor_scores",
    "write_parent_scores",
]
