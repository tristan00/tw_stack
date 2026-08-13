from __future__ import annotations


from analytics import agreement_rollup, campaign_growth, generations, growth_rollup
from analytics import model_agreement

FACTS = (model_agreement, campaign_growth, generations)
ROLLUPS = tuple(agreement_rollup.TENANTS) + tuple(growth_rollup.TENANTS)

TENANTS = list(FACTS) + list(ROLLUPS)
