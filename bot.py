import os
import re
import json
import logging
import asyncio
from glob import glob
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from otp_poller import OTPPoller, load_prices

# Global poller reference
_poller: OTPPoller | None = None

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BASE_DIR      = os.path.dirname(__file__)
NUMBERS_DIR   = os.path.join(BASE_DIR, "numbers")
GIVEN_FILE    = os.path.join(BASE_DIR, "given_numbers.txt")
BALANCE_FILE  = os.path.join(BASE_DIR, "balances.txt")
EARNINGS_FILE = os.path.join(BASE_DIR, "earnings.txt")
WITHDRAW_FILE   = os.path.join(BASE_DIR, "withdrawals.txt")
PENDING_FILE    = os.path.join(BASE_DIR, "pending_withdrawals.json")
SLOT_PRICES_FILE = os.path.join(BASE_DIR, "slot_prices.json")
ADMIN_ID      = int(os.environ.get("ADMIN_TELEGRAM_ID", "0"))
OTP_GROUP_ID  = os.environ.get("OTP_GROUP_CHAT_ID", "")
OTP_GROUP     = "https://t.me/honest_otp"
BOT_NAME      = "MR Honest"
MIN_WITHDRAW  = 0.50

# ── Config ──────────────────────────────────────────────────────────────────────

PLATFORMS = {
    "discord":  {"label": "DISCORD",  "emoji": "🎮"},
    "tiktok":   {"label": "TikTok",   "emoji": "🎵"},
    "whatsapp": {"label": "WhatsApp", "emoji": "💬"},
    "facebook": {"label": "Facebook", "emoji": "📘"},
}

COUNTRIES = {
    "usa":        {"label": "USA",        "flag": "🇺🇸"},
    "bangladesh": {"label": "Bangladesh", "flag": "🇧🇩"},
    "uk":         {"label": "UK",         "flag": "🇬🇧"},
    "india":      {"label": "India",      "flag": "🇮🇳"},
    "canada":     {"label": "Canada",     "flag": "🇨🇦"},
    "kosovo":     {"label": "Kosovo",     "flag": "🇽🇰"},
    "israel":     {"label": "Israel",     "flag": "🇮🇱"},
    "algeria":    {"label": "Algeria",    "flag": "🇩🇿"},
    "ethiopia":   {"label": "Ethiopia",   "flag": "🇪🇹"},
    "nigeria":    {"label": "Nigeria",    "flag": "🇳🇬"},
    "ghana":      {"label": "Ghana",      "flag": "🇬🇭"},
    "kenya":      {"label": "Kenya",      "flag": "🇰🇪"},
    "pakistan":   {"label": "Pakistan",   "flag": "🇵🇰"},
    "indonesia":  {"label": "Indonesia",  "flag": "🇮🇩"},
    "vietnam":    {"label": "Vietnam",    "flag": "🇻🇳"},
    "brazil":     {"label": "Brazil",     "flag": "🇧🇷"},
    "mexico":     {"label": "Mexico",     "flag": "🇲🇽"},
    "turkey":     {"label": "Turkey",     "flag": "🇹🇷"},
    "russia":     {"label": "Russia",     "flag": "🇷🇺"},
    "ukraine":    {"label": "Ukraine",    "flag": "🇺🇦"},
    "philippines":{"label": "Philippines","flag": "🇵🇭"},
}

PURCHASE_PRICES: dict[tuple[str, str], float] = {}
DEFAULT_PURCHASE_PRICE = 0.008
STARTING_BALANCE       = 0.00
PAGE_SIZE              = 5
DIVIDER                = "━━━━━━━━━━━━━━━━━━━━━━━━━"

WITHDRAW_METHODS = {
    "bkash":   {"label": "Bkash",   "emoji": "❤️",  "prompt": "📱 Please send your *Bkash Number:*"},
    "nagad":   {"label": "Nagad",   "emoji": "🛺",  "prompt": "📱 Please send your *Nagad Number:*"},
    "binance": {"label": "Binance", "emoji": "🔶", "prompt": "💛 Please send your *Binance UID:*"},
}

BTN_NUMBERS = "📞 numbers"
BTN_STATUS  = "📊 status"
BTN_STOCK   = "🎁 stock"
BTN_BALANCE = "💰 balance"
BTN_PROFILE = "👤 profile"

# ── File / data helpers ─────────────────────────────────────────────────────────

def numbers_path(platform: str, country: str) -> str:
    return os.path.join(NUMBERS_DIR, f"{platform}_{country}.txt")

