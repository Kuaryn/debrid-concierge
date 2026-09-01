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


def already_running(name: str) -> bool:
    if os.name != "nt":
        return False
    ctypes.windll.kernel32.CreateMutexW(None, False, name)
    # 183 = ERROR_ALREADY_EXISTS: another process holds this name
    return ctypes.windll.kernel32.GetLastError() == 183
