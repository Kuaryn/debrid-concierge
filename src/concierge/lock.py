"""Named mutex: one worker process writes jobs.json at a time."""

import contextlib
import ctypes
import os


@contextlib.contextmanager
def worker_lock():
    if os.name != "nt":
        yield  # no detached workers off windows; CI stays lock-free
        return
    k32 = ctypes.windll.kernel32
    h = k32.CreateMutexW(None, False, "debrid-concierge-worker")
    k32.WaitForSingleObject(h, 0xFFFFFFFF)  # INFINITE
    try:
        yield
    finally:
        k32.ReleaseMutex(h)
        k32.CloseHandle(h)
