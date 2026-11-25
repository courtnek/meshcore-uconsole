from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gdk, GLib, Gtk, Pango

if TYPE_CHECKING:
    pass

from meshcore_console.core.models import Peer, find_peer, peer_identity
from meshcore_console.core.radio import format_rssi, format_snr
from meshcore_console.core.services import MeshcoreService
from meshcore_console.ui_gtk.helpers import clear_children, clear_listbox, navigate
from meshcore_console.ui_gtk.keyboard import activation_allowed, focus_within, single_tab_list_box
from meshcore_console.ui_gtk.layout import Layout
from meshcore_console.ui_gtk.state import UiEventStore
from meshcore_console.core.time import to_local
from meshcore_console.ui_gtk.widgets import (
    DetailRow,
    EmptyState,
    PathVisualization,
    PeerListRow,
)
from meshcore_console.ui_gtk.widgets.node_badge import STYLE_DEFAULT, STYLE_SELF

logger = logging.getLogger(__name__)


def format_public_key(key: str | None) -> str:
    """Format public key as two-byte hex chunks."""
    if not key:
        return "Unknown"
    # Split into 4-char (2 byte) chunks
    chunks = [key[i : i + 4] for i in range(0, len(key), 4)]
    return " ".join(chunks)


def format_coordinates(lat: float | None, lon: float | None) -> str:
    """Format lat/lon coordinates for display."""
    if lat is None or lon is None:
        return "Unknown"
    lat_dir = "N" if lat >= 0 else "S"
    lon_dir = "E" if lon >= 0 else "W"
    return f"{abs(lat):.5f}° {lat_dir}, {abs(lon):.5f}° {lon_dir}"


