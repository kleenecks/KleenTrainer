"""Read HP and MP from the status bars, and watch for an unexpected screen.

Each bar is read along one row: filled pixels are colored (red or blue), the
empty part is grey. Fill % = colored pixels / bar width. If the row holds
pixels that are neither (another screen covers the bar), the bar is
unreadable and reads as None.

At low HP the HP bar blinks between its normal look and a dark one (dark red
fill, dark grey empty part), so brightness is ignored: only color vs grey.
"""

from pathlib import Path

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
    if row.max() < 30:
        return None   # black: not captured (region capture), not an empty bar
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


class ExpTracker:
    """EXP gained over a run, in % of a level, from the EXP bar's fill: the
    end reading minus the start reading, plus 100 per level-up. A level-up
    shows as the bar dropping from near full to near empty."""

    # A level-up: the bar goes from above LEVEL_UP_FROM % to below
    # LEVEL_UP_TO % (one bad reading or a death's EXP loss is not one).
    LEVEL_UP_FROM = 80
    LEVEL_UP_TO = 20

    def __init__(self):
        self.start = self.last = None
        self.level_ups = 0
        # Exact readings of the EXP text: (points, percent), first and latest.
        self.text_start = self.text_last = None

    def update_text(self, reading):
        if reading is None:
            return
        if self.text_start is None:
            self.text_start = reading
        self.text_last = reading

    def update(self, exp, event=None):
        if exp is None:
            return
        if self.start is None:
            self.start = exp
            if event:
                event(f"EXP at start: {exp:.1f}%.")
        elif (self.last is not None and self.last > self.LEVEL_UP_FROM
              and exp < self.LEVEL_UP_TO):
            self.level_ups += 1
            if event:
                event(f"Level up (EXP {self.last:.1f}% -> {exp:.1f}%).")
        self.last = exp

    def gained(self):
        """EXP gained in % of a level: from the EXP text (2 decimals) when
        read at the start and later, else from the bar (about 1%)."""
        if self.text_start and self.text_last:
            return round(self.text_last[1] - self.text_start[1] + 100 * self.level_ups, 2)
        if self.start is None or self.last is None:
            return None
        return round(self.last - self.start + 100 * self.level_ups, 1)

    def points_gained(self):
        """EXP points gained, if the text was read and no level-up happened
        (points restart at a new level)."""
        if self.text_start and self.text_last and not self.level_ups:
            return self.text_last[0] - self.text_start[0]
        return None

    def summary(self):
        if self.gained() is None:
            return "EXP: not read."
        if self.text_start and self.text_last:
            points = self.points_gained()
            return (f"EXP gained {self.gained():+.2f}%"
                    + (f" ({points:+,} EXP)" if points is not None else "")
                    + f" (start {self.text_start[0]} [{self.text_start[1]:.2f}%], end "
                      f"{self.text_last[0]} [{self.text_last[1]:.2f}%], level-ups {self.level_ups}).")
        return (f"EXP gained {self.gained():+.1f}% (start {self.start:.1f}%, end "
                f"{self.last:.1f}%, level-ups {self.level_ups}; from the bar).")


# --- the EXP text ("200968[70.38%]" on the status bar) -------------------------

DIGITS = Path(__file__).parent / "templates" / "digits"


class ExpText:
    """Reads the status bar's EXP text: points and percentage (2 decimals),
    and notices when it changes (a kill raises it at once).

    The text is placed from its two green brackets: the points' digits end
    right before the first, the percentage's four digits sit at fixed
    offsets back from the second (NN.NN%). Each digit is read at full
    resolution against saved samples (templates/digits/<digit>_<n>.png)
    over small sideways shifts, which covers the half-pixel variants the
    game's 1.5x stretch produces. config: [status] exp_text = [x0, y0, x1,
    y1] around the text."""

    CELL_W, PITCH = 8, 9                   # one digit: 6 game px = 9 client px
    PCT_OFFSETS = (-54, -45, -30, -21)     # tens, ones, tenths, hundredths
    MAX_DIFF = 4.5                         # a clear digit match (same digit
                                           # <= 2.9, other digits >= 5.6)

    def __init__(self, config):
        self.x0, self.y0, self.x1, self.y1 = config["exp_text"]
        self.samples = []
        for path in sorted(DIGITS.glob("*.png")):
            image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
            self.samples.append((path.stem.split("_")[0], image.astype(np.float32)))
        self.text_rows = (self.y0 + 3, self.y0 + 3 + self.samples[0][1].shape[0]) if self.samples else None
        self.last_pixels = None
        self.changed_at = 0.0

    def watch(self, frame, now):
        """Note whether the text area changed since the last look."""
        area = frame[self.y0:self.y1, self.x0:self.x1].min(axis=2)
        if area.max() < 30:
            return              # not captured this frame
        if self.last_pixels is not None and np.abs(
                area.astype(np.int16) - self.last_pixels).max() > 40:
            self.changed_at = now
        self.last_pixels = area.astype(np.int16)

    def _brackets(self, frame):
        f = frame[self.y0:self.y1, self.x0:self.x1].astype(np.int16)
        b, g, r = f[..., 0], f[..., 1], f[..., 2]
        cols = np.flatnonzero((((g > r + 30) & (g > b + 60)).sum(0) >= 4))
        runs = np.split(cols, np.flatnonzero(np.diff(cols) > 1) + 1)
        return [int(run[0]) + self.x0 for run in runs if len(run)]

    def _digit(self, gray, x):
        cell = gray[:, x:x + self.CELL_W]
        best = (1e9, None)
        for ch, s in self.samples:
            for k in range(s.shape[1] - self.CELL_W + 1):
                d = float(np.abs(s[:, k:k + self.CELL_W] - cell).mean())
                if d < best[0]:
                    best = (d, ch)
        return best

    def read(self, frame):
        """(points, percent) or None if the text cannot be read clearly."""
        if not self.samples:
            return None
        brackets = self._brackets(frame)
        if len(brackets) < 2:
            return None
        b1, b2 = brackets[:2]
        gray = frame[self.text_rows[0]:self.text_rows[1]].min(axis=2).astype(np.float32)
        xs, x = [], b1 - self.PITCH
        while x >= self.x0 and (gray[:, x:x + self.CELL_W] > 110).any():
            xs.insert(0, x)
            x -= self.PITCH
        digits = []
        for x in xs + [b2 + off for off in self.PCT_OFFSETS]:
            d, ch = self._digit(gray, x)
            if d > self.MAX_DIFF:
                if x == b2 + self.PCT_OFFSETS[0] and not (gray[:, x:x + self.CELL_W] > 110).any():
                    ch = "0"    # a one-digit percentage (e.g. 7.25%)
                else:
                    return None
            digits.append(ch)
        if not xs:
            return None
        points = int("".join(digits[:len(xs)]))
        percent = int("".join(digits[len(xs):])) / 100
        return points, percent
