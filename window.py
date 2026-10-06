"""Find the game client window and bring it to the front, using ctypes."""

import ctypes
import time
from ctypes import wintypes

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

SW_RESTORE = 9


def make_dpi_aware():
    # Without this, Windows display scaling makes window coordinates
    # disagree with the screen pixels mss captures.
    PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)
    if not user32.SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2):
        user32.SetProcessDPIAware()


def find_window(title):
    """Handle of the window with this exact title, or None."""
    return user32.FindWindowW(None, title) or None


def is_foreground(hwnd):
    return user32.GetForegroundWindow() == hwnd


def bring_to_front(hwnd, settle_s):
    """Make the client the foreground window. Returns False if Windows refuses."""
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    # Windows blocks background programs from taking focus. Temporarily
    # sharing input state with the current foreground window lifts that block
    # without sending any keys.
    fg_thread = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
    our_thread = kernel32.GetCurrentThreadId()
    attached = fg_thread != our_thread and user32.AttachThreadInput(our_thread, fg_thread, True)
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(our_thread, fg_thread, False)
    time.sleep(settle_s)
    return is_foreground(hwnd)


def client_area(hwnd):
    """Screen rectangle of the window's client area (no title bar or border)."""
    rect = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    origin = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(origin))
    return {"left": origin.x, "top": origin.y,
            "width": rect.right, "height": rect.bottom}
