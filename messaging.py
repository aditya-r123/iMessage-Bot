import os
import re
import subprocess
import time
from dotenv import load_dotenv
from openai import OpenAI

# .env wins over shell-exported env vars; either alone works too.
load_dotenv(override=True)

MODEL = "gpt-5.4-mini"

# Pause between consecutive chunks so iMessage delivers them in order.
INTER_MESSAGE_DELAY_S = 0.3

# How strongly to bias the model toward multiple short messages vs one longer one.
# 0.0 = always one message; 0.5 = neutral; 1.0 = always split. Overridable via
# the MULTI_MESSAGE_WEIGHT env var (see .env.example).
MULTI_MESSAGE_WEIGHT = 1.0

DEFAULT_DM_PROMPT = (
    "Draft my next iMessage to {name}. "
    "The transcript below shows our recent conversation. Lines beginning with 'Me:' are "
    "messages I sent; lines beginning with 'Them:' are messages from {name}. "
    "I am drafting the next message from me. "
    "Match my tone, formality, and message length from the 'Me:' lines specifically. "
    "Critical: do NOT repeat, paraphrase, or restate anything I already said in a 'Me:' line. "
    "Move the conversation forward with new content — answer a question they asked, react to "
    "their most recent message, or add a new thought. "
    "If the reply naturally breaks into multiple separate thoughts, output each as its own "
    "short message — one thought per paragraph, separated by a blank line — and they'll be "
    "delivered as consecutive iMessages. "
    "Reply with only the message text — no quotes, no preface, no labels."
)

DEFAULT_GROUP_PROMPT = (
    "Draft my next iMessage to the group chat '{name}'. "
    "The transcript below shows recent messages from multiple people. Lines beginning with "
    "'Me:' are messages I sent; every other line begins with the sender's name (e.g. "
    "'Aaron Smith:', 'Betty Jones:'). "
    "I am drafting the next message from me. "
    "Pay close attention to who said what — track each person separately so your reply fits "
    "naturally into the ongoing conversation and references the right speaker. "
    "Match my tone, formality, and message length from the 'Me:' lines specifically. "
    "Critical: do NOT repeat, paraphrase, or restate anything I already said in a 'Me:' line, "
    "and do not impersonate or echo what someone else said. "
    "Move the conversation forward with new content — answer a question someone asked, react "
    "to the latest message, or add a new thought. "
    "If the reply naturally breaks into multiple separate thoughts, output each as its own "
    "short message — one thought per paragraph, separated by a blank line — and they'll be "
    "delivered as consecutive iMessages. "
    "Reply with only the message text — no quotes, no preface, no labels."
)


def _split_into_messages(text):
    """Split a draft into separate iMessages by blank lines.
    Each non-empty paragraph becomes its own message."""
    chunks = re.split(r"\n\s*\n+", (text or "").strip())
    return [c.strip() for c in chunks if c.strip()]


def _resolve_weight():
    """Read MULTI_MESSAGE_WEIGHT from env, falling back to the module default. Clamped to [0, 1]."""
    raw = os.environ.get("MULTI_MESSAGE_WEIGHT")
    try:
        w = float(raw) if raw not in (None, "") else MULTI_MESSAGE_WEIGHT
    except ValueError:
        w = MULTI_MESSAGE_WEIGHT
    return max(0.0, min(1.0, w))


def _multi_message_guidance(weight):
    """Inline the weight as guidance for the model. Strict requirements at the extremes,
    continuous interpolation in between."""
    if weight <= 0.0:
        return (
            "REQUIREMENT: Output exactly ONE message. Do NOT use blank lines or paragraph "
            "breaks. Even if you have multiple thoughts, combine them into a single continuous "
            "message. This is mandatory."
        )
    if weight >= 1.0:
        return (
            "REQUIREMENT: Output MUST be split into at least two separate short messages. "
            "Each message on its own paragraph, separated by a blank line (one thought per "
            "message). Do NOT output a single continuous block. This is mandatory."
        )
    return (
        f"Multi-message preference: {weight:.2f} on a 0.00–1.00 scale where 0.00 = a single "
        "message only (no blank lines) and 1.00 = always split into multiple messages "
        "(separated by blank lines). Interpolate smoothly — higher values should produce more "
        "splits / shorter individual messages, lower values fewer / longer ones."
    )