def all_platform_country_pairs() -> list[tuple[str, str]]:
    pairs = []
    for path in glob(os.path.join(NUMBERS_DIR, "*.txt")):
        name  = os.path.splitext(os.path.basename(path))[0]
        parts = name.split("_", 1)
        if len(parts) == 2 and parts[0].lower() in PLATFORMS:
            pairs.append((parts[0].lower(), parts[1].lower()))
    return pairs

def load_all_numbers(platform: str, country: str) -> list[str]:
    path = numbers_path(platform, country)
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [l.strip() for l in f if l.strip()]

def load_given() -> list[dict]:
    if not os.path.exists(GIVEN_FILE):
        return []
    rows = []
    with open(GIVEN_FILE) as f:
        for line in f:
            parts = line.strip().split(":", 3)
            if len(parts) == 4:
                rows.append({"user_id": parts[0], "platform": parts[1],
                             "country": parts[2], "number": parts[3]})
    return rows

def save_given(user_id: str, platform: str, country: str, number: str) -> None:
    with open(GIVEN_FILE, "a") as f:
        f.write(f"{user_id}:{platform}:{country}:{number}\n")

def given_numbers_set(platform: str, country: str) -> set[str]:
    return {r["number"] for r in load_given()
            if r["platform"] == platform and r["country"] == country}

def available_numbers(platform: str, country: str) -> list[str]:
    all_n = load_all_numbers(platform, country)
    used  = given_numbers_set(platform, country)
    return [n for n in all_n if n not in used]

def given_for_user(user_id: str) -> list[dict]:
    return [r for r in load_given() if r["user_id"] == user_id]

def get_assigned_numbers_map() -> dict[str, dict]:
    result = {}
    for r in load_given():
        result[r["number"]] = {
            "user_id":  r["user_id"],
            "platform": r["platform"],
            "country":  r["country"],
        }
    return result

def load_slot_prices() -> dict:
    if not os.path.exists(SLOT_PRICES_FILE):
        return {}
    with open(SLOT_PRICES_FILE) as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}

def save_slot_prices(data: dict) -> None:
    with open(SLOT_PRICES_FILE, "w") as f:
        json.dump(data, f, indent=2)

def get_purchase_price(platform: str, country: str) -> float:
    key = f"{platform}:{country}"
    slot = load_slot_prices()
    if key in slot:
        return slot[key]
    return PURCHASE_PRICES.get((platform, country), DEFAULT_PURCHASE_PRICE)

def countries_with_stock(platform: str) -> list[str]:
    return [c for p, c in all_platform_country_pairs()
            if p == platform and available_numbers(platform, c)]

def platforms_with_stock() -> list[str]:
    return [p for p in PLATFORMS
            if any(available_numbers(p, c)
                   for pl, c in all_platform_country_pairs() if pl == p)]

# ── Balance helpers ─────────────────────────────────────────────────────────────

def load_balances() -> dict[str, float]:
    if not os.path.exists(BALANCE_FILE):
        return {}
    result = {}
    with open(BALANCE_FILE) as f:
        for line in f:
            parts = line.strip().split(":", 1)
            if len(parts) == 2:
                try:
                    result[parts[0]] = float(parts[1])
                except ValueError:
                    pass
    return result

def save_balances(balances: dict[str, float]) -> None:
    with open(BALANCE_FILE, "w") as f:
        for uid, bal in balances.items():
            f.write(f"{uid}:{bal:.4f}\n")

def get_balance(user_id: str) -> float:
    balances = load_balances()
    if user_id not in balances:
        balances[user_id] = STARTING_BALANCE
        save_balances(balances)
    return balances[user_id]

def add_balance(user_id: str, amount: float) -> float:
    balances = load_balances()
    if user_id not in balances:
        balances[user_id] = STARTING_BALANCE
    balances[user_id] = round(balances[user_id] + amount, 4)
    save_balances(balances)
    return balances[user_id]

def deduct_balance(user_id: str, amount: float) -> float:
    balances = load_balances()
    if user_id not in balances:
        balances[user_id] = STARTING_BALANCE
    balances[user_id] = round(balances[user_id] - amount, 4)
    save_balances(balances)
    return balances[user_id]

# ── OTP earnings tracker ────────────────────────────────────────────────────────

def load_earnings() -> dict[str, float]:
    if not os.path.exists(EARNINGS_FILE):
        return {}
    result = {}
    with open(EARNINGS_FILE) as f:
        for line in f:
            parts = line.strip().split(":", 1)
            if len(parts) == 2:
                try:
                    result[parts[0]] = float(parts[1])
                except ValueError:
                    pass
    return result

