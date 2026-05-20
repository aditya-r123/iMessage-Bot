#Run with /Users/adityar/.venvs/message_bot/bin/python /Users/adityar/Downloads/iMessage-Bot/bot.py

from db import get_recent_messages, list_all_contacts
from gui import pick_contact_gui, reply_preview_gui
from messaging import clean_number, send_imessage, suggest_reply

AUTO_SEND = False  #D True: *DANGEROUS*: send the suggested reply immediately. False: prompt y/N/edit.


if __name__ == "__main__":
    print("Opening contact picker (loading contacts in background)...")
    chosen = pick_contact_gui(list_all_contacts)
    if not chosen:
        print("No contact selected. Cancelled.")
        raise SystemExit(0)

    name = chosen["name"]
    identifiers = chosen["phones"] + chosen["emails"]
    primary_phone = chosen["phones"][0] if chosen["phones"] else None

    if not identifiers:
        print(f"'{name}' has no phone or email on file.")
        raise SystemExit(1)
    if not primary_phone:
        print(f"'{name}' has no phone number on file — cannot send iMessage.")
        raise SystemExit(1)

    clean = clean_number(primary_phone)
    print(f"Selected {name}. Identifiers: {identifiers}")
    print(f"Will send to {clean}. Pulling recent messages...")

    history = get_recent_messages(identifiers)
    if not history:
        print("No prior messages found — nothing to base a reply on.")
        raise SystemExit(1)

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
