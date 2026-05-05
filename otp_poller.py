import os
import re
import json
import time
import logging
import threading
from datetime import datetime, timedelta
import httpx

logger = logging.getLogger(__name__)

BASE_DIR     = os.path.dirname(__file__)
PANEL_BASE   = "http://93.190.143.35/ints"
PRICES_FILE  = os.path.join(BASE_DIR, "prices.json")
SEEN_FILE    = os.path.join(BASE_DIR, "otp_seen.txt")
POLL_INTERVAL = 15          # ওটিপি চেক করার বিরতি (১৫ সেকেন্ড)
SESSION_TTL   = 60 * 25    # ২৫ মিনিট পর পর সেশন রিনিউ হবে

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120"

# ── প্যানেল কনফিগ (এখানে আপনার তথ্য দিন) ──────────────────────────────────────
PANEL_USER = os.environ.get("OTP_PANEL_USERNAME", "rabbani017") 
PANEL_PASS = os.environ.get("OTP_PANEL_PASSWORD", "rabbani017")
# ────────────────────────────────────────────────────────────────────────────

def load_seen() -> set[str]:
    if not os.path.exists(SEEN_FILE):
        return set()
    with open(SEEN_FILE) as f:
        return {l.strip() for l in f if l.strip()}

def save_seen(key: str) -> None:
    with open(SEEN_FILE, "a") as f:
        f.write(key + "\n")

class PanelSession:
    def __init__(self):
        self._client: httpx.Client | None = None
        self._sesskey: str = ""
        self._last_login: float = 0.0
        self._lock = threading.Lock()

    def _do_login(self) -> bool:
        user = PANEL_USER
        pw   = PANEL_PASS
        if not user or user == "আপনার_ইউজারনেম":
            logger.error("OTP_PANEL_USERNAME সেট করা নেই!")
            return False
        try:
            if self._client:
                self._client.close()
            self._client = httpx.Client(headers={"User-Agent": UA}, follow_redirects=True, timeout=20)
            
            # লগইন পেজ থেকে ক্যাপচা এবং টোকেন সংগ্রহ
            r = self._client.get(f"{PANEL_BASE}/Login")
            capt = re.search(r"What is (\d+) \+ (\d+)", r.text)
            crlf = re.search(r"name='crlf' value='([^']+)'", r.text)
            
            if not capt or not crlf:
                logger.error("লগইন ফর্ম পাওয়া যায়নি")
                return False
                
            answer = int(capt.group(1)) + int(capt.group(2))
            self._client.post(
                f"{PANEL_BASE}/signin",
                data={"username": user, "password": pw, "capt": str(answer), "crlf": crlf.group(1)},
                headers={"Referer": f"{PANEL_BASE}/Login"},
            )
            
            # সেশন ভেরিফাই
            r2 = self._client.get(f"{PANEL_BASE}/client/SMSCDRStats")
            sk = re.search(r"sesskey=([A-Za-z0-9+/=%]+)", r2.text)
            if not sk:
                logger.error("লগইন ব্যর্থ! ইউজারনেম বা পাসওয়ার্ড চেক করুন।")
                return False
                
            self._sesskey = sk.group(1)
            self._last_login = time.time()
            logger.info("প্যানেল লগইন সফল হয়েছে!")
            return True
        except Exception as e:
            logger.error("লগইন এরর: %s", e)
            return False

    def ensure_logged_in(self) -> bool:
        with self._lock:
            if time.time() - self._last_login > SESSION_TTL or not self._client:
                return self._do_login()
            return True

    def fetch_test_cdrs(self) -> list[list]:
        if not self.ensure_logged_in():
            return []
        try:
            r = self._client.get(
                f"{PANEL_BASE}/client/res/data_testsmscdr.php",
                params={"iDisplayStart": "0", "iDisplayLength": "200", "sEcho": str(int(time.time()))},
                headers={"Referer": f"{PANEL_BASE}/client/SMSTestPanel", "X-Requested-With": "XMLHttpRequest"},
            )
            return r.json().get("aaData", [])
        except Exception:
            self._last_login = 0 
            return []

class OTPPoller:
    def __init__(self, assigned_numbers_fn, on_otp_fn):
        self._get_assigned = assigned_numbers_fn
        self._on_otp       = on_otp_fn
        self._session      = PanelSession()
        self._seen         = load_seen()
        self._stop         = threading.Event()

    def start(self):
        threading.Thread(target=self._run, daemon=True, name="otp-poller").start()
        logger.info("ওটিপি পোলার চালু হয়েছে...")

    def _run(self):
        while not self._stop.is_set():
            try:
                self._poll()
            except Exception as e:
                logger.error("পোলার এরর: %s", e)
            self._stop.wait(POLL_INTERVAL)

    def _poll(self):
        assigned = self._get_assigned()
        if not assigned:
            return

        rows = self._session.fetch_test_cdrs()
        if not rows:
            return

        for row in rows:
            if len(row) < 5:
                continue
            
            # row: [সময়, রেঞ্জ, নাম্বার, প্রেরক, এসএমএস]
            dt_str, number, sms = str(row[0]), str(row[2]), str(row[4])
            key = f"{dt_str}|{number}|{sms[:20]}"
            
            if key in self._seen:
                continue

            # নাম্বার ম্যাচিং লজিক
            norm_num = number.lstrip("+").lstrip("0")
            match = None
            for assigned_num, info in assigned.items():
                an = assigned_num.lstrip("+").lstrip("0")
                if an == norm_num or assigned_num == number:
                    match = info
                    break

            if match:
                self._seen.add(key)
                save_seen(key)
                logger.info(f"ম্যাচ পাওয়া গেছে! নাম্বার: {number}")
                # bot.py-তে ওটিপি পাঠানো এবং ব্যালেন্স যোগ করার কলব্যাক
                self._on_otp(
                    user_id=match["user_id"],
                    number=number,
                    platform=match["platform"],
                    country=match["country"],
                    sms_text=sms,
                    dt_str=dt_str
                )
            else:
                self._seen.add(key)
                save_seen(key)