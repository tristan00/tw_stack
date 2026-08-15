import bake_starts as S
from unittest import mock


class _Bus:
    def __init__(self, calls):
        self.calls = calls

    def send(self, command, payload, timeout=None):
        self.calls.append(("bus", command, payload))
        return {"result": "ok"}


def test_prepare_for_trim_ends_tour_and_clears_twice():
    calls = []
    bus = _Bus(calls)

    def clear(_bus, log=print, rounds=3):
        calls.append(("clear",))
        return []

    with mock.patch.object(S, "clear_screen", clear):
        S.prepare_for_trim(bus, log=lambda _line: None)
    assert calls == [
        ("bus", "eval", S._LUA_END_CAMPAIGN_TOUR),
        ("clear",),
        ("bus", "eval", S._LUA_END_CAMPAIGN_TOUR),
        ("clear",),
    ]


def test_end_campaign_tour_does_not_conflate_blocker_or_ui_restore_work():
    calls = []
    S.end_campaign_tour(_Bus(calls), log=lambda _line: None)

    payload = calls[0][2]
    assert "skip_all_campaign_cutscenes" in payload
    assert "dismiss_advice" not in payload
    assert "steal_user_input" not in payload
    assert "ToggleCinematicBorders" not in payload
