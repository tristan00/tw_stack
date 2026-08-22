from __future__ import annotations


RUN = {
    "campaigns": 1000,
    "turns": 20,
    "retrain_every": 100,
    "retrain_first": True,
    "strategies": "greedy_catboost=0.2,greedy_gnn=0.2,marwil_gnn=0.2,random=0.4",
    "interrupt_strategies": "greedy_catboost=0.5,random=0.5",
    "ruleset": None,
    "factions": "all",
    "presave_radius": 150,
    "ucb": 2.0,
    "dev": True,
}


if __name__ == "__main__":
    import json
    print(json.dumps(RUN, indent=1))
