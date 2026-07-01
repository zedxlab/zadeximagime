"""
ZadeXImagine — Telegram AI Image Generation Bot
Developer: @zade4everbot
API: CallMissed (https://api.callmissed.com/v1)

Run:
    pip install -r requirements.txt
    python bot.py

Token + API key are hardcoded below in CONFIG. API key can also be changed
live from the admin panel (🔑 Change API Key) — that overrides the hardcoded
one and is stored in the database, no restart needed.
"""

import asyncio
import base64
import html as html_lib
import json
import logging
import os
import re
import sqlite3
from datetime import date, datetime
from io import BytesIO

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ─────────────────────────────  CONFIG  ─────────────────────────────
# 🔑 Hardcode your real values here before running.
BOT_TOKEN = "8853018550:AAEGf61WdSNdDx7UUOhKT7SNxnqXH4RtXYc"
CALLMISSED_API_KEY = "cm_HOwWI9nXr_8-isyJ9mfZPgo96BYop4aTHcD9UOKOF1U"  # fallback default — can be overridden live via /admin
API_BASE = "https://api.callmissed.com/v1"

# FreeToChat — used only for flux-2-pro & nano-banana-2 (cheaper/free route)
FREETOCHAT_URL = "https://api.freetochat.app/api/v1/ai/chat/completions"
FREETOCHAT_TOKEN = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJlNTEzZWZkZS0zZDEzLTQ0OGEtODkzMy"
    "02NzMxY2JjNzljMTAiLCJyb2xlIjoidXNlciIsImlhdCI6MTc4Mjg0NzQ4NywiZXhwIjoxNzg1NDM5"
    "NDg3fQ.YPqkcOPj_Yyb-ckhRrvFNo_h0lpH2EzxojN9_hHoasc"
)  # fallback default — can be overridden live via /admin

OWNER_ID = 8558910409
BOT_NAME = "ZadeXImagine"
DEV_USERNAME = "zade4everbot"
DEV_URL = f"https://t.me/{DEV_USERNAME}"

DEFAULT_DAILY_LIMIT = 5
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "zadeximagine.db")

MODELS = {
    "flux-2-pro": "🚀 Flux 2 Pro",
    "nano-banana-2": "🍌 Nano Banana 2",
    "flux-2-dev": "🌊 Flux 2 Dev",
    "flux-2-klein-9b": "🌊 Flux 2 Klein 9B",
    "lucid-origin": "✨ Lucid Origin",
    "phoenix-1.0": "🔥 Phoenix 1.0",
    "sdxl-lightning": "⚡ SDXL Lightning",
    "dreamshaper-8-lcm": "🎨 Dreamshaper 8 LCM",
}

# Models routed through FreeToChat instead of CallMissed, with the exact
# hint string the router expects appended to the prompt.
FREETOCHAT_MODELS = {
    "flux-2-pro": "flux pro",
    "nano-banana-2": "nano banana 2",
}

# The underlying FreeToChat *chat* model used to drive the image-artist skill
# (i.e. the "model" field in the /chat/completions payload — NOT the image
# model above, which is just a hint in the prompt text). Admin can switch
# this live from /admin without touching code.
DEFAULT_FREETOCHAT_CHAT_MODEL = "kimi-k2.7-code"

FREETOCHAT_CHAT_MODELS = {
    "kimi-k2.7-code": "Kimi K2.7 Code",
    "sarvam-30b": "Sarvam 30B",
    "kimi-k2.6": "Kimi K2.6",
    "kimi-k2.5": "Kimi K2.5",
    "gpt-oss-120b": "GPT-OSS 120B",
    "nemotron-3-super": "Nemotron 3 Super",
    "glm-4.7-flash": "GLM 4.7 Flash",
    "glm-5.2": "GLM 5.2",
    "gemma-4-26b-a4b-it": "Gemma 4 26B",
    "gpt-4o": "GPT-4o",
    "gpt-4.1": "GPT-4.1",
    "gpt-5-mini": "GPT-5 Mini",
    "grok-4.3": "Grok 4.3",
    "DeepSeek-V4-Pro": "DeepSeek V4 Pro",
    "DeepSeek-V4-Flash": "DeepSeek V4 Flash",
}

DEFAULT_WAITING_MESSAGE = "🎨 Generating with *{model}*..."

SIZES = ["512x512", "768x768", "1024x1024", "1024x1536", "1536x1024"]

HISTORY_PAGE_SIZE = 5
USERS_PAGE_SIZE = 8

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
log = logging.getLogger("ZadeXImagine")


