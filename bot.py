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
# otp_poller থেকে শুধু যা দরকার তা নেওয়া হচ্ছে
from otp_poller import OTPPoller

# --- আপনার টোকেন এখানে বসানো হয়েছে ---
TOKEN = "8521269510:AAH1B89grrs-pgV_uBz4lTciQn6i1Fam4PQ"
# ----------------------------------

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

# ফোল্ডার না থাকলে তৈরি করে নেওয়া
if not os.path.exists(NUMBERS_DIR):
    os.makedirs(NUMBERS_DIR)

# এনভায়রনমেন্ট ভ্যারিয়েবল না থাকলে ডিফল্ট মান সেট করা
ADMIN_ID      = int(os.environ.get("ADMIN_TELEGRAM_ID", "12345678"))
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
    # ... (বাকি দেশগুলো কোডে আগের মতোই থাকবে)
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

# ── load_prices এরর দূর করতে এই ফাংশনটি যোগ করা হলো ────────────────
def load_prices():
    return {}

# ── File / data helpers ─────────────────────────────────────────────────────────

def numbers_path(platform: str, country: str) -> str:
    return os.path.join(NUMBERS_DIR, f"{platform}_{country}.txt")

def all_platform_country_pairs() -> list[tuple[str, str]]:
    pairs = []
    for path in glob(os.path.join(NUMBERS_DIR, "*.txt")):
        name  = os.path.splitext(os.path.basename(path))[0]
        parts = name.split("_", 1)
        if len(parts) == 2:
            pairs.append((parts[0].lower(), parts[1].lower()))
    return pairs

def load_all_numbers(platform: str, country: str) -> list[str]:
    path = numbers_path(platform, country)
    if not os.path.exists(path):
        return []
    with open(path, 'r', encoding='utf-8') as f:
        return [l.strip() for l in f if l.strip()]

def load_given() -> list[dict]:
    if not os.path.exists(GIVEN_FILE):
        return []
    rows = []
    with open(GIVEN_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            parts = line.strip().split(":", 3)
            if len(parts) == 4:
                rows.append({"user_id": parts[0], "platform": parts[1],
                             "country": parts[2], "number": parts[3]})
    return rows

def save_given(user_id: str, platform: str, country: str, number: str) -> None:
    with open(GIVEN_FILE, "a", encoding='utf-8') as f:
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
    with open(SLOT_PRICES_FILE, 'r') as f:
        try:
            return json.load(f)
        except:
            return {}

def get_purchase_price(platform: str, country: str) -> float:
    key = f"{platform}:{country}"
    slot = load_slot_prices()
    if key in slot:
        return slot[key]
    return PURCHASE_PRICES.get((platform, country), DEFAULT_PURCHASE_PRICE)

def platforms_with_stock() -> list[str]:
    pairs = all_platform_country_pairs()
    stock_plats = set()
    for p, c in pairs:
        if len(available_numbers(p, c)) > 0:
            stock_plats.add(p)
    return list(stock_plats)

# ── Balance helpers ─────────────────────────────────────────────────────────────

def load_balances() -> dict[str, float]:
    if not os.path.exists(BALANCE_FILE):
        return {}
    result = {}
    with open(BALANCE_FILE, 'r') as f:
        for line in f:
            parts = line.strip().split(":", 1)
            if len(parts) == 2:
                try: result[parts[0]] = float(parts[1])
                except: pass
    return result

def save_balances(balances: dict[str, float]) -> None:
    with open(BALANCE_FILE, "w") as f:
        for uid, bal in balances.items():
            f.write(f"{uid}:{bal:.4f}\n")

def get_balance(user_id: str) -> float:
    balances = load_balances()
    return balances.get(user_id, STARTING_BALANCE)

def add_balance(user_id: str, amount: float) -> float:
    balances = load_balances()
    balances[user_id] = round(balances.get(user_id, 0.0) + amount, 4)
    save_balances(balances)
    return balances[user_id]

def add_earning(user_id: str, amount: float) -> float:
    if not os.path.exists(EARNINGS_FILE):
        earnings = {}
    else:
        with open(EARNINGS_FILE, 'r') as f:
            earnings = {l.split(':')[0]: float(l.split(':')[1]) for l in f if ':' in l}
    
    earnings[user_id] = round(earnings.get(user_id, 0.0) + amount, 4)
    with open(EARNINGS_FILE, 'w') as f:
        for uid, amt in earnings.items():
            f.write(f"{uid}:{amt:.4f}\n")
    return earnings[user_id]

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
        [InlineKeyboardButton(f"{dot('discord')} DISCORD",  callback_data="plat_discord"),
         InlineKeyboardButton(f"{dot('tiktok')} TikTok",   callback_data="plat_tiktok")],
        [InlineKeyboardButton(f"{dot('whatsapp')} WhatsApp", callback_data="plat_whatsapp")],
        [InlineKeyboardButton(f"{dot('facebook')} Facebook",  callback_data="plat_facebook")],
        [InlineKeyboardButton("🚫 Close", callback_data="close_menu")],
    ])

