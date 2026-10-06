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

    def grab(self):
        # Look up the client area every time, so moving the window is fine.
        shot = self.sct.grab(window.client_area(self.hwnd))
        return np.array(shot)[:, :, :3]  # BGRA -> BGR

    def close(self):
        self.sct.close()


def save_frame(frame, folder):
    """Save as the next numbered PNG in folder. Returns the path."""
    folder.mkdir(parents=True, exist_ok=True)
    number = len(list(folder.glob("frame_*.png"))) + 1
    path = folder / f"frame_{number:04d}.png"
    cv2.imwrite(str(path), frame)
    return path


def load_frames(folder):
    """All PNGs in folder, sorted by name, as (path, frame) pairs."""
    return [(p, cv2.imread(str(p))) for p in sorted(Path(folder).glob("*.png"))]
