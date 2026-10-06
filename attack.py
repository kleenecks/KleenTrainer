"""Attack: fight reachable mobs with the basic attack.

The target sticks: once picked (the nearest reachable mob), the bot stays on
it until it has not been seen for a short while (killed or gone); a brief
gap, e.g. the mob hidden behind loot, does not count. Then the nearest
reachable mob becomes the next target. While walking to an out-of-range
target, a clearly closer mob takes over.

Each step: out of range, walk toward the target on screen. In range but
facing away, tap the arrow toward it to turn. In range and facing it, hold
the attack key (the game repeats the swing). The character faces the way it
last moved.

Range is the horizontal distance in screen px between the character's feet
and the mob's feet.
"""

import time

# The same mob in the next frame is within this many screen px of where it
# was (mobs move slowly; the camera can shift a little).
SAME_TARGET_PX = 40


class Attacker:
    def __init__(self, session, config):
        self.session = session
        self.keys = session.keys
        self.range = config["attack"]["range_px"]
        self.target_lost_s = config["attack"]["target_lost_s"]
        self.facing = None        # "left"/"right" once known
        self.attacking = False
        self.attacks = 0          # times an attack was started (for the summary)
        self.switch_margin = config["attack"]["switch_margin_px"]
        self.target = None        # last known feet of the current target
        self.target_seen = 0.0

    def _pick_target(self, found, feet):
        """The current target if it is still there, else the nearest mob.
        While the target is out of range (the bot is walking to it), a mob
        closer by more than the switch margin takes over."""
        now = time.monotonic()
        if self.target:
            same = [m for m in found
                    if abs(m.feet[0] - self.target[0]) <= SAME_TARGET_PX
                    and abs(m.feet[1] - self.target[1]) <= SAME_TARGET_PX]
            if same:
                mob = min(same, key=lambda m: abs(m.feet[0] - self.target[0]))
                self.target, self.target_seen = mob.feet, now
                distance = abs(mob.feet[0] - feet[0])
                if distance > self.range:
                    nearest = min(found, key=lambda m: abs(m.feet[0] - feet[0]))
                    if abs(nearest.feet[0] - feet[0]) + self.switch_margin < distance:
                        self.target = nearest.feet
                return self.target
            if now - self.target_seen <= self.target_lost_s:
                return self.target          # briefly hidden: keep at it
            self.target = None
        if not found:
            return None
        mob = min(found, key=lambda m: abs(m.feet[0] - feet[0]))
        self.target, self.target_seen = mob.feet, now
        return mob.feet

    def step(self):
        """One fight step. Returns True while there is a target."""
        self.session.tick()
        d = self.session.detectors
        feet = d.me.feet if d.me else None
        target = self._pick_target(d.found, feet) if feet else None
        if target is None:
            self._stop_attacking()
            self._stop_walking()
            return False

        dx = target[0] - feet[0]
        toward = "right" if dx > 0 else "left"

        if abs(dx) > self.range:
            self._stop_attacking()
            self._walk(toward)
            return True

        self._stop_walking()
        if dx != 0 and self.facing != toward:
            self._stop_attacking()
            self.keys.tap(toward)
            self.facing = toward
        if not self.attacking:
            self.keys.hold("attack")
            self.attacking = True
            self.attacks += 1
        return True

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