def save_earnings(earnings: dict[str, float]) -> None:
    with open(EARNINGS_FILE, "w") as f:
        for uid, amt in earnings.items():
            f.write(f"{uid}:{amt:.4f}\n")

def add_earning(user_id: str, amount: float) -> float:
    earnings = load_earnings()
    earnings[user_id] = round(earnings.get(user_id, 0.0) + amount, 4)
    save_earnings(earnings)
    return earnings[user_id]

def get_earning(user_id: str) -> float:
    return load_earnings().get(user_id, 0.0)

# ── Withdrawal log / Pending ──────────────────────────────────────────────────

def log_withdrawal(user_id: str, method: str, amount: float, account: str) -> None:
    from datetime import datetime
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    with open(WITHDRAW_FILE, "a") as f:
        f.write(f"{ts}|{user_id}|{method}|{amount:.4f}|{account}\n")

def _load_pending() -> dict:
    if not os.path.exists(PENDING_FILE):
        return {}
    with open(PENDING_FILE) as f:
        try: return json.load(f)
        except: return {}

def _save_pending(data: dict) -> None:
    with open(PENDING_FILE, "w") as f:
        json.dump(data, f, indent=2)

def create_pending_withdrawal(user_id: str, method: str, amount: float,
                               account: str, user_name: str, username: str) -> str:
    from datetime import datetime
    data = _load_pending()
    wid  = str(int(datetime.utcnow().timestamp() * 1000))
    data[wid] = {
        "user_id": user_id, "method": method, "amount": amount,
        "account": account, "user_name": user_name, "username": username,
        "ts": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"), "status": "pending",
    }
    _save_pending(data)
    return wid

def resolve_withdrawal(wid: str, status: str) -> None:
    data = _load_pending()
    if wid in data:
        data[wid]["status"] = status
        _save_pending(data)

def get_pending_withdrawal(wid: str) -> dict | None:
    return _load_pending().get(wid)

# ── Keyboards ───────────────────────────────────────────────────────────────────

def main_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[BTN_NUMBERS, BTN_STATUS], [BTN_STOCK, BTN_BALANCE], [BTN_PROFILE]],
        resize_keyboard=True,
    )

def platform_inline_keyboard() -> InlineKeyboardMarkup:
    in_stock = platforms_with_stock()
    def dot(pid): return "🟢" if pid in in_stock else "🔴"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{dot('discord')} 🎮 DISCORD",  callback_data="plat_discord"),
         InlineKeyboardButton(f"{dot('tiktok')}  🎵 TikTok",   callback_data="plat_tiktok")],
        [InlineKeyboardButton(f"{dot('whatsapp')} 💬 WhatsApp", callback_data="plat_whatsapp")],
        [InlineKeyboardButton(f"{dot('facebook')} 📘 Facebook",  callback_data="plat_facebook")],
        [InlineKeyboardButton("🚫 Close",                         callback_data="close_menu")],
    ])

def country_inline_keyboard(platform: str) -> InlineKeyboardMarkup:
    all_c = [c for p, c in all_platform_country_pairs() if p == platform]
    buttons = []
    for country in all_c:
        cinfo = COUNTRIES.get(country, {"label": country.title(), "flag": "🌐"})
        price = get_purchase_price(platform, country)
        avail = len(available_numbers(platform, country))
        dot   = "🟢" if avail > 0 else "🔴"
        cb    = f"country_{platform}_{country}" if avail > 0 else f"nostock_{platform}_{country}"
        buttons.append([InlineKeyboardButton(f"{dot} {cinfo['flag']} {cinfo['label']}  🎁${price:.2f}  📦{avail}", callback_data=cb)])
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="back_platforms")])
    return InlineKeyboardMarkup(buttons)

# ক্লিকের সাথে কপি করার জন্য বাটন পরিবর্তন করা হয়েছে
def number_copy_keyboard(platform: str, number: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"📋 {number}", callback_data=f"copynum_{number}")],
        [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_main")],
    ])

def number_picker_keyboard(platform: str, country: str, page_numbers: list[str], total_avail: int) -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton(f"📱 {num}", callback_data=f"num_{i}")] for i, num in enumerate(page_numbers)]
    if total_avail > PAGE_SIZE:
        buttons.append([InlineKeyboardButton("🔄 Change Number", callback_data="change_num")])
    buttons.append([InlineKeyboardButton("📢 OTP Group", url=OTP_GROUP)])
    return InlineKeyboardMarkup(buttons)

def withdraw_method_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❤️ Bkash", callback_data="withdraw_bkash"),
         InlineKeyboardButton("🛺 Nagad", callback_data="withdraw_nagad")],
        [InlineKeyboardButton("🔶 Binance", callback_data="withdraw_binance")],
    ])

