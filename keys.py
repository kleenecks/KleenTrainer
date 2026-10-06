"""Game key input through pydirectinput.

Every key the bot holds is tracked, so release_all() can let go of all of
them (kill hotkey, focus lost, any stop). Nothing is sent unless the client
is in front; the focus check is the caller's job (Session.ensure_focus), and
keys only refuses as a last guard.
"""

import time

import pydirectinput

import window

pydirectinput.PAUSE = 0
# How long a tap holds the key down.
TAP_S = 0.05


class NotInFront(Exception):
    """The client lost focus right before a key would have been sent."""


class Keys:
    def __init__(self, hwnd, bindings):
        self.hwnd = hwnd
        self.bindings = bindings   # action name -> pydirectinput key name
        self.held = set()

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
        """Hold a key and send its key-down again on every call, like a real
        held key that Windows auto-repeats. Injected key presses are not
        auto-repeated, and some game actions (picking up loot) only react to
        each key-down. Call it once per loop step while the key should be
        held; release() lets go."""
        key = self._key(action)
        self._guard()
        pydirectinput.keyDown(key)
        self.held.add(key)

    def release(self, action):
        key = self._key(action)
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
        for key in list(self.held):
            pydirectinput.keyUp(key)
        self.held.clear()
