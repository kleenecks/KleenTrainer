"""Movement: walk to a minimap x, jump, open the minimap, and get onto a safe
spot (searching for a route the first time, then reusing it).

Positions are minimap dot positions (relative to the minimap's map area);
smaller y = higher up. Everything runs through session.tick(), which grabs a
frame, runs the detectors, updates the overlay and raises Stopped on the kill
hotkey or a stop condition.
"""

import time

import points


# Least time between two jumps over mobs (s): about one jump's airtime.
JUMP_COOLDOWN_S = 0.8


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
        self.runup = c["runup"]
        self.min_air_s = c["min_air_s"]
        self.land_still_s = c["land_still_s"]
        self.air_hold_s = c["air_hold_s"]
        # Called on every reading while moving (e.g. to tap the loot key).
        self.on_tick = None
        # Optional: jump_check(direction) -> True when a mob is just ahead and
        # should be jumped over while walking.
        self.jump_check = None
        self.last_jump = 0.0

    # --- reading -----------------------------------------------------------

    def dot(self, wait_s=1.0):
        """Current minimap dot, ticking until it is visible. The session stops
        the run if the minimap stays missing (unexpected screen)."""
        end = time.monotonic() + wait_s
        while True:
            reading = self.session.tick()
            if self.on_tick:
                self.on_tick()
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
                    self._maybe_jump_over(direction)

                now = time.monotonic()
                if best is None or abs(dx) < best:
                    best, best_at = abs(dx), now
                elif now - best_at > self.no_progress_s:
                    raise MoveFailed(f"no progress walking to x {x}")
        finally:
            self._stop_walking()

    def _maybe_jump_over(self, direction):
        """While walking, jump if jump_check(direction) says a mob is just
        ahead (used on the way to rest). If the jump fails, walking simply
        goes on."""
        now = time.monotonic()
        if (self.jump_check and now - self.last_jump >= JUMP_COOLDOWN_S
                and self.jump_check(direction)):
            self.keys.tap("jump")
            self.last_jump = now

    def _stop_walking(self):
        self.keys.release("left")
        self.keys.release("right")

    # --- jumping -------------------------------------------------------------

    def jump(self, direction=None):
        """Jump straight up (direction None) or toward "left"/"right" from a
        standstill, holding that arrow until landing. Returns the dot
        afterwards."""
        try:
            if direction:
                self.keys.hold(direction)
            self.keys.tap("jump")
            self._wait_landing(direction)
        finally:
            if direction:
                self.keys.release(direction)
        return self.settle()

    def run_jump(self, takeoff_x, direction):
        """A running jump: back up runup minimap px, walk toward takeoff_x
        and jump while still walking when passing it, holding the arrow until
        landing. Clears mobs in the way, where a standing jump next to them
        gets knocked down. Returns the dot afterwards."""
        sign = 1 if direction == "right" else -1
        self.walk_to(takeoff_x - sign * self.runup)
        start = time.monotonic()
        try:
            self.keys.hold(direction)
            while (self.dot()[0] - takeoff_x) * sign < 0:
                if time.monotonic() - start > self.no_progress_s:
                    raise MoveFailed(f"no progress running to x {takeoff_x}")
            self.keys.tap("jump")
            self._wait_landing(direction)
        finally:
            self.keys.release(direction)
        return self.settle()

    def _wait_landing(self, direction=None):
        """After a jump: landed once the dot's height has not changed for
        land_still_s, at least min_air_s after the jump (the height also
        stays put briefly at the top of a jump, so a count of equal readings
        is not enough at a high frame rate). A held arrow (direction) is let
        go air_hold_s after the jump: the jump keeps its sideways momentum in
        the air, and holding the arrow until touchdown made the character
        walk on past the spot (or off a narrow platform) after landing."""
        start = time.monotonic()
        last_y, still_since = None, start
        while time.monotonic() - start < self.jump_timeout_s:
            y = self.dot()[1]
            now = time.monotonic()
            if direction and now - start >= self.air_hold_s:
                self.keys.release(direction)
                direction = None
            if y != last_y:
                last_y, still_since = y, now
            elif now - start >= self.min_air_s and now - still_since >= self.land_still_s:
                return

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
        x, y = self.dot()
        if self._at_level(y, sy) and abs(x - sx) <= self.search_max_offset:
            # Already on the spot's level (e.g. after a fight while resting).
            self.walk_to(sx)
            if self._at_level(self.dot()[1], sy):
                return
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
            _, y = self._do_jump(step["takeoff_x"], step["direction"], step.get("running", False))
            if not self._at_level(y, step["lands_y"]):
                return False
        self.walk_to(sx)
        return self._at_level(self.dot()[1], sy)

    def _do_jump(self, takeoff, direction, running):
        """One route step: a running jump, or walk to takeoff and jump from
        a standstill (direction None = straight up)."""
        if running:
            return self.run_jump(takeoff, direction)
        self.walk_to(takeoff)
        return self.jump(direction)

    def _search(self, sx, sy):
        """Find a way up to (sx, sy) by trying jumps; returns the route as a
        list of {takeoff_x, direction, running, lands_y} steps."""
        log = self.session.log
        offsets = [0]
        for k in range(self.search_step, self.search_max_offset + 1, self.search_step):
            offsets += [k, -k]

        # Edges found per level: walking past them drops the character.
        edges = {}   # level y -> [lowest x, highest x]

        def level_key(y):
            return min(edges, key=lambda k: abs(k - y)) if any(
                self._at_level(y, k) for k in edges) else y

        def attempts(base_x, y, x_now):
            """(takeoff x, direction, running) tries around base_x on the
            level at height y, nearest first and inside that level's known
            edges. First running jumps toward the safe spot (they clear mobs
            in the way; a standing jump next to a mob gets knocked down),
            then jumps straight up (platforms can be jumped up through from
            below), then standing jumps toward the spot."""
            lo, hi = edges.get(level_key(y), [-10**6, 10**6])
            spots = [x for x in (base_x + o for o in offsets) if lo <= x <= hi]

            def toward(x):
                if x != sx:
                    return "right" if sx > x else "left"
                return "right" if sx >= x_now else "left"   # approach from this side
            running = [(x, toward(x), True) for x in spots
                       if lo <= x - (1 if toward(x) == "right" else -1) * self.runup <= hi]
            up = [(x, None, False) for x in spots]
            standing = [(x, toward(x), False) for x in spots if x != sx]
            return running + up + standing

        floor_y = self.dot()[1]
        route, tries = [], 0

        while True:
            y = self.dot()[1]
            if y <= sy + self.level:
                # At the spot's height, or a little above it (platforms can
                # slope): the height that counts is the one at the spot's x.
                self.walk_to(sx)
                y = self.dot()[1]
                if self._at_level(y, sy):
                    return route
                if y < sy - self.level:
                    raise MoveFailed(f"ended up above the safe spot (height {y}, spot {sy})")

            # On the starting level search around the safe spot's x; on a
            # level partway up, around where the character landed, so it
            # does not walk off that level.
            x_now, y_now = self.dot()
            base = sx if self._at_level(y_now, floor_y) else x_now
            climbed = False
            for takeoff, direction, running in attempts(base, y_now, x_now):
                if tries >= self.search_max_tries:
                    raise MoveFailed(f"no route found in {tries} tries")
                x_before, level_before = self.dot()
                if running:
                    y_before = level_before
                    try:
                        _, y_after = self.run_jump(takeoff, direction)
                    except MoveFailed:
                        continue   # e.g. a wall; try the next takeoff spot
                else:
                    try:
                        self.walk_to(takeoff)
                    except MoveFailed:
                        continue   # e.g. a wall; try the next takeoff spot
                    _, y_before = self.dot()
                    if y_before > level_before + self.level:
                        # Walked off an edge: remember it for that level, drop
                        # the steps above where the character is now, and
                        # start over from here.
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
                kind = "running jump" if running else "jump"
                log.event(f"Try {tries}: {kind} {direction or 'up'} at x {takeoff}, "
                          f"height {y_before} -> {y_after}.")
                if y_after < y_before - self.level:
                    route.append({"takeoff_x": takeoff, "direction": direction,
                                  "running": running, "lands_y": y_after})
                    climbed = True
                    break
                if y_after > y_before + self.level:
                    route = [s for s in route if s["lands_y"] >= y_after - self.level]
                    climbed = True   # fell; search again from this level
                    break
            if not climbed:
                raise MoveFailed("no route found: all takeoff spots tried")