# ─────────────────────────────  DATABASE  ─────────────────────────────
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            daily_limit INTEGER DEFAULT 5,
            is_vip INTEGER DEFAULT 0,
            is_banned INTEGER DEFAULT 0,
            used_today INTEGER DEFAULT 0,
            last_reset TEXT,
            joined_at TEXT
        );
        CREATE TABLE IF NOT EXISTS generations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            prompt TEXT,
            model TEXT,
            size TEXT,
            file_id TEXT,
            status TEXT,
            error TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        """
    )
    conn.commit()
    conn.close()


def get_or_create_user(user_id: int, username: str) -> sqlite3.Row:
    conn = db()
    row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
    today = str(date.today())
    if row is None:
        conn.execute(
            "INSERT INTO users (user_id, username, daily_limit, used_today, last_reset, joined_at) "
            "VALUES (?,?,?,0,?,?)",
            (user_id, username, DEFAULT_DAILY_LIMIT, today, datetime.utcnow().isoformat()),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
    else:
        # reset daily counter if date changed
        if row["last_reset"] != today:
            conn.execute(
                "UPDATE users SET used_today=0, last_reset=? WHERE user_id=?", (today, user_id)
            )
            conn.commit()
            row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        # keep username fresh
        if username and row["username"] != username:
            conn.execute("UPDATE users SET username=? WHERE user_id=?", (username, user_id))
            conn.commit()
    conn.close()
    return row


def increment_usage(user_id: int):
    conn = db()
    conn.execute("UPDATE users SET used_today = used_today + 1 WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()


def log_generation(user_id, username, prompt, model, size, file_id, status, error=None):
    conn = db()
    conn.execute(
        "INSERT INTO generations (user_id, username, prompt, model, size, file_id, status, error, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (user_id, username, prompt, model, size, file_id, status, error, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()


def get_setting(key: str, default=None):
    conn = db()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(key: str, value: str):
    conn = db()
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()
    conn.close()


def get_active_api_key() -> str:
    """Live-overridden key (set via admin panel) takes priority over the hardcoded default."""
    return get_setting("callmissed_api_key") or CALLMISSED_API_KEY


def get_active_freetochat_token() -> str:
    """Live-overridden token (set via admin panel) takes priority over the hardcoded default."""
    return get_setting("freetochat_token") or FREETOCHAT_TOKEN


def get_active_freetochat_chat_model() -> str:
    """Live-switched FreeToChat chat model (admin panel) — falls back to the default."""
    stored = get_setting("freetochat_chat_model")
    if stored and stored in FREETOCHAT_CHAT_MODELS:
        return stored
    return DEFAULT_FREETOCHAT_CHAT_MODEL


def set_active_freetochat_chat_model(model_id: str):
    set_setting("freetochat_chat_model", model_id)


def get_waiting_message() -> str:
    return get_setting("waiting_message") or DEFAULT_WAITING_MESSAGE


def set_limit(user_id: int, limit: int) -> bool:
    conn = db()
    cur = conn.execute("UPDATE users SET daily_limit=? WHERE user_id=?", (limit, user_id))
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


def set_vip(user_id: int, vip: bool) -> bool:
    conn = db()
    cur = conn.execute("UPDATE users SET is_vip=? WHERE user_id=?", (1 if vip else 0, user_id))
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


def set_ban(user_id: int, banned: bool) -> bool:
    conn = db()
    cur = conn.execute("UPDATE users SET is_banned=? WHERE user_id=?", (1 if banned else 0, user_id))
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


def get_history(offset: int, limit: int = HISTORY_PAGE_SIZE):
    conn = db()
    rows = conn.execute(
        "SELECT * FROM generations ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)
    ).fetchall()
    total = conn.execute("SELECT COUNT(*) c FROM generations").fetchone()["c"]
    conn.close()
    return rows, total


def get_users_page(offset: int, limit: int = USERS_PAGE_SIZE):
    conn = db()
    rows = conn.execute(
        "SELECT * FROM users ORDER BY joined_at DESC LIMIT ? OFFSET ?", (limit, offset)
    ).fetchall()
    total = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
    conn.close()
    return rows, total


def get_stats():
    conn = db()
    total_users = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
    vip_users = conn.execute("SELECT COUNT(*) c FROM users WHERE is_vip=1").fetchone()["c"]
    banned_users = conn.execute("SELECT COUNT(*) c FROM users WHERE is_banned=1").fetchone()["c"]
    total_gens = conn.execute("SELECT COUNT(*) c FROM generations").fetchone()["c"]
    today_gens = conn.execute(
        "SELECT COUNT(*) c FROM generations WHERE date(created_at)=date('now')"
    ).fetchone()["c"]
    conn.close()
    return {
        "total_users": total_users,
        "vip_users": vip_users,
        "banned_users": banned_users,
        "total_gens": total_gens,
        "today_gens": today_gens,
    }


# ─────────────────────────────  KEYBOARDS  ─────────────────────────────
def dev_button_row():
    return [InlineKeyboardButton("👨‍💻 Developer", url=DEV_URL, style="primary")]


def main_menu_keyboard():
    rows = [[InlineKeyboardButton("🎨 Generate Image", callback_data="gen:start", style="success")]]
    rows.append(dev_button_row())
    return InlineKeyboardMarkup(rows)


def models_keyboard():
    items = list(MODELS.items())
    rows = []
    for i in range(0, len(items), 2):
        pair = items[i : i + 2]
        rows.append(
            [InlineKeyboardButton(label, callback_data=f"model:{mid}", style="primary") for mid, label in pair]
        )
    rows.append([InlineKeyboardButton("❌ Cancel", callback_data="gen:cancel", style="danger")])
    return InlineKeyboardMarkup(rows)


def sizes_keyboard():
    rows = []
    for i in range(0, len(SIZES), 2):
        pair = SIZES[i : i + 2]
        rows.append([InlineKeyboardButton(s, callback_data=f"size:{s}", style="primary") for s in pair])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="gen:start", style="primary")])
    rows.append([InlineKeyboardButton("❌ Cancel", callback_data="gen:cancel", style="danger")])
    return InlineKeyboardMarkup(rows)


def after_generate_keyboard():
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔁 Generate Another", callback_data="gen:start", style="success")]]
    )


def admin_menu_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📊 History", callback_data="admin:history:0", style="primary")],
            [InlineKeyboardButton("👥 Users", callback_data="admin:users:0", style="primary")],
            [InlineKeyboardButton("📈 Stats", callback_data="admin:stats", style="primary")],
            [
                InlineKeyboardButton("⏱ Set Limit", callback_data="admin:ask:setlimit", style="primary"),
            ],
            [
                InlineKeyboardButton("✅ Grant VIP", callback_data="admin:ask:vip", style="success"),
                InlineKeyboardButton("❌ Revoke VIP", callback_data="admin:ask:unvip", style="danger"),
            ],
            [
                InlineKeyboardButton("🚫 Ban User", callback_data="admin:ask:ban", style="danger"),
                InlineKeyboardButton("♻️ Unban User", callback_data="admin:ask:unban", style="success"),
            ],
            [
                InlineKeyboardButton("🔑 CallMissed Key", callback_data="admin:ask:apikey", style="primary"),
                InlineKeyboardButton("🔑 FreeToChat Token", callback_data="admin:ask:ftc_token", style="primary"),
            ],
            [InlineKeyboardButton("⏳ Waiting Message", callback_data="admin:ask:waitmsg", style="primary")],
        ]
    )


def back_to_admin_menu():
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬅️ Admin Menu", callback_data="admin:menu", style="primary")]]
    )


def history_nav_keyboard(offset, total):
    btns = []
    if offset > 0:
        btns.append(
            InlineKeyboardButton("⬅️ Prev", callback_data=f"admin:history:{max(0, offset - HISTORY_PAGE_SIZE)}", style="primary")
        )
    if offset + HISTORY_PAGE_SIZE < total:
        btns.append(
            InlineKeyboardButton("Next ➡️", callback_data=f"admin:history:{offset + HISTORY_PAGE_SIZE}", style="primary")
        )
    rows = [btns] if btns else []
    rows.append([InlineKeyboardButton("⬅️ Admin Menu", callback_data="admin:menu", style="primary")])
    return InlineKeyboardMarkup(rows)


def users_nav_keyboard(offset, total):
    btns = []
    if offset > 0:
        btns.append(
            InlineKeyboardButton("⬅️ Prev", callback_data=f"admin:users:{max(0, offset - USERS_PAGE_SIZE)}", style="primary")
        )
    if offset + USERS_PAGE_SIZE < total:
        btns.append(
            InlineKeyboardButton("Next ➡️", callback_data=f"admin:users:{offset + USERS_PAGE_SIZE}", style="primary")
        )
    rows = [btns] if btns else []
    rows.append([InlineKeyboardButton("⬅️ Admin Menu", callback_data="admin:menu", style="primary")])
    return InlineKeyboardMarkup(rows)


# ─────────────────────────────  HELPERS  ─────────────────────────────
def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID


def display_name(user) -> str:
    return f"@{user.username}" if user.username else user.full_name


def mask_key(key: str) -> str:
    if not key or len(key) < 8:
        return "****"
    return f"{key[:6]}...{key[-4:]}"


def render_template(template: str, **kwargs) -> str:
    """Safely replace {placeholder} tokens without using str.format (avoids
    crashes if the admin-supplied template contains stray braces)."""
    out = template
    for key, val in kwargs.items():
        out = out.replace("{" + key + "}", str(val))
    return out


async def call_image_api(model: str, prompt: str, size: str):
    """Returns (image_bytes, error_message) — CallMissed provider."""
    url = f"{API_BASE}/images/generations"
    headers = {"Authorization": f"Bearer {get_active_api_key()}", "Content-Type": "application/json"}
    payload = {"model": model, "prompt": prompt, "n": 1, "size": size}
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(url, headers=headers, json=payload)
        if resp.status_code != 200:
            return None, (
                f"HTTP {resp.status_code} from CallMissed.\n\n"
                f"📦 Payload sent:\n{json.dumps(payload, ensure_ascii=False)}\n\n"
                f"📄 Response body:\n{resp.text}"
            )
        data = resp.json()
        try:
            b64 = data["data"][0]["b64_json"]
        except (KeyError, IndexError, TypeError):
            return None, (
                f"Unexpected response shape from CallMissed (no data[0].b64_json).\n\n"
                f"📦 Payload sent:\n{json.dumps(payload, ensure_ascii=False)}\n\n"
                f"📄 Response body:\n{resp.text}"
            )
        return base64.b64decode(b64), None
    except httpx.TimeoutException:
        return None, f"⏱ Request timed out (120s).\n\n📦 Payload sent:\n{json.dumps(payload, ensure_ascii=False)}"
    except Exception as e:
        return None, f"Unexpected exception: {e!r}\n\n📦 Payload sent:\n{json.dumps(payload, ensure_ascii=False)}"


def _extract_image_url(obj: dict):
    """Pull an image URL out of a FreeToChat event payload, tolerating a
    few different shapes the upstream router might use."""
    data_block = obj.get("data")
    if not isinstance(data_block, dict):
        data_block = obj
    for key in ("url", "image_url"):
        if data_block.get(key):
            return data_block[key]
    images = data_block.get("images")
    if isinstance(images, list) and images:
        first = images[0]
        if isinstance(first, dict):
            return first.get("url") or first.get("image_url")
        if isinstance(first, str):
            return first
    return None


IMAGE_URL_RE = re.compile(
    r'https?://[^\s"\'<>\\]+\.(?:png|jpe?g|webp|gif)(?:\?[^\s"\'<>\\]*)?', re.IGNORECASE
)


def _find_image_url(obj):
    """Recursively scan a parsed JSON value for the first string that looks
    like a direct image URL — used as a fallback in case the upstream event
    name/shape doesn't match the documented 'image_generated' format."""
    if isinstance(obj, str):
        m = IMAGE_URL_RE.search(obj)
        return m.group(0) if m else None
    if isinstance(obj, dict):
        for v in obj.values():
            found = _find_image_url(v)
            if found:
                return found
        return None
    if isinstance(obj, list):
        for v in obj:
            found = _find_image_url(v)
            if found:
                return found
    return None


