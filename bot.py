import queue
import sqlite3
import subprocess
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import ttk

from openai import OpenAI

CHAT_DB = Path.home() / "Library" / "Messages" / "chat.db"
MODEL = "gpt-5.4-mini"
HISTORY_LIMIT = 10
AUTO_SEND = False  #D True: *DANGEROUS*: send the suggested reply immediately. False: prompt y/N/edit.
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
    """Return all Contacts entries as a list of dicts:
    [{'name': str, 'phones': [...], 'emails': [...]}, ...] sorted by name.

    Uses batched AppleScript ('every person') instead of a per-person loop —
    one Apple Event per attribute is ~50x faster than one per person."""
    script = '''
    tell application "Contacts"
        launch
        set theNames to name of every person
        set thePhonesList to value of phones of every person
        set theEmailsList to value of emails of every person
        set output to ""
        repeat with i from 1 to count of theNames
            set theName to item i of theNames
            if theName is missing value then set theName to ""
            if theName is not "" then
                set output to output & "N:" & (theName as string) & linefeed
                set thesePhones to item i of thePhonesList
                if thesePhones is not missing value then
                    repeat with p in thesePhones
                        set output to output & "P:" & (p as string) & linefeed
                    end repeat
                end if
                set theseEmails to item i of theEmailsList
                if theseEmails is not missing value then
                    repeat with e in theseEmails
                        set output to output & "E:" & (e as string) & linefeed
                    end repeat
                end if
                set output to output & "---" & linefeed
            end if
        end repeat
        return output
    end tell
    '''
    result = subprocess.check_output(['osascript', '-e', script]).decode('utf-8')
    contacts, current = [], None
    for line in result.splitlines():
        if line.startswith("N:"):
            current = {"name": line[2:].strip(), "phones": [], "emails": []}
        elif line.startswith("P:") and current is not None:
            current["phones"].append(line[2:].strip())
        elif line.startswith("E:") and current is not None:
            current["emails"].append(line[2:].strip())
        elif line.strip() == "---" and current is not None:
            contacts.append(current)
            current = None
    contacts.sort(key=lambda c: c["name"].lower())
    return contacts


def _bring_to_front(root):
    root.lift()
    root.attributes("-topmost", True)
    root.after(120, lambda: root.attributes("-topmost", False))


def _apply_macos_theme(root):
    """Use the aqua theme on macOS so ttk widgets render with native chrome."""
    style = ttk.Style(root)
    if "aqua" in style.theme_names():
        style.theme_use("aqua")
    return style


