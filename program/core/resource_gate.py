"""One heavy operation at a time across the public GUI and automation entries.

The OS owns the file lock: a crashed owner cannot leave a permanent lock.
No inference settings, process priorities or thread counts are modified.
"""
from contextlib import contextmanager
from pathlib import Path
from studio_paths import CACHE
import os
import threading
import time

ROOT = Path(__file__).resolve().parent.parent
_threads = threading.RLock()
_local = threading.local()


def _lock(handle, acquire):
    handle.seek(0)
    if os.name == 'nt':
        import msvcrt
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK if acquire else msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB if acquire else fcntl.LOCK_UN)


@contextmanager
def production_slot(label, report=lambda line: None, *, directory=None, timeout=None):
    """Wait off the UI thread. Re-entry by the same thread is safe."""
    if getattr(_local, 'depth', 0):
        _local.depth += 1
        try:
            yield
        finally:
            _local.depth -= 1
        return
    directory = Path(directory) if directory else CACHE / '任务调度'
    directory.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    next_report = started + .4
    local_owned = False
    file_owned = False
    handle = None
    waited = False

    def waiting():
        nonlocal next_report, waited
        now = time.monotonic()
        if timeout is not None and now - started >= timeout:
            raise TimeoutError('另一个制作任务仍在运行，尚未取得执行位置。')
        if now >= next_report:
            waited = True
            report('等待其他制作任务结束；当前素材已保留，尚未加载模型。')
            next_report = now + 15

    try:
        while not _threads.acquire(timeout=.2):
            waiting()
        local_owned = True
        # Keep the inode and the lock file, even after completion. Deleting an
        # unlocked pathname could create two independent locks in a race.
        handle = (directory / 'production.lock').open('a+b')
        handle.seek(0, 2)
        if not handle.tell():
            handle.write(b'0')
            handle.flush()
        while True:
            try:
                _lock(handle, True)
                file_owned = True
                break
            except OSError as exc:
                if exc.errno not in (11, 13, 36):
                    raise
                waiting()
                time.sleep(.2)
        _local.depth = 1
        from process_lifetime import protect_children
        protect_children(report)
        if waited:
            report('已取得执行位置，开始' + str(label) + '。')
        yield
    finally:
        _local.depth = 0
        if file_owned:
            _lock(handle, False)
        if handle is not None:
            handle.close()
        if local_owned:
            _threads.release()
