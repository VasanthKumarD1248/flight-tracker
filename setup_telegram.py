"""Connect Telegram alerts: finds your chat id, sends a test message, stores both GitHub secrets.

1. In Telegram, message @BotFather, send /newbot and follow the prompts. Copy the token.
2. Open your new bot in Telegram and send it any message (e.g. "hi").
3. Run:  python3 setup_telegram.py
"""

import getpass
import json
import subprocess
import urllib.request


def call(token, method, payload=None):
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=json.dumps(payload).encode() if payload else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


token = getpass.getpass("Paste your bot token (hidden): ").strip()
me = call(token, "getMe")["result"]
print(f"Bot: @{me['username']}")

updates = call(token, "getUpdates")["result"]
chats = [u["message"]["chat"] for u in updates if "message" in u]
if not chats:
    raise SystemExit(f"No messages found. Send any message to @{me['username']} in Telegram, then run this again.")
chat = chats[-1]
print(f"Chat: {chat.get('first_name', '')} {chat.get('last_name', '')} (id {chat['id']})")

call(token, "sendMessage", {"chat_id": chat["id"], "text": "✅ Flight tracker connected. You'll get fare alerts here."})
print("Test message sent.")

for name, value in (("TELEGRAM_BOT_TOKEN", token), ("TELEGRAM_CHAT_ID", str(chat["id"]))):
    subprocess.run(["gh", "secret", "set", name, "--body", value], check=True)
print("GitHub secrets saved. Alerts start from the next check.")