def pick_contact_gui(load_contacts_fn):
    """Modal contact picker. `load_contacts_fn()` is called on a background thread
    so the window appears immediately. Returns the selected contact dict, or None."""
    state = {"selected": None, "contacts": []}
    root = tk.Tk()
    root.title("Contacts")
    root.geometry("460x600")
    root.minsize(380, 420)
    _apply_macos_theme(root)

    container = ttk.Frame(root, padding=(18, 16, 18, 16))
    container.pack(fill=tk.BOTH, expand=True)

    title = ttk.Label(container, text="Contacts",
                      font=("TkDefaultFont", 20, "bold"))
    title.pack(anchor=tk.W, pady=(0, 2))
    subtitle = ttk.Label(container, text="Loading contacts…",
                         foreground="#6e6e73")
    subtitle.pack(anchor=tk.W, pady=(0, 12))

    search_var = tk.StringVar()
    search_entry = ttk.Entry(container, textvariable=search_var,
                             font=("TkDefaultFont", 14))
    search_entry.pack(fill=tk.X, pady=(0, 12), ipady=4)
    search_entry.configure(state="disabled")

    progress = ttk.Progressbar(container, mode="indeterminate")
    progress.pack(fill=tk.X, pady=(0, 12))
    progress.start(15)

    btn_row = ttk.Frame(container)
    btn_row.pack(side=tk.BOTTOM, fill=tk.X, pady=(14, 0))

    tree_frame = ttk.Frame(container)
    tree_frame.pack(fill=tk.BOTH, expand=True)
    scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL)
    tree = ttk.Treeview(
        tree_frame,
        columns=("subtitle",),
        show="tree",
        selectmode="browse",
        yscrollcommand=scrollbar.set,
        height=18,
    )
    tree.column("#0", anchor="w", stretch=True, width=240)
    tree.column("subtitle", anchor="e", stretch=True, width=170)
    scrollbar.config(command=tree.yview)
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
    tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    filtered = []

    def refresh(*_):
        q = search_var.get().lower()
        tree.delete(*tree.get_children())
        filtered.clear()
        for c in state["contacts"]:
            if q in c["name"].lower():
                hint = c["phones"][0] if c["phones"] else (c["emails"][0] if c["emails"] else "")
                iid = tree.insert("", tk.END, text=c["name"], values=(hint,))
                filtered.append((iid, c))
        if filtered:
            tree.selection_set(filtered[0][0])
            tree.focus(filtered[0][0])

    def confirm(event=None):
        sel = tree.selection()
        if not sel:
            return
        target_iid = sel[0]
        for iid, c in filtered:
            if iid == target_iid:
                state["selected"] = c
                root.destroy()
                return

    def cancel(event=None):
        root.destroy()

    ttk.Button(btn_row, text="Cancel", command=cancel).pack(side=tk.RIGHT, padx=(8, 0))
    pick_btn = ttk.Button(btn_row, text="Pick", command=confirm)
    pick_btn.pack(side=tk.RIGHT)
    pick_btn.configure(state="disabled")

    def click_pick(event):
        iid = tree.identify_row(event.y)
        if iid:
            tree.selection_set(iid)
            confirm()

    search_var.trace_add("write", refresh)
    tree.bind("<ButtonRelease-1>", click_pick)
    root.bind("<Return>", confirm)
    root.bind("<Escape>", cancel)

    def focus_list(event):
        tree.focus_set()
        if not tree.selection() and filtered:
            tree.selection_set(filtered[0][0])
            tree.focus(filtered[0][0])
    search_entry.bind("<Down>", focus_list)

    result_q = queue.Queue()

    def worker():
        try:
            result_q.put(("ok", load_contacts_fn()))
        except Exception as exc:  # noqa: BLE001 — surface anything to the UI
            result_q.put(("err", exc))

    threading.Thread(target=worker, daemon=True).start()

    def poll():
        try:
            status, payload = result_q.get_nowait()
        except queue.Empty:
            root.after(80, poll)
            return
        progress.stop()
        progress.pack_forget()
        if status == "err":
            subtitle.configure(text=f"Failed to load contacts: {payload}")
            return
        state["contacts"] = payload
        subtitle.configure(text=f"{len(payload)} contacts. Pick someone to message.")
        search_entry.configure(state="normal")
        pick_btn.configure(state="normal")
        refresh()
        search_entry.focus_set()

    root.after(80, poll)
    _bring_to_front(root)
    root.mainloop()
    return state["selected"]


