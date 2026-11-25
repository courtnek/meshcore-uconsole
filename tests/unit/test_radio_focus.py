"""After Connect/Disconnect, the focus comes back only for a toggle the user
started: autoconnect must not park it on Disconnect."""

from __future__ import annotations

from types import SimpleNamespace

from meshcore_console.ui_gtk.windows.main_window import MainWindow


class FakeButton:
    def __init__(self) -> None:
        self.focused = 0

    def grab_focus(self) -> bool:
        self.focused += 1
        return True


def _window(pending: bool) -> SimpleNamespace:
    """What _restore_radio_focus reads: nothing has the focus (the pending
    button was disabled, or the loading screen went away at startup)."""
    return SimpleNamespace(
        _radio_focus_origin=None,
        _radio_focus_pending=pending,
        _content_stack=SimpleNamespace(get_visible_child_name=lambda: "main"),
        _connect_button=FakeButton(),
        get_focus=lambda: None,
    )


def test_autoconnect_leaves_the_focus_alone() -> None:
    window = _window(pending=False)
    MainWindow._restore_radio_focus(window)
    assert window._connect_button.focused == 0


def test_a_user_toggle_gets_the_focus_back() -> None:
    window = _window(pending=True)
    MainWindow._restore_radio_focus(window)
    assert window._connect_button.focused == 1
    assert window._radio_focus_pending is False  # once per toggle
    MainWindow._restore_radio_focus(window)  # e.g. a later autoconnect retry
    assert window._connect_button.focused == 1