class PeersView(Gtk.Box):
    def __init__(self, service: MeshcoreService, event_store: UiEventStore, layout: Layout) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._service = service
        self._event_store = event_store
        self._last_peer_snapshot: str = ""
        self._selected_peer: Peer | None = None
        self._refreshing = False
        self._peer_telemetry: dict[str, dict] = {}

        # Column 1: Contacts
        contacts_column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        contacts_column.add_css_class("panel-card")
        contacts_column.set_size_request(layout.peers_list_width, -1)

        contacts_header = Gtk.Label(label="Contacts")
        contacts_header.add_css_class("panel-title")
        contacts_header.set_halign(Gtk.Align.START)
        contacts_header.set_margin_start(12)
        contacts_header.set_margin_top(10)
        contacts_header.set_margin_bottom(8)
        contacts_column.append(contacts_header)

        contacts_scroll = Gtk.ScrolledWindow.new()
        contacts_scroll.set_vexpand(True)
        contacts_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self._contacts_list = single_tab_list_box()
        self._contacts_list.add_css_class("peer-list")
        self._contacts_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._contacts_list.connect("row-selected", self._on_peer_selected)
        contacts_scroll.set_child(self._contacts_list)
        contacts_column.append(contacts_scroll)

        self.append(contacts_column)

        # Column 2: Repeaters/Network
        network_column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        network_column.add_css_class("panel-card")
        network_column.set_size_request(layout.peers_list_width, -1)

        network_header = Gtk.Label(label="Repeaters")
        network_header.add_css_class("panel-title")
        network_header.set_halign(Gtk.Align.START)
        network_header.set_margin_start(12)
        network_header.set_margin_top(10)
        network_header.set_margin_bottom(8)
        network_column.append(network_header)

        network_scroll = Gtk.ScrolledWindow.new()
        network_scroll.set_vexpand(True)
        network_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self._network_list = single_tab_list_box()
        self._network_list.add_css_class("peer-list")
        self._network_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._network_list.connect("row-selected", self._on_peer_selected)
        network_scroll.set_child(self._network_list)
        network_column.append(network_scroll)

        self.append(network_column)

        # Column 3: Details panel
        details_column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        details_column.add_css_class("panel-card")
        details_column.set_hexpand(True)

        self._details_title = Gtk.Label(label="Select a peer")
        self._details_title.add_css_class("panel-title")
        self._details_title.set_halign(Gtk.Align.START)
        self._details_title.set_margin_start(12)
        self._details_title.set_margin_top(10)
        self._details_title.set_margin_bottom(8)
        details_column.append(self._details_title)

        self._details_scroll = Gtk.ScrolledWindow.new()
        details_scroll = self._details_scroll
        details_scroll.set_vexpand(True)
        details_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self._details_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._details_content.set_margin_start(12)
        self._details_content.set_margin_end(12)
        self._details_content.set_margin_bottom(12)
        details_scroll.set_child(self._details_content)
        details_column.append(details_scroll)

        self.append(details_column)

        keys = Gtk.EventControllerKey.new()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_peer_key)
        self.add_controller(keys)

        self._show_empty_details()
        self._refresh_peers()
        self._event_store.connect("events-available", lambda _store: self._poll_peers())

    def _on_peer_key(
        self,
        _controller: Gtk.EventControllerKey,
        keyval: int,
        _keycode: int,
        state: Gdk.ModifierType,
    ) -> bool:
        if state & (
            Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.SUPER_MASK
        ):
            return False

        in_contacts = focus_within(self._contacts_list)
        in_repeaters = focus_within(self._network_list)
        if in_contacts and keyval == Gdk.KEY_Right:
            return self._network_list.child_focus(Gtk.DirectionType.TAB_FORWARD)
        if in_repeaters and keyval == Gdk.KEY_Left:
            return self._contacts_list.child_focus(Gtk.DirectionType.TAB_FORWARD)

        if keyval not in (Gdk.KEY_Tab, Gdk.KEY_KP_Tab, Gdk.KEY_ISO_Left_Tab):
            return False
        selected = self._selected_peer
        if selected is None:
            return False
        backward = keyval == Gdk.KEY_ISO_Left_Tab or bool(state & Gdk.ModifierType.SHIFT_MASK)
        if not backward and (in_contacts or in_repeaters):
            # Entering the other list selects its first row. Go to the
            # chosen peer's details so Tab can't change the action target.
            return self._details_scroll.child_focus(Gtk.DirectionType.TAB_FORWARD)
        if backward and focus_within(self._details_scroll):
            if self._details_scroll.child_focus(Gtk.DirectionType.TAB_BACKWARD):
                return True
            row = self._row_for(peer_identity(selected))
            return row is not None and row.grab_focus()
        return False

    @staticmethod
    def _peer_snapshot(peers: list[Peer]) -> str:
        """Compute a snapshot string to detect peer data changes."""
        parts = []
        for p in peers:
            parts.append(
                f"{p.display_name}:{p.last_advert_time}:{p.signal_quality}:{p.rssi}:{p.snr}:{p.is_favorite}"
            )
        return "|".join(parts)

    def _poll_peers(self) -> bool:
        """Check for peer changes and refresh if needed."""
        peers = self._service.list_peers()
        snapshot = self._peer_snapshot(peers)
        if snapshot != self._last_peer_snapshot:
            self._refresh_peers()
        return True

    def _refresh_peers(self) -> None:
        """Refresh the peers list display, preserving selection, focus and the
        details panel.

        This runs on every advert anywhere in the mesh.  The rebuild used to
        re-render the details panel (scroll jumped to the top, an in-flight
        telemetry request lost its spinner and the button re-armed, inviting
        duplicate requests) and move keyboard focus to another row.

        Selection and focus follow the peer (``peer_identity``), not the row
        position: an advert reorders the list, and the row that was at the
        focused position is then somebody else.
        """
        selected_key = peer_identity(self._selected_peer) if self._selected_peer else None
        focus_list, focus_key = self._focused_row()
        self._refreshing = True
        try:
            self._rebuild_lists(selected_key)
        finally:
            self._refreshing = False
        # The new rows aren't mapped until the next frame, so restore focus
        # (and undo the selection GTK makes when focus falls back to the list)
        # once they are.
        GLib.idle_add(self._restore_focus, selected_key, focus_list, focus_key)

    def _restore_focus(
        self,
        selected_key: str | None,
        focus_list: Gtk.ListBox | None,
        focus_key: str | None,
    ) -> bool:
        target = self._row_for(selected_key) if selected_key else None
        self._refreshing = True  # a rebuild must not select anything by itself
        try:
            if focus_list is not None:
                row = self._row_for(focus_key) if focus_key else None
                if row is None and target is not None and target.get_parent() is focus_list:
                    row = target  # the focused peer is gone: back to the selection
                if row is not None:
                    row.grab_focus()
                elif not focus_list.grab_focus():
                    first = focus_list.get_row_at_index(0)
                    if first is not None:
                        first.grab_focus()
            # The selection is the action target: exactly the peer it was
            for listbox in (self._contacts_list, self._network_list):
                if target is None or target.get_parent() is not listbox:
                    listbox.unselect_all()
            if target is not None and not target.is_selected():
                parent = target.get_parent()
                if isinstance(parent, Gtk.ListBox):
                    parent.select_row(target)
        finally:
            self._refreshing = False
        if (
            target is None
            and selected_key is not None
            and self._selected_peer is not None
            and peer_identity(self._selected_peer) == selected_key
        ):
            # The selected peer left the list.  Drop it rather than let its
            # buttons (or GTK's fallback selection) act on somebody else.
            logger.debug("UI: selected peer left the list")
            self._selected_peer = None
            self._show_empty_details()
        return False

    def _focused_row(self) -> tuple[Gtk.ListBox | None, str | None]:
        root = self.get_root()
        focus = root.get_focus() if root is not None else None
        if isinstance(focus, Gtk.ListBoxRow):
            for listbox in (self._contacts_list, self._network_list):
                if focus.get_parent() is listbox:
                    peer = getattr(focus, "peer", None)
                    return listbox, peer_identity(peer) if peer is not None else None
        return None, None

    def _row_for(self, identity: str) -> Gtk.ListBoxRow | None:
        """The row showing the peer with ``identity``, in either list."""
        for listbox in (self._contacts_list, self._network_list):
            index = 0
            while (row := listbox.get_row_at_index(index)) is not None:
                peer = getattr(row, "peer", None)
                if peer is not None and peer_identity(peer) == identity:
                    return row
                index += 1
        return None

    def _current_peer(self, peer: Peer) -> Peer | None:
        """The service's current record of ``peer``, looked up by identity
        when an action runs: the details may show a stale copy (renamed since,
        or gone)."""
        return find_peer(self._service.list_peers(), peer_identity(peer))

    def _rebuild_lists(self, selected_key: str | None) -> None:
        # Clear existing rows
        for listbox in (self._contacts_list, self._network_list):
            clear_listbox(listbox)

        peers = self._service.list_peers()
        self._last_peer_snapshot = self._peer_snapshot(peers)

        contacts = sorted(
            (p for p in peers if not p.is_repeater),
            key=lambda p: (p.is_favorite, p.last_advert_time is not None, p.last_advert_time),
            reverse=True,
        )
        network = sorted(
            (p for p in peers if p.is_repeater),
            key=lambda p: (p.is_favorite, p.last_advert_time is not None, p.last_advert_time),
            reverse=True,
        )

        self._populate_list(self._contacts_list, contacts, "No contacts yet")
        self._populate_list(self._network_list, network, "No repeaters yet")

        # Restore selection
        if selected_key:
            row = self._row_for(selected_key)
            if row is not None:
                parent = row.get_parent()
                if isinstance(parent, Gtk.ListBox):
                    parent.select_row(row)

    def _populate_list(self, listbox: Gtk.ListBox, peers: list[Peer], empty_msg: str) -> None:
        """Populate a listbox with peers."""
        if not peers:
            row = Gtk.ListBoxRow.new()
            row.set_selectable(False)
            row.set_child(EmptyState(empty_msg))
            listbox.append(row)
            return

        for peer in peers:
            listbox.append(PeerListRow(peer))

    def _on_peer_selected(self, listbox: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        """Handle peer selection to show details."""
        if self._refreshing:
            # Rows are being rebuilt: keep the details panel as it is, only
            # pick up the fresh Peer object for the same selection.
            peer = getattr(row, "peer", None) if row is not None else None
            if peer is not None and self._selected_peer is not None:
                if peer_identity(peer) == peer_identity(self._selected_peer):
                    self._selected_peer = peer
            return
        # Deselect the other list
        other = self._network_list if listbox == self._contacts_list else self._contacts_list
        other.unselect_all()

        if row is None:
            if listbox.get_selected_row() is None and other.get_selected_row() is None:
                self._selected_peer = None  # or the next refresh re-selects it
            self._show_empty_details()
            return

        peer = getattr(row, "peer", None)
        if peer is None:
            self._show_empty_details()
            return

        logger.debug("UI: peer selected name=%s id=%s", peer.display_name, peer.peer_id)
        self._selected_peer = peer
        self._show_peer_details(peer)

    def get_default_focus(self) -> Gtk.Widget:
        """Return the widget that should receive focus when this view is shown:
        the selected peer's row, so browsing continues where it was."""
        if self._selected_peer is not None:
            row = self._row_for(peer_identity(self._selected_peer))
            if row is not None:
                return row
        return self._contacts_list

    def _show_empty_details(self) -> None:
        """Show empty state in details panel."""
        self._details_title.set_text("Peer Details")
        self._clear_details()
        self._details_content.append(
            EmptyState("Select a contact or repeater to view details", vexpand=True)
        )

    def _clear_details(self) -> None:
        """Clear the details content area."""
        clear_children(self._details_content)

    def _show_peer_details(self, peer: Peer) -> None:
        """Show details for a selected peer."""
        self._details_title.set_text(peer.display_name)
        self._clear_details()

        # Type badge
        type_label = Gtk.Label(label="Repeater" if peer.is_repeater else "Contact")
        type_label.add_css_class("status-pill")
        type_label.add_css_class("ok" if not peer.is_repeater else "warn")
        type_label.set_halign(Gtk.Align.START)
        self._details_content.append(type_label)

        # === Signal Section ===
        self._add_section_header("Signal")

        if peer.rssi is not None:
            self._details_content.append(DetailRow("RSSI:", format_rssi(peer.rssi)))
        if peer.snr is not None:
            self._details_content.append(DetailRow("SNR:", format_snr(peer.snr)))
        if peer.signal_quality is not None:
            self._details_content.append(DetailRow("Quality:", f"{peer.signal_quality}%"))

        if peer.rssi is None and peer.snr is None and peer.signal_quality is None:
            no_signal = Gtk.Label(label="No signal data available")
            no_signal.add_css_class("panel-muted")
            no_signal.set_halign(Gtk.Align.START)
            self._details_content.append(no_signal)

        # === Location Section ===
        self._add_section_header("Location")

        if peer.latitude is not None and peer.longitude is not None:
            coords = format_coordinates(peer.latitude, peer.longitude)
            self._details_content.append(DetailRow("Coordinates:", coords))

            if peer.location_updated:
                loc_time = to_local(peer.location_updated).strftime("%b %d at %H:%M")
                self._details_content.append(DetailRow("Updated:", loc_time))
        else:
            no_loc = Gtk.Label(label="No location reported")
            no_loc.add_css_class("panel-muted")
            no_loc.set_halign(Gtk.Align.START)
            self._details_content.append(no_loc)

        # === Last Seen Section ===
        self._add_section_header("Activity")

        if peer.last_advert_time:
            time_str = to_local(peer.last_advert_time).strftime("%b %d, %Y at %I:%M %p")
            self._details_content.append(DetailRow("Last Seen:", time_str))
        else:
            self._details_content.append(DetailRow("Last Seen:", "Unknown"))

        # === Network Path Section ===
        self._add_section_header("Network Path")

        if peer.last_path:
            peer_prefix = (peer.public_key or peer.display_name)[:2].upper()
            self_key = self._service.get_self_public_key()
            self_prefix = (self_key or "")[:2].upper() or "Me"
            self_name = self._service.get_status().node_id
            path = PathVisualization(
                hops=peer.last_path,
                peers=self._service.list_peers(),
                start=(self_prefix, self_name, None, STYLE_SELF),
                end=(peer_prefix, peer.display_name, peer, STYLE_DEFAULT),
            )
            self._details_content.append(path)
        else:
            direct_label = Gtk.Label(label="Direct connection (no hops)")
            direct_label.add_css_class("panel-muted")
            direct_label.set_halign(Gtk.Align.START)
            self._details_content.append(direct_label)

        # === Public Key Section ===
        self._add_section_header("Public Key")

        key_text = format_public_key(peer.public_key)
        key_label = Gtk.Label(label=key_text)
        key_label.add_css_class("analyzer-raw")
        key_label.set_halign(Gtk.Align.START)
        key_label.set_wrap(True)
        key_label.set_wrap_mode(Pango.WrapMode.CHAR)
        key_label.set_selectable(True)
        self._details_content.append(key_label)

        # === Cached Telemetry ===
        cached = self._peer_telemetry.get(peer.peer_id)
        if cached is not None:
            self._render_telemetry(cached)

        # === Action buttons === (wrap rather than widen the window)
        # Each FlowBox child is one tab stop, the arrow keys move between them
        # and Enter runs the button inside (child-activated).  Focusable
        # buttons inside focusable children made two stops per action, and
        # Enter on the child did nothing.
        actions = Gtk.FlowBox()
        actions.set_selection_mode(Gtk.SelectionMode.NONE)
        actions.add_css_class("peer-actions")
        actions.connect("child-activated", self._on_action_child_activated)
        actions.set_column_spacing(8)
        actions.set_row_spacing(8)
        actions.set_max_children_per_line(3)
        actions.set_margin_top(16)

        fav_label = "☆ Favorite" if not peer.is_favorite else "★ Unfavorite"
        fav_btn = Gtk.Button.new_with_label(fav_label)
        fav_btn.connect("clicked", self._on_toggle_favorite_clicked, peer)
        actions.append(fav_btn)

        if not peer.is_repeater:
            message_btn = Gtk.Button.new_with_label("Send Message")
            message_btn.add_css_class("suggested-action")
            message_btn.connect("clicked", self._on_send_message_clicked, peer)
            actions.append(message_btn)

        telem_btn = Gtk.Button.new_with_label("Request Telemetry")
        telem_btn.connect("clicked", self._on_request_telemetry_clicked, peer)
        actions.append(telem_btn)

        child = actions.get_first_child()
        while child is not None:
            button = child.get_first_child() if isinstance(child, Gtk.FlowBoxChild) else None
            if isinstance(button, Gtk.Button):
                button.set_focusable(False)  # the FlowBox child holds the focus
            child = child.get_next_sibling()

        self._details_content.append(actions)

    @staticmethod
    def _on_action_child_activated(flowbox: Gtk.FlowBox, child: Gtk.FlowBoxChild) -> None:
        button = child.get_child()
        if not isinstance(button, Gtk.Button) or not button.is_sensitive():
            return
        if not activation_allowed(flowbox):
            return  # Enter held: one favorite toggle, one request
        button.emit("clicked")

    def _add_section_header(self, title: str) -> None:
        """Add a section header to the details panel."""
        header = Gtk.Label(label=title)
        header.add_css_class("message-detail-header")
        header.set_halign(Gtk.Align.START)
        header.set_margin_top(12)
        self._details_content.append(header)

    def select_peer(self, peer_id: str) -> None:
        """Select a peer programmatically and show its details."""
        for listbox in (self._contacts_list, self._network_list):
            index = 0
            while True:
                row = listbox.get_row_at_index(index)
                if row is None:
                    break
                peer = getattr(row, "peer", None)
                if peer and peer.peer_id == peer_id:
                    listbox.select_row(row)
                    return
                index += 1

    def _on_toggle_favorite_clicked(self, _button: Gtk.Button, peer: Peer) -> None:
        """Toggle the favorite status of a peer."""
        current = self._current_peer(peer)
        if current is None:
            return
        logger.debug(
            "UI: toggle favorite peer=%s currently=%s", current.display_name, current.is_favorite
        )
        self._service.set_favorite(current.peer_id, not current.is_favorite)
        GLib.idle_add(self._refresh_peers)

    def _on_send_message_clicked(self, _button: Gtk.Button, peer: Peer) -> None:
        """Open the DM thread with this peer in Messages, ready to compose.

        The thread is resolved now, by the peer's identity: the button was
        built with the peer as it was then, and its name may since belong to
        somebody else.
        """
        current = self._current_peer(peer)
        if current is None:
            logger.debug("UI: send message to a peer that left the list")
            return
        logger.debug("UI: send message to peer=%s", current.display_name)
        channel = self._service.ensure_dm_channel(
            current.display_name, public_key=current.public_key
        )
        navigate(self, "messages", ("open_dm", channel.channel_id))

    def _on_request_telemetry_clicked(self, button: Gtk.Button, stale: Peer) -> None:
        """Request telemetry from a peer in a background thread."""
        import threading

        peer = self._current_peer(stale)
        if peer is None:
            return

        button.set_sensitive(False)
        button.set_label("Requesting...")

        # Add a spinner below the actions
        spinner_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        spinner_box.set_halign(Gtk.Align.START)
        spinner = Gtk.Spinner()
        spinner.start()
        spinner_box.append(spinner)
        spinner_label = Gtk.Label(label="Waiting for telemetry response...")
        spinner_label.add_css_class("panel-muted")
        spinner_box.append(spinner_label)
        self._details_content.append(spinner_box)

        def _do_request() -> None:
            try:
                logger.debug("telemetry request starting for peer=%s", peer.display_name)
                result = self._service.request_telemetry(peer.display_name)
                logger.debug("telemetry request result: %s", result)
                GLib.idle_add(self._show_telemetry_result, spinner_box, button, peer, result)
            except Exception as exc:
                logger.exception("telemetry request failed for peer=%s", peer.display_name)
                GLib.idle_add(self._show_telemetry_error, spinner_box, button, peer, str(exc))

        threading.Thread(target=_do_request, daemon=True, name="telemetry-req").start()

    def _show_telemetry_result(
        self, spinner_box: Gtk.Box, button: Gtk.Button, peer: Peer, result: dict
    ) -> None:
        """Display telemetry results in the details panel (called on main thread)."""
        self._peer_telemetry[peer.peer_id] = result

        if self._selected_peer is None or self._selected_peer.peer_id != peer.peer_id:
            return

        if spinner_box.get_parent() is not None:
            self._details_content.remove(spinner_box)
        button.set_sensitive(True)
        button.set_label("Request Telemetry")

        self._render_telemetry(result)
        self._scroll_details_to_bottom()

    def _show_telemetry_error(
        self, spinner_box: Gtk.Box, button: Gtk.Button, peer: Peer, error: str
    ) -> None:
        """Display telemetry error in the details panel (called on main thread)."""
        if self._selected_peer is None or self._selected_peer.peer_id != peer.peer_id:
            return

        if spinner_box.get_parent() is not None:
            self._details_content.remove(spinner_box)
        button.set_sensitive(True)
        button.set_label("Request Telemetry")

        self._add_section_header("Telemetry")

        err_scroll = Gtk.ScrolledWindow()
        err_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        err_scroll.set_max_content_height(120)
        err_scroll.set_propagate_natural_height(True)

        err_label = Gtk.Label(label=f"Request failed: {error}")
        err_label.add_css_class("panel-muted")
        err_label.set_halign(Gtk.Align.START)
        err_label.set_wrap(True)
        err_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        err_label.set_selectable(True)
        err_scroll.set_child(err_label)
        self._details_content.append(err_scroll)

        self._scroll_details_to_bottom()

    def _render_telemetry(self, result: dict) -> None:
        """Render telemetry data widgets into the details panel."""
        self._add_section_header("Telemetry")

        if not result or not result.get("success"):
            reason = result.get("reason", "No telemetry data returned") if result else "No response"
            no_data = Gtk.Label(label=reason)
            no_data.add_css_class("panel-muted")
            no_data.set_halign(Gtk.Align.START)
            self._details_content.append(no_data)
            return

        rtt = result.get("rtt_ms")
        if rtt is not None:
            self._details_content.append(DetailRow("RTT:", f"{rtt:.0f} ms"))

        telemetry = result.get("telemetry_data") or {}
        sensors = telemetry.get("sensors", [])

        if not sensors:
            text = telemetry.get("formatted") or result.get("response_text") or "No sensor data"
            fallback = Gtk.Label(label=text)
            fallback.add_css_class("panel-muted")
            fallback.set_halign(Gtk.Align.START)
            self._details_content.append(fallback)
            return

        for sensor in sensors:
            value = sensor.get("value")
            sensor_type = sensor.get("type", "Unknown")

            if isinstance(value, dict) and "latitude" in value:
                self._details_content.append(
                    DetailRow(
                        "Location:", format_coordinates(value["latitude"], value["longitude"])
                    )
                )
                alt = value.get("altitude")
                if alt is not None and alt != 0.0:
                    self._details_content.append(DetailRow("Altitude:", f"{alt:.1f} m"))
            elif sensor_type == "Voltage":
                self._details_content.append(DetailRow("Battery:", f"{value:.2f} V"))
            elif sensor_type == "Temperature":
                self._details_content.append(DetailRow("Temperature:", f"{value:.1f} °C"))
            elif sensor_type == "Humidity":
                self._details_content.append(DetailRow("Humidity:", f"{value:.0f}%"))
            elif sensor_type == "Barometer":
                self._details_content.append(DetailRow("Pressure:", f"{value:.1f} hPa"))
            else:
                self._details_content.append(DetailRow(f"{sensor_type}:", str(value)))

    def _scroll_details_to_bottom(self) -> None:
        """Scroll the details panel to the bottom after content is added."""

        def _do_scroll() -> bool:
            vadj = self._details_scroll.get_vadjustment()
            vadj.set_value(vadj.get_upper())
            return False

        # Defer so GTK has time to allocate the new widgets
        GLib.idle_add(_do_scroll)
