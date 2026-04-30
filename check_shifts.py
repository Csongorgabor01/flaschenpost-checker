import requests
import os
import json
import sys
from datetime import datetime, timezone, timedelta

# ── Config from GitHub Secrets ─────────────────────────────────────────────────
REFRESH_TOKEN      = os.environ.get("REFRESH_TOKEN", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")

EMPLOYEE_ID  = "193716"
CLIENT_ID    = "86fe707f-ea47-4bf3-aa81-42579bf180cd"
TOKEN_URL    = "https://fauth.flaschenpost.de/oauth2/token"
SHIFTS_BASE  = "https://api.flaschen.io/employee-portal-api/v1/shift-offer"
SEEN_FILE    = "seen_shifts.json"

# ── Telegram ───────────────────────────────────────────────────────────────────
def send_telegram(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=10)
        r.raise_for_status()
    except Exception as e:
        print(f"Telegram send failed: {e}")

# ── Seen shifts persistence ────────────────────────────────────────────────────
def load_seen() -> set:
    try:
        with open(SEEN_FILE) as f:
            return set(json.load(f))
    except Exception:
        return set()

def save_seen(seen: set):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen), f)

# ── Step 1: Exchange refresh token for a fresh access token ───────────────────
def get_access_token(refresh_token: str) -> tuple:
    resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type":    "refresh_token",
            "refresh_token": refresh_token,
            "client_id":     CLIENT_ID,
            "scope":         "openid profile email offline_access",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=15,
    )

    if resp.status_code != 200:
        send_telegram(
            f"Flaschenpost: Token refresh failed (HTTP {resp.status_code}).\n\n"
            "Fix:\n"
            "1. Log in at portal.flaschenpost.de\n"
            "2. F12 -> Application -> Local Storage -> portal.flaschenpost.de\n"
            "3. Copy the value of 'refresh_token'\n"
            "4. GitHub repo -> Settings -> Secrets -> update REFRESH_TOKEN"
        )
        print(f"Token refresh failed: {resp.status_code} {resp.text[:200]}")
        sys.exit(1)

    data        = resp.json()
    new_access  = data["access_token"]
    new_refresh = data.get("refresh_token", refresh_token)
    print("Token refreshed successfully.")
    return new_access, new_refresh

# ── Step 2: Warn if refresh token was rotated ─────────────────────────────────
def warn_if_rotated(old_refresh: str, new_refresh: str):
    if old_refresh == new_refresh:
        return
    send_telegram(
        "Flaschenpost: Refresh token was rotated.\n\n"
        "Update REFRESH_TOKEN in GitHub Secrets:\n"
        f"{new_refresh}"
    )
    print("Refresh token rotated — Telegram alert sent.")

# ── Step 3: Check for available shifts ────────────────────────────────────────
def check_shifts(access_token: str):
    now       = datetime.now(timezone.utc)
    from_date = now.strftime("%Y-%m-%dT00:00:00.000Z")
    to_date   = (now + timedelta(days=30)).strftime("%Y-%m-%dT23:59:59.999Z")

    url = (
        f"{SHIFTS_BASE}/{EMPLOYEE_ID}/target-shift-slots-assignable-to-employee"
        f"?From={from_date}&To={to_date}"
    )
    headers = {
        "Authorization":   f"Bearer {access_token}",
        "Accept":          "application/json, text/plain, */*",
        "Origin":          "https://portal.flaschenpost.de",
        "Accept-Language": "en-DE,en;q=0.9,hu-HU;q=0.8",
    }

    resp = requests.get(url, headers=headers, timeout=15)
    print(f"Shift check status: {resp.status_code} at {now.strftime('%Y-%m-%d %H:%M')} UTC")

    if resp.status_code == 204:
        print("No shifts available.")
        return

    if resp.status_code != 200:
        print(f"Unexpected status {resp.status_code}: {resp.text[:200]}")
        return

    try:
        data = resp.json()
    except Exception:
        print("Could not parse response JSON.")
        return

    seen = load_seen()

    new_shifts = []
    for s in data:
        key = s.get("start", "") + str(s.get("durationInMinutes", ""))
        if key not in seen:
            new_shifts.append((key, s))

    if not new_shifts:
        print(f"{len(data)} shift(s) found but all already notified.")
        return

    # Build clean message
    lines = []
    for key, s in new_shifts:
        start_raw = s.get("start", "")
        duration  = s.get("durationInMinutes", 0)
        dt_utc    = datetime.fromisoformat(start_raw.replace("+00:00", "+00:00"))
        dt_local  = dt_utc + timedelta(hours=2)
        date_str  = dt_local.strftime("%a %d.%m.%Y")
        time_str  = dt_local.strftime("%H:%M")
        end_str   = (dt_local + timedelta(minutes=duration)).strftime("%H:%M")
        lines.append(f"📦 {date_str}  {time_str} - {end_str}  ({duration//60}h)")
        seen.add(key)

    send_telegram(
        f"🚨 {len(new_shifts)} NEW SHIFT(S)!\n\n"
        + "\n".join(lines)
        + "\n\n👉 portal.flaschenpost.de"
    )
    print(f"{len(new_shifts)} new shift(s) — Telegram sent!")

    save_seen(seen)

# ── Entrypoint ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not REFRESH_TOKEN:
        print("ERROR: REFRESH_TOKEN secret is missing.")
        sys.exit(1)

    access_token, new_refresh = get_access_token(REFRESH_TOKEN)
    warn_if_rotated(REFRESH_TOKEN, new_refresh)
    check_shifts(access_token)
