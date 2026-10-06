"""Marked map points, one JSON file per map in points/.

v1 marks one point: the safe spot, as the minimap dot position (relative to
the minimap's map area) where the character rests.
"""

import json
from pathlib import Path

FOLDER = Path(__file__).parent / "points"


def path_for(map_name):
    return FOLDER / f"{map_name}.json"


def load(map_name):
    """The map's points as a dict, empty if none are marked yet."""
    path = path_for(map_name)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_safe_spot(map_name, dot):
    points = load(map_name)
    points["safe_spot"] = list(dot)
    FOLDER.mkdir(exist_ok=True)
    path_for(map_name).write_text(json.dumps(points, indent=2) + "\n", encoding="utf-8")
    return path_for(map_name)
