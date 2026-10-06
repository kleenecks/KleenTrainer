"""Detect mobs at the character's height (the reachable ones).

The game draws at 1366x768 and the client stretches that 1.5x with smoothing,
so each sprite (templates/mobs/<name>/, facing left) is scaled 1.5x in four
versions: shifted 0 or 1 source pixel in x and y before scaling, which covers
every way a sprite can land between screen pixels. Plus a mirrored copy of
each for facing right. Matching uses solid sprite pixels only, so the
background behind a mob does not matter. The rough pass is grayscale; the
exact pass is in color (in grayscale a Red Snail passes for a Blue Snail).

Only rows where a mob's feet are within the height tolerance of the
character's feet are searched: that is the reachable filter, and it keeps the
search small. A rough pass on a half-size image finds candidates; the exact
pass then checks only around them.
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

TEMPLATES = Path(__file__).parent / "templates" / "mobs"
# Animations a live, targetable mob can show.
ANIMATIONS = ("stand", "move", "hit1")
GAME_SCALE = 1.5
# Two detections closer than this (px) are the same mob.
SAME_MOB = 25
# The exact pass searches this far around each rough candidate.
REFINE_MARGIN = 6
ERODE = np.ones((3, 3), np.uint8)
# Threads for the parallel matching.
WORKERS = 8


@dataclass
class Mob:
    name: str
    feet: tuple    # client pixels
    rms: float     # grey-level difference; 0 = exact


@dataclass
class _Template:
    name: str
    image: np.ndarray   # grayscale (rough) or BGR (exact)
    mask: np.ndarray
    feet: int           # row of the lowest visible pixel
    pixels: int         # solid pixels x channels


def _solid(bgra):
    # Solid pixels away from the edge; the edge blends with the background.
    return cv2.erode((bgra[..., 3] >= 250).astype(np.uint8), ERODE)


def _template(name, bgra, color):
    mask = _solid(bgra)
    feet = int(np.nonzero((bgra[..., 3] >= 128).any(1))[0].max())
    bgr = np.ascontiguousarray(bgra[..., :3])
    image = bgr if color else cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    return _Template(name, image, mask, feet, int(mask.sum()) * (3 if color else 1))


class MobDetector:
    def __init__(self, mob_names, config):
        self.tolerance = config["height_tolerance"]
        self.max_rms = config["max_rms"]
        self.rough_max_rms = config["rough_max_rms"]
        self.exact = []    # (rough template, [exact templates]) per sprite and facing
        for name in mob_names:
            for path in sorted((TEMPLATES / name).glob("*.png")):
                if path.stem.split("_")[0] not in ANIMATIONS:
                    continue
                sprite = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
                for flip in (False, True):
                    s = sprite[:, ::-1] if flip else sprite
                    rough = _template(name, cv2.resize(
                        s, None, fx=GAME_SCALE / 2, fy=GAME_SCALE / 2, interpolation=cv2.INTER_AREA),
                        color=False)
                    exact = []
                    for py in (0, 1):
                        for px in (0, 1):
                            pad = cv2.copyMakeBorder(s, py, 2 - py, px, 2 - px,
                                                     cv2.BORDER_CONSTANT, value=(0, 0, 0, 0))
                            exact.append(_template(name, cv2.resize(
                                pad, None, fx=GAME_SCALE, fy=GAME_SCALE,
                                interpolation=cv2.INTER_LINEAR), color=True))
                    self.exact.append((rough, exact))
        # Rows above the feet the strip must cover: the tallest sprite, the
        # tolerance and the exact pass's search margin.
        self.reach_up = (max(t.feet for _, ex in self.exact for t in ex)
                         + self.tolerance + REFINE_MARGIN + 4)
        # OpenCV releases Python's lock while matching, so the independent
        # template matches run in parallel on several cores.
        self.pool = ThreadPoolExecutor(max_workers=WORKERS)

    def detect(self, frame, feet_y):
        """Mobs whose feet are within the height tolerance of feet_y."""
        # Only a strip of rows around the feet can hold a reachable mob, so
        # only that strip is converted and shrunk (top row kept even so the
        # half-size rows line up).
        y0 = max(feet_y - self.reach_up, 0) & ~1
        strip = frame[y0:feet_y + self.tolerance + 2]
        half = cv2.resize(cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY), None,
                          fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
        # A mob is usually flagged by several of its sprites (walk frames look
        # alike at half size). Group the flags per mob name and spot, then
        # confirm each group starting with its best-scoring sprite.
        flags = []    # (rough score, feet x estimate, name, x, y, exact templates)
        rough_hits = self.pool.map(lambda pair: self._rough(half, pair[0], feet_y, y0), self.exact)
        for (rough, exact), hits in zip(self.exact, rough_hits):
            for x, y, score in hits:
                flags.append((score, x + rough.image.shape[1], rough.name, x, y, exact))
        flags.sort(key=lambda f: f[0])
        groups = []
        for flag in flags:
            for group in groups:
                if group[0][2] == flag[2] and abs(group[0][1] - flag[1]) <= SAME_MOB:
                    group.append(flag)
                    break
            else:
                groups.append([flag])

        def confirm(group):
            for _, _, _, x, y, exact in group:
                hit = self._refine(frame, exact, x, y, feet_y)
                if hit:
                    return hit
            return None

        found = [hit for hit in self.pool.map(confirm, groups) if hit]
        found.sort(key=lambda m: m.rms)
        mobs = []
        for m in found:
            if all(abs(m.feet[0] - k.feet[0]) > SAME_MOB or abs(m.feet[1] - k.feet[1]) > SAME_MOB
                   for k in mobs):
                mobs.append(m)
        return sorted(mobs, key=lambda m: m.feet[0])

    def _match(self, image, t):
        diff = cv2.matchTemplate(image, t.image, cv2.TM_SQDIFF, mask=t.mask)
        return np.sqrt(np.maximum(diff, 0) / t.pixels)

    def _rough(self, half, t, feet_y, strip_top):
        """Candidates (x, y, score) from the half-size pass: top-left corners
        in full-size frame pixels. half is the half-size strip starting at
        strip_top."""
        tol = self.tolerance // 2 + 1
        top = max((feet_y - strip_top) // 2 - t.feet - tol, 0)
        band = half[top:top + t.image.shape[0] + 2 * tol]
        top += strip_top // 2
        if band.shape[0] < t.image.shape[0] or t.pixels == 0:
            return []
        scores = self._match(band, t)
        ys, xs = np.nonzero(scores <= self.rough_max_rms)
        # Neighbouring pixels flag the same mob: keep the best per area.
        picks = []
        for i in np.argsort(scores[ys, xs]):
            x, y = int(xs[i]), int(ys[i])
            if all(abs(x - px) > REFINE_MARGIN // 2 or abs(y - py) > REFINE_MARGIN // 2
                   for px, py, _ in picks):
                picks.append((x, y, float(scores[y, x])))
        return [(2 * x, 2 * (top + y), s) for x, y, s in picks]

    def _refine(self, frame, exact, x, y, feet_y):
        best = None
        for t in exact:
            x0, y0 = max(x - REFINE_MARGIN, 0), max(y - REFINE_MARGIN, 0)
            window = frame[y0:y0 + t.image.shape[0] + 2 * REFINE_MARGIN,
                          x0:x0 + t.image.shape[1] + 2 * REFINE_MARGIN]
            if window.shape[0] < t.image.shape[0] or window.shape[1] < t.image.shape[1]:
                continue
            rms, _, at, _ = cv2.minMaxLoc(self._match(window, t))
            mob_feet = (x0 + at[0] + t.image.shape[1] // 2, y0 + at[1] + t.feet)
            if rms <= self.max_rms and abs(mob_feet[1] - feet_y) <= self.tolerance:
                if best is None or rms < best.rms:
                    best = Mob(t.name, mob_feet, rms)
        return best
