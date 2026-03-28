import os
from dotenv import load_dotenv

load_dotenv()


def _require(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise ValueError(f"Missing required environment variable: {key}")
    return value


BOT_TOKEN: str = _require("BOT_TOKEN")
GROUP_CHAT_ID: int = int(_require("GROUP_CHAT_ID"))
REQUIRED_CHANNELS: list[str] = [
    ch.strip().lstrip("@")
    for ch in _require("REQUIRED_CHANNELS").split(",")
    if ch.strip()
]
WELCOME_MESSAGE: str = os.getenv(
    "WELCOME_MESSAGE",
    "👋 Welcome, <b>{first_name}</b>!\n\nTo chat in <b>{group_name}</b>, join our channels below and click <b>Verify Me ✅</b>.\n\nUntil you verify, your messages will be removed.",
)
VERIFIED_MESSAGE: str = os.getenv(
    "VERIFIED_MESSAGE",
    "🎉 <b>{first_name}</b> is now verified! Welcome to the community! 🎊",
)
