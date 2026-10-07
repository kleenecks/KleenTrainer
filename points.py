"""Marked map points, one JSON file per map in points/.

v1 marks safe spots: minimap dot positions (relative to the minimap's map
area) where the character rests. Each spot can also hold the route the bot
learned for getting there (filled in from stage 7), or null.

File format: {"safe_spots": [{"spot": [x, y], "route": null}, ...]}; a spot
can also hold "exit": the direction ("left"/"right") that got back down to
the floor after resting.
"""

import json
from pathlib import Path

FOLDER = Path(__file__).parent / "points"


def path_for(map_name):
    return FOLDER / f"{map_name}.json"


def load(map_name):
    """The map's points as a dict, with an empty safe_spots list if none are
    marked yet. An older single "safe_spot" entry is converted."""
    path = path_for(map_name)
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if "safe_spot" in data:
        data.setdefault("safe_spots", []).append({"spot": data.pop("safe_spot"), "route": None})
    data.setdefault("safe_spots", [])
    return data


def save(map_name, data):
    FOLDER.mkdir(exist_ok=True)
    path_for(map_name).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def safe_spots(map_name):
    """Marked safe spots as (x, y) tuples."""
    return [tuple(s["spot"]) for s in load(map_name)["safe_spots"]]


def _distance(a, b):
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def add_safe_spot(map_name, dot, merge_distance):
    """Add a safe spot at dot, or move the existing spot within merge_distance
    (minimap px) of it. Returns "added" or "moved"."""
    data = load(map_name)
    for s in data["safe_spots"]:
        if _distance(s["spot"], dot) <= merge_distance:
            s["spot"], s["route"] = list(dot), None   # the old route no longer applies
            s.pop("exit", None)
            save(map_name, data)
            return "moved"
    data["safe_spots"].append({"spot": list(dot), "route": None})
    save(map_name, data)
    return "added"


def remove_nearest_safe_spot(map_name, dot):
    """Remove the safe spot nearest dot. Returns its position, or None if
    there are none."""
    data = load(map_name)
    if not data["safe_spots"]:
        return None
    nearest = min(data["safe_spots"], key=lambda s: _distance(s["spot"], dot))
    data["safe_spots"].remove(nearest)
    save(map_name, data)
    return tuple(nearest["spot"])
