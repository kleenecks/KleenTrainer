"""Read HP and MP from the status bars, and watch for an unexpected screen.

Each bar is read along one row: filled pixels are colored (red or blue), the
empty part is grey. Fill % = colored pixels / bar width. If the row holds
pixels that are neither (another screen covers the bar), the bar is
unreadable and reads as None.

At low HP the HP bar blinks between its normal look and a dark one (dark red
fill, dark grey empty part), so brightness is ignored: only color vs grey.
"""

import cv2
import numpy as np

from capture import Region

# How far from its usual spot the death dialog is searched for.
DEATH_MARGIN = 100

# A pixel counts as colored when its channels differ by more than this, and
# as grey when they differ by less than GREY.
COLORED = 40
GREY = 20


def bar_fill(frame, bar):
    """Fill % of the bar [x_first, x_last, y], or None if it is not visible."""
    x0, x1, y = bar
    row = frame[y, x0:x1 + 1].astype(np.int16)
    spread = row.max(1) - row.min(1)
    colored = spread > COLORED
    empty = spread < GREY
    if (colored | empty).mean() < 0.9:
        return None
    return 100 * colored.mean()


class DeathDetector:
    """Matches the "PRESS OK TO BE REVIVED." headline of the death dialog.
    The dialog appears a couple of seconds after dying."""

    def __init__(self, config):
        self.template = cv2.imread(config["death_dialog"])
        x, y = config["death_dialog_at"]
        h, w = self.template.shape[:2]
        self.area = Region(max(x - DEATH_MARGIN, 0), max(y - DEATH_MARGIN, 0),
                           w + 2 * DEATH_MARGIN, h + 2 * DEATH_MARGIN)
        self.min_score = config["death_min_score"]

    def is_dead(self, frame):
        search = self.area.crop(frame)
        h, w = self.template.shape[:2]
        if search.shape[0] < h or search.shape[1] < w:
            return False
        _, score, _, _ = cv2.minMaxLoc(cv2.matchTemplate(search, self.template, cv2.TM_CCOEFF_NORMED))
        return score >= self.min_score


class UnexpectedScreenWatch:
    """Flags the minimap missing for too long, or a different minimap size
    than at startup."""

    def __init__(self, config):
        self.missing_s = config["minimap_missing_s"]
        self.startup_size = None
        self.missing_since = None

    def update(self, reading, now):
        """Returns a reason string when the screen is unexpected, else None."""
        if reading.state == "normal":
            self.missing_since = None
            size = (reading.bounds.width, reading.bounds.height)
            if self.startup_size is None:
                self.startup_size = size
            elif size != self.startup_size:
                return f"minimap size {size[0]}x{size[1]}, was {self.startup_size[0]}x{self.startup_size[1]}"
            return None
        if self.missing_since is None:
            self.missing_since = now
        if now - self.missing_since >= self.missing_s:
            return f"minimap {reading.state} for {now - self.missing_since:.0f} s"
        return None