def country_inline_keyboard(platform: str) -> InlineKeyboardMarkup:
    # numbers ফোল্ডার থেকে ফাইল খুঁজে বের করা হচ্ছে
    files = glob(os.path.join(NUMBERS_DIR, f"{platform}_*.txt"))
    buttons = []
    for fpath in files:
        country = os.path.basename(fpath).replace(f"{platform}_", "").replace(".txt", "")
        cinfo = COUNTRIES.get(country, {"label": country.title(), "flag": "🌐"})
        price = get_purchase_price(platform, country)
        avail = len(available_numbers(platform, country))
        dot   = "🟢" if avail > 0 else "🔴"
        cb    = f"country_{platform}_{country}" if avail > 0 else f"nostock_{platform}_{country}"
        buttons.append([InlineKeyboardButton(f"{dot} {cinfo['flag']} {cinfo['label']}  🎁${price:.2f}  📦{avail}", callback_data=cb)])
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="back_platforms")])
    return InlineKeyboardMarkup(buttons)

def number_picker_keyboard(platform: str, country: str, page_numbers: list[str], total_avail: int) -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton(f"📱 {num}", callback_data=f"num_{i}")] for i, num in enumerate(page_numbers)]
    buttons.append([InlineKeyboardButton("📢 OTP Group", url=OTP_GROUP)])
    return InlineKeyboardMarkup(buttons)

# ── Handlers ────────────────────────────────────────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    welcome = (f"🖤 *Welcome to {BOT_NAME}*\n"
               f"🆔 ID: `{user.id}`\n"
               f"{DIVIDER}\n"
               f"⚡ Fast Number — 👑 Premium Rate\n"
               f"🚀 Fast Response — 🔒 100% Safe")
    await update.message.reply_text(welcome, parse_mode="Markdown", reply_markup=main_reply_keyboard())

async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text.strip().lower()
    uid  = str(update.effective_user.id)
    
    if BTN_NUMBERS in text:
        await update.message.reply_text("📲 *Select Platform:*", parse_mode="Markdown", reply_markup=platform_inline_keyboard())
    elif BTN_STATUS in text:
        records = given_for_user(uid)
        lines = [f"📊 *ʏᴏᴜʀ sᴛᴀᴛᴜs*\n{DIVIDER}\n💰 Balance: *${get_balance(uid):.4f}*"]
        for r in records[-5:]: # শেষ ৫টি রেকর্ড দেখানো হচ্ছে
            lines.append(f"📱 `{r['number']}` · 🎁 *+${get_purchase_price(r['platform'], r['country']):.2f}*")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    elif BTN_BALANCE in text:
        msg = (f"💰 *ʙᴀʟᴀɴᴄᴇ*\n{DIVIDER}\n🆔 ID: `{uid}`\n🪙 Balance: *{get_balance(uid):.4f}$*\n"
               f"💸 Min withdraw: *${MIN_WITHDRAW:.2f}$*\n{DIVIDER}")
        await update.message.reply_text(msg, parse_mode="Markdown")

async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    data  = query.data
    uid   = str(query.from_user.id)

    if data == "close_menu":
        await query.message.delete()
    elif data.startswith("plat_"):
        platform = data.split("_")[1]
        await query.edit_message_text(f"🌍 *Select Country for {platform.upper()}:*", 
                                     parse_mode="Markdown", 
                                     reply_markup=country_inline_keyboard(platform))
    elif data == "back_platforms":
        await query.edit_message_text("📲 *Select Platform:*", parse_mode="Markdown", reply_markup=platform_inline_keyboard())
    elif data.startswith("country_"):
        _, platform, country = data.split("_", 2)
        avail = available_numbers(platform, country)
        ctx.user_data["picker"] = {"platform": platform, "country": country, "offset": 0}
        page = avail[:PAGE_SIZE]
        msg = f"Your {country.title()} {platform.upper()} *NUMBER*\n{DIVIDER}\n👇 Tap to claim:"
        await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=number_picker_keyboard(platform, country, page, len(avail)))
    elif data.startswith("num_"):
        state = ctx.user_data.get("picker")
        if not state: return
        idx = int(data.split("_")[1])
        avail = available_numbers(state["platform"], state["country"])
        number = avail[idx]
        save_given(uid, state["platform"], state["country"], number)
        await query.edit_message_text(f"✅ *NUMBER ASSIGNED!*\n{DIVIDER}\n📱 Number: `{number}`\n🎁 Wait for OTP...", parse_mode="Markdown")

# ── OTP callback ──────────────────────────────────────────────────────────

def make_otp_callback(app: Application, loop: asyncio.AbstractEventLoop):
    def on_otp(user_id: str, number: str, platform: str, country: str, sms_text: str, dt_str: str):
        reward = get_purchase_price(platform, country)
        add_balance(user_id, reward)
        add_earning(user_id, reward)
        
        async def _send():
            msg = (f"📨 *NEW OTP RECEIVED!*\n{DIVIDER}\n📱 Number: `{number}`\n"
                   f"💬 *Message:* `{sms_text}`\n{DIVIDER}\n💵 Earned: *+${reward:.2f}*")
            try: await app.bot.send_message(chat_id=int(user_id), text=msg, parse_mode="Markdown")
            except: pass
        asyncio.run_coroutine_threadsafe(_send(), loop)
    return on_otp

def main() -> None:
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    global _poller
    try:
        loop = asyncio.get_event_loop()
        _poller = OTPPoller(assigned_numbers_fn=get_assigned_numbers_map, on_otp_fn=make_otp_callback(app, loop))
        _poller.start()
    except:
        print("OTP Poller could not start. Check otp_poller.py")

    print("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()