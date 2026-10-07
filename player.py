"""Find the character on screen from its name tag.

The name tag is light grey text in a see-through dark box. Only pixels that
are light grey count. The score is the share of the template's letter pixels
that have a light pixel within 1 px (letter edges render a little
differently over different backgrounds), minus light pixels where the dark
box should be. So a tag partly covered by grass or mobs still scores well
(it only loses the covered letters), while white areas like clouds score low.

The letters come from the status bar, which shows the character's name in
the same letters (name_tag = "auto"; any character, nothing to cut), or
from a template file: a plain crop of the tag box, top edge at the box's
top.
"""

import time
from dataclasses import dataclass

import cv2
import numpy as np

from capture import Region

# The feet are this many pixels above the top of the tag box, at its center.
TAG_TO_FEET_Y = 5
# Space between the tag box's edge and its letters (px).
TAG_PADDING = 5
# How far the tag is searched for around its last position.
TRACK_MARGIN = 60
# A letter pixel counts as found if a light pixel is within 1 px of it.
NEAR = np.ones((3, 3), np.uint8)


def _light(image):
    """1 where a pixel is light grey (name tag text), else 0."""
    i = image.astype(np.int16)
    low = i.min(2)
    return ((low > 170) & (i.max(2) - low < 30)).astype(np.float32)


@dataclass
class Reading:
    status: str               # found, held (tag lost briefly), or lost
    feet: tuple | None = None   # client pixels
    score: float = 0.0


def name_from_status_bar(frame, region):
    """The character's name letters (light pixels) from the status bar,
    which shows the name in the same letters as the tag above the
    character, padded like a tag box (TAG_PADDING on each side), or None.
    region: [x, y, width, height] of the name in the status bar."""
    x, y, w, h = region
    light = _light(frame[y:y + h, x:x + w])
    ys, xs = np.nonzero(light)
    if len(xs) < 20:
        return None
    letters = light[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    return cv2.copyMakeBorder(letters, TAG_PADDING, TAG_PADDING, TAG_PADDING, TAG_PADDING,
                              cv2.BORDER_CONSTANT, value=0)


class PlayerLocator:
    def __init__(self, config):
        """config name_tag: "auto" = read the name from the status bar on the
        first frame (any character, no template to cut), or a template file
        (a crop of the tag box)."""
        self.name_region = config["name_region"]
        self.letters = None
        if config["name_tag"] != "auto":
            self._use(_light(cv2.imread(config["name_tag"])))
        self.min_score = config["min_score"]
        self.hold_s = config["lost_hold_s"]
        self.world_bottom = config["world_bottom"]
        self.last_tag = None     # top-left of the tag last time it was found
        self.last_seen = 0.0

    def _use(self, letters):
        self.letters = letters
        # Box pixels at least 1 px away from any letter: these should be dark.
        self.box = (cv2.dilate(letters, NEAR) == 0).astype(np.float32)
        self.letter_count = letters.sum()

    def read(self, frame, now=None):
        now = time.monotonic() if now is None else now
        if self.letters is None:
            letters = name_from_status_bar(frame, self.name_region)
            if letters is None:
                return Reading("lost")
            self._use(letters)
        world = frame[:self.world_bottom]
        found = None
        if self.last_tag:
            x, y = self.last_tag
            found = self._search(world, Region(max(x - TRACK_MARGIN, 0), max(y - TRACK_MARGIN, 0),
                                               2 * TRACK_MARGIN, 2 * TRACK_MARGIN))
        if found is None:
            found = self._search(world, Region(0, 0, *world.shape[1::-1]))

        if found:
            (x, y), score = found
            self.last_tag, self.last_seen = (x, y), now
            return Reading("found", self._feet(x, y), score)
        if self.last_tag and now - self.last_seen <= self.hold_s:
            return Reading("held", self._feet(*self.last_tag))
        self.last_tag = None
        return Reading("lost")

    def _feet(self, x, y):
        return x + self.letters.shape[1] // 2, y - TAG_TO_FEET_Y

    def _search(self, world, area):
        """((x, y), score) of the best tag whose top-left is inside area, or None."""
        h, w = self.letters.shape
        search = Region(area.x, area.y, area.width + w, area.height + h).crop(world)
        if search.shape[0] < h or search.shape[1] < w:
            return None
        light = _light(search)
        hits = cv2.matchTemplate(cv2.dilate(light, NEAR), self.letters, cv2.TM_CCORR)
        stray = cv2.matchTemplate(light, self.box, cv2.TM_CCORR)
        scores = (hits - stray) / self.letter_count
        _, score, _, at = cv2.minMaxLoc(scores)
        if score < self.min_score:
            return None
        return (area.x + at[0], area.y + at[1]), score
