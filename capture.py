"""Grab the client area as an image, and crop regions relative to it.

A frame is a numpy array (height, width, 3) in OpenCV's BGR order. Pixel
(0, 0) is the top-left of the client area, so every region is relative to the
client window, wherever the window is on screen.
"""

from pathlib import Path

import cv2
import mss
import numpy as np

import window


class Region:
    """A rectangle in client-area pixels."""

    def __init__(self, x, y, width, height):
        self.x, self.y, self.width, self.height = x, y, width, height

    def crop(self, frame):
        return frame[self.y:self.y + self.height, self.x:self.x + self.width]


class Capturer:
    def __init__(self, hwnd):
        self.hwnd = hwnd
        self.sct = mss.MSS()
        self.canvas = None

    def grab(self):
        # Look up the client area every time, so moving the window is fine.
        shot = self.sct.grab(window.client_area(self.hwnd))
        return np.array(shot)[:, :, :3]  # BGRA -> BGR

    def grab_regions(self, regions):
        """A client-sized frame where only the given Regions are captured and
        the rest is black. Capturing is the slowest step of the loop and its
        cost grows with the pixels copied, so this is much faster than grab()
        while frame positions keep their meaning. The returned array is
        reused by the next call."""
        area = window.client_area(self.hwnd)
        w, h = area["width"], area["height"]
        if self.canvas is None or self.canvas.shape[:2] != (h, w):
            self.canvas = np.zeros((h, w, 3), np.uint8)
        else:
            self.canvas[:] = 0
        for r in regions:
            x0, y0 = max(r.x, 0), max(r.y, 0)
            x1, y1 = min(r.x + r.width, w), min(r.y + r.height, h)
            if x1 <= x0 or y1 <= y0:
                continue
            shot = self.sct.grab({"left": area["left"] + x0, "top": area["top"] + y0,
                                  "width": x1 - x0, "height": y1 - y0})
            self.canvas[y0:y1, x0:x1] = np.array(shot)[:, :, :3]
        return self.canvas

    def close(self):
        self.sct.close()


def save_frame(frame, folder):
    """Save as the next numbered PNG in folder. Returns the path."""
    folder.mkdir(parents=True, exist_ok=True)
    number = len(list(folder.glob("frame_*.png"))) + 1
    path = folder / f"frame_{number:04d}.png"
    cv2.imwrite(str(path), frame)
    return path


def save_full_screen(path):
    """Screenshot of all monitors (e.g. to show what took focus). Returns
    the path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with mss.MSS() as sct:
        shot = sct.grab(sct.monitors[0])
    cv2.imwrite(str(path), np.array(shot)[:, :, :3])
    return path


def load_frames(folder):
    """All PNGs in folder, sorted by name, as (path, frame) pairs."""
    return [(p, cv2.imread(str(p))) for p in sorted(Path(folder).glob("*.png"))]
