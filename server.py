"""
Flask server — handles:
  GET  /approve/<post_id>  → posts to LinkedIn, marks approved
  GET  /skip/<post_id>     → marks skipped
  POST /telegram           → Telegram webhook receives messages
"""

import os
import json
import sqlite3
import requests
from datetime import datetime
from flask import Flask, request, jsonify, abort

app = Flask(__name__)

DB_PATH              = os.environ.get("DB_PATH", "queue.db")
LINKEDIN_ACCESS_TOKEN = os.environ.get("LINKEDIN_ACCESS_TOKEN", "")
LINKEDIN_PERSON_URN  = os.environ.get("LINKEDIN_PERSON_URN", "")   # urn:li:person:XXXXXXX
TELEGRAM_BOT_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_ALLOWED_ID  = os.environ.get("TELEGRAM_ALLOWED_CHAT_ID", "")  # your personal chat ID

# ── DB helpers ─────────────────────────────────────────────────────────────────
def get_post(post_id):
    con = sqlite3.connect(DB_PATH)
    row = con.execute(
        "SELECT id, status, draft, source_url FROM posts WHERE id=?", (post_id,)
    ).fetchone()
    con.close()
    return row

def update_post_status(post_id, status):
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "UPDATE posts SET status=?, approved_at=? WHERE id=?",
        (status, datetime.utcnow().isoformat(), post_id)
    )
    con.commit()
    con.close()

def add_to_telegram_queue(url):
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "INSERT INTO telegram_queue (url, added_at) VALUES (?, ?)",
        (url, datetime.utcnow().isoformat())
    )
    con.commit()
    con.close()

def init_db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS posts (
            id TEXT PRIMARY KEY,
            status TEXT DEFAULT 'pending',
            draft TEXT,
            source_url TEXT,
            source_title TEXT,
            created_at TEXT,
            approved_at TEXT
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS telegram_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT,
            added_at TEXT,
            processed INTEGER DEFAULT 0
        )
    """)
    con.commit()
    con.close()

# ── LinkedIn posting ───────────────────────────────────────────────────────────
def post_to_linkedin(text: str) -> bool:
    if not LINKEDIN_ACCESS_TOKEN or not LINKEDIN_PERSON_URN:
        print("LinkedIn credentials not configured — skipping actual post.")
        return False

    payload = {
        "author": LINKEDIN_PERSON_URN,
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": text},
                "shareMediaCategory": "NONE"
            }
        },
        "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"}
    }

    resp = requests.post(
        "https://api.linkedin.com/v2/ugcPosts",
        headers={
            "Authorization": f"Bearer {LINKEDIN_ACCESS_TOKEN}",
            "Content-Type": "application/json",
            "X-Restli-Protocol-Version": "2.0.0",
        },
        json=payload,
        timeout=15,
    )

    if resp.status_code in (200, 201):
        print(f"Posted to LinkedIn: {resp.headers.get('X-RestLi-Id', '')}")
        return True
    else:
        print(f"LinkedIn error {resp.status_code}: {resp.text}")
        return False

# ── Telegram helper ────────────────────────────────────────────────────────────
def telegram_reply(chat_id, text):
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={"chat_id": chat_id, "text": text},
        timeout=10,
    )

# ── Routes ─────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return "LinkedIn Agent is running.", 200

@app.route("/approve/<post_id>")
def approve(post_id):
    row = get_post(post_id)
    if not row:
        abort(404)

    post_id, status, draft, source_url = row

    if status != "pending":
        return f"<h2>This post was already {status}.</h2>", 200

    success = post_to_linkedin(draft)
    new_status = "posted" if success else "approved_not_posted"
    update_post_status(post_id, new_status)

    if success:
        return """
        <html><body style="font-family:Arial;text-align:center;padding:60px">
        <h1 style="color:#0a66c2">✅ Posted to LinkedIn!</h1>
        <p>Your post is now live.</p>
        </body></html>
        """, 200
    else:
        return f"""
        <html><body style="font-family:Arial;text-align:center;padding:60px">
        <h1>⚠️ Approved but not posted</h1>
        <p>LinkedIn credentials may not be configured yet.</p>
        <p>Post text:</p>
        <pre style="text-align:left;background:#f4f4f4;padding:16px">{draft}</pre>
        </body></html>
        """, 200

@app.route("/skip/<post_id>")
def skip(post_id):
    row = get_post(post_id)
    if not row:
        abort(404)
    update_post_status(post_id, "skipped")
    return """
    <html><body style="font-family:Arial;text-align:center;padding:60px">
    <h1>⏭ Post skipped</h1>
    <p>No worries — the next one will be better.</p>
    </body></html>
    """, 200

@app.route("/telegram", methods=["POST"])
def telegram_webhook():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"ok": True})

    message = data.get("message", {})
    chat_id  = str(message.get("chat", {}).get("id", ""))
    text     = message.get("text", "").strip()

    # Security: only accept messages from your personal chat
    if TELEGRAM_ALLOWED_ID and chat_id != TELEGRAM_ALLOWED_ID:
        return jsonify({"ok": True})

    if not text:
        return jsonify({"ok": True})

    # Detect URLs
    if text.startswith("http://") or text.startswith("https://"):
        add_to_telegram_queue(text)
        telegram_reply(chat_id, f"✅ Added to queue:\n{text}\n\nI'll use it in the next post run.")
    elif text.lower() in ("/start", "/help"):
        telegram_reply(chat_id,
            "👋 LinkedIn Agent Bot\n\n"
            "Send me any URL (article, Instagram post, news story) "
            "and I'll add it to the queue for the next LinkedIn post.\n\n"
            "Just paste the link and hit send."
        )
    else:
        telegram_reply(chat_id, "Just send me a URL and I'll queue it. No other commands needed.")

    return jsonify({"ok": True})

# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
