from __future__ import annotations


RUN = {
    "campaigns": 1000,
    "turns": 20,
    "retrain_every": 0,
    "retrain_first": False,
    "strategies": "greedy_catboost=0.3,greedy_gnn=0.3,random=0.4",
    "interrupt_strategies": "greedy_catboost=0.8,random=0.2",
    "factions": "all",
    "presave_radius": 150,
    "ucb": 1.0,
    "dev": True,
}


if __name__ == "__main__":
    import json
    print(json.dumps(RUN, indent=1))