def _enforce_weight(text, weight):
    """Safety net at the 0.0 extreme: collapse any blank lines so the output is one chunk.
    No enforcement at 1.0 — auto-splitting semantically would be fragile, so we trust the
    strict prompt instead."""
    if weight <= 0.0:
        return re.sub(r"\n\s*\n+", "\n", (text or "").strip())
    return text


def _build_system_prompt(kind, name):
    """Compose the system prompt for a DM or group conversation.
    kind: 'dm' or 'group'. name: contact name (DM) or group display name (group).
    CUSTOM_DM_PROMPT / CUSTOM_GROUP_PROMPT env vars override the defaults."""
    if kind == "group":
        template = os.environ.get("CUSTOM_GROUP_PROMPT") or DEFAULT_GROUP_PROMPT
    else:
        template = os.environ.get("CUSTOM_DM_PROMPT") or DEFAULT_DM_PROMPT
    # str.replace (not str.format) so user-supplied templates with stray `{}` don't crash.
    return template.replace("{name}", name) + " " + _multi_message_guidance(_resolve_weight())


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


def suggest_reply(contact_name, history):
    if not history:
        raise ValueError("No prior messages found with this contact.")

    transcript = "\n".join(f"{sender.capitalize()}: {text}" for sender, text, _ in history)

    system_prompt = _build_system_prompt("dm", contact_name)
    user_prompt = f"Conversation with {contact_name} — recent messages:\n\n{transcript}"

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
    raw = response.choices[0].message.content.strip().strip('"').strip("'")
    return _enforce_weight(raw, _resolve_weight())


def send_imessage(clean_num, message):
    chunks = _split_into_messages(message)
    if not chunks:
        return

    def try_send(service_type, text):
        # Escape for AppleScript string literal: backslashes first, then double quotes.
        escaped = text.replace('\\', '\\\\').replace('"', '\\"')
        script = f'''
        tell application "Messages"
            set targetService to 1st service whose service type = {service_type}
            set targetBuddy to buddy "{clean_num}" of targetService
            send "{escaped}" to targetBuddy
        end tell
        '''
        subprocess.run(
            ['osascript', '-e', script],
            check=True, capture_output=True,
        )

    # Probe iMessage with the first chunk; fall back to SMS if the contact isn't on iMessage.
    service = None
    last_err = None
    for candidate in ("iMessage", "SMS"):
        try:
            try_send(candidate, chunks[0])
            service = candidate
            print(f"Sent via {candidate} to {clean_num}: {chunks[0]!r}")
            break
        except subprocess.CalledProcessError as e:
            last_err = e

    if service is None:
        stderr = last_err.stderr.decode('utf-8', errors='replace').strip() if last_err else ""
        print(f"Failed to send message to {clean_num}: {stderr}")
        return

    for chunk in chunks[1:]:
        time.sleep(INTER_MESSAGE_DELAY_S)
        try:
            try_send(service, chunk)
            print(f"Sent via {service} to {clean_num}: {chunk!r}")
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode('utf-8', errors='replace').strip()
            print(f"Failed to send chunk to {clean_num}: {stderr}")
            return


def suggest_group_reply(group_name, history):
    if not history:
        raise ValueError("No prior messages found in this group chat.")

    transcript = "\n".join(
        f"{'Me' if sender == 'me' else sender}: {text}"
        for sender, text, _ in history
    )

    system_prompt = _build_system_prompt("group", group_name)
    user_prompt = f"Group chat '{group_name}' — recent messages:\n\n{transcript}"

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
    raw = response.choices[0].message.content.strip().strip('"').strip("'")
    return _enforce_weight(raw, _resolve_weight())


def send_imessage_to_chat(chat_guid, message):
    """Send a message to a group chat by its full GUID (e.g. 'iMessage;+;chat123456789').
    The service (iMessage vs SMS) is encoded in the GUID, so we don't need to fall back."""
    chunks = _split_into_messages(message)
    if not chunks:
        return

    def try_send(text):
        escaped = text.replace('\\', '\\\\').replace('"', '\\"')
        script = f'''
        tell application "Messages"
            set targetChat to a reference to chat id "{chat_guid}"
            send "{escaped}" to targetChat
        end tell
        '''
        subprocess.run(['osascript', '-e', script], check=True, capture_output=True)

    for i, chunk in enumerate(chunks):
        if i > 0:
            time.sleep(INTER_MESSAGE_DELAY_S)
        try:
            try_send(chunk)
            print(f"Sent to group {chat_guid}: {chunk!r}")
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode('utf-8', errors='replace').strip()
            print(f"Failed to send chunk to group {chat_guid}: {stderr}")
            return
