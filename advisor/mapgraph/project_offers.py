from __future__ import annotations


def _match(rows, field, value):
    return next(r for r in rows if str(r.get(field)) == str(value))


def offer_params(rec, entity, at, key, slot, action_key=None, ability=None):
    st, world = entity["state"], rec["world"]
    campaign = next((e["state"] for e in rec["entities"]
                     if e["context_kind"] == "campaign"), {})
    cid = entity["context_id"]
    if at in ("noop", "end_turn", "stance", "skills", "rites"):
        return {}
    if at == "move":
        x, y = key.removeprefix("xy:").split(",")
        return {"x": float(x), "y": float(y)}
    if at == "leave_garrison":
        if not st.get("garrisoned"):
            return {}
        points = list(zip(st["move_x"] or [], st["move_y"] or []))
        if not points:
            return {}
        if st.get("x") is not None and st.get("y") is not None:
            points.sort(key=lambda p: (p[0] - st["x"]) ** 2 + (p[1] - st["y"]) ** 2)
        return dict(zip(("x", "y"), points[0]))
    if at == "diplomacy":
        return {"faction": key.split(":", 1)[0]}
    if at == "recruit_unit":
        return {"cost": _match(st.get("recruitable", []), "key", key).get("cost")}
    if at in ("raise_dead", "recruit_ror", "recruit_blessed", "recruit_imperial"):
        row = _match(st.get("merc_pools", {}).get(at, []), "key", key)
        return {"cost": row["cost"], "pool_avail": row["avail"]}
    if at == "cancel_recruit":
        qi = int(key.rsplit("@", 1)[1])
        return {"queue_index": qi, "turns_left": st["pending_queue"][qi]["turns_left"]}
    if at == "research":
        return {"cost": _match(campaign.get("tech", []), "key", key).get("cost")}
    if at in ("recruit_lord", "recruit_hero", "edict"):
        return {"region": cid}
    if at in ("items", "item_unequip"):
        rows = campaign.get("anc_pool", []) if at == "items" else st.get("equipped", [])
        row = next(r for r in rows if str(r.get("key") or r.get("name")) == key)
        return {"item_key": row["key"]}
    if at == "building":
        row = next(r for r in st.get("buildable", []) if r["key"] == key
                   and (slot is None or r["slot_index"] == slot))
        return {"slot_index": row["slot_index"], "cost": row["cost"]}
    if at in ("building_repair", "building_dismantle"):
        idx = int(key.rsplit("@", 1)[1])
        row = _match(st.get("slot_states", []), "index", idx)
        out = {"region": cid, "slot_index": row["index"], "building_key": row["key"]}
        if at == "building_repair":
            out["repair_cost"] = row["repair_cost"]
        return out
    if at == "horde_building":
        row = next(r for r in st.get("horde_slots", [])
                   if "%s@%s" % (r["slot_id"], r["key"]) == key)
        return {"slot_id": row["slot_id"], "slot_index": row["slot_index"],
                "building_key": row["key"]}
    if at == "attack_army":
        row = _match([r for r in world.get("hostiles", []) if r["kind"] == "army"],
                     "cqi", key.removeprefix("cqi:"))
        return {"target_cqi": row["cqi"], "target_faction": row.get("faction"),
                "x": row.get("x"), "y": row.get("y")}
    if at in ("attack_settlement", "colonize", "garrison"):
        rows = ([r for r in world.get("hostiles", []) if r["kind"] == "settlement"]
                if at == "attack_settlement" else
                world.get("ruins" if at == "colonize" else "settlements", []))
        row = _match(rows, "region", key.removeprefix("settlement:"))
        out = {"x": row.get("x"), "y": row.get("y")}
        if at == "attack_settlement":
            out["target_faction"] = row.get("faction")
        return out
    if at == "hero_action":
        if action_key is None or ability != "assist_army":
            raise ValueError("unsupported hero action %s" % key)
        target = key.split("@", 1)[1]
        row = _match(world.get("armies", []), "cqi", target.removeprefix("cqi:"))
        return {"action_key": action_key, "ability": ability, "target_cqi": row["cqi"],
                "x": row.get("x"), "y": row.get("y")}
    raise ValueError("unsupported graph action %s" % at)
