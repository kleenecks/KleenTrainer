"""Stage 0 smoke test: find the client window, bring it to the front and
capture one frame of it.

Sends no input. Usage: python smoke_test.py [config.toml]
"""

import ctypes
import sys
import time
import tomllib
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

import mss
import mss.tools

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
    hwnd = user32.FindWindowW(None, title)
    if not hwnd:
        sys.exit(f'No window titled "{title}". Is the client running?')
    return hwnd


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
    return user32.GetForegroundWindow() == hwnd


def client_area(hwnd):
    """Screen rectangle of the window's client area (no title bar or border)."""
    rect = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    origin = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(origin))
    return {"left": origin.x, "top": origin.y,
            "width": rect.right, "height": rect.bottom}


def main():
    config_path = Path(sys.argv[1] if len(sys.argv) > 1 else "config.toml")
    with config_path.open("rb") as f:
        config = tomllib.load(f)

    make_dpi_aware()
    hwnd = find_window(config["window_title"])
    # mss copies screen pixels, so the client must be on top to be captured.
    if not bring_to_front(hwnd, config["focus_settle_s"]):
        sys.exit("Windows did not let the client come to the front. "
                 "Click the client and run again.")
    region = client_area(hwnd)

    run_dir = Path("runs") / datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    run_dir.mkdir(parents=True)
    out = run_dir / "frame.png"

    with mss.MSS() as sct:
        shot = sct.grab(region)
    mss.tools.to_png(shot.rgb, shot.size, output=str(out))

    print(f"Client area: {region['width']}x{region['height']} "
          f"at ({region['left']}, {region['top']})")
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
