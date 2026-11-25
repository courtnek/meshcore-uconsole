"""Peers are targeted by identity, not list position or name.

An advert reorders the peer list; the peer that was focused or selected must
stay the target, and a name that passed to another node must not redirect
Send Message to it."""

from __future__ import annotations

from meshcore_console.core.models import Peer, find_peer, peer_identity
from meshcore_console.mock import MockMeshcoreClient

ALICE_KEY = "aa" * 32
BOB_KEY = "bb" * 32


def peer(name: str, key: str | None, peer_id: str | None = None) -> Peer:
    return Peer(peer_id=peer_id or name, display_name=name, public_key=key)


def test_identity_survives_a_reorder() -> None:
    before = [peer("Alice", ALICE_KEY), peer("Bob", BOB_KEY)]
    focused = peer_identity(before[1])  # Bob's row has the focus
    after = [peer("Bob", BOB_KEY), peer("Alice", ALICE_KEY)]  # Bob adverted
    assert after[1].display_name == "Alice"  # the old position is somebody else now
    found = find_peer(after, focused)
    assert found is not None and found.display_name == "Bob"


def test_identity_survives_a_rename() -> None:
    target = peer_identity(peer("Bob", BOB_KEY))
    found = find_peer([peer("Robert", BOB_KEY)], target)
    assert found is not None and found.display_name == "Robert"


def test_a_name_passed_to_another_node_is_not_the_target() -> None:
    target = peer_identity(peer("Bob", BOB_KEY))
    # Bob is gone; another node now calls itself Bob
    assert find_peer([peer("Bob", "cc" * 32)], target) is None


def test_peer_without_a_key_falls_back_to_its_id() -> None:
    assert peer_identity(peer("relay", None, peer_id="relay-001")) == "relay-001"


def test_send_message_resolves_the_thread_from_the_current_peer() -> None:
    # What Peers' Send Message does: re-resolve by identity, then ask the
    # service for that peer's DM thread.
    client = MockMeshcoreClient()
    peers = client.list_peers()
    chosen = next(p for p in peers if not p.is_repeater)
    current = find_peer(client.list_peers(), peer_identity(chosen))
    assert current is not None
    channel = client.ensure_dm_channel(current.display_name)
    assert channel.kind == "dm"
    assert channel.peer_name == chosen.display_name
