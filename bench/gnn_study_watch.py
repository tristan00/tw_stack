import argparse
from collections import deque
import json
from pathlib import Path
import subprocess
import sys
import time

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from advisor.mapgraph import optimize_greedy as O
from bench.gnn_study_run import export, write_json


def process_for(status, out):
    try:
        process = psutil.Process(status["pid"])
        command = " ".join(process.cmdline()).replace("\\", "/").lower()
        if (abs(process.create_time() - status["started"]) < 10
                and "gnn_study_run.py" in command and out.name.lower() in command):
            return process
    except psutil.NoSuchProcess:
        pass
    return None


def main():
    import optuna
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--recover", action="store_true")
    action.add_argument("--restart", action="store_true")
    args = parser.parse_args()
    out = args.output.resolve()
    status = json.loads((out / "status.json").read_text())
    process = process_for(status, out)
    if args.restart and process:
        owned = process.children(recursive=True) + [process]
        for child in owned:
            try:
                child.terminate()
            except psutil.NoSuchProcess:
                pass
        _, alive = psutil.wait_procs(owned, timeout=10)
        if alive:
            raise RuntimeError("Study processes did not exit; refusing another launch")
        status.update(state="FAILED", error="Interrupted for user-requested maintenance relaunch", ended=time.time())
        write_json(out / "status.json", status)
        process = None
    study = optuna.load_study(study_name=status["study_name"], storage=O._storage())
    if process is None and status["state"] not in ("CANCELLED", "COMPLETE"):
        for trial in study.trials:
            if trial.state.name == "RUNNING":
                reason = status.get("error", "Study process exited unexpectedly")
                study._storage.set_trial_user_attr(trial._trial_id, "reason", reason)
                study.tell(trial.number, state=optuna.trial.TrialState.FAIL)
        status.update(state="FAILED", finished_trials=len(study.trials))
        write_json(out / "status.json", status)
    export(study, out)
    if process is None and (args.recover or args.restart) and status["state"] == "FAILED":
        if len(study.trials) >= status["requested_trials"]:
            status.update(state="COMPLETE", ended=time.time())
            write_json(out / "status.json", status)
        else:
            resources = out / "resources.jsonl"
            last = {}
            if resources.exists():
                with resources.open() as file:
                    lines = deque(file, maxlen=1)
                last = json.loads(lines[0]) if lines else {}
            if any(psutil.pid_exists(pid) for pid in last.get("children", [])):
                raise RuntimeError("Recorded workers still exist; inspect their identities before recovery")
            root = Path(__file__).resolve().parents[1]
            with (out / "launch.log").open("a") as log:
                subprocess.Popen([str(root / ".venv/Scripts/python.exe"), "-u", "bench/gnn_study_run.py",
                                  str(out), "--trials", str(status["requested_trials"]),
                                  "--window", str(status["window"]), "--study-patience",
                                  str(status.get("study_patience", 0)), "--budget",
                                  str(status["budget_s"]), "--resume"],
                                 cwd=root, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                status = json.loads((out / "status.json").read_text())
                process = process_for(status, out)
                if process:
                    break
                time.sleep(0.5)
            if process is None:
                raise RuntimeError("Recovery launch did not produce a verified study process; inspect launch.log")
    export(study, out)
    trials = study.get_trials()
    finished = sum(t.state.is_finished() for t in trials)
    scored = [t for t in trials if t.state.name == "COMPLETE"]
    summary = dict(state=status["state"], process_alive=process is not None, pid=status["pid"],
                   finished=finished, requested=status["requested_trials"],
                   counts={name: sum(t.state.name == name for t in trials)
                           for name in ("COMPLETE", "PRUNED", "FAIL", "RUNNING")},
                   log_age_seconds=round(time.time() - (out / "run.log").stat().st_mtime, 1),
                   best_mse=min((t.value for t in scored), default=None), table=str(out / "results.html"))
    write_json(out / "watch.json", dict(checked=time.time(), **summary))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
