"""Game key input through pydirectinput.

Every key the bot holds is tracked, so release_all() can let go of all of
them (kill hotkey, focus lost, any stop). Nothing is sent unless the client
is in front; the focus check is the caller's job (Session.ensure_focus), and
keys only refuses as a last guard.
"""

import threading
import time

import pydirectinput

import window

pydirectinput.PAUSE = 0
# How long a tap holds the key down.
TAP_S = 0.05
# Key-downs per second for a repeating held key; a real held key repeats at
# about this rate.
REPEAT_PER_S = 30


class NotInFront(Exception):
    """The client lost focus right before a key would have been sent."""


class Keys:
    def __init__(self, hwnd, bindings):
        self.hwnd = hwnd
        self.bindings = bindings   # action name -> pydirectinput key name
        self.held = set()
        # Keys whose key-down a background thread re-sends (see
        # hold_repeating). The lock keeps a release from racing a repeat,
        # which could leave the key stuck down.
        self.repeating = set()
        self.lock = threading.Lock()
        self.running = True
        threading.Thread(target=self._repeat, daemon=True).start()

    def _repeat(self):
        while self.running:
            time.sleep(1 / REPEAT_PER_S)
            with self.lock:
                if self.repeating and window.is_foreground(self.hwnd):
                    for key in self.repeating:
                        pydirectinput.keyDown(key)

    def _key(self, action):
        return self.bindings[action]

    def _guard(self):
        if not window.is_foreground(self.hwnd):
            self.release_all()
            raise NotInFront()

    def hold(self, action):
        key = self._key(action)
        if key not in self.held:
            self._guard()
            pydirectinput.keyDown(key)
            self.held.add(key)

    def hold_repeating(self, action):
        """Hold a key and keep re-sending its key-down about 30 times a
        second from a background thread, like a real held key that Windows
        auto-repeats. Injected key presses are not auto-repeated, and some
        game actions (picking up loot) only react to each key-down. Safe to
        call every loop step; release() lets go."""
        key = self._key(action)
        if key in self.repeating:
            return
        self._guard()
        with self.lock:
            pydirectinput.keyDown(key)
            self.held.add(key)
            self.repeating.add(key)

    def release(self, action):
        key = self._key(action)
        with self.lock:
            self.repeating.discard(key)
            if key in self.held:
                pydirectinput.keyUp(key)
                self.held.discard(key)

    def tap(self, action):
        self._guard()
        key = self._key(action)
        pydirectinput.keyDown(key)
        try:
            time.sleep(TAP_S)
        finally:
            pydirectinput.keyUp(key)

    def release_all(self):
        with self.lock:
            self.repeating.clear()
            for key in list(self.held):
                pydirectinput.keyUp(key)
            self.held.clear()

    def close(self):
        """Release everything and stop the repeat thread."""
        self.release_all()
        self.running = False
