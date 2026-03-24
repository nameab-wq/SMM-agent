"""
LinkedIn Post Agent - Main Runner
Fetches news, picks best story, drafts a post, sends for approval via email.
Run this on a schedule (cron / Railway cron job).
"""

import os
import json
import uuid
import sqlite3
import smtplib
import feedparser
from openai import OpenAI
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ── Config (set these as environment variables on Railway) ─────────────────────
OPENAI_API_KEY      = os.environ["OPENAI_API_KEY"]
EMAIL_SENDER        = os.environ["EMAIL_SENDER"]
EMAIL_PASSWORD      = os.environ["EMAIL_APP_PASSWORD"]
EMAIL_RECIPIENT     = os.environ["EMAIL_RECIPIENT"]
APPROVAL_BASE_URL   = os.environ["APPROVAL_BASE_URL"]
DB_PATH             = os.environ.get("DB_PATH", "queue.db")

# ── Keywords (grouped for smarter prompting) ───────────────────────────────────
KEYWORD_CATEGORIES = {
    "Geopolitics & conflict": ["geopolitics", "proxy war", "hybrid warfare", "disinformation campaign", "NATO", "ceasefire", "war crimes", "ICC"],
    "Economic sanctions & trade": ["economic sanctions", "sanctions evasion", "sanctions compliance", "OFAC", "EU sanctions", "de-dollarization", "SDN list", "export controls"],
    "Corruption & governance": ["corruption", "bribery", "embezzlement", "kleptocracy", "state capture", "whistleblower", "anti-corruption", "Transparency International"],
    "Democracy & rule of law": ["democratic backsliding", "authoritarianism", "press freedom", "judicial independence", "electoral integrity", "media censorship"],
    "Transparency & open data": ["beneficial ownership", "UBO register", "open data", "Panama Papers", "Pandora Papers", "FinCEN Files", "financial disclosure"],
    "Financial crime & AML": ["money laundering", "AML", "shell company", "tax haven", "crypto laundering", "FATF", "grey list", "suspicious transaction", "SAR"],
    "Tax evasion & avoidance": ["tax evasion", "transfer pricing", "profit shifting", "BEPS", "global minimum tax", "tax haven", "FATCA", "tax justice"],
    "Organized crime": ["organized crime", "drug trafficking", "human trafficking", "cybercrime", "ransomware", "cartel", "criminal asset recovery"],
    "OSINT & investigations": ["OSINT", "investigative journalism", "Bellingcat", "ICIJ", "OCCRP", "due diligence", "PEP", "politically exposed person", "adverse media"],
}

# RSS feeds relevant to this niche
RSS_FEEDS = [
    "https://feeds.occrp.org/organised-crime-and-corruption-reporting-project",
    "https://www.bellingcat.com/feed/",
    "https://www.transparency.org/en/rss",
    "https://www.fatf-gafi.org/en/publications/rss.xml",
    "https://news.google.com/rss/search?q=money+laundering+sanctions&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=anti-corruption+financial+crime&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=OSINT+investigative+journalism&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=beneficial+ownership+transparency&hl=en-US&gl=US&ceid=US:en",
]

# ── Database ───────────────────────────────────────────────────────────────────
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

def save_post(post_id, draft, source_url, source_title):
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "INSERT INTO posts (id, draft, source_url, source_title, created_at) VALUES (?,?,?,?,?)",
        (post_id, draft, source_url, source_title, datetime.utcnow().isoformat())
    )
    con.commit()
    con.close()

def get_telegram_queue():
    con = sqlite3.connect(DB_PATH)
    rows = con.execute("SELECT id, url FROM telegram_queue WHERE processed=0").fetchall()
    con.close()
    return rows

def mark_telegram_processed(row_id):
    con = sqlite3.connect(DB_PATH)
    con.execute("UPDATE telegram_queue SET processed=1 WHERE id=?", (row_id,))
    con.commit()
    con.close()

# ── News fetching ──────────────────────────────────────────────────────────────
def fetch_rss_articles(max_per_feed=5):
    articles = []
    for url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:max_per_feed]:
                articles.append({
                    "title": entry.get("title", ""),
                    "url": entry.get("link", ""),
                    "summary": entry.get("summary", "")[:300],
                    "published": entry.get("published", ""),
                })
        except Exception as e:
            print(f"Feed error ({url}): {e}")
    return articles

