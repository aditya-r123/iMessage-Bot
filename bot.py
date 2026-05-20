import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path

from openai import OpenAI

CHAT_DB = Path.home() / "Library" / "Messages" / "chat.db"
MODEL = "gpt-5.4-mini"
HISTORY_LIMIT = 10
AUTO_SEND = False  # True: send the suggested reply immediately. False: prompt y/N/edit.
APPLE_EPOCH_OFFSET = 978307200  # seconds between 1970-01-01 and 2001-01-01 UTC


def apple_ns_to_datetime(apple_ns):
    return datetime.fromtimestamp(apple_ns / 1e9 + APPLE_EPOCH_OFFSET)


def extract_attributed_text(blob):
    """Extract plain text from iMessage's `attributedBody` (Apple typedstream blob).
    Modern macOS stores message text here, not in `message.text`."""
    if not blob:
        return None
    idx = blob.find(b'NSString')
    if idx == -1:
        idx = blob.find(b'NSMutableString')
        if idx == -1:
            return None
    plus = blob.find(b'+', idx)
    if plus == -1 or plus + 1 >= len(blob):
        return None
    p = plus + 1
    marker = blob[p]
    if marker == 0x81:
        if p + 3 > len(blob):
            return None
        length = int.from_bytes(blob[p + 1:p + 3], 'little')
        text_start = p + 3
    elif marker == 0x82:
        if p + 5 > len(blob):
            return None
        length = int.from_bytes(blob[p + 1:p + 5], 'little')
        text_start = p + 5
    else:
        length = marker
        text_start = p + 1
    return blob[text_start:text_start + length].decode('utf-8', errors='replace')


def get_contact_identifiers(contact_name):
    """Return (primary_phone, [all_identifiers]) for a contact.
    all_identifiers contains every phone and email on the contact card —
    any of these can be the handle iMessage actually uses."""
    script = f'''
    tell application "Contacts"
        launch
        set thePeople to (people whose name is "{contact_name}")
        if (count of thePeople) > 0 then
            set thePerson to item 1 of thePeople
            set output to ""
            try
                set phoneList to value of phones of thePerson
                repeat with p in phoneList
                    set output to output & "P:" & p & linefeed
                end repeat
            end try
            try
                set emailList to value of emails of thePerson
                repeat with e in emailList
                    set output to output & "E:" & e & linefeed
                end repeat
            end try
            return output
        else
            return "NOT_FOUND"
        end if
    end tell
    '''
    result = subprocess.check_output(['osascript', '-e', script]).decode('utf-8').strip()
    if result == "NOT_FOUND" or not result:
        return None, []

    phones, emails = [], []
    for line in result.splitlines():
        line = line.strip()
        if line.startswith("P:"):
            phones.append(line[2:].strip())
        elif line.startswith("E:"):
            emails.append(line[2:].strip())
    primary_phone = phones[0] if phones else None
    return primary_phone, phones + emails


def clean_number(number):
    return ''.join(filter(lambda x: x.isdigit() or x == '+', number))


