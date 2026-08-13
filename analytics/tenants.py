from __future__ import annotations

"""The registry: every table the analytics layer maintains.

A tenant that is not in this list is not built, not backfilled, and not checked. Adding one
here is the whole registration step -- `store.order_tenants` sorts them so a rollup runs
after the facts it summarises, and `runner.py` folds them in that order.

FACT tenants hold one row per source id. ROLLUP tenants hold aggregates derived from fact
tables and declare `DEPENDS_ON`, which is what makes a metric's formula change invalidate
every summary built on it instead of leaving a stale one sitting beside fresh facts.
"""

from analytics import agreement_rollup, campaign_growth, generations, growth_rollup
from analytics import model_agreement

FACTS = (model_agreement, campaign_growth, generations)
ROLLUPS = tuple(agreement_rollup.TENANTS) + tuple(growth_rollup.TENANTS)

TENANTS = list(FACTS) + list(ROLLUPS)
