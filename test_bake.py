import bake as S
import presaves as P
import pytest


def test_save_name_round_trips():
    name = P.save_name("wh3_main_combi", "wh2_main_hef_nagarythe", 150, 1)
    meta = P.parse_save_name(name + ".save")
    assert meta == {"campaign_map": "wh3_main_combi",
                    "faction": "wh2_main_hef_nagarythe",
                    "radius": 150.0, "turn": 1}
    assert P.parse_save_name("autosave_1.save") is None


class _Bus:
    def __init__(self, reply):
        self.reply = reply

    def send(self, command, payload, timeout=None):
        return self.reply


def test_trim_refuses_a_partial_disarm():
    bus = _Bus({"n_killed": 90, "n_kept": 10, "n_unplaced": 0, "n_failed": 3,
                "failed": ["a", "b", "c"]})
    with pytest.raises(RuntimeError, match="disarming failed on 3"):
        S.trim(bus, 150)


def test_trim_refuses_a_countless_result():
    with pytest.raises(RuntimeError, match="trim returned no n_kept"):
        S.trim(_Bus({"n_killed": 90, "n_unplaced": 0, "n_failed": 0}), 150)