async def call_freetochat_api(model: str, prompt: str):
    """Returns (image_url, error_message) — FreeToChat provider (SSE stream).

    Parsing is deliberately tolerant: rather than requiring an exact
    `type == "image_generated"` match, every parsed event is scanned
    (recursively) for any string that looks like a direct image URL. This
    survives the upstream using a different event name/shape than the
    documented one (e.g. a `tool_call_end`/`tool_call_result` event instead
    of `image_generated`), as long as the URL itself shows up somewhere in
    the payload.

    On failure, the returned error string is a FULL diagnostic dump
    (exact payload sent + every SSE event received) so the cause can be
    read straight from the forwarded admin message, no guessing needed.
    """
    hint = FREETOCHAT_MODELS.get(model, model)
    payload = {
        "model": get_active_freetochat_chat_model(),
        "messages": [{"role": "user", "content": f"Make {prompt} with {hint}"}],
        "stream": True,
        "tools_enabled": False,
        "web_search_enabled": False,
        "skill": "image-artist",
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {get_active_freetochat_token()}",
    }
    event_log = []  # every (event_type, raw_json_str) received, in order — no cap
    tool_args = None
    try:
        async with httpx.AsyncClient(timeout=180) as client:
            async with client.stream("POST", FREETOCHAT_URL, headers=headers, json=payload) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    return None, (
                        f"HTTP {resp.status_code} from FreeToChat.\n\n"
                        f"📦 Payload sent:\n{json.dumps(payload, ensure_ascii=False)}\n\n"
                        f"📄 Response body:\n{body.decode(errors='ignore')}"
                    )

                current_event = None
                async for raw_line in resp.aiter_lines():
                    line = raw_line.strip()
                    if not line:
                        continue

                    if line.startswith("event:"):
                        current_event = line.split(":", 1)[1].strip()
                        continue

                    if not line.startswith("data:"):
                        continue

                    raw = line.split(":", 1)[1].strip()
                    if raw in ("", "[DONE]"):
                        continue

                    try:
                        obj = json.loads(raw)
                    except Exception:
                        event_log.append((current_event or "unparsed", raw))
                        continue

                    evt_type = obj.get("type") or current_event
                    event_log.append((evt_type or "unknown", raw))

                    if evt_type == "tool_call_start" and tool_args is None:
                        tool_args = (obj.get("data") or {}).get("args")

                    if evt_type == "error" or (isinstance(obj, dict) and "error" in obj and evt_type is None):
                        err_msg = (
                            (obj.get("data") or {}).get("message")
                            or obj.get("message")
                            or json.dumps(obj)
                        )
                        return None, (
                            f"Provider returned an error event.\n\n"
                            f"📦 Payload sent:\n{json.dumps(payload, ensure_ascii=False)}\n\n"
                            f"⚠️ Error: {err_msg}\n\n"
                            f"📡 Full event log:\n" + _format_event_log(event_log)
                        )

                    # Primary path: documented shape
                    if evt_type == "image_generated":
                        img_url = _extract_image_url(obj) or _find_image_url(obj)
                        if img_url:
                            return img_url, None

                    # Fallback path: any event carrying an image URL, regardless of type name
                    img_url = _find_image_url(obj)
                    if img_url:
                        return img_url, None

                return None, (
                    "No image URL found in stream — full diagnostic dump below.\n\n"
                    f"📦 Payload sent:\n{json.dumps(payload, ensure_ascii=False)}\n\n"
                    f"🔧 tool_call_start args:\n"
                    f"{json.dumps(tool_args, ensure_ascii=False) if tool_args else 'never received'}\n\n"
                    f"📡 Full event log ({len(event_log)} events):\n" + _format_event_log(event_log)
                )
    except httpx.TimeoutException:
        return None, (
            f"⏱ Request timed out (180s).\n\n"
            f"📦 Payload sent:\n{json.dumps(payload, ensure_ascii=False)}\n\n"
            f"📡 Events received before timeout ({len(event_log)}):\n" + _format_event_log(event_log)
        )
    except Exception as e:
        return None, (
            f"Unexpected exception: {e!r}\n\n"
            f"📦 Payload sent:\n{json.dumps(payload, ensure_ascii=False)}\n\n"
            f"📡 Events received before exception ({len(event_log)}):\n" + _format_event_log(event_log)
        )


