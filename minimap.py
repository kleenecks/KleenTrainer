"""Read the minimap: its state, the map area's bounds and the player dot.

State comes from the - / + / WORLD button group, which looks different in
each state: normal (both blue), large (+ greyed), closed (- greyed). Only the
normal state is read further. Its map area is found from the inner corners
of the minimap frame, and the yellow player dot is matched inside it.

Templates are in templates/minimap/, cut from test-server frames.
"""

from dataclasses import dataclass
from pathlib import Path

import cv2

from capture import Region

TEMPLATES = Path(__file__).parent / "templates" / "minimap"
STATES = ("normal", "large", "closed")

# Offsets from each corner template's top-left to the map area's edge.
TL_TO_MAP = (9, 14)
BR_TO_MAP = (12, 12)
DOT_CENTER = (3, 3)

# Searching the whole frame takes about a second, so each read first looks
# within this many pixels of where things were last time.
TRACK_MARGIN = 8


def _near(at, margin=TRACK_MARGIN):
    return Region(max(at[0] - margin, 0), max(at[1] - margin, 0), 2 * margin, 2 * margin)


def _load(name):
    return cv2.imread(str(TEMPLATES / name), cv2.IMREAD_UNCHANGED)


@dataclass
class Reading:
    state: str                  # normal, large, closed, or missing
    bounds: Region | None = None  # map area in client pixels (normal only)
    dot: tuple | None = None      # player dot center, relative to bounds


class MinimapReader:
    def __init__(self, config):
        self.buttons = {s: _load(f"buttons_{s}.png") for s in STATES}
        self.corner_tl = _load("inner_tl.png")
        self.corner_br = _load("inner_br.png")
        self.dot = _load("player_dot.png")
        self.button_min_score = config["button_min_score"]
        self.corner_max_rms = config["corner_max_rms"]
        self.dot_min_score = config["dot_min_score"]
        # Where the buttons and corners were found last time.
        self.last_buttons = None
        self.last_corners = None

    def read(self, frame):
        state, buttons_at = None, None
        if self.last_buttons:
            state, buttons_at = self._state(frame, _near(self.last_buttons))
        if state is None:
            state, buttons_at = self._state(frame, Region(0, 0, *frame.shape[1::-1]))
        self.last_buttons = buttons_at
        if state is None:
            self.last_corners = None
            return Reading("missing")
        if state != "normal":
            self.last_corners = None
            return Reading(state)

        bounds = self._bounds(frame, buttons_at)
        if bounds is None:
            return Reading("missing")
        return Reading("normal", bounds, self._dot(bounds.crop(frame)))

    def _state(self, frame, area):
        """(state, top-left of the button group) for the best match whose
        top-left is inside area, or (None, None)."""
        best, best_score, best_at = None, self.button_min_score, None
        for state, template in self.buttons.items():
            h, w = template.shape[:2]
            search = Region(area.x, area.y, area.width + w, area.height + h).crop(frame)
            if search.shape[0] < h or search.shape[1] < w:
                continue
            scores = cv2.matchTemplate(search, template, cv2.TM_CCOEFF_NORMED)
            _, score, _, at = cv2.minMaxLoc(scores)
            if score >= best_score:
                best, best_score, best_at = state, score, (area.x + at[0], area.y + at[1])
        return best, best_at

    def _bounds(self, frame, buttons_at):
        tl = br = None
        if self.last_corners:
            tl = self._corner(frame, self.corner_tl, _near(self.last_corners[0]))
            br = self._corner(frame, self.corner_br, _near(self.last_corners[1]))
        if tl is None or br is None:
            # The map area sits below the button group: its top-left corner is
            # to the left, its bottom-right corner below and to the right.
            bx, by = buttons_at
            tl = self._corner(frame, self.corner_tl, Region(0, by, bx, frame.shape[0] // 2))
            br = self._corner(frame, self.corner_br,
                              Region(bx, by, frame.shape[1] // 2, frame.shape[0] // 2))
        self.last_corners = (tl, br) if tl and br else None
        if tl is None or br is None:
            return None
        x0, y0 = tl[0] + TL_TO_MAP[0], tl[1] + TL_TO_MAP[1]
        x1, y1 = br[0] + BR_TO_MAP[0], br[1] + BR_TO_MAP[1]
        if x1 <= x0 or y1 <= y0:
            return None
        return Region(x0, y0, x1 - x0, y1 - y0)

    def _corner(self, frame, template, area):
        """Top-left of the best corner match inside area, in frame pixels."""
        h, w = template.shape[:2]
        search = Region(area.x, area.y, area.width + w, area.height + h).crop(frame)
        if search.shape[0] < h or search.shape[1] < w:
            return None
        mask = template[..., 3]
        diff = cv2.matchTemplate(search, template[..., :3], cv2.TM_SQDIFF, mask=mask)
        value, _, at, _ = cv2.minMaxLoc(diff)
        rms = (max(value, 0) / (mask > 0).sum() / 3) ** 0.5
        if rms > self.corner_max_rms:
            return None
        return area.x + at[0], area.y + at[1]

    def _dot(self, map_image):
        scores = cv2.matchTemplate(map_image, self.dot, cv2.TM_CCOEFF_NORMED)
        _, score, _, at = cv2.minMaxLoc(scores)
        if score < self.dot_min_score:
            return None
        return at[0] + DOT_CENTER[0], at[1] + DOT_CENTER[1]
