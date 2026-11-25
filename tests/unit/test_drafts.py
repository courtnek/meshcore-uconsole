"""Unsent drafts stay with their thread, and a send completing late clears
only the draft it sent."""

from __future__ import annotations

from meshcore_console.ui_gtk.views.messages import DraftBook

DM = "dm:" + "ab" * 32


def test_each_thread_keeps_its_own_draft() -> None:
    drafts = DraftBook()
    drafts.set("public", "hello mesh")
    drafts.set(DM, "hi Bob")
    assert drafts.get("public") == "hello mesh"
    assert drafts.get(DM) == "hi Bob"
    assert drafts.get("#hiking") == ""


def test_send_clears_the_sent_draft() -> None:
    drafts = DraftBook()
    drafts.set("public", "hello mesh")
    sent = drafts.revision("public")
    assert drafts.clear_if_unchanged("public", sent)
    assert drafts.get("public") == ""


def test_completion_after_a_thread_switch_leaves_the_other_draft() -> None:
    drafts = DraftBook()
    drafts.set("public", "hello mesh")
    sent = drafts.revision("public")  # Enter in #public, then the DM opens
    drafts.set(DM, "hi Bob")  # typed in the DM while #public's send is on air
    assert drafts.clear_if_unchanged("public", sent)
    assert drafts.get(DM) == "hi Bob"
    assert drafts.get("public") == ""


def test_draft_edited_during_the_send_is_kept() -> None:
    drafts = DraftBook()
    drafts.set("public", "hello mesh")
    sent = drafts.revision("public")
    drafts.set("public", "hello mesh, and more")
    assert not drafts.clear_if_unchanged("public", sent)
    assert drafts.get("public") == "hello mesh, and more"


def test_retyping_the_same_text_still_counts_as_an_edit() -> None:
    drafts = DraftBook()
    drafts.set("public", "ok")
    sent = drafts.revision("public")
    drafts.set("public", "o")
    drafts.set("public", "ok")  # same text again: a new message, not the sent one
    assert not drafts.clear_if_unchanged("public", sent)
    assert drafts.get("public") == "ok"


def test_removed_thread_does_not_revive_with_a_late_completion() -> None:
    drafts = DraftBook()
    drafts.set("#hiking", "meet at 9")
    sent = drafts.revision("#hiking")
    drafts.discard("#hiking")
    drafts.set("#hiking", "new channel, new draft")
    assert not drafts.clear_if_unchanged("#hiking", sent)
    assert drafts.get("#hiking") == "new channel, new draft"