def _format_event_log(event_log) -> str:
    """Render the full (event_type, raw_json) list as readable lines, untruncated."""
    if not event_log:
        return "(no events received)"
    return "\n".join(f"[{i+1}] event={etype}\n    data={raw}" for i, (etype, raw) in enumerate(event_log))


async def generate_image(model: str, prompt: str, size: str):
    """Unified dispatcher. Returns ((kind, payload), error) where kind is
    'url' (FreeToChat) or 'bytes' (CallMissed)."""
    if model in FREETOCHAT_MODELS:
        result, error = await call_freetochat_api(model, prompt)
        return ("url", result), error
    result, error = await call_image_api(model, prompt, size)
    return ("bytes", result), error


# ─────────────────────────────  USER HANDLERS  ─────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_or_create_user(user.id, user.username or "")
    text = (
        f"✨ *Welcome to {BOT_NAME}!*\n\n"
        "AI se images generate karo — multiple top models, multiple sizes, ek tap mein.\n\n"
        "👉 *Generate Image* dabao aur shuru karo.\n\n"
        f"_Daily limit applies. VIP users get extended limits._"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_keyboard())


async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    s = get_stats()
    text = (
        f"🛠 *{BOT_NAME} — Admin Panel*\n\n"
        f"👥 Users: {s['total_users']} (VIP: {s['vip_users']}, Banned: {s['banned_users']})\n"
        f"🖼 Generations: {s['total_gens']} (Today: {s['today_gens']})\n"
        f"🔑 API Key: `{mask_key(get_active_api_key())}`"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=admin_menu_keyboard())


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = query.from_user

    # ── Generation flow ──
    if data == "gen:start":
        context.user_data.pop("awaiting_prompt", None)
        await query.edit_message_text(
            "🤖 *Step 1/3 — Choose a model:*", parse_mode=ParseMode.MARKDOWN, reply_markup=models_keyboard()
        )
        return

    if data == "gen:cancel":
        context.user_data.clear()
        await query.edit_message_text("❌ Cancelled.", reply_markup=main_menu_keyboard())
        return

    if data.startswith("model:"):
        model_id = data.split(":", 1)[1]
        context.user_data["model"] = model_id

        if model_id in FREETOCHAT_MODELS:
            # These models don't support custom size/negative_prompt upstream —
            # asking for a size just tempts the router to pass one along and
            # break the generation. Skip straight to the prompt step.
            context.user_data["size"] = None
            context.user_data["awaiting_prompt"] = True
            await query.edit_message_text(
                f"✅ Model: *{MODELS.get(model_id, model_id)}*\n\n"
                "✍️ *Step 2/2 — Send your prompt now* as a text message.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        await query.edit_message_text(
            f"✅ Model: *{MODELS.get(model_id, model_id)}*\n\n📐 *Step 2/3 — Choose a size:*",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=sizes_keyboard(),
        )
        return

    if data.startswith("size:"):
        size = data.split(":", 1)[1]
        context.user_data["size"] = size
        context.user_data["awaiting_prompt"] = True
        model_id = context.user_data.get("model")
        await query.edit_message_text(
            f"✅ Model: *{MODELS.get(model_id, model_id)}*\n✅ Size: *{size}*\n\n"
            "✍️ *Step 3/3 — Send your prompt now* as a text message.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # ── Admin flow ──
    if data.startswith("admin:") and is_owner(user.id):
        await handle_admin_callback(query, context, data)
        return


async def handle_admin_callback(query, context, data):
    parts = data.split(":")
    action = parts[1]

    if action == "menu":
        s = get_stats()
        text = (
            f"🛠 *{BOT_NAME} — Admin Panel*\n\n"
            f"👥 Users: {s['total_users']} (VIP: {s['vip_users']}, Banned: {s['banned_users']})\n"
            f"🖼 Generations: {s['total_gens']} (Today: {s['today_gens']})\n"
            f"🔑 API Key: `{mask_key(get_active_api_key())}`"
        )
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=admin_menu_keyboard())
        return

    if action == "stats":
        s = get_stats()
        text = (
            "📈 *Stats*\n\n"
            f"👥 Total users: {s['total_users']}\n"
            f"⭐ VIP users: {s['vip_users']}\n"
            f"🚫 Banned users: {s['banned_users']}\n"
            f"🖼 Total generations: {s['total_gens']}\n"
            f"📅 Today's generations: {s['today_gens']}"
        )
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=back_to_admin_menu())
        return

    if action == "history":
        offset = int(parts[2])
        rows, total = get_history(offset)
        if not rows:
            await query.edit_message_text("📭 No generations yet.", reply_markup=back_to_admin_menu())
            return
        lines = [f"📊 *History* ({offset + 1}-{offset + len(rows)} of {total})\n"]
        for r in rows:
            lines.append(
                f"🆔 `{r['id']}` | 👤 {r['username'] or r['user_id']} | {r['status']}\n"
                f"🤖 {r['model']} | 📐 {r['size']}\n"
                f"📝 _{(r['prompt'] or '')[:100]}_\n"
                f"🕐 {r['created_at'][:19]}\n"
            )
        await query.edit_message_text(
            "\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=history_nav_keyboard(offset, total)
        )
        return

    if action == "users":
        offset = int(parts[2])
        rows, total = get_users_page(offset)
        if not rows:
            await query.edit_message_text("📭 No users yet.", reply_markup=back_to_admin_menu())
            return
        lines = [f"👥 *Users* ({offset + 1}-{offset + len(rows)} of {total})\n"]
        for r in rows:
            flags = []
            if r["is_vip"]:
                flags.append("⭐VIP")
            if r["is_banned"]:
                flags.append("🚫BANNED")
            flag_str = " ".join(flags)
            lines.append(
                f"🆔 `{r['user_id']}` | {r['username'] or '-'} {flag_str}\n"
                f"   Usage: {r['used_today']}/{r['daily_limit']} today\n"
            )
        await query.edit_message_text(
            "\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=users_nav_keyboard(offset, total)
        )
        return

    if action == "ask":
        sub = parts[2]
        context.user_data["admin_action"] = sub
        prompts = {
            "setlimit": "⏱ Send: `user_id limit`\nExample: `123456789 10`",
            "vip": "✅ Send the `user_id` to grant VIP access.",
            "unvip": "❌ Send the `user_id` to revoke VIP access.",
            "ban": "🚫 Send the `user_id` to ban.",
            "unban": "♻️ Send the `user_id` to unban.",
            "apikey": (
                f"🔑 Current CallMissed key: `{mask_key(get_active_api_key())}`\n\n"
                "Send the new CallMissed API key (e.g. `cm_xxxxxxxx`) to replace it.\n"
                "This takes effect immediately, no restart needed."
            ),
            "ftc_token": (
                f"🔑 Current FreeToChat token: `{mask_key(get_active_freetochat_token())}`\n\n"
                "Send the new FreeToChat bearer token (JWT) to replace it.\n"
                "Used for *Flux 2 Pro* and *Nano Banana 2*. Takes effect immediately."
            ),
            "waitmsg": (
                f"⏳ Current waiting message:\n`{get_waiting_message()}`\n\n"
                "Send the new message. Available placeholders:\n"
                "`{model}` `{size}` `{prompt}` `{username}`\n\n"
                "Markdown (`*bold*`, `_italic_`) is supported."
            ),
        }
        await query.edit_message_text(
            prompts[sub], parse_mode=ParseMode.MARKDOWN, reply_markup=back_to_admin_menu()
        )
        return


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg_text = update.message.text or ""

    # ── Admin pending action ──
    if is_owner(user.id) and context.user_data.get("admin_action"):
        await process_admin_input(update, context, msg_text)
        return

    # ── Awaiting image prompt ──
    if context.user_data.get("awaiting_prompt"):
        await process_generation(update, context, msg_text)
        return

    await update.message.reply_text(
        "👋 /start se shuru karo, ya neeche button dabao.", reply_markup=main_menu_keyboard()
    )


