from __future__ import annotations


from analytics import agreement_rollup, campaign_growth, gamestate, generations
from analytics import growth_rollup, item_events, model_agreement, state_facts

FACTS = (model_agreement, campaign_growth, generations) \
    + tuple(state_facts.TENANTS) + tuple(item_events.TENANTS) \
    + tuple(gamestate.TENANTS)
ROLLUPS = tuple(agreement_rollup.TENANTS) + tuple(growth_rollup.TENANTS)

TENANTS = list(FACTS) + list(ROLLUPS)
