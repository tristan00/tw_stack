import time


def remaining(deadline):
    seconds = deadline - time.perf_counter()
    if seconds <= 0:
        raise TimeoutError("Trial wall-clock budget exhausted")
    return seconds
