import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
API_ID: int    = int(os.getenv("API_ID", "0"))
API_HASH: str  = os.getenv("API_HASH", "")

_raw_admins = os.getenv("ADMIN_IDS", "")
ADMIN_IDS: list[int] = [int(x.strip()) for x in _raw_admins.split(",") if x.strip()]

SESSIONS_DIR: str = os.path.join(os.path.dirname(__file__), "sessions")
LOGS_DIR: str     = os.path.join(os.path.dirname(__file__), "logs")
DATA_DIR: str     = os.path.join(os.path.dirname(__file__), "data")
DB_PATH: str      = os.path.join(DATA_DIR, "bot.db")

os.makedirs(SESSIONS_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

# â”€â”€â”€ Subscription plans â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
DEFAULT_UPI_ID = "msjanvee@bpunity"

PLANS: dict[str, dict] = {
    "basic": {
        "name":          "â­ Basic",
        "price":         49,
        "account_limit": 5,
        "duration_days": 7,
        "label":         "â­ Basic â€” â‚¹49/week (5 accounts)",
    },
    "pro": {
        "name":          "ðŸ’Ž Pro",
        "price":         99,
        "account_limit": 10,
        "duration_days": 7,
        "label":         "ðŸ’Ž Pro â€” â‚¹99/week (10 accounts)",
    },
}

# â”€â”€â”€ Speed presets â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
SPEED_PRESETS: dict[str, dict] = {
    "safe": {
        "label": "🟢 Safe", "risk": "Low Risk",
        "desc": "3 groups, 5-10s batch delay, 30-60s cycle delay",
        "batch_size": 3, "batch_delay": (5, 10), "cycle_delay": (30, 60),
    },
    "medium": {
        "label": "🟡 Medium", "risk": "Balanced",
        "desc": "5 groups, 2-3s batch delay, 10-20s cycle delay",
        "batch_size": 5, "batch_delay": (2, 3), "cycle_delay": (10, 20),
    },
    "aggressive": {
        "label": "🔴 Aggressive", "risk": "High Risk",
        "desc": "Instant batches, 3-8s cycle delay",
        "batch_size": None, "batch_delay": 0, "cycle_delay": (3, 8),
    },
    "custom": {
        "label": "⚙ Custom", "risk": "User Defined",
        "desc": "You set batch size, batch delay, cycle delay",
        "batch_size": None, "batch_delay": 0, "cycle_delay": 30,
    },
}
