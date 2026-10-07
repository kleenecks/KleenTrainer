"""Fight one target with the basic attack until it is dead.

The trainer picks the target (a mob's feet on screen) and calls step() every
frame. Out of range: walk toward it. In range but facing away: tap the arrow
toward it. In range and facing it: hold the attack key (the game repeats the
swing). The character faces the way it last moved.

The target is recognized from frame to frame by its map position (minimap x
when seen, plus its screen offset), so camera movement does not lose it. It
is dead once it has been seen during the attack and then not seen for
target_lost_s (a dying mob vanishes from detection). A target not seen for
lost_s otherwise is given up.

Range is the horizontal distance in screen px between the character's feet
and the mob's feet. The bot acts on frames that are already a little old, so
while walking toward the target it starts attacking when the gap expected
lead_s later is in range. Once attacking it keeps at it while the target is
within the range plus hold_margin_px, so stopping to swing does not flip it
straight back to walking.
"""

import time

# The same mob is within this many screen px of where its last-seen map
# position puts it now, plus MOB_SPEED_PX_S for each second since it was
# last seen (mobs keep walking; a little above their walking speed).
SAME_TARGET_PX = 40
MOB_SPEED_PX_S = 150


class Attacker:
    def __init__(self, session, config):
        c = config["attack"]
        self.session = session
        self.keys = session.keys
        self.range = c["range_px"]
        self.hold_margin = c["hold_margin_px"]
        self.lead_s = c["lead_s"]
        self.target_lost_s = c["target_lost_s"]
        self.lost_s = c["lost_s"]
        self.debug = c["debug_log"]
        self.screen_per_minimap = config["map"]["screen_px_per_minimap_px"]
        self.facing = None        # "left"/"right" once known
        self.attacking = False
        self.attack_started = 0.0
        self.attacks = 0          # times an attack was started (for the summary)
        self.last_action = None
        self.target = None        # map position of the target (screen px)
        self.target_y = 0
        self.target_seen = 0.0
        self.target_dx = 0        # its offset from the feet when last seen
        self.closing = 0.0        # how fast the gap is closing (screen px/s)

    # --- target ------------------------------------------------------------

    def here(self):
        """The character's map position in screen px (minimap x times screen
        px per minimap px), or None without a minimap reading."""
        reading = self.session.detectors.reading
        if not reading or not reading.dot:
            return None
        return reading.dot[0] * self.screen_per_minimap

    def set_target(self, mob, feet):
        """Start fighting mob (from the detectors). Returns False (and does
        nothing) if the minimap dot is not visible this frame: map positions
        need it."""
        here = self.here()
        if here is None:
            return False
        self.target = here + mob.feet[0] - feet[0]
        self.target_y = mob.feet[1]
        self.target_seen = time.monotonic()
        self.target_dx = mob.feet[0] - feet[0]
        self.closing = 0.0
        return True

    def _track(self, found, feet, here):
        """Find the target among the detected mobs and update it. Returns
        its screen offset from the feet if seen this frame, else None."""
        now = time.monotonic()
        expected_dx = self.target - here
        reach = SAME_TARGET_PX + MOB_SPEED_PX_S * (now - self.target_seen)
        same = [m for m in found
                if abs(m.feet[0] - feet[0] - expected_dx) <= reach
                and abs(m.feet[1] - self.target_y) <= SAME_TARGET_PX]
        if not same:
            return None
        mob = min(same, key=lambda m: abs(m.feet[0] - feet[0] - expected_dx))
        dx = mob.feet[0] - feet[0]
        if now > self.target_seen:
            closing = (abs(self.target_dx) - abs(dx)) / (now - self.target_seen)
            self.closing = 0.5 * self.closing + 0.5 * closing
        self.target, self.target_seen, self.target_dx = here + dx, now, dx
        return dx

    # --- fighting ------------------------------------------------------------

    def step(self):
        """One fight step on the latest readings (the caller ticks the
        session first). Returns "fighting", "dead" (killed; the target's map
        position stays in self.target) or "lost"."""
        d = self.session.detectors
        feet = d.me.feet if d.me else None
        here = self.here()
        if feet is None or here is None:
            # A frame without the name tag or the minimap dot (briefly
            # covered): keep doing what it was doing, so a swing is not cut
            # short; give up only if it lasts.
            if time.monotonic() - self.target_seen <= self.lost_s:
                return "fighting"
            self.stop()
            return "lost"

        dx = self._track(d.found, feet, here)
        unseen = time.monotonic() - self.target_seen
        if dx is None:
            if self.attacking and self.target_seen >= self.attack_started:
                if unseen > self.target_lost_s:
                    self.stop()
                    self._note("killed", self.target - here)
                    return "dead"
                return "fighting"           # briefly hidden: keep swinging
            if unseen > self.lost_s:
                self.stop()
                self._note("lost", self.target - here)
                return "lost"
            dx = round(self.target - here)  # keep going to where it was

        toward = "right" if dx > 0 else "left"
        if self.attacking:
            in_range = abs(dx) <= self.range + self.hold_margin
        else:
            # Lead the target while closing in (frames are a little old).
            in_range = abs(dx) - max(self.closing, 0.0) * self.lead_s <= self.range

        if not in_range:
            self._stop_attacking()
            self._walk(toward)
            self._note(f"walk {toward}", dx)
            return "fighting"

        self._stop_walking()
        if dx != 0 and self.facing != toward:
            self._stop_attacking()
            self.keys.tap(toward)
            self.facing = toward
            self._note(f"turn {toward}", dx)
        if not self.attacking:
            self.keys.hold("attack")
            self.attacking = True
            self.attack_started = time.monotonic()
            self.attacks += 1
            self._note("attack", dx)
        return "fighting"

    def _note(self, action, dx):
        """Decision log (config debug_log): one line per change of action."""
        if self.debug and action != self.last_action:
            self.session.log.event(f"Attack: {action}; target dx {round(dx)}, "
                                   f"closing {self.closing:.0f} px/s.")
        self.last_action = action

    def _walk(self, direction):
        other = "left" if direction == "right" else "right"
        self.keys.release(other)
        self.keys.hold(direction)
        self.facing = direction

    def _stop_walking(self):
        self.keys.release("left")
        self.keys.release("right")

    def _stop_attacking(self):
        if self.attacking:
            self.keys.release("attack")
            self.attacking = False

    def stop(self):
        self._stop_attacking()
        self._stop_walking()