def fetch_telegram_articles():
    rows = get_telegram_queue()
    articles = []
    for row_id, url in rows:
        articles.append({
            "title": f"[User submitted] {url}",
            "url": url,
            "summary": "User-submitted link — prioritise this.",
            "published": datetime.utcnow().isoformat(),
            "_telegram_id": row_id,
        })
    return articles

# ── OpenAI: pick best story + write post ──────────────────────────────────────
def pick_and_write(articles: list[dict]) -> dict:
    client = OpenAI(api_key=OPENAI_API_KEY)

    article_list = "\n\n".join(
        f"{i+1}. TITLE: {a['title']}\n   URL: {a['url']}\n   SUMMARY: {a['summary']}"
        for i, a in enumerate(articles)
    )

    categories_text = "\n".join(f"- {k}" for k in KEYWORD_CATEGORIES.keys())

    prompt = f"""You are a LinkedIn content strategist for a company building compliance and investigation tools.

Your audience: compliance officers, investigators, journalists, policy makers, and financial crime professionals.

Your goal: pick ONE story from the list below and write a LinkedIn post that:
1. Explains why this matters — analytically but accessibly
2. Highlights the systemic or structural problem behind the story
3. Subtly seeds interest in the idea that better tools/data could help
4. Ends with a thought-provoking question to drive comments
5. Feels human — short paragraphs, no corporate jargon, no buzzwords like "leverage" or "synergy"
6. Is 150–220 words

Relevant topic categories:
{categories_text}

Articles to choose from:
{article_list}

Respond ONLY in valid JSON with this exact structure:
{{
  "chosen_index": <number, 1-based>,
  "chosen_title": "<title of chosen article>",
  "chosen_url": "<url of chosen article>",
  "reasoning": "<1-2 sentences on why you picked this>",
  "linkedin_post": "<the full post text>"
}}"""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
    )

    raw = response.choices[0].message.content.strip()
    return json.loads(raw)

# ── Email approval ─────────────────────────────────────────────────────────────
def send_approval_email(post_id: str, result: dict):
    approve_url = f"{APPROVAL_BASE_URL}/approve/{post_id}"
    skip_url    = f"{APPROVAL_BASE_URL}/skip/{post_id}"

    html = f"""
    <html><body style="font-family:Arial,sans-serif;max-width:600px;margin:auto;padding:20px">
    <h2 style="color:#0a66c2">LinkedIn Post Ready for Review</h2>
    <p><strong>Source:</strong> <a href="{result['chosen_url']}">{result['chosen_title']}</a></p>
    <p><strong>Why this story:</strong> {result['reasoning']}</p>
    <hr/>
    <h3>Draft Post</h3>
    <div style="background:#f4f4f4;padding:16px;border-radius:8px;white-space:pre-wrap">{result['linkedin_post']}</div>
    <br/>
    <a href="{approve_url}" style="background:#0a66c2;color:white;padding:12px 24px;text-decoration:none;border-radius:6px;margin-right:12px">✅ Approve & Post</a>
    <a href="{skip_url}" style="background:#888;color:white;padding:12px 24px;text-decoration:none;border-radius:6px">⏭ Skip</a>
    <br/><br/>
    <p style="color:#888;font-size:12px">Generated at {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}</p>
    </body></html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"LinkedIn Draft: {result['chosen_title'][:60]}"
    msg["From"]    = EMAIL_SENDER
    msg["To"]      = EMAIL_RECIPIENT
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(EMAIL_SENDER, EMAIL_PASSWORD)
        smtp.sendmail(EMAIL_SENDER, EMAIL_RECIPIENT, msg.as_string())

    print(f"Approval email sent for post {post_id}")

# ── Main ───────────────────────────────────────────────────────────────────────
def run():
    init_db()
    print(f"[{datetime.utcnow().isoformat()}] Agent starting...")

    telegram_articles = fetch_telegram_articles()
    rss_articles = fetch_rss_articles()
    all_articles = telegram_articles + rss_articles

    if not all_articles:
        print("No articles found. Exiting.")
        return

    print(f"Found {len(all_articles)} articles ({len(telegram_articles)} from Telegram queue)")

    result = pick_and_write(all_articles[:30])
    print(f"Chosen: {result['chosen_title']}")

    post_id = str(uuid.uuid4())
    save_post(post_id, result["linkedin_post"], result["chosen_url"], result["chosen_title"])

    for a in telegram_articles:
        if "_telegram_id" in a:
            mark_telegram_processed(a["_telegram_id"])

    send_approval_email(post_id, result)
    print("Done.")

if __name__ == "__main__":
    run()