async def process_admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    action = context.user_data.pop("admin_action")

    # Free-text actions that must NOT be split/int-parsed
    if action == "apikey":
        new_key = text.strip()
        if len(new_key) < 6:
            msg = "⚠️ That doesn't look like a valid key. Try again from the admin menu."
        else:
            set_setting("callmissed_api_key", new_key)
            msg = f"🔑 CallMissed key updated to `{mask_key(new_key)}`. Live with immediate effect."
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=admin_menu_keyboard())
        return

    if action == "ftc_token":
        new_token = text.strip()
        if len(new_token) < 10:
            msg = "⚠️ That doesn't look like a valid token. Try again from the admin menu."
        else:
            set_setting("freetochat_token", new_token)
            msg = f"🔑 FreeToChat token updated to `{mask_key(new_token)}`. Live with immediate effect."
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=admin_menu_keyboard())
        return

    if action == "waitmsg":
        new_template = text.strip()
        if len(new_template) < 2:
            msg = "⚠️ Message too short. Try again from the admin menu."
        else:
            set_setting("waiting_message", new_template)
            msg = "⏳ Waiting message updated. Live with immediate effect."
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=admin_menu_keyboard())
        return

    parts = text.strip().split()
    try:
        if action == "setlimit":
            uid, limit = int(parts[0]), int(parts[1])
            ok = set_limit(uid, limit)
            msg = f"✅ Daily limit for `{uid}` set to *{limit}*." if ok else f"⚠️ User `{uid}` not found."
        elif action == "vip":
            uid = int(parts[0])
            ok = set_vip(uid, True)
            msg = f"⭐ `{uid}` is now VIP." if ok else f"⚠️ User `{uid}` not found."
        elif action == "unvip":
            uid = int(parts[0])
            ok = set_vip(uid, False)
            msg = f"✅ VIP revoked for `{uid}`." if ok else f"⚠️ User `{uid}` not found."
        elif action == "ban":
            uid = int(parts[0])
            ok = set_ban(uid, True)
            msg = f"🚫 `{uid}` has been banned." if ok else f"⚠️ User `{uid}` not found."
        elif action == "unban":
            uid = int(parts[0])
            ok = set_ban(uid, False)
            msg = f"♻️ `{uid}` has been unbanned." if ok else f"⚠️ User `{uid}` not found."
        else:
            msg = "⚠️ Unknown action."
    except (ValueError, IndexError):
        msg = "⚠️ Invalid format. Try again from the admin menu."

    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=admin_menu_keyboard())


