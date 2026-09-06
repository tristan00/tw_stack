import argparse
import csv
import gc
import html
import json
import os
from pathlib import Path
import sys
import threading
import time

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from advisor.mapgraph import optimize_greedy as O


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".%d.%d.tmp" % (os.getpid(), threading.get_ident()))
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    temporary.replace(path)


def export(study, out):
    status = json.loads((out / "status.json").read_text(encoding="utf-8"))
    trials = study.get_trials(deepcopy=False)
    ordered = sorted(trials, key=lambda t: (t.state.name != "COMPLETE",
                                            t.value if t.value is not None else float("inf"), t.number))
    params = sorted({key for trial in trials for key in trial.params})
    fields = ["rank", "trial", "status", "val_mse", "val_r2", "epochs", "seconds", "first_step_seconds",
              "graphs", "tensor_gib", "reason"] + params + ["effective_model_config"]
    rows = []
    rank = 0
    for trial in ordered:
        complete = trial.state.name == "COMPLETE"
        rank += int(complete)
        attrs = trial.user_attrs
        metrics = attrs.get("graph_metrics", attrs.get("graph_probe_metrics", {}))
        rows.append(dict(rank=rank if complete else "", trial=trial.number,
                         status=trial.state.name, val_mse=trial.value,
                         val_r2=attrs.get("r2"), epochs=attrs.get("epochs"),
                         seconds=round(trial.duration.total_seconds(), 2) if trial.duration else None,
                         first_step_seconds=attrs.get("first_step_seconds"),
                         graphs=metrics.get("graphs"), tensor_gib=metrics.get("tensor_gib"),
                         reason=attrs.get("reason", status.get("failure_reasons", {}).get(str(trial.number), "")), **trial.params,
                         effective_model_config=json.dumps(attrs.get("model_config", {}), sort_keys=True)))
    temporary = out / ("results.%d.csv.tmp" % os.getpid())
    with temporary.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(out / "results.csv")
    headers = "".join("<th>" + html.escape(field) + "</th>" for field in fields)
    body = "".join("<tr>" + "".join("<td>" + html.escape(str(row.get(field, "") if row.get(field) is not None else ""))
                                   + "</td>" for field in fields) + "</tr>" for row in rows)
    counts = {name: sum(t.state.name == name for t in trials)
              for name in ("COMPLETE", "PRUNED", "FAIL", "RUNNING")}
    page = ("<!doctype html><meta charset='utf-8'><meta http-equiv='refresh' content='30'>"
            "<title>GNN tuning trials</title>"
            "<style>body{font:14px system-ui;margin:24px}table{border-collapse:collapse}"
            "th,td{padding:8px;border-bottom:1px solid #ddd;text-align:left;white-space:nowrap}"
            "th{position:sticky;top:0;background:#eef2f6}tr:nth-child(even){background:#f7f9fb}"
            "td:last-child{white-space:normal;min-width:480px}</style>"
            "<h1>GNN tuning: all trials</h1>"
            f"<p>Study: {html.escape(status['study_name'])}. Status: {html.escape(status['state'])}. "
            f"Updated: {time.strftime('%Y-%m-%d %H:%M:%S %Z')}. Refreshes every 30 seconds.</p>"
            f"<p>{counts['COMPLETE']} scored, {counts['PRUNED']} skipped, {counts['FAIL']} failed, "
            f"{counts['RUNNING']} running.</p><p>Sorted by validation MSE, lowest first. "
            f"Skipped and failed trials follow scored trials. Window: {status['window']:,} campaigns. "
            f"Requested trials: {status['requested_trials']}. Fitting budget: {status['budget_s']} seconds "
            "per accepted trial.</p><p><a href='results.csv'>Download CSV</a></p>"
            "<table><thead><tr>" + headers + "</tr></thead><tbody>" + body + "</tbody></table>")
    temporary = out / ("results.%d.html.tmp" % os.getpid())
    temporary.write_text(page, encoding="utf-8")
    temporary.replace(out / "results.html")
    write_json(out / "trials.json", [dict(number=t.number, state=t.state.name,
                                           value=t.value, params=t.params, attrs=t.user_attrs) for t in trials])
    return len(trials)


