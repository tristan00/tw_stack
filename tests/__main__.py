import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


def main():
    root = Path(__file__).resolve().parents[1]
    started = time.perf_counter()
    environment = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHONDONTWRITEBYTECODE="1",
                       OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1")
    if any(arg.split("=", 1)[0] in {"--run-adhoc", "--run-integration", "--run-gpu-tests"} for arg in sys.argv[1:]):
        raise SystemExit("Use pytest directly for explicit ad hoc checks; this command runs the safe suite only.")
    temporary = tempfile.TemporaryDirectory(prefix="tw-pytest-run-")
    environment.update(TMP=temporary.name, TEMP=temporary.name)
    process = subprocess.Popen([sys.executable, "-m", "pytest", *sys.argv[1:]], cwd=root, env=environment)
    try:
        code = process.wait(timeout=max(0, 20 - (time.perf_counter() - started)))
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        code = 124
    except KeyboardInterrupt:
        process.kill()
        process.wait()
        code = 130
    finally:
        temporary.cleanup()
    elapsed = time.perf_counter() - started
    result = {"exit_code": code, "process_wall_seconds": elapsed, "budget_seconds": 20,
              "timed_out": code == 124}
    output = root / "tests" / ".results"
    output.mkdir(parents=True, exist_ok=True)
    (output / "process.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print("Entire pytest process: %.3fs / 20s; exit %d" % (elapsed, code), flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
