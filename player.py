"""Find the character on screen from its name tag.

The name tag is light grey text in a see-through dark box. Only pixels that
are light grey count; the template scores +1 for each letter pixel found and
-1 for each light pixel where the dark box should be. So a tag partly covered
by grass or mobs still scores well (it only loses the covered letters), while
white areas like clouds score low.

The template is a plain crop of the tag box, top edge at the box's top. Cut
one per character.
"""

import time
from dataclasses import dataclass

import cv2
import numpy as np

from capture import Region

# The feet are this many pixels above the top of the tag box, at its center.
TAG_TO_FEET_Y = 5
# How far the tag is searched for around its last position.
TRACK_MARGIN = 60


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


class PlayerLocator:
    def __init__(self, config):
        tag = cv2.imread(config["name_tag"])
        letters = _light(tag)
        self.kernel = np.where(letters > 0, 1.0, -1.0).astype(np.float32)
        self.letter_count = letters.sum()
        self.min_score = config["min_score"]
        self.hold_s = config["lost_hold_s"]
        self.world_bottom = config["world_bottom"]
        self.last_tag = None     # top-left of the tag last time it was found
        self.last_seen = 0.0

    def read(self, frame, now=None):
        now = time.monotonic() if now is None else now
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
        return x + self.kernel.shape[1] // 2, y - TAG_TO_FEET_Y

    def _search(self, world, area):
        """((x, y), score) of the best tag whose top-left is inside area, or None."""
        h, w = self.kernel.shape
        search = Region(area.x, area.y, area.width + w, area.height + h).crop(world)
        if search.shape[0] < h or search.shape[1] < w:
            return None
        scores = cv2.matchTemplate(_light(search), self.kernel, cv2.TM_CCORR) / self.letter_count
        _, score, _, at = cv2.minMaxLoc(scores)
        if score < self.min_score:
            return None
        return (area.x + at[0], area.y + at[1]), score
