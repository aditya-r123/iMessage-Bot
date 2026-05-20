import sqlite3
import subprocess
from pathlib import Path

from openai import OpenAI

CHAT_DB = Path.home() / "Library" / "Messages" / "chat.db"
MODEL = "gpt-5.4-mini"
HISTORY_LIMIT = 10


def get_number_by_name(contact_name):
    script = f'''
    tell application "Contacts"
        launch
        set thePeople to (people whose name is "{contact_name}")
        if (count of thePeople) > 0 then
            set thePerson to item 1 of thePeople
            set thePhones to value of phones of thePerson
            return item 1 of thePhones
        else
            return "NOT_FOUND"
        end if
    end tell
    '''
    result = subprocess.check_output(['osascript', '-e', script]).decode('utf-8').strip()
    return None if result == "NOT_FOUND" else result


def clean_number(number):
    return ''.join(filter(lambda x: x.isdigit() or x == '+', number))


def get_recent_messages(phone_number, limit=HISTORY_LIMIT):
    """Fetch recent iMessage history with a contact. Returns list of (sender, text)
    in chronological order, where sender is 'me' or 'them'."""
    if not CHAT_DB.exists():
        raise FileNotFoundError(f"iMessage database not found at {CHAT_DB}")

    digits = ''.join(c for c in phone_number if c.isdigit())
    last10 = digits[-10:] if len(digits) >= 10 else digits

    conn = sqlite3.connect(f"file:{CHAT_DB}?mode=ro", uri=True)
    cursor = conn.cursor()

    query = """
        SELECT message.text, message.is_from_me, message.date
        FROM message
        JOIN chat_message_join ON message.ROWID = chat_message_join.message_id
        WHERE message.text IS NOT NULL
          AND chat_message_join.chat_id IN (
            SELECT chat.ROWID
            FROM chat
            JOIN chat_handle_join ON chat.ROWID = chat_handle_join.chat_id
            JOIN handle ON chat_handle_join.handle_id = handle.ROWID
            WHERE chat.style = 45
              AND REPLACE(REPLACE(REPLACE(REPLACE(handle.id, '+', ''), '-', ''), ' ', ''), '(', '')
                  LIKE ?
          )
        ORDER BY message.date DESC
        LIMIT ?
    """
    cursor.execute(query, (f"%{last10}", limit))
    rows = cursor.fetchall()
    conn.close()

    return [
        ("me" if is_from_me else "them", text)
        for text, is_from_me, _ in reversed(rows)
    ]


def suggest_reply(contact_name, history):
    if not history:
        raise ValueError("No prior messages found with this contact.")

    transcript = "\n".join(f"{sender.capitalize()}: {text}" for sender, text in history)

    system_prompt = (
        f"Draft my next iMessage to {contact_name}. Match my tone from prior messages. "
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

    number = get_number_by_name(name)
    if not number:
        print(f"Could not find '{name}' in your Contacts app.")
        raise SystemExit(1)

    clean = clean_number(number)
    print(f"Found {name} at {clean}. Pulling recent messages...")

    history = get_recent_messages(clean)
    if not history:
        print("No prior messages found — nothing to base a reply on.")
        raise SystemExit(1)

    print(f"Loaded {len(history)} messages. Asking OpenAI for a suggested reply...\n")
    suggestion = suggest_reply(name, history)

    print(f"Suggested reply:\n  {suggestion}\n")
    confirm = input("Send this? [y/N/edit]: ").strip().lower()

    if confirm == "edit":
        suggestion = input("Edit the message: ").strip()
        confirm = "y"

    if confirm == "y":
        send_imessage(clean, suggestion)
    else:
        print("Cancelled.")