# ── Handlers ────────────────────────────────────────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    get_balance(str(user.id))
    welcome = (f"🖤 *Welcome to {BOT_NAME}*\n"
               f"🆔 ID: `{user.id}`\n"
               f"{DIVIDER}\n"
               f"⚡ Fast Number — 👑 Premium Rate\n"
               f"🚀 Fast Response — 🔒 100% Safe")
    await update.message.reply_text(welcome, parse_mode="Markdown", reply_markup=main_reply_keyboard())

async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text.strip()
    user = update.effective_user
    uid  = str(user.id)
    
    # Withdrawal logic... (unchanged)
    tl = text.lower()
    if "numbers" in tl:
        await update.message.reply_text("📲 *Select Platform:*", parse_mode="Markdown", reply_markup=platform_inline_keyboard())
    elif "status" in tl:
        # Status Text code...
        records = given_for_user(uid)
        lines = [f"📊 *ʏᴏᴜʀ sᴛᴀᴛᴜs*\n{DIVIDER}\n💰 Balance: *${get_balance(uid):.4f}*"]
        for r in records:
            lines.append(f"📱 `{r['number']}` · 🎁 *+${get_purchase_price(r['platform'], r['country']):.2f}*")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    elif "balance" in tl:
        msg = (f"💰 *ʙᴀʟᴀɴᴄᴇ*\n{DIVIDER}\n🆔 ID: `{uid}`\n🪙 Balance: *{get_balance(uid):.4f}$*\n"
               f"💸 Min withdraw: *${MIN_WITHDRAW:.2f}$*\n{DIVIDER}")
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=withdraw_method_keyboard())

async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    data  = query.data
    user  = query.from_user
    uid   = str(user.id)

    if data.startswith("copynum_"):
        number = data.removeprefix("copynum_")
        await query.answer(text=f"Copied: {number}", show_alert=False)
        return

    elif data.startswith("num_"):
        state = ctx.user_data.get("picker")
        if not state: return
        idx = int(data.split("_")[1])
        avail = available_numbers(state["platform"], state["country"])
        number = avail[state["offset"] + idx]
        
        # ফ্রি নাম্বার অ্যাসাইন করা হচ্ছে (টাকা কাটবে না)
        save_given(uid, state["platform"], state["country"], number)
        ctx.user_data.pop("picker", None)
        
        success = (f"✅ *NUMBER ASSIGNED!*\n{DIVIDER}\n📱 Number: `{number}`\n"
                   f"🎁 You earn *+${get_purchase_price(state['platform'], state['country']):.2f}* per OTP!")
        await query.edit_message_text(success, parse_mode="Markdown", reply_markup=number_copy_keyboard(state["platform"], number))

    elif data.startswith("country_"):
        _, platform, country = data.split("_", 2)
        avail = available_numbers(platform, country)
        ctx.user_data["picker"] = {"platform": platform, "country": country, "offset": 0}
        page = avail[:PAGE_SIZE]
        msg = f"Your {country.title()} {platform.upper()} *NUMBER*\n{DIVIDER}\n👇 Tap to claim:"
        await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=number_picker_keyboard(platform, country, page, len(avail)))

    elif data == "back_platforms":
        await query.edit_message_text("📲 *Select Platform:*", parse_mode="Markdown", reply_markup=platform_inline_keyboard())

# ── OTP callback ──────────────────────────────────────────────────────────

def make_otp_callback(app: Application, loop: asyncio.AbstractEventLoop):
    def on_otp(user_id: str, number: str, platform: str, country: str, sms_text: str, dt_str: str):
        reward = get_purchase_price(platform, country)
        new_bal = add_balance(user_id, reward)
        add_earning(user_id, reward)
        
        async def _send():
            msg = (f"📨 *NEW OTP RECEIVED!*\n{DIVIDER}\n📱 Number: `{number}`\n"
                   f"💬 *Message:* `{sms_text}`\n{DIVIDER}\n💵 Earned: *+${reward:.2f}*")
            await app.bot.send_message(chat_id=int(user_id), text=msg, parse_mode="Markdown")
        asyncio.run_coroutine_threadsafe(_send(), loop)
    return on_otp

def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    global _poller
    loop = asyncio.get_event_loop()
    _poller = OTPPoller(assigned_numbers_fn=get_assigned_numbers_map, on_otp_fn=make_otp_callback(app, loop))
    _poller.start()
    app.run_polling()

if __name__ == "__main__":
    main()