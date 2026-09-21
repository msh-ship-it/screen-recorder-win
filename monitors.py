"""Enumerate monitors so a single screen can be recorded instead of the whole desktop."""
import ctypes
from ctypes import wintypes


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT), ("rcWork", RECT),
                ("dwFlags", wintypes.DWORD), ("szDevice", wintypes.WCHAR * 32)]


MONITORINFOF_PRIMARY = 1
MONITOR_ENUM_PROC = ctypes.WINFUNCTYPE(
    ctypes.c_int, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(RECT), wintypes.LPARAM
)


DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)

ctypes.windll.user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
ctypes.windll.user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p


def list_monitors() -> list[dict]:
    """Left-to-right list of {name, x, y, width, height, primary} in real pixels."""
    monitors = []
    # Without this the coordinates come back scaled (e.g. 1536x864 for a 1920x1080
    # screen at 125%), while ffmpeg grabs real pixels. Thread-scoped so the Tk UI
    # keeps its normal scaling.
    previous_context = ctypes.windll.user32.SetThreadDpiAwarenessContext(
        DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
    )

    def callback(hmonitor, hdc, rect, lparam):
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if ctypes.windll.user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
            r = info.rcMonitor
            monitors.append({
                "name": info.szDevice,
                "x": r.left,
                "y": r.top,
                # x264 needs even dimensions for yuv420p.
                "width": (r.right - r.left) // 2 * 2,
                "height": (r.bottom - r.top) // 2 * 2,
                "primary": bool(info.dwFlags & MONITORINFOF_PRIMARY),
            })
        return 1

    try:
        ctypes.windll.user32.EnumDisplayMonitors(None, None, MONITOR_ENUM_PROC(callback), 0)
    finally:
        if previous_context:
            ctypes.windll.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(previous_context))
    monitors.sort(key=lambda m: (m["x"], m["y"]))
    return monitors


def find_monitor(name: str) -> dict | None:
    return next((m for m in list_monitors() if m["name"] == name), None)


def monitor_at_cursor() -> dict | None:
    point = wintypes.POINT()
    previous_context = ctypes.windll.user32.SetThreadDpiAwarenessContext(
        DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
    )
    try:
        ok = ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
    finally:
        if previous_context:
            ctypes.windll.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(previous_context))
    if not ok:
        return None
    for monitor in list_monitors():
        if (monitor["x"] <= point.x < monitor["x"] + monitor["width"]
                and monitor["y"] <= point.y < monitor["y"] + monitor["height"]):
            return monitor
    return None
