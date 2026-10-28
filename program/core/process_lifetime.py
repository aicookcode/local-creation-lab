"""Keep spawned model/encoder processes inside their Windows owner's lifetime."""
import ctypes
import os
import threading

_handle = None
_attempted = False
_mutex = threading.Lock()


def protect_children(report=lambda line: None):
    """Windows Job Object, without CPU/memory limits or scheduling changes.

    Keep the non-inheritable job handle alive until this process exits. If the
    GUI/automation process crashes, the OS terminates its descendant workers.
    Closing a normal work operation does NOT close this handle.
    """
    global _handle, _attempted
    if os.name != 'nt':
        return False
    with _mutex:
        if _attempted:
            return _handle is not None
        _attempted = True
        from ctypes import wintypes as wt

        class Limits(ctypes.Structure):
            _fields_ = [('PerProcessUserTimeLimit',ctypes.c_longlong),('PerJobUserTimeLimit',ctypes.c_longlong),
                        ('LimitFlags',wt.DWORD),('MinimumWorkingSetSize',ctypes.c_size_t),('MaximumWorkingSetSize',ctypes.c_size_t),
                        ('ActiveProcessLimit',wt.DWORD),('Affinity',ctypes.c_size_t),('PriorityClass',wt.DWORD),('SchedulingClass',wt.DWORD)]

        class IO(ctypes.Structure):
            _fields_ = [(name,ctypes.c_ulonglong) for name in ('ReadOperationCount','WriteOperationCount','OtherOperationCount','ReadTransferCount','WriteTransferCount','OtherTransferCount')]

        class Extended(ctypes.Structure):
            _fields_ = [('BasicLimitInformation',Limits),('IoInfo',IO),('ProcessMemoryLimit',ctypes.c_size_t),
                        ('JobMemoryLimit',ctypes.c_size_t),('PeakProcessMemoryUsed',ctypes.c_size_t),('PeakJobMemoryUsed',ctypes.c_size_t)]

        api=ctypes.WinDLL('kernel32',use_last_error=True)
        api.CreateJobObjectW.argtypes=[ctypes.c_void_p,wt.LPCWSTR];api.CreateJobObjectW.restype=wt.HANDLE
        api.SetInformationJobObject.argtypes=[wt.HANDLE,ctypes.c_int,ctypes.c_void_p,wt.DWORD];api.SetInformationJobObject.restype=wt.BOOL
        api.AssignProcessToJobObject.argtypes=[wt.HANDLE,wt.HANDLE];api.AssignProcessToJobObject.restype=wt.BOOL
        api.GetCurrentProcess.restype=wt.HANDLE;api.CloseHandle.argtypes=[wt.HANDLE]
        handle=api.CreateJobObjectW(None,None)
        info=Extended();info.BasicLimitInformation.LimitFlags=0x2000 # KILL_ON_JOB_CLOSE
        if handle and api.SetInformationJobObject(handle,9,ctypes.byref(info),ctypes.sizeof(info)) and api.AssignProcessToJobObject(handle,api.GetCurrentProcess()):
            _handle=handle
            return True
        error=ctypes.get_last_error()
        if handle:api.CloseHandle(handle)
        report('当前系统未启用子进程退出保护（系统错误'+str(error)+'）；制作仍可运行，异常退出后请检查残留任务。')
        return False
