import importlib.abc
import os
from pathlib import Path
import sys
import tempfile
import threading


class Isolation(importlib.abc.MetaPathFinder):
    def __init__(self, root):
        self.active = False
        self.temp = tempfile.TemporaryDirectory(prefix="tw-pytest-")
        self.roots = (Path(self.temp.name).resolve(), (root / "tests" / ".results").resolve())
        self.previous_temp = tempfile.tempdir
        self.previous_bytecode = sys.dont_write_bytecode
        tempfile.tempdir = self.temp.name
        sys.dont_write_bytecode = True
        self.active = True
        sys.addaudithook(self.audit)
        sys.meta_path.insert(0, self)
        self.timer = threading.Timer(20, self.timeout)
        self.timer.daemon = True
        self.timer.start()

    def timeout(self):
        os.write(2, b"\nDefault pytest exceeded its 20-second deadline; terminating test process (124).\n")
        os._exit(124)

    def find_spec(self, fullname, path=None, target=None):
        if self.active and fullname.split(".")[0] in {"psycopg", "psycopg2", "torch", "catboost", "tensorflow", "cupy"}:
            raise RuntimeError("Default tests cannot import database or model frameworks: " + fullname)

    def check_write(self, path):
        if isinstance(path, int) or path is None:
            return
        resolved = Path(os.fsdecode(path)).resolve()
        if resolved == Path(os.devnull).resolve():
            return
        if not any(resolved == root or root in resolved.parents for root in self.roots):
            raise RuntimeError("Default tests cannot write outside test-owned directories: " + str(resolved))

    def audit(self, event, args):
        if not self.active:
            return
        if event in {"socket.connect", "socket.bind", "socket.getaddrinfo", "sqlite3.connect", "subprocess.Popen", "os.system", "os.startfile", "os.posix_spawn", "os.fork", "os.exec", "os.spawn"}:
            raise RuntimeError("Default tests cannot access external services or launch processes: " + event)
        if event == "open":
            path, mode, flags = args
            if (mode and any(c in mode for c in "wax+")) or flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
                self.check_write(path)
        elif event in {"os.remove", "os.rmdir", "os.mkdir", "os.chmod", "os.utime", "os.truncate"}:
            self.check_write(args[0])
        elif event in {"os.rename", "os.link", "os.symlink"}:
            self.check_write(args[0])
            self.check_write(args[1])

    def close(self):
        self.timer.cancel()
        self.active = False
        sys.meta_path.remove(self)
        tempfile.tempdir = self.previous_temp
        sys.dont_write_bytecode = self.previous_bytecode
        self.temp.cleanup()
