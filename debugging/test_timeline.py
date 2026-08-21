from __future__ import annotations

import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common
from debugging import timeline as TL


def _epoch(text):
    return datetime.datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%f").timestamp()


T0 = _epoch("2026-08-21T12:00:00.000")


def test_parse_when_forms():
    assert TL.parse_when("-90s", T0) == T0 - 90
    assert TL.parse_when("-2m", T0) == T0 - 120
    assert TL.parse_when("+1h", T0) == T0 + 3600
    assert TL.parse_when("now", T0) == T0
    assert TL.parse_when(str(T0), T0) == T0
    assert TL.parse_when("2026-08-21T12:00:00.500", T0) == T0 + 0.5
    assert TL.parse_when("12:00:01", T0) == T0 + 1


def test_parse_when_rejects_ambiguity():
    for bad in ("-90", "12", "yesterday"):
        try:
            TL.parse_when(bad, T0)
        except SystemExit:
            continue
        raise AssertionError("%r should not parse as a time" % bad)


def test_stamped_log_keeps_window_and_continuations(tmp_path):
    p = tmp_path / "session_x.log"
    p.write_text(
        "2026-08-21T11:59:59.500 before the window\n"
        "2026-08-21T12:00:00.250 == TURN 3.0 ==\n"
        "2026-08-21T12:00:00.750 WAIT settle 0.50s ok=True\n"
        "    a continuation line\n"
        "2026-08-21T12:00:09.000 after the window\n", encoding="utf-8")
    rows = TL.from_stamped_log(str(p), T0, T0 + 1, "session")
    assert [r[2] for r in rows] == ["turn", "wait", "log"]
    assert rows[0][0] == T0 + 0.25
    assert rows[2][0] == T0 + 0.75
    assert "continuation" in rows[2][3]


def test_jsonl_reads_epoch_and_anchored_rows(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text(
        json.dumps({"ts": T0 + 0.5, "kind": "panel", "root": "x"}) + "\n"
        + json.dumps({"t": 2.0, "kind": "shot", "n": 4}) + "\n"
        + json.dumps({"kind": "no_time_at_all"}) + "\n", encoding="utf-8")
    rows = TL.from_jsonl(str(p), T0, T0 + 3, "s", anchor=T0)
    assert [r[2] for r in rows] == ["panel", "shot"]
    assert rows[1][0] == T0 + 2.0
    assert TL.from_jsonl(str(p), T0, T0 + 1, "s", anchor=None) == [
        (T0 + 0.5, "s", "panel", "root=x")]


def test_screens_use_the_dump_timestamp(tmp_path):
    d = tmp_path / "screens"
    d.mkdir()
    (d / ("%d_predismiss_thing.json" % int((T0 + 0.4) * 1000))).write_text(
        json.dumps({"ts": T0 + 0.4, "why": "predismiss", "root": "thing",
                    "roots": ["hud_campaign"], "nodes": [{"path": "a"}]}), encoding="utf-8")
    rows = TL.from_screens(str(d), T0, T0 + 1)
    assert len(rows) == 1
    assert rows[0][0] == T0 + 0.4
    assert rows[0][2] == "panel"
    assert "predismiss" in rows[0][3] and "nodes=1" in rows[0][3]


def _fake_run(tmp_path, monkeypatch):
    run = tmp_path / "run"
    logs = tmp_path / "logs" / "advisor"
    services = tmp_path / "logs" / "services"
    dev = tmp_path / "logs" / "dev"
    screens = tmp_path / "screens"
    for d in (run, logs, services, dev, screens):
        d.mkdir(parents=True)
    (logs / "session_a.log").write_text(
        "2026-08-21T12:00:00.100 CAMPAIGN 7/1000  (up to 20 turns)\n"
        "2026-08-21T12:00:00.300 == TURN 1.0 ==\n"
        "2026-08-21T12:00:00.900 WAIT end_turn_settle 0.40s ok=True\n", encoding="utf-8")
    (services / "manager_a.log").write_text(
        "2026-08-21T12:00:00.500 RECORDING -> run\n", encoding="utf-8")
    (run / "meta.json").write_text(json.dumps({"t0_epoch": T0}), encoding="utf-8")
    (run / "trace.jsonl").write_text(
        json.dumps({"ts": T0 + 0.7, "kind": "launcher", "stage": "execute_done"}) + "\n",
        encoding="utf-8")
    (run / "events.jsonl").write_text(
        json.dumps({"t": 0.6, "kind": "shot", "n": 1}) + "\n", encoding="utf-8")
    monkeypatch.setattr(common, "RUN_DIR", str(run))
    monkeypatch.setattr(common, "LOGS_ADVISOR", str(logs))
    monkeypatch.setattr(common, "LOGS_SERVICES", str(services))
    monkeypatch.setattr(common, "LOGS_DEV", str(dev))
    monkeypatch.setattr(common, "SCREEN_DUMP_DIR", str(screens))
    return run


def test_build_writes_one_ordered_file(tmp_path, monkeypatch):
    _fake_run(tmp_path, monkeypatch)
    out = tmp_path / "out" / "timeline.txt"
    path, n, dropped, t0, t1 = TL.build(start="2026-08-21T12:00:00.000",
                                        end="2026-08-21T12:00:01.000", out=str(out))
    assert path == common.native(str(out)) and os.path.exists(path)
    assert n == 6 and dropped == 0
    body = open(path, encoding="utf-8").read().splitlines()
    head = [l for l in body if l.startswith("#")]
    rows = [l for l in body if not l.startswith("#")]
    assert any("window 2026-08-21T12:00:00.000" in l for l in head)
    assert any("kinds " in l for l in head)
    assert len(rows) == 6
    assert [l.split()[0] for l in rows] == sorted(l.split()[0] for l in rows)
    assert "campaign" in rows[0] and "12:00:00.100" in rows[0]
    assert any("shot" in l for l in rows)
    assert any("manager" in l for l in rows)


def test_build_defaults_to_the_last_campaign(tmp_path, monkeypatch):
    _fake_run(tmp_path, monkeypatch)
    out = tmp_path / "out" / "t2.txt"
    path, n, dropped, t0, t1 = TL.build(end="2026-08-21T12:00:01.000", out=str(out))
    assert t0 == T0 + 0.1
    assert n == 6


def test_limit_is_announced_not_silent(tmp_path, monkeypatch):
    _fake_run(tmp_path, monkeypatch)
    out = tmp_path / "out" / "t3.txt"
    path, n, dropped, _, _ = TL.build(start="2026-08-21T12:00:00.000",
                                      end="2026-08-21T12:00:01.000", out=str(out), limit=2)
    assert n == 2 and dropped == 4
    assert "TRUNCATED" in open(path, encoding="utf-8").read()


def test_only_and_exclude_filter_kinds(tmp_path, monkeypatch):
    _fake_run(tmp_path, monkeypatch)
    out = tmp_path / "out" / "t4.txt"
    path, n, _, _, _ = TL.build(start="2026-08-21T12:00:00.000",
                                end="2026-08-21T12:00:01.000", out=str(out),
                                only=["wait"])
    assert n == 1
    path, n, _, _, _ = TL.build(start="2026-08-21T12:00:00.000",
                                end="2026-08-21T12:00:01.000", out=str(out),
                                exclude=["wait", "shot"])
    assert n == 4