async def send_full_error_to_owner(bot, header_html: str, full_text: str):
    """Sends a complete, untruncated diagnostic dump to the owner.

    Short dumps go as one or more <pre> code-block messages (HTML-escaped,
    so JSON braces/quotes/newlines in the payload/event log can never break
    Telegram's parser). Very large dumps go as a .txt file attachment
    instead of spamming multiple chat messages.
    """
    try:
        await bot.send_message(OWNER_ID, header_html, parse_mode=ParseMode.HTML)
    except Exception:
        try:
            await bot.send_message(OWNER_ID, re.sub(r"<[^>]+>", "", header_html))
        except Exception:
            pass

    if len(full_text) > 12000:
        try:
            await bot.send_document(
                OWNER_ID,
                document=BytesIO(full_text.encode("utf-8")),
                filename="zadeximagine_error_dump.txt",
                caption="📄 Full diagnostic dump (too long for chat messages)",
            )
        except Exception:
            pass
        return

    escaped = html_lib.escape(full_text)
    chunk_size = 3500
    chunks = [escaped[i : i + chunk_size] for i in range(0, len(escaped), chunk_size)] or [""]
    for chunk in chunks:
        try:
            await bot.send_message(OWNER_ID, f"<pre>{chunk}</pre>", parse_mode=ParseMode.HTML)
        except Exception:
            try:
                await bot.send_message(OWNER_ID, chunk[:4000])
            except Exception:
                pass