def get_recent_messages(identifiers, limit=HISTORY_LIMIT):
    """Fetch recent iMessage history with a contact.
    identifiers: list of phone numbers and/or emails associated with the contact.
    Returns list of (sender, text, datetime) in chronological order."""
    if not CHAT_DB.exists():
        raise FileNotFoundError(f"iMessage database not found at {CHAT_DB}")

    conditions, params = [], []
    for ident in identifiers:
        ident = ident.strip()
        if not ident:
            continue
        if "@" in ident:
            conditions.append("LOWER(handle.id) = LOWER(?)")
            params.append(ident)
        else:
            digits = ''.join(c for c in ident if c.isdigit())
            last10 = digits[-10:] if len(digits) >= 10 else digits
            if last10:
                conditions.append(
                    "REPLACE(REPLACE(REPLACE(REPLACE(handle.id, '+', ''), '-', ''), ' ', ''), '(', '') LIKE ?"
                )
                params.append(f"%{last10}")

    if not conditions:
        return []

    where_handles = " OR ".join(conditions)

    conn = sqlite3.connect(f"file:{CHAT_DB}?mode=ro", uri=True)
    cursor = conn.cursor()

    # Find the single most recently active DM chat with this contact.
    # Match across all of the contact's handles (phones + emails) since iMessage
    # may route via any of them.
    chat_query = f"""
        SELECT chat.ROWID
        FROM chat
        JOIN chat_handle_join ON chat.ROWID = chat_handle_join.chat_id
        JOIN handle ON chat_handle_join.handle_id = handle.ROWID
        JOIN chat_message_join ON chat.ROWID = chat_message_join.chat_id
        JOIN message ON chat_message_join.message_id = message.ROWID
        WHERE chat.style = 45
          AND ({where_handles})
        GROUP BY chat.ROWID
        ORDER BY MAX(message.date) DESC
        LIMIT 1
    """
    cursor.execute(chat_query, params)
    row = cursor.fetchone()
    if row is None:
        conn.close()
        return []
    chat_id = row[0]

    # Pull more than `limit` raw rows so we can drop empty/system rows and still hit the target.
    msg_query = """
        SELECT message.text, message.attributedBody, message.is_from_me, message.date
        FROM message
        JOIN chat_message_join ON message.ROWID = chat_message_join.message_id
        WHERE chat_message_join.chat_id = ?
          AND message.item_type = 0
        ORDER BY message.date DESC
        LIMIT ?
    """
    cursor.execute(msg_query, (chat_id, limit * 3))
    rows = cursor.fetchall()
    conn.close()

    decoded = []
    for text, attr_body, is_from_me, date in rows:
        body = text if text else extract_attributed_text(attr_body)
        if not body:
            continue
        decoded.append(("me" if is_from_me else "them", body, apple_ns_to_datetime(date)))
        if len(decoded) >= limit:
            break
    return list(reversed(decoded))


def suggest_reply(contact_name, history):
    if not history:
        raise ValueError("No prior messages found with this contact.")

    transcript = "\n".join(f"{sender.capitalize()}: {text}" for sender, text, _ in history)

    system_prompt = (
        f"Draft my next iMessage to {contact_name}. Match my tone from prior messages. "
        "Do not repeat or rephrase what I already said — move the conversation forward "
        "with new content (answer a question they asked, react to their last message, "
        "or add a new thought). "
        "Reply with only the message text — no quotes, no preface."
    )
    user_prompt = (
        f"Conversation (Me = me, Them = {contact_name}):\n\n{transcript}"
    )

    print("--- Sending to OpenAI ---")
    print(f"[system]\n{system_prompt}\n")
    print(f"[user]\n{user_prompt}")
    print("--- End ---\n")

    client = OpenAI()
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    return response.choices[0].message.content.strip().strip('"').strip("'")


def send_imessage(clean_num, message):
    applescript_command = f'tell application "Messages" to send "{message}" to buddy "{clean_num}"'
    try:
        subprocess.run(['osascript', '-e', applescript_command], check=True)
        print(f"Message sent successfully to {clean_num}!")
    except subprocess.CalledProcessError:
        print(f"Failed to send message to {clean_num}.")


if __name__ == "__main__":
    name = input("Enter the contact name: ").strip()

    primary_phone, identifiers = get_contact_identifiers(name)
    if not identifiers:
        print(f"Could not find '{name}' in your Contacts app.")
        raise SystemExit(1)

    if not primary_phone:
        print(f"'{name}' has no phone number on file — cannot send iMessage.")
        raise SystemExit(1)

    clean = clean_number(primary_phone)
    print(f"Found {name}. Identifiers: {identifiers}")
    print(f"Will send to {clean}. Pulling recent messages...")

    history = get_recent_messages(identifiers)
    if not history:
        print("No prior messages found — nothing to base a reply on.")
        raise SystemExit(1)

    print(f"Loaded {len(history)} messages:")
    for sender, text, ts in history:
        print(f"  [{ts:%Y-%m-%d %H:%M:%S}] {sender}: {text}")
    print("\nAsking OpenAI for a suggested reply...\n")
    suggestion = suggest_reply(name, history)

    print(f"Suggested reply:\n  {suggestion}\n")

    if AUTO_SEND:
        send_imessage(clean, suggestion)
    else:
        confirm = input("Send this? [y/N/edit]: ").strip().lower()
        if confirm == "edit":
            suggestion = input("Edit the message: ").strip()
            confirm = "y"
        if confirm == "y":
            send_imessage(clean, suggestion)
        else:
            print("Cancelled.")
