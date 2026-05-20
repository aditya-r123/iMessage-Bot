import sqlite3
from datetime import datetime
from pathlib import Path

CHAT_DB = Path.home() / "Library" / "Messages" / "chat.db"
HISTORY_LIMIT = 5
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


def list_all_contacts():
    """Return all Contacts entries by reading AddressBook's SQLite databases directly.
    No AppleScript, no Contacts.app launch. Requires Full Disk Access (same as chat.db).

    Returns: [{'name': str, 'phones': [...], 'emails': [...]}, ...] sorted by name."""
    base = Path.home() / "Library" / "Application Support" / "AddressBook"
    db_paths = []
    top = base / "AddressBook-v22.abcddb"
    if top.exists():
        db_paths.append(top)
    sources_dir = base / "Sources"
    if sources_dir.exists():
        for src in sources_dir.iterdir():
            db = src / "AddressBook-v22.abcddb"
            if db.exists():
                db_paths.append(db)

    contacts, seen = [], set()
    for db_path in db_paths:
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        except sqlite3.Error:
            continue
        try:
            records = conn.execute(
                "SELECT Z_PK, ZFIRSTNAME, ZMIDDLENAME, ZLASTNAME, ZNICKNAME, ZORGANIZATION "
                "FROM ZABCDRECORD"
            ).fetchall()
            phones_by_owner = {}
            for owner, number in conn.execute(
                "SELECT ZOWNER, ZFULLNUMBER FROM ZABCDPHONENUMBER "
                "WHERE ZFULLNUMBER IS NOT NULL"
            ):
                phones_by_owner.setdefault(owner, []).append(number)
            emails_by_owner = {}
            for owner, addr in conn.execute(
                "SELECT ZOWNER, ZADDRESS FROM ZABCDEMAILADDRESS "
                "WHERE ZADDRESS IS NOT NULL"
            ):
                emails_by_owner.setdefault(owner, []).append(addr)
        except sqlite3.Error:
            conn.close()
            continue
        conn.close()

        for pk, first, middle, last, nickname, org in records:
            parts = [p.strip() for p in (first, middle, last) if p and p.strip()]
            if parts:
                name = " ".join(parts)
            elif nickname and nickname.strip():
                name = nickname.strip()
            elif org and org.strip():
                name = org.strip()
            else:
                continue

            phones = phones_by_owner.get(pk, [])
            emails = emails_by_owner.get(pk, [])
            # Dedup across sources (e.g. iCloud + local) when the same contact
            # would appear twice with identical fields.
            key = (name.lower(), tuple(sorted(phones)), tuple(sorted(emails)))
            if key in seen:
                continue
            seen.add(key)
            contacts.append({"name": name, "phones": phones, "emails": emails})

    contacts.sort(key=lambda c: c["name"].lower())
    return contacts


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
    # Rank chats by the most recent *text* message (item_type = 0) — location-share
    # events (item_type = 4) get broadcast across multiple chats and would
    # otherwise tie-break us onto a stale chat.
    chat_query = f"""
        SELECT chat.ROWID
        FROM chat
        JOIN chat_handle_join ON chat.ROWID = chat_handle_join.chat_id
        JOIN handle ON chat_handle_join.handle_id = handle.ROWID
        JOIN chat_message_join ON chat.ROWID = chat_message_join.chat_id
        JOIN message ON chat_message_join.message_id = message.ROWID
        WHERE chat.style = 45
          AND message.item_type = 0
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
        # Skip empty bodies and decode-garbage (e.g. just the U+FFFD replacement char).
        if not body or not body.replace("�", "").strip():
            continue
        decoded.append(("me" if is_from_me else "them", body, apple_ns_to_datetime(date)))
        if len(decoded) >= limit:
            break
    return list(reversed(decoded))


def _build_handle_lookup(contacts):
    """Map handle ids (last-10-digits for phones, lowercased emails) → contact name."""
    lookup = {}
    for c in contacts:
        for phone in c["phones"]:
            digits = ''.join(d for d in phone if d.isdigit())
            last10 = digits[-10:] if len(digits) >= 10 else digits
            if last10:
                lookup[last10] = c["name"]
        for email in c["emails"]:
            lookup[email.lower()] = c["name"]
    return lookup


def _resolve_handle(handle_id, lookup):
    if not handle_id:
        return None
    if "@" in handle_id:
        return lookup.get(handle_id.lower(), handle_id)
    digits = ''.join(d for d in handle_id if d.isdigit())
    last10 = digits[-10:] if len(digits) >= 10 else digits
    return lookup.get(last10, handle_id)


def list_all_group_chats(contacts=None):
    """Return all group chats (chat.style = 43) sorted by most recent text activity.
    contacts: list from list_all_contacts() — used to resolve participant handles to names.

    Returns: [{'name': str, 'guid': str, 'rowid': int, 'participants': [str],
               'last_active': datetime or None}, ...]"""
    if not CHAT_DB.exists():
        raise FileNotFoundError(f"iMessage database not found at {CHAT_DB}")
    lookup = _build_handle_lookup(contacts or [])

    conn = sqlite3.connect(f"file:{CHAT_DB}?mode=ro", uri=True)
    rows = conn.execute("""
        SELECT chat.ROWID, chat.display_name, chat.chat_identifier, chat.guid,
               (SELECT MAX(message.date) FROM message
                JOIN chat_message_join ON message.ROWID = chat_message_join.message_id
                WHERE chat_message_join.chat_id = chat.ROWID
                  AND message.item_type = 0) AS last_date
        FROM chat
        WHERE chat.style = 43
        ORDER BY last_date DESC
    """).fetchall()

    handles_by_chat = {}
    for chat_id, handle_id in conn.execute("""
        SELECT chat_handle_join.chat_id, handle.id
        FROM chat_handle_join
        JOIN handle ON chat_handle_join.handle_id = handle.ROWID
    """):
        handles_by_chat.setdefault(chat_id, []).append(handle_id)
    conn.close()

    groups = []
    for rowid, display_name, chat_identifier, guid, last_date in rows:
        if not guid:
            continue
        handles = handles_by_chat.get(rowid, [])
        names = [_resolve_handle(h, lookup) or h for h in handles]
        if display_name and display_name.strip():
            name = display_name.strip()
        elif names:
            preview = names[:3]
            extra = f" +{len(names) - 3}" if len(names) > 3 else ""
            name = ", ".join(preview) + extra
        else:
            name = chat_identifier or f"chat#{rowid}"
        last_active = apple_ns_to_datetime(last_date) if last_date else None
        groups.append({
            "name": name,
            "guid": guid,
            "rowid": rowid,
            "participants": names,
            "last_active": last_active,
        })
    return groups


def get_recent_group_messages(chat_rowid, contacts=None, limit=HISTORY_LIMIT):
    """Fetch recent messages from a group chat by its chat.ROWID.
    Returns list of (sender_name, text, datetime) in chronological order, where
    sender_name is 'me' for own messages or the resolved contact name
    (falling back to the raw handle id)."""
    if not CHAT_DB.exists():
        raise FileNotFoundError(f"iMessage database not found at {CHAT_DB}")
    lookup = _build_handle_lookup(contacts or [])

    conn = sqlite3.connect(f"file:{CHAT_DB}?mode=ro", uri=True)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT message.text, message.attributedBody, message.is_from_me,
               message.date, handle.id
        FROM message
        JOIN chat_message_join ON message.ROWID = chat_message_join.message_id
        LEFT JOIN handle ON message.handle_id = handle.ROWID
        WHERE chat_message_join.chat_id = ?
          AND message.item_type = 0
        ORDER BY message.date DESC
        LIMIT ?
    """, (chat_rowid, limit * 3))
    rows = cursor.fetchall()
    conn.close()

    decoded = []
    for text, attr_body, is_from_me, date, handle_id in rows:
        body = text if text else extract_attributed_text(attr_body)
        if not body or not body.replace("�", "").strip():
            continue
        if is_from_me:
            sender = "me"
        else:
            sender = _resolve_handle(handle_id, lookup) or "them"
        decoded.append((sender, body, apple_ns_to_datetime(date)))
        if len(decoded) >= limit:
            break
    return list(reversed(decoded))