def monitor(out, stop):
    import torch
    process = psutil.Process()
    with (out / "resources.jsonl").open("a", buffering=1, encoding="utf-8") as file:
        while not stop.wait(15):
            children = process.children(recursive=True)
            rss = process.memory_info().rss
            for child in children:
                try:
                    rss += child.memory_info().rss
                except psutil.NoSuchProcess:
                    pass
            available = psutil.virtual_memory().available
            committed, commit_limit = memory_commit()
            file.write(json.dumps(dict(ts=time.time(), pid=process.pid, children=[p.pid for p in children],
                                       tree_rss_gib=rss / 2**30, available_gib=available / 2**30,
                                       committed_gib=committed / 2**30, commit_limit_gib=commit_limit / 2**30,
                                       gpu_allocated_gib=torch.cuda.memory_allocated() / 2**30,
                                       gpu_reserved_gib=torch.cuda.memory_reserved() / 2**30)) + "\n")
            if available < 8 * 2**30:
                print("RESOURCE STOP: less than 8 GiB system memory available", flush=True)
                write_json(out / "resource_stop.json", dict(ts=time.time(), available_gib=available / 2**30))
                status_path = out / "status.json"
                if status_path.exists():
                    status = json.loads(status_path.read_text())
                    status.update(state="FAILED", ended=time.time(), error="Resource guard: less than 8 GiB available RAM")
                    write_json(status_path, status)
                for child in children:
                    try:
                        child.terminate()
                    except psutil.NoSuchProcess:
                        pass
                psutil.wait_procs(children, timeout=5)
                os._exit(3)


def memory_commit():
    import ctypes
    class MemoryStatus(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
            (key, ctypes.c_ulonglong) for key in (
                "total_physical", "available_physical", "total_page_file", "available_page_file",
                "total_virtual", "available_virtual", "available_extended_virtual")]
    status = MemoryStatus()
    status.length = ctypes.sizeof(status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        raise ctypes.WinError()
    return status.total_page_file - status.available_page_file, status.total_page_file


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--trials", type=int, default=100)
    parser.add_argument("--window", type=int, default=2000)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if args.trials < 1 or args.window < 1:
        parser.error("trials and window must be positive")
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=args.resume)
    previous = json.loads((out / "status.json").read_text()) if args.resume else None
    if previous:
        if (previous["requested_trials"], previous["window"]) != (args.trials, args.window):
            raise ValueError("Resume trial count and window must match the existing study")
        if psutil.pid_exists(previous["pid"]):
            process = psutil.Process(previous["pid"])
            if abs(process.create_time() - previous["started"]) < 10:
                raise RuntimeError("Existing study process is still alive")
    sys.stdout = (out / "run.log").open("a" if args.resume else "w", buffering=1, encoding="utf-8")
    sys.stderr = sys.stdout
    O.OUT_DIR, O.TRIALS_JSONL = str(out), str(out / "completed.jsonl")
    status = dict(pid=os.getpid(), started=time.time(), state="RUNNING", requested_trials=args.trials,
                  window=args.window, budget_s=300, study_name="gnn_greedy_" + O.STAMP, finished_trials=0)
    remaining = args.trials
    if previous:
        import optuna
        status["study_name"] = previous["study_name"]
        study = optuna.load_study(study_name=status["study_name"], storage=O._storage())
        if any(t.state.name == "RUNNING" for t in study.trials):
            raise RuntimeError("Existing study contains an unresolved running trial")
        status["failure_reasons"] = previous.get("failure_reasons", {})
        if previous.get("error") and study.trials and study.trials[-1].state.name == "FAIL":
            status["failure_reasons"][str(study.trials[-1].number)] = previous["error"]
        write_json(out / "status.json", status)
        status["finished_trials"] = export(study, out)
        remaining -= status["finished_trials"]
        print("STUDY_RESUME", status["study_name"], "remaining", remaining, flush=True)
    write_json(out / "status.json", status)
    stop = threading.Event()
    thread = threading.Thread(target=monitor, args=(out, stop), daemon=True)
    thread.start()
    def completed(study, trial):
        import torch
        gc.collect()
        torch.cuda.empty_cache()
        status["finished_trials"] = export(study, out)
        status["updated"] = time.time()
        write_json(out / "status.json", status)
        print("RESULTS_UPDATED", status["finished_trials"], flush=True)
    try:
        result = O.run(trials=remaining, budget_s=300, window=args.window,
                       study_patience=None, on_trial=completed,
                       study_name=status["study_name"] if args.resume else None)
        status.update(state="COMPLETE", exit_code=result, ended=time.time())
    except BaseException as error:
        status.update(state="FAILED", error=repr(error), ended=time.time())
        raise
    finally:
        stop.set()
        thread.join(timeout=5)
        write_json(out / "status.json", status)


if __name__ == "__main__":
    main()
