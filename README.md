# JoinBot — Telegram Verification Bot

Mutes new group members until they join required channels and verify themselves.

## Features

- Instantly mutes new members when they join
- Sends a welcome message with channel join buttons + "Verify Me" button
- Checks the user actually joined all required channels
- Unmutes and publicly welcomes them on successful verification
- Auto-kicks unverified users after a configurable timeout

## Setup

### 1. Create the bot

1. Message [@BotFather](https://t.me/BotFather) → `/newbot`
2. Copy the **bot token**
3. Add the bot to your group as an **Administrator** with these permissions:
   - Restrict members
   - Delete messages
   - Ban users (for auto-kick)
4. Add the bot to each required channel as **Administrator** (so it can check memberships)

### 2. Enable `chat_member` updates

In BotFather: `/setprivacy` → select your bot → **Disable**
This lets the bot see join/leave events.

### 3. Configure environment

```bash
cp .env.example .env
# Edit .env with your values
```

Required variables:

| Variable | Description |
|---|---|
| `BOT_TOKEN` | Token from BotFather |
| `GROUP_CHAT_ID` | Your group's chat ID (e.g. `-100123456789`) |
| `REQUIRED_CHANNELS` | Comma-separated channel usernames (no `@`) |
| `VERIFICATION_TIMEOUT` | Minutes before auto-kick (`0` to disable) |

**Getting your group ID:** Add [@userinfobot](https://t.me/userinfobot) to the group — it shows the ID. Remove it after.

### 4. Install & run

```bash
pip install -r requirements.txt
python bot.py
```

## How verification works

```
User joins group
      │
      ▼
Bot mutes user + sends welcome message
(with channel links & "Verify Me" button)
      │
      ├─ User clicks "Verify Me"
      │         │
      │         ├─ Not joined all channels → shows error, stays muted
      │         │
      │         └─ Joined all channels → unmuted, welcome message sent ✅
      │
      └─ Timeout expires → user is kicked (can rejoin and try again)
```

## Running with systemd (optional)

```ini
[Unit]
Description=JoinBot Telegram Verification Bot
After=network.target

[Service]
WorkingDirectory=/path/to/JoinBot
ExecStart=/usr/bin/python3 /path/to/JoinBot/bot.py
Restart=always
EnvironmentFile=/path/to/JoinBot/.env

[Install]
WantedBy=multi-user.target
```
