# iMessage Bot

Drafts a reply to your most recent iMessage DM or group chat using OpenAI, then previews it before sending. Pick a chat from the tabbed picker (DMs / Group Chats), edit the AI draft, hit Send.

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

Copy `.env.example` to `.env` and fill in your key:

```sh
cp .env.example .env
```

Or, if you'd rather not use a `.env` file, export it in your shell (or add to `~/.zshrc`):

```sh
export OPENAI_API_KEY="sk-..."
```

`.env` takes precedence over shell exports. You can also customize `DM_SYSTEM_PROMPT` and `GROUP_SYSTEM_PROMPT` in `.env` (placeholders: `{contact_name}` and `{group_name}`); if unset, sensible defaults in [messaging.py](messaging.py) are used.

### 4. Grant Full Disk Access

In **System Settings → Privacy & Security → Full Disk Access**, add your terminal (and Python if prompted) so the bot can read `~/Library/Messages/chat.db` and the AddressBook databases.

## Run

```sh
~/.venvs/message_bot/bin/python /Users/adityar/Downloads/iMessage-Bot/bot.py
```

Set `AUTO_SEND = True` in [bot.py](bot.py) to skip the preview window and send immediately (dangerous).

## Files

- [bot.py](bot.py) — entry point / orchestration
- [db.py](db.py) — SQLite reads from `chat.db` (DMs + group chats) and AddressBook
- [gui.py](gui.py) — tabbed picker (DMs / Group Chats) + reply preview
- [messaging.py](messaging.py) — OpenAI reply generation + AppleScript send (DM and group)
- [.env.example](.env.example) — template for `OPENAI_API_KEY`, `DM_SYSTEM_PROMPT`, `GROUP_SYSTEM_PROMPT`