async def process_generation(update: Update, context: ContextTypes.DEFAULT_TYPE, prompt: str):
    context.user_data["awaiting_prompt"] = False
    user = update.effective_user
    model = context.user_data.get("model")
    size = context.user_data.get("size")  # None is valid for FreeToChat-routed models

    if not model or (model not in FREETOCHAT_MODELS and not size):
        await update.message.reply_text("⚠️ Session expired. /start se phir try karo.")
        return

    size_display = size or "auto"

    row = get_or_create_user(user.id, user.username or "")

    if row["is_banned"]:
        await update.message.reply_text("🚫 Aap is bot se banned ho. Contact admin.", reply_markup=InlineKeyboardMarkup([dev_button_row()]))
        return

    if not row["is_vip"] and row["used_today"] >= row["daily_limit"]:
        await update.message.reply_text(
            f"⏳ Aapka daily limit ({row['daily_limit']}) khatam ho gaya. Kal phir try karo, ya VIP ke liye admin se contact karo.",
            reply_markup=InlineKeyboardMarkup([dev_button_row()]),
        )
        return

    if len(prompt.strip()) < 2:
        await update.message.reply_text("⚠️ Prompt bahut chhota hai. Phir se bhejo.")
        return

    status_text = render_template(
        get_waiting_message(),
        model=MODELS.get(model, model),
        size=size_display,
        prompt=prompt,
        username=display_name(user),
    )
    status_msg = await update.message.reply_text(status_text, parse_mode=ParseMode.MARKDOWN)

    (kind, result), error = await generate_image(model, prompt, size)

    uname = display_name(user)

    if error:
        # Short, clean summary for the user — first line only, no raw payload noise.
        user_summary = error.split("\n")[0][:300]
        try:
            await status_msg.edit_text(f"❌ Generation failed:\n{user_summary}")
        except Exception:
            pass

        log_generation(user.id, uname, prompt, model, size, None, "failed", error)

        header_html = (
            "❌ <b>Failed generation — full diagnostic</b>\n"
            f"👤 {html_lib.escape(uname)} (<code>{user.id}</code>)\n"
            f"🤖 {html_lib.escape(str(model))} | 📐 {html_lib.escape(str(size_display))}\n"
            f"📝 {html_lib.escape(prompt)}"
        )
        await send_full_error_to_owner(context.bot, header_html, error)
        return

    increment_usage(user.id)
    caption = f"🖼 *{BOT_NAME}*\n👤 {uname}\n🤖 {MODELS.get(model, model)}\n📐 {size_display}\n📝 {prompt}"

    photo_arg = result if kind == "url" else BytesIO(result)

    sent = await update.message.reply_photo(
        photo=photo_arg,
        caption=caption,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=after_generate_keyboard(),
    )
    await status_msg.delete()

    file_id = sent.photo[-1].file_id
    log_generation(user.id, uname, prompt, model, size, file_id, "success")

    # forward to admin
    if user.id != OWNER_ID:
        try:
            await context.bot.send_photo(
                OWNER_ID,
                photo=file_id,
                caption=f"📥 *New generation*\n👤 {uname} (`{user.id}`)\n🤖 {model}\n📐 {size_display}\n📝 {prompt}",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("👤 Open Chat", url=f"tg://user?id={user.id}", style="primary")]]
                ),
            )
        except Exception as e:
            log.warning(f"Failed to forward to admin: {e}")

    context.user_data.pop("model", None)
    context.user_data.pop("size", None)


# ─────────────────────────────  MAIN  ─────────────────────────────
def main():
    if "PUT_YOUR" in BOT_TOKEN or "PUT_YOUR" in CALLMISSED_API_KEY:
        print("⚠️  Edit BOT_TOKEN and CALLMISSED_API_KEY at the top of bot.py before running.")

    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    log.info(f"{BOT_NAME} starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
