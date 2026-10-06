"""Debug overlays. Later stages draw their detections on them.

Overlay: a separate OpenCV window showing a scaled copy of the frame. Used
for replay, and for live mode when there is room beside the client.

GameOverlay: a see-through window drawn directly over the client. It is
excluded from screen capture, so the bot never sees its own drawings. Clicks
and keys pass through it, and it never takes focus. It hides while the client
is not in front. It cannot be clicked, so quit with Ctrl+C in the terminal.
"""

import ctypes
import time
import tkinter as tk

import cv2

import window

WINDOW_NAME = "KleenTrainer overlay"


class Overlay:
    def __init__(self, scale):
        self.scale = scale
        cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_AUTOSIZE)

    def show(self, frame, lines, rects=(), points=()):
        """Show frame scaled down, with each string in lines as status text.

        rects are Regions and points are (x, y), both in frame pixels; each
        is drawn in green.
        """
        image = cv2.resize(frame, None, fx=self.scale, fy=self.scale,
                           interpolation=cv2.INTER_AREA)
        s = self.scale
        for r in rects:
            cv2.rectangle(image, (int(r.x * s), int(r.y * s)),
                          (int((r.x + r.width) * s), int((r.y + r.height) * s)),
                          (0, 255, 0), 1)
        for x, y in points:
            cv2.circle(image, (int(x * s), int(y * s)), 5, (0, 255, 0), 1)
        for i, text in enumerate(lines):
            y = 24 + i * 24
            cv2.putText(image, text, (8, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                        (0, 0, 0), 4, cv2.LINE_AA)
            cv2.putText(image, text, (8, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                        (255, 255, 255), 1, cv2.LINE_AA)
        cv2.imshow(WINDOW_NAME, image)

    def key(self, wait_ms):
        """Process window events; returns the key pressed in the overlay, or -1."""
        return cv2.waitKey(wait_ms)

    def is_closed(self):
        return cv2.getWindowProperty(WINDOW_NAME, cv2.WND_PROP_VISIBLE) < 1

    def close(self):
        cv2.destroyAllWindows()


GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x20      # clicks pass through
WS_EX_TOOLWINDOW = 0x80       # no taskbar button
WS_EX_NOACTIVATE = 0x08000000  # never takes focus
WDA_EXCLUDEFROMCAPTURE = 0x11
# Pixels of this color are fully see-through.
KEY_COLOR = "#ff00fe"
GREEN = "#00ff00"
TEXT_WIDTH = 460


class GameOverlay:
    def __init__(self, hwnd):
        self.hwnd = hwnd
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-transparentcolor", KEY_COLOR)
        self.canvas = tk.Canvas(self.root, bg=KEY_COLOR, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.root.update()

        user32 = ctypes.windll.user32
        own = user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
        style = user32.GetWindowLongW(own, GWL_EXSTYLE)
        user32.SetWindowLongW(own, GWL_EXSTYLE,
                              style | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)
        if not user32.SetWindowDisplayAffinity(own, WDA_EXCLUDEFROMCAPTURE):
            self.root.destroy()
            raise RuntimeError("Windows would not exclude the overlay from screen "
                               "capture (needs Windows 10 2004 or later). Set "
                               'overlay = "window" in config.toml.')
        self.geometry = None
        self.closed = False

    def show(self, frame, lines, rects=(), points=()):
        """Draw over the client. Same arguments as Overlay.show; frame is
        not drawn, only the annotations."""
        c = self.canvas
        c.delete("all")
        # Hiding the window and showing it again could hand it focus, so a
        # hidden overlay is just an empty (fully see-through) one.
        if not window.is_foreground(self.hwnd):
            return
        area = window.client_area(self.hwnd)
        geometry = f"{area['width']}x{area['height']}+{area['left']}+{area['top']}"
        if geometry != self.geometry:
            self.root.geometry(geometry)
            self.geometry = geometry

        for r in rects:
            c.create_rectangle(r.x, r.y, r.x + r.width, r.y + r.height, outline=GREEN, width=2)
        for x, y in points:
            c.create_oval(x - 7, y - 7, x + 7, y + 7, outline=GREEN, width=2)
        # Status text at the top right, on a dark panel so it stays readable.
        if lines:
            x0 = area["width"] - TEXT_WIDTH - 10
            c.create_rectangle(x0, 10, x0 + TEXT_WIDTH, 16 + 20 * len(lines), fill="#101010", outline="")
            for i, text in enumerate(lines):
                c.create_text(x0 + 8, 14 + 20 * i, text=text, anchor="nw",
                              fill="white", font=("Consolas", 11))

    def key(self, wait_ms):
        """Process window events for wait_ms. The overlay never has focus, so
        it reports no keys."""
        end = time.perf_counter() + wait_ms / 1000
        while True:
            try:
                self.root.update()
            except tk.TclError:
                self.closed = True
                return -1
            left = end - time.perf_counter()
            if left <= 0:
                return -1
            time.sleep(min(left, 0.01))

    def is_closed(self):
        return self.closed

    def close(self):
        if not self.closed:
            self.root.destroy()
            self.closed = True
