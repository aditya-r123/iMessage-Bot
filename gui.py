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
