#Run with /Users/adityar/.venvs/message_bot/bin/python /Users/adityar/Downloads/iMessage-Bot/bot.py

import threading

from db import (
    get_recent_group_messages,
    get_recent_messages,
    list_all_contacts,
    list_all_group_chats,
)
from gui import pick_target_gui, reply_preview_gui
from messaging import (
    clean_number,
    send_imessage,
    send_imessage_to_chat,
    suggest_group_reply,
    suggest_reply,
)

AUTO_SEND = False  #D True: *DANGEROUS*: send the suggested reply immediately. False: prompt y/N/edit.


_contacts_lock = threading.Lock()
_contacts_cache = {"value": None}


def cached_contacts():
    """Load contacts at most once across both picker tabs."""
    with _contacts_lock:
        if _contacts_cache["value"] is None:
            _contacts_cache["value"] = list_all_contacts()
        return _contacts_cache["value"]


def run_dm(contact):
    name = contact["name"]
    identifiers = contact["phones"] + contact["emails"]
    primary_phone = contact["phones"][0] if contact["phones"] else None

    if not identifiers:
        print(f"'{name}' has no phone or email on file.")
        return
    if not primary_phone:
        print(f"'{name}' has no phone number on file — cannot send iMessage.")
        return

    clean = clean_number(primary_phone)
    print(f"Selected {name}. Identifiers: {identifiers}")
    print(f"Will send to {clean}. Pulling recent messages...")

    history = get_recent_messages(identifiers)
    if not history:
        print("No prior messages found — nothing to base a reply on.")
        return

    print(f"Loaded {len(history)} messages:")
    for sender, text, ts in history:
        print(f"  [{ts:%Y-%m-%d %H:%M:%S}] {sender}: {text}")
    print()

    if AUTO_SEND:
        suggestion = suggest_reply(name, history)
        print(f"Suggested reply:\n  {suggestion}\n")
        send_imessage(clean, suggestion)
    else:
        action, final_text = reply_preview_gui(name, lambda: suggest_reply(name, history))
        if action == "send" and final_text:
            print(f"Sending:\n  {final_text}\n")
            send_imessage(clean, final_text)
        else:
            print("Cancelled.")


def run_group(group):
    name = group["name"]
    guid = group["guid"]
    print(f"Selected group '{name}' ({guid}). Pulling recent messages...")

    history = get_recent_group_messages(group["rowid"], contacts=cached_contacts())
    if not history:
        print("No prior messages found in this group — nothing to base a reply on.")
        return

    print(f"Loaded {len(history)} messages:")
    for sender, text, ts in history:
        print(f"  [{ts:%Y-%m-%d %H:%M:%S}] {sender}: {text}")
    print()

    if AUTO_SEND:
        suggestion = suggest_group_reply(name, history)
        print(f"Suggested reply:\n  {suggestion}\n")
        send_imessage_to_chat(guid, suggestion)
    else:
        action, final_text = reply_preview_gui(name, lambda: suggest_group_reply(name, history))
        if action == "send" and final_text:
            print(f"Sending:\n  {final_text}\n")
            send_imessage_to_chat(guid, final_text)
        else:
            print("Cancelled.")


if __name__ == "__main__":
    print("Opening picker (loading contacts & group chats in background)...")
    chosen = pick_target_gui(
        load_contacts_fn=cached_contacts,
        load_groups_fn=lambda: list_all_group_chats(cached_contacts()),
    )
    if not chosen:
        print("Nothing selected. Cancelled.")
        raise SystemExit(0)

    if chosen["kind"] == "dm":
        run_dm(chosen["contact"])
    else:
        run_group(chosen["group"])
