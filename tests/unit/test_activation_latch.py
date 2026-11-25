"""A held Enter (key repeat) starts one radio action or send, not one per repeat."""

from __future__ import annotations

import gi

gi.require_version("Gdk", "4.0")

from gi.repository import Gdk

from meshcore_console.ui_gtk.keyboard import ActivationLatch

# Hardware keycodes (any distinct numbers: the latch only compares them)
RETURN = 36
KP_ENTER = 104
SPACE = 65
LETTER_A = 38


def test_held_enter_starts_one_action() -> None:
    latch = ActivationLatch()
    latch.press(Gdk.KEY_Return, RETURN)
    assert latch.begin()  # Connect
    for _ in range(3):  # key repeat: presses without a release
        latch.press(Gdk.KEY_Return, RETURN)
        assert not latch.begin()  # Disconnect, once re-enabled under the held key
    latch.release(RETURN)
    latch.press(Gdk.KEY_Return, RETURN)
    assert latch.begin()  # a new press acts again


def test_a_long_repeat_delay_is_still_a_repeat() -> None:
    # No timing guess: however late the first repeat comes (the repeat delay
    # is a user setting), only the release ends the press.
    latch = ActivationLatch()
    latch.press(Gdk.KEY_Return, RETURN)
    assert latch.begin()
    latch.press(Gdk.KEY_Return, RETURN)  # first repeat after, say, 2 s
    assert not latch.begin()


def test_enter_that_switched_pages_does_not_send() -> None:
    latch = ActivationLatch()
    latch.press(Gdk.KEY_Return, RETURN)
    assert latch.begin()  # Send Message in Peers
    latch.latch()  # navigate_to: Messages, focus in the composer
    latch.press(Gdk.KEY_Return, RETURN)
    assert not latch.begin()  # the entry's activate: draft stays unsent
    latch.release(RETURN)
    latch.press(Gdk.KEY_Return, RETURN)
    assert latch.begin()  # deliberate Enter sends


def test_enter_that_opened_the_advert_menu_does_not_choose() -> None:
    latch = ActivationLatch()
    latch.press(Gdk.KEY_Return, RETURN)
    latch.latch()  # the menu opened under the key
    latch.press(Gdk.KEY_Return, RETURN)
    assert not latch.begin()  # repeat lands on Flood Advert: ignored
    latch.release(RETURN)
    latch.press(Gdk.KEY_Return, RETURN)
    assert latch.begin()


def test_a_release_reporting_another_keyval_ends_the_press() -> None:
    # Num Lock or a modifier changed while the key was down: the release is
    # matched by the physical key, not by its keyval
    latch = ActivationLatch()
    latch.press(Gdk.KEY_KP_Enter, KP_ENTER)
    assert latch.begin()
    latch.release(KP_ENTER)
    assert not latch.held
    latch.press(Gdk.KEY_KP_Enter, KP_ENTER)
    assert latch.begin()


def test_releasing_another_key_does_not_end_the_press() -> None:
    latch = ActivationLatch()
    latch.press(Gdk.KEY_Return, RETURN)
    assert latch.begin()
    latch.press(Gdk.KEY_a, LETTER_A)
    latch.release(LETTER_A)
    latch.press(Gdk.KEY_Return, RETURN)  # Enter still repeating
    assert not latch.begin()


def test_mouse_activation_is_never_latched() -> None:
    latch = ActivationLatch()
    assert latch.begin()
    assert latch.begin()  # no key held: every click counts


def test_pointer_clicks_work_after_a_release_went_missing() -> None:
    # Enter held until a menu opened, released inside it, unseen: the key
    # still counts as down.  Clicks must not be held back by it.
    latch = ActivationLatch()
    latch.press(Gdk.KEY_Return, RETURN)
    assert latch.begin()
    latch.pointer_pressed()
    assert latch.begin()  # click Connect
    latch.latch()  # a click that switched pages
    latch.pointer_pressed()
    assert latch.begin()  # and the next click


def test_a_held_key_after_a_click_latches_again() -> None:
    latch = ActivationLatch()
    latch.pointer_pressed()
    assert latch.begin()  # a click
    latch.press(Gdk.KEY_Return, RETURN)
    assert latch.begin()  # then Enter on a focused button
    latch.press(Gdk.KEY_Return, RETURN)
    assert not latch.begin()  # its repeat


def test_a_missed_release_swallows_one_press_never_sends_twice() -> None:
    # The release went to a surface we don't watch.  The next press is taken
    # for a repeat (no action); its release ends it and Enter works again.
    latch = ActivationLatch()
    latch.press(Gdk.KEY_Return, RETURN)
    assert latch.begin()
    latch.press(Gdk.KEY_Return, RETURN)
    assert not latch.begin()
    latch.release(RETURN)
    latch.press(Gdk.KEY_Return, RETURN)
    assert latch.begin()


def test_other_keys_are_ignored() -> None:
    latch = ActivationLatch()
    latch.press(Gdk.KEY_a, LETTER_A)
    assert latch.begin()
    assert latch.begin()


def test_space_and_enter_both_latch_until_both_released() -> None:
    latch = ActivationLatch()
    latch.press(Gdk.KEY_space, SPACE)
    latch.press(Gdk.KEY_Return, RETURN)
    assert latch.begin()
    latch.release(SPACE)
    assert not latch.begin()  # Enter still down
    latch.release(RETURN)
    assert latch.begin()


def test_attach_tracks_the_hardware_keycode_of_each_event() -> None:
    """The controller's signals feed the keycode: a release reporting
    another keyval (here KP_Enter pressed, released as Return) ends it."""
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    latch = ActivationLatch()
    widget = Gtk.Box()
    latch.attach(widget)
    controllers = widget.observe_controllers()
    keys = next(
        controllers.get_item(i)
        for i in range(controllers.get_n_items())
        if isinstance(controllers.get_item(i), Gtk.EventControllerKey)
    )
    no_mods = Gdk.ModifierType(0)

    assert not keys.emit("key-pressed", Gdk.KEY_KP_Enter, KP_ENTER, no_mods)
    assert latch.held and latch.begin()
    # Stop the repeat before Gtk.Button can queue another activation at key-up.
    assert keys.emit("key-pressed", Gdk.KEY_KP_Enter, KP_ENTER, no_mods)
    assert not keys.emit("key-pressed", Gdk.KEY_a, LETTER_A, no_mods)
    # A separate Space that did not trigger the action can still type.
    assert not keys.emit("key-pressed", Gdk.KEY_space, SPACE, no_mods)
    assert not keys.emit("key-pressed", Gdk.KEY_space, SPACE, no_mods)
    keys.emit("key-released", Gdk.KEY_space, SPACE, no_mods)
    assert not latch.begin()
    keys.emit("key-released", Gdk.KEY_Return, KP_ENTER, no_mods)
    assert not latch.held and not latch.latched
    assert not keys.emit("key-pressed", Gdk.KEY_KP_Enter, KP_ENTER, no_mods)


def test_reset_when_the_window_loses_focus() -> None:
    latch = ActivationLatch()
    latch.press(Gdk.KEY_Return, RETURN)
    assert latch.begin()
    latch.reset()
    assert not latch.held
    assert latch.begin()
