# iMessage Bot for MacOS

Drafts a reply to your most recent iMessage DM or group chat using OpenAI, then previews it before sending. Pick a chat from the tabbed picker (DMs / Group Chats), edit the AI draft, hit Send. If the draft has paragraphs separated by blank lines, each is sent as its own iMessage.

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

`.env` takes precedence over shell exports. Other knobs you can set in `.env`:
- `CUSTOM_DM_PROMPT` / `CUSTOM_GROUP_PROMPT` — override the system prompts (use `{name}` as a placeholder).
- `MULTI_MESSAGE_WEIGHT` — `0.0`–`1.0`. `0.0` guarantees a single message, `1.0` guarantees multiple; values in between bias the model smoothly.

Defaults live in [messaging.py](messaging.py).

### 4. Grant Full Disk Access

In **System Settings → Privacy & Security → Full Disk Access**, add your terminal (and Python if prompted) so the bot can read `~/Library/Messages/chat.db` and the AddressBook databases.

## Run

```sh
~/.venvs/message_bot/bin/python /Users/adityar/Downloads/iMessage-Bot/bot.py
```

Set `AUTO_SEND = True` in [bot.py](bot.py) to skip the preview window and send immediately (*note that this is highly dangerous and discouraged*).

## Files

- [bot.py](bot.py) — entry point / orchestration
- [db.py](db.py) — SQLite reads from `chat.db` (DMs + group chats) and AddressBook
- [gui.py](gui.py) — tabbed picker (DMs / Group Chats) + reply preview
- [messaging.py](messaging.py) — OpenAI reply generation + AppleScript send (DM and group)
- [.env.example](.env.example) — template for `OPENAI_API_KEY`, `CUSTOM_DM_PROMPT`, `CUSTOM_GROUP_PROMPT`, `MULTI_MESSAGE_WEIGHT`
