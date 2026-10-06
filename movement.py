"""Movement: walk to a minimap x, jump, open the minimap, and get onto a safe
spot (searching for a route the first time, then reusing it).

Positions are minimap dot positions (relative to the minimap's map area);
smaller y = higher up. Everything runs through session.tick(), which grabs a
frame, runs the detectors, updates the overlay and raises Stopped on the kill
hotkey or a stop condition.
"""

import time

import points


class MoveFailed(Exception):
    """A move could not be completed (no progress, route not found)."""


class Mover:
    def __init__(self, session, config):
        c = config["movement"]
        self.session = session
        self.keys = session.keys
        self.arrive = c["arrive_tolerance"]
        self.slow_zone = c["slow_zone"]
        self.pulse_s = c["pulse_s"]
        self.no_progress_s = c["no_progress_s"]
        self.level = c["level_tolerance"]
        self.jump_timeout_s = c["jump_timeout_s"]
        self.search_step = c["search_step"]
        self.search_max_offset = c["search_max_offset"]
        self.search_max_tries = c["search_max_tries"]

    # --- reading -----------------------------------------------------------

    def dot(self, wait_s=1.0):
        """Current minimap dot, ticking until it is visible. The session stops
        the run if the minimap stays missing (unexpected screen)."""
        end = time.monotonic() + wait_s
        while True:
            reading = self.session.tick()
            if reading and reading.state == "normal" and reading.dot:
                return reading.dot
            if time.monotonic() > end:
                raise MoveFailed("minimap dot not visible")

    def settle(self, ticks=3):
        """Let the character come to rest; returns the dot afterwards."""
        dot = self.dot()
        for _ in range(ticks):
            dot = self.dot()
        return dot

    # --- walking -------------------------------------------------------------

    def walk_to(self, x):
        """Walk until the dot's x is within the arrive tolerance of x. Far
        away, the arrow key is held; close by, it is pulsed so the character
        does not overshoot."""
        best, best_at = None, time.monotonic()
        try:
            while True:
                dx = x - self.dot()[0]
                if abs(dx) <= self.arrive:
                    self._stop_walking()
                    if abs(x - self.settle()[0]) <= self.arrive:
                        return
                    continue
                direction, other = ("right", "left") if dx > 0 else ("left", "right")
                self.keys.release(other)
                if abs(dx) <= self.slow_zone:
                    self.keys.hold(direction)
                    time.sleep(self.pulse_s)
                    self.keys.release(direction)
                else:
                    self.keys.hold(direction)

                now = time.monotonic()
                if best is None or abs(dx) < best:
                    best, best_at = abs(dx), now
                elif now - best_at > self.no_progress_s:
                    raise MoveFailed(f"no progress walking to x {x}")
        finally:
            self._stop_walking()

    def _stop_walking(self):
        self.keys.release("left")
        self.keys.release("right")

    # --- jumping -------------------------------------------------------------

    def jump(self, direction=None):
        """Jump straight up (direction None) or toward "left"/"right", holding
        that arrow until landing. Landed = the dot's y has not changed for 3
        readings, at least 0.3 s after the jump. Returns the dot afterwards."""
        start = time.monotonic()
        ys = []
        try:
            if direction:
                self.keys.hold(direction)
            self.keys.tap("jump")
            while time.monotonic() - start < self.jump_timeout_s:
                ys.append(self.dot()[1])
                if (time.monotonic() - start >= 0.3 and len(ys) >= 3
                        and ys[-1] == ys[-2] == ys[-3]):
                    break
        finally:
            if direction:
                self.keys.release(direction)
        return self.settle()

    # --- minimap -------------------------------------------------------------

    def ensure_minimap(self):
        """Make sure the minimap is at normal size, pressing M if needed
        (twice if it is large: the first press closes it)."""
        state = self.session.tick().state
        if state == "normal":
            return
        presses = 2 if state == "large" else 1
        self.session.log.event(f"Minimap is {state}: pressing M {presses}x.")
        for _ in range(presses):
            self.keys.tap("minimap")
            for _ in range(5):
                self.session.tick()
        state = self.session.tick().state
        if state != "normal":
            raise MoveFailed(f"minimap still {state} after pressing M")

    # --- safe spots ----------------------------------------------------------

    def closest_safe_spot(self, map_name):
        """Index of the safe spot with the shortest walk from here (to its
        first takeoff point, or the spot itself before a route is known)."""
        spots = points.load(map_name)["safe_spots"]
        if not spots:
            raise MoveFailed("no safe spots marked")
        x = self.dot()[0]

        def walk(s):
            target = s["route"][0]["takeoff_x"] if s["route"] else s["spot"][0]
            return abs(target - x)
        return min(range(len(spots)), key=lambda i: walk(spots[i]))

    def goto_safe_spot(self, map_name, index):
        """Get onto safe spot number index: reuse its saved route, or search
        for one (and save it)."""
        data = points.load(map_name)
        entry = data["safe_spots"][index]
        sx, sy = entry["spot"]
        log = self.session.log
        if entry["route"]:
            log.event(f"Going to safe spot {sx, sy} by its saved route.")
            if self._replay(entry["route"], sx, sy):
                return
            log.event("Saved route did not work; searching again.")
        else:
            log.event(f"Going to safe spot {sx, sy}: searching for a route.")
        route = self._search(sx, sy)
        entry["route"] = route
        points.save(map_name, data)
        log.event(f"Reached safe spot {sx, sy}; route saved ({len(route)} jump(s)).")

    def _at_level(self, y, level_y):
        return abs(y - level_y) <= self.level

    def _replay(self, route, sx, sy):
        for step in route:
            self.walk_to(step["takeoff_x"])
            _, y = self.jump(step["direction"])
            if not self._at_level(y, step["lands_y"]):
                return False
        self.walk_to(sx)
        return self._at_level(self.dot()[1], sy)

    def _search(self, sx, sy):
        """Find a way up to (sx, sy) by trying jumps; returns the route as a
        list of {takeoff_x, direction, lands_y} steps."""
        log = self.session.log
        offsets = [0]
        for k in range(self.search_step, self.search_max_offset + 1, self.search_step):
            offsets += [k, -k]

        # Edges found per level: walking past them drops the character.
        edges = {}   # level y -> [lowest x, highest x]

        def level_key(y):
            return min(edges, key=lambda k: abs(k - y)) if any(
                self._at_level(y, k) for k in edges) else y

        def attempts(base_x, y):
            """Takeoff spots around base_x on the level at height y, nearest
            first and inside that level's known edges. At each spot: a jump
            straight up (platforms can be jumped up through from below), then
            a jump toward the safe spot."""
            lo, hi = edges.get(level_key(y), [-10**6, 10**6])
            out = []
            for x in (base_x + o for o in offsets):
                if lo <= x <= hi:
                    out.append((x, None))
                    if x != sx:
                        out.append((x, "right" if sx > x else "left"))
            return out

        floor_y = self.dot()[1]
        route, tries = [], 0

        while True:
            y = self.dot()[1]
            if self._at_level(y, sy):
                self.walk_to(sx)
                if self._at_level(self.dot()[1], sy):
                    return route
            elif y < sy - self.level:
                raise MoveFailed("ended up above the safe spot")

            # On the starting level search around the safe spot's x; on a
            # level partway up, around where the character landed, so it
            # does not walk off that level.
            x_now, y_now = self.dot()
            base = sx if self._at_level(y_now, floor_y) else x_now
            climbed = False
            for takeoff, direction in attempts(base, y_now):
                if tries >= self.search_max_tries:
                    raise MoveFailed(f"no route found in {tries} tries")
                x_before, level_before = self.dot()
                try:
                    self.walk_to(takeoff)
                except MoveFailed:
                    continue   # e.g. a wall; try the next takeoff spot
                _, y_before = self.dot()
                if y_before > level_before + self.level:
                    # Walked off an edge: remember it for that level, drop the
                    # steps above where the character is now, and start over
                    # from here.
                    lo_hi = edges.setdefault(level_key(level_before), [-10**6, 10**6])
                    if takeoff > x_before:
                        lo_hi[1] = min(lo_hi[1], takeoff - self.search_step)
                    else:
                        lo_hi[0] = max(lo_hi[0], takeoff + self.search_step)
                    route = [s for s in route if s["lands_y"] >= y_before - self.level]
                    climbed = True
                    break
                _, y_after = self.jump(direction)
                tries += 1
                log.event(f"Try {tries}: jump {direction or 'up'} from x {takeoff}, "
                          f"height {y_before} -> {y_after}.")
                if y_after < y_before - self.level:
                    route.append({"takeoff_x": takeoff, "direction": direction,
                                  "lands_y": y_after})
                    climbed = True
                    break
                if y_after > y_before + self.level:
                    route = [s for s in route if s["lands_y"] >= y_after - self.level]
                    climbed = True   # fell; search again from this level
                    break
            if not climbed:
                raise MoveFailed("no route found: all takeoff spots tried")
