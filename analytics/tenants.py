from __future__ import annotations

from analytics import agreement_rollup, item_events, model_agreement, state_facts

FACTS = (model_agreement,) + tuple(state_facts.TENANTS) + tuple(item_events.TENANTS)
ROLLUPS = tuple(agreement_rollup.TENANTS)

TENANTS = list(FACTS) + list(ROLLUPS)
