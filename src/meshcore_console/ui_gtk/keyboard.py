"""Keyboard helpers shared by the main window and its views.

A held key sends more presses (key repeat) without a release in between, and
GTK activates on those presses: an entry on each press, a button ~250 ms after
the press (or at the release, if that comes first) and again for each repeat
once the previous activation has finished.  Holding Enter on Connect would then
disconnect again once the button re-enabled, hold an advert menu open long
enough to send an advert, or carry on through a page switch into the message
entry and send a draft.  ``ActivationLatch`` turns one held activation key
into one action.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gdk, Gtk

ACTIVATION_KEYS = frozenset(
    {
        Gdk.KEY_Return,
        Gdk.KEY_KP_Enter,
        Gdk.KEY_ISO_Enter,
        Gdk.KEY_space,
        Gdk.KEY_KP_Space,
    }
)


class ActivationLatch:
    """Lets an action start once per press of an activation key.

    The window feeds it every key press and release (``attach``).  An action
    calls ``begin()``: the first call while a key is held latches, and further
    calls fail until the keys used by that action are released.  ``latch()`` marks the
    held key as used without starting anything, e.g. when Enter opened a menu
    or switched pages, so its repeats can't choose from the menu or send from
    the new page.

    Keys are tracked by hardware keycode: the keyval only decides whether a
    press is an activation key.  A release can report another keyval than its
    press (a modifier or Num Lock changed in between, a layout switch), and
    must still end the press.  There is no timeout: a repeat may come any
    time after the press (the repeat delay is a user setting), so only the
    release, or the window losing the keyboard (``reset``), ends it.  If a
    release ever goes unseen, the next press of that key acts once its own
    release arrives: one swallowed Enter, never an extra send.

    Pointer clicks are never held back: a pointer press marks the next
    activation as the pointer's, which neither latches nor waits for a key.
    """

    def __init__(self) -> None:
        self._held: set[int] = set()  # hardware keycodes of activation keys down
        self._used: set[int] = set()
        self._pointer = False  # the last input was a pointer press, not a key

    @property
    def held(self) -> bool:
        return bool(self._held)

    @property
    def latched(self) -> bool:
        return bool(self._used)

    def press(self, keyval: int, keycode: int) -> bool:
        """Return True when GTK should stop propagating a used key's repeat."""
        already_held = keycode in self._held
        if not already_held and keyval not in ACTIVATION_KEYS:
            return False
        self._pointer = False
        self._held.add(keycode)
        return keycode in self._used

    def release(self, keycode: int) -> None:
        if keycode not in self._held:
            return  # not an activation key's press: nothing to end
        self._held.discard(keycode)
        self._used.discard(keycode)

    def pointer_pressed(self) -> None:
        self._pointer = True

    def reset(self) -> None:
        self._held.clear()
        self._used.clear()

    def latch(self) -> None:
        if not self._pointer:
            self._used.update(self._held)

    def begin(self) -> bool:
        """True when an action may start now; False for a repeat of a used key."""
        if self._pointer:
            return True
        if self.latched:
            return False
        self.latch()
        return True

    def attach(self, widget: Gtk.Widget) -> None:
        """Watch the key presses and releases, and pointer presses, that reach
        ``widget``.

        Popovers are surfaces of their own: attach to the ones whose buttons
        start actions, or a release inside them goes unseen.
        """

        def on_pressed(
            _controller: Gtk.EventControllerKey, keyval: int, keycode: int, _state: object
        ) -> bool:
            # A repeated press can queue Gtk.Button activation for key-up.
            # Block it here so clearing the latch on release cannot fire it.
            return self.press(keyval, keycode)

        def on_released(
            _controller: Gtk.EventControllerKey, _keyval: int, keycode: int, _state: object
        ) -> None:
            self.release(keycode)

        keys = Gtk.EventControllerKey.new()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", on_pressed)
        keys.connect("key-released", on_released)
        widget.add_controller(keys)

        pointer = Gtk.GestureClick.new()
        pointer.set_button(0)
        pointer.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        pointer.connect("pressed", lambda *_args: self.pointer_pressed())
        widget.add_controller(pointer)


def _latch_for(widget: Gtk.Widget) -> ActivationLatch | None:
    root = widget.get_root()
    latch = getattr(root, "activation_latch", None)
    return latch if isinstance(latch, ActivationLatch) else None


def activation_allowed(widget: Gtk.Widget) -> bool:
    """``ActivationLatch.begin`` of the window holding ``widget``."""
    latch = _latch_for(widget)
    return latch.begin() if latch is not None else True


def focus_within(widget: Gtk.Widget) -> bool:
    """True when the keyboard focus is ``widget`` or inside it."""
    root = widget.get_root()
    focus = root.get_focus() if root is not None else None
    return focus is not None and (focus is widget or focus.is_ancestor(widget))


def focus_is_lost(root: Gtk.Root | None) -> bool:
    """True when nothing usable has the focus (it went with a hidden or
    disabled widget), so the app may put it somewhere sensible."""
    if root is None:
        return False
    focus = root.get_focus()
    return (
        focus is None
        or focus is root
        or not focus.get_mapped()
        or not focus.is_sensitive()
        or not focus.get_visible()
    )
