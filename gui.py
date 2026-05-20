import queue
import threading
import tkinter as tk
from tkinter import ttk


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


def _build_list_panel(parent, load_fn, on_pick, item_label, item_hint, load_summary):
    """Reusable search-and-list panel.
    Calls load_fn() on a background thread, then displays items with a search
    box. Calls on_pick(item) when the user picks one (Return / double-click / Pick button)."""
    state = {"items": [], "filtered": []}

    subtitle = ttk.Label(parent, text="Loading…", foreground="#6e6e73")
    subtitle.pack(anchor=tk.W, pady=(0, 12))

    search_var = tk.StringVar()
    search_entry = ttk.Entry(parent, textvariable=search_var,
                             font=("TkDefaultFont", 14))
    search_entry.pack(fill=tk.X, pady=(0, 12), ipady=4)
    search_entry.configure(state="disabled")

    progress = ttk.Progressbar(parent, mode="indeterminate")
    progress.pack(fill=tk.X, pady=(0, 12))
    progress.start(15)

    btn_row = ttk.Frame(parent)
    btn_row.pack(side=tk.BOTTOM, fill=tk.X, pady=(14, 0))

    tree_frame = ttk.Frame(parent)
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
    tree.column("#0", anchor="w", stretch=True, width=300)
    tree.column("subtitle", anchor="e", stretch=True, width=160)
    scrollbar.config(command=tree.yview)
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
    tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    def refresh(*_):
        q = search_var.get().lower()
        tree.delete(*tree.get_children())
        state["filtered"].clear()
        for item in state["items"]:
            label = item_label(item)
            if q in label.lower():
                iid = tree.insert("", tk.END, text=label, values=(item_hint(item),))
                state["filtered"].append((iid, item))
        if state["filtered"]:
            tree.selection_set(state["filtered"][0][0])
            tree.focus(state["filtered"][0][0])

    def confirm(event=None):
        sel = tree.selection()
        if not sel:
            return
        target = sel[0]
        for iid, item in state["filtered"]:
            if iid == target:
                on_pick(item)
                return

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
    tree.bind("<Return>", confirm)
    search_entry.bind("<Return>", confirm)

    def focus_list(event):
        tree.focus_set()
        if not tree.selection() and state["filtered"]:
            tree.selection_set(state["filtered"][0][0])
            tree.focus(state["filtered"][0][0])
    search_entry.bind("<Down>", focus_list)

    result_q = queue.Queue()

    def worker():
        try:
            result_q.put(("ok", load_fn()))
        except Exception as exc:  # noqa: BLE001 — surface anything to the UI
            result_q.put(("err", exc))

    threading.Thread(target=worker, daemon=True).start()

    def poll():
        try:
            status, payload = result_q.get_nowait()
        except queue.Empty:
            parent.after(80, poll)
            return
        progress.stop()
        progress.pack_forget()
        if status == "err":
            subtitle.configure(text=f"Failed to load: {payload}")
            return
        state["items"] = payload
        subtitle.configure(text=load_summary(payload))
        search_entry.configure(state="normal")
        pick_btn.configure(state="normal")
        refresh()

    parent.after(80, poll)
    return search_entry


def pick_target_gui(load_contacts_fn, load_groups_fn):
    """Landing-page picker with two tabs: DMs and Group Chats.
    Returns:
      {'kind': 'dm', 'contact': {...}} when a contact is picked,
      {'kind': 'group', 'group': {...}} when a group is picked,
      or None if cancelled."""
    state = {"selected": None}
    root = tk.Tk()
    root.title("iMessage Bot")
    root.geometry("520x680")
    root.minsize(420, 480)
    _apply_macos_theme(root)

    container = ttk.Frame(root, padding=(18, 16, 18, 16))
    container.pack(fill=tk.BOTH, expand=True)

    notebook = ttk.Notebook(container)
    notebook.pack(fill=tk.BOTH, expand=True)

    dm_tab = ttk.Frame(notebook, padding=(0, 12, 0, 0))
    group_tab = ttk.Frame(notebook, padding=(0, 12, 0, 0))
    notebook.add(dm_tab, text="DMs")
    notebook.add(group_tab, text="Group Chats")

    def on_dm_pick(contact):
        state["selected"] = {"kind": "dm", "contact": contact}
        root.destroy()

    def on_group_pick(group):
        state["selected"] = {"kind": "group", "group": group}
        root.destroy()

    dm_search = _build_list_panel(
        parent=dm_tab,
        load_fn=load_contacts_fn,
        on_pick=on_dm_pick,
        item_label=lambda c: c["name"],
        item_hint=lambda c: c["phones"][0] if c["phones"] else (c["emails"][0] if c["emails"] else ""),
        load_summary=lambda items: f"{len(items)} contacts. Pick someone to message.",
    )
    _build_list_panel(
        parent=group_tab,
        load_fn=load_groups_fn,
        on_pick=on_group_pick,
        item_label=lambda g: g["name"],
        item_hint=lambda g: (g["last_active"].strftime("%Y-%m-%d") if g["last_active"] else ""),
        load_summary=lambda items: f"{len(items)} group chats. Pick one to reply.",
    )

    root.bind("<Escape>", lambda _e: root.destroy())
    # Focus the DM search box once the window is mapped.
    root.after(120, dm_search.focus_set)
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