def reply_preview_gui(contact_name, suggest_fn):
    """Modal preview of the suggested reply. `suggest_fn()` is called on a background
    thread so the window appears immediately. Returns (action, text)."""
    state = {"action": "cancel", "text": ""}
    root = tk.Tk()
    root.title("New Message")
    root.geometry("560x380")
    root.minsize(440, 300)
    _apply_macos_theme(root)

    container = ttk.Frame(root, padding=(18, 16, 18, 16))
    container.pack(fill=tk.BOTH, expand=True)

    title = ttk.Label(container, text=f"To: {contact_name}",
                      font=("TkDefaultFont", 18, "bold"))
    title.pack(anchor=tk.W, pady=(0, 2))
    helper = ttk.Label(container,
                       text="Generating reply…",
                       foreground="#6e6e73")
    helper.pack(anchor=tk.W, pady=(0, 12))

    progress = ttk.Progressbar(container, mode="indeterminate")
    progress.pack(fill=tk.X, pady=(0, 12))
    progress.start(15)

    btn_row = ttk.Frame(container)
    btn_row.pack(side=tk.BOTTOM, fill=tk.X, pady=(14, 0))

    text_frame = ttk.Frame(container)
    text_frame.pack(fill=tk.BOTH, expand=True)
    text_scroll = ttk.Scrollbar(text_frame, orient=tk.VERTICAL)
    text_widget = tk.Text(
        text_frame,
        wrap=tk.WORD,
        font=("TkTextFont", 14),
        relief="flat",
        highlightthickness=1,
        highlightbackground="#d2d2d7",
        highlightcolor="#0a84ff",
        padx=12,
        pady=10,
        yscrollcommand=text_scroll.set,
    )
    text_scroll.config(command=text_widget.yview)
    text_scroll.pack(side=tk.RIGHT, fill=tk.Y)
    text_widget.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    text_widget.configure(state="disabled")

    def send(event=None):
        if str(send_btn["state"]) == "disabled":
            return "break"
        state["action"] = "send"
        state["text"] = text_widget.get("1.0", "end-1c").strip()
        root.destroy()
        return "break"

    def cancel(event=None):
        state["action"] = "cancel"
        root.destroy()

    ttk.Button(btn_row, text="Cancel", command=cancel).pack(side=tk.RIGHT, padx=(8, 0))
    send_btn = ttk.Button(btn_row, text="Send", command=send)
    send_btn.pack(side=tk.RIGHT)
    send_btn.configure(state="disabled")
    root.bind("<Escape>", cancel)
    root.bind("<Command-Return>", send)

    result_q = queue.Queue()

    def worker():
        try:
            result_q.put(("ok", suggest_fn()))
        except Exception as exc:  # noqa: BLE001 — surface anything to the UI
            result_q.put(("err", exc))

    threading.Thread(target=worker, daemon=True).start()

    def poll():
        try:
            status, payload = result_q.get_nowait()
        except queue.Empty:
            root.after(80, poll)
            return
        progress.stop()
        progress.pack_forget()
        text_widget.configure(state="normal")
        text_widget.delete("1.0", tk.END)
        if status == "err":
            helper.configure(text="Failed to generate reply.")
            text_widget.insert("1.0", f"Error: {payload}")
        else:
            helper.configure(text="Edit the message if needed, then click Send.")
            text_widget.insert("1.0", payload)
            text_widget.tag_add("sel", "1.0", "end-1c")
            send_btn.configure(state="normal")
        text_widget.focus_set()

    root.after(80, poll)
    _bring_to_front(root)
    root.mainloop()
    return state["action"], state["text"]


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


def suggest_reply(contact_name, history):
    if not history:
        raise ValueError("No prior messages found with this contact.")

    transcript = "\n".join(f"{sender.capitalize()}: {text}" for sender, text, _ in history)

    system_prompt = (
        f"Draft my next iMessage to {contact_name}. Match my tone and formality levels from prior messages sent by [Me]. Capitalize first word. Avoid emojis in most cases. "
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
    # Escape for AppleScript string literal: backslashes first, then double quotes.
    escaped = message.replace('\\', '\\\\').replace('"', '\\"')

    def try_send(service_type):
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

    # Try iMessage first (blue), fall back to SMS (green) if the contact isn't on iMessage.
    try:
        try_send("iMessage")
        print(f"Sent via iMessage to {clean_num}.")
        return
    except subprocess.CalledProcessError:
        pass
    try:
        try_send("SMS")
        print(f"Sent via SMS to {clean_num}.")
    except subprocess.CalledProcessError as e:
        print(f"Failed to send message to {clean_num}: {e.stderr.decode('utf-8', errors='replace').strip()}")


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
