import os

BOT_NAME = "Vote Pro X"
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
UPI_ID = os.getenv("UPI_ID", "ani21mesh@upi")
DB_PATH = os.getenv("DB_PATH", "votepro.db")


def _link(v: str) -> str:
    v = (v or "").strip()
    if v.startswith("@"):
        return "https://t.me/" + v[1:]
    if v.startswith("t.me/"):
        return "https://" + v
    return v


# optional settings (purane bot jaise)
SUPPORT_GROUP = _link(os.getenv("SUPPORT_GROUP", ""))
SUPPORT_CHANNEL = _link(os.getenv("SUPPORT_CHANNEL", ""))
START_IMAGE = os.getenv("START_IMAGE", "").strip()
VOTE_IMAGE = os.getenv("VOTE_IMAGE", "").strip()
FSUB_IDS = [int(x) for x in os.getenv("FSUB_CHAT", "").replace(" ", "").split(",") if x.lstrip("-").isdigit()]

TZ_OFFSET_MIN = int(os.getenv("TZ_OFFSET_MIN", "330"))  # India = +5:30
TZ_NAME = os.getenv("TZ_NAME", "IST")

PLAN_DAYS = 30

PLANS = {
    # editable from Owner Panel -> Plans (values below are only the first-run defaults)
    "free":     {"name": "Free",     "price": 0,  "days": 0,  "giveaways": 1,    "channels": 1,    "bonus": 0},
    "basic":    {"name": "Basic",    "price": 10, "days": 1,  "giveaways": 3,    "channels": 2,    "bonus": 100},
    "pro":      {"name": "Pro",      "price": 30, "days": 7,  "giveaways": 10,   "channels": 5,    "bonus": 500},
    "ultimate": {"name": "Ultimate", "price": 50, "days": 30, "giveaways": 9999, "channels": 9999, "bonus": 2000},
}

BLOCKED_WORDS = [
    "porn", "xxx", "nude", "casino", "betting", "scam", "fuck", "bitch",
    "madarchod", "bhenchod", "chutiya", "randi", "gaand",
] + [w.strip().lower() for w in os.getenv("BLOCKED_WORDS", "").split(",") if w.strip()]

# MongoDB backup (optional but recommended)
MONGO_URL = os.getenv("MONGO_URL", "").strip()
MONGO_DB = os.getenv("MONGO_DB", "voteprox").strip() or "voteprox"

# welcome message "Powered by ..." (default: the bot's own @username)
POWERED_BY = os.getenv("POWERED_BY", "").strip()

# contest text limits
TITLE_MAX = 200
PRIZE_MAX = 10000
PRIZE_PREVIEW = 500   # how much of the prize is shown inside the channel post
