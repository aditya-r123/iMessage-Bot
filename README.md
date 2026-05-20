# iMessage Bot

Drafts a reply to your most recent iMessage thread with a contact using OpenAI, then previews it before sending.

## Setup

### 1. Create a venv

```sh
python3 -m venv ~/.venvs/message_bot
```

### 2. Install dependencies

```sh
~/.venvs/message_bot/bin/pip install -r requirements.txt
```

### 3. Add your OpenAI API key

Export it in your shell (or add to `~/.zshrc`):

```sh
export OPENAI_API_KEY="sk-..."
```

### 4. Grant Full Disk Access

In **System Settings → Privacy & Security → Full Disk Access**, add your terminal (and Python if prompted) so the bot can read `~/Library/Messages/chat.db` and the AddressBook databases.

## Run

```sh
~/.venvs/message_bot/bin/python /Users/adityar/Downloads/iMessage-Bot/bot.py
```

(See the comment at the top of [bot.py](bot.py) for the exact command.)

## Files

- [bot.py](bot.py) — entry point / orchestration
- [db.py](db.py) — SQLite reads (`chat.db`, AddressBook)
- [gui.py](gui.py) — Tk contact picker and reply preview windows
- [messaging.py](messaging.py) — OpenAI reply generation and AppleScript send
