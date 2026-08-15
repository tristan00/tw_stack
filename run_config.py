from __future__ import annotations


RUN = {
    "campaigns": 500,
    "turns": 20,
    "retrain_every": 0,
    "retrain_first": False,
    "strategies": "marwil_gnn=0.4,greedy_catboost=0.4,random=0.1,ruleset=0.1",
    "ruleset": "probe_gaps",
    "factions": "all",
    "campaign": "Realm of Chaos=0.5,Immortal Empires=0.5",
    "presave_radius": 150,
    "dev": True,
}


if __name__ == "__main__":
    import json
    print(json.dumps(RUN, indent=1))
