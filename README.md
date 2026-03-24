# LinkedIn Post Agent — Setup Guide

A fully automated LinkedIn posting agent that scans news, drafts posts with Claude,
and sends you an email approval link before publishing. You can also feed it links
via a Telegram bot.

---

## What you'll need (all free)

| Service | What for | Cost |
|---|---|---|
| [Render](https://render.com) | Hosting | Free tier |
| [Anthropic](https://console.anthropic.com) | Claude API | ~$1–3/month |
| Gmail | Sending approval emails | Free |
| [Telegram](https://telegram.org) | Submitting links | Free |
| [LinkedIn Developer](https://developer.linkedin.com) | Posting | Free |

---

## Step 1 — Get your API keys

### Anthropic (Claude)
1. Go to https://console.anthropic.com
2. Create an account and go to **API Keys**
3. Create a new key — save it as `ANTHROPIC_API_KEY`

### Gmail App Password
1. Go to your Google Account → Security → 2-Step Verification (must be enabled)
2. Search for **App Passwords** → create one for "Mail"
3. Save the 16-character password as `EMAIL_APP_PASSWORD`

### Telegram Bot
1. Open Telegram and message [@BotFather](https://t.me/botfather)
2. Send `/newbot` and follow the prompts
3. Save the token as `TELEGRAM_BOT_TOKEN`
4. To get your personal chat ID: message [@userinfobot](https://t.me/userinfobot)
5. Save the number it returns as `TELEGRAM_ALLOWED_CHAT_ID`

### LinkedIn API
This is the most involved step. LinkedIn requires OAuth:

1. Go to https://developer.linkedin.com → **Create App**
2. Fill in app name (e.g. "My Post Agent"), associate with your LinkedIn profile
3. Under **Products**, request access to **Share on LinkedIn** and **Sign In with LinkedIn**
4. Under **Auth**, add a redirect URL: `https://yourapp.onrender.com/linkedin/callback`
   (use a placeholder for now, update after Render deployment)
5. Note your **Client ID** and **Client Secret**

To get your access token (one-time):
```
https://www.linkedin.com/oauth/v2/authorization?response_type=code&client_id=YOUR_CLIENT_ID&redirect_uri=YOUR_REDIRECT_URI&scope=openid%20profile%20w_member_social
```
Open this in your browser, authorize, then exchange the code for a token:
```bash
curl -X POST https://www.linkedin.com/oauth/v2/accessToken \
  -d grant_type=authorization_code \
  -d code=YOUR_CODE \
  -d redirect_uri=YOUR_REDIRECT_URI \
  -d client_id=YOUR_CLIENT_ID \
  -d client_secret=YOUR_CLIENT_SECRET
```
Save the returned `access_token` as `LINKEDIN_ACCESS_TOKEN`.

To get your Person URN:
```bash
curl -H "Authorization: Bearer YOUR_ACCESS_TOKEN" https://api.linkedin.com/v2/userinfo
```
Your URN will look like `urn:li:person:XXXXXXX` — save as `LINKEDIN_PERSON_URN`.

> ⚠️ LinkedIn access tokens expire after 60 days. You'll need to refresh them.
> Free tier tokens last 60 days; paid LinkedIn partner tokens last longer.

---

## Step 2 — Deploy to Render

1. Push all files to a **GitHub repository** (can be private)
2. Go to https://render.com → **New** → **Blueprint**
3. Connect your GitHub repo
4. Render will detect `render.yaml` and create both services automatically
5. Go to each service → **Environment** → add all the environment variables:

```
ANTHROPIC_API_KEY        = sk-ant-...
EMAIL_SENDER             = you@gmail.com
EMAIL_APP_PASSWORD       = xxxx xxxx xxxx xxxx
EMAIL_RECIPIENT          = you@gmail.com
APPROVAL_BASE_URL        = https://linkedin-agent.onrender.com  (your Render URL)
LINKEDIN_ACCESS_TOKEN    = ...
LINKEDIN_PERSON_URN      = urn:li:person:...
TELEGRAM_BOT_TOKEN       = 123456:ABC-...
TELEGRAM_ALLOWED_CHAT_ID = 987654321
```

---

## Step 3 — Connect Telegram webhook

Once your Render app is live, register the webhook with Telegram:

```bash
curl "https://api.telegram.org/botYOUR_TOKEN/setWebhook?url=https://linkedin-agent.onrender.com/telegram"
```

You should get: `{"ok":true,"result":true}`

Test it by sending your bot a link. You should get a confirmation reply.

---

## Step 4 — Test a run

Trigger the agent manually from your Render dashboard:
- Go to the **linkedin-agent-cron** service → **Trigger Run**
- Check logs to see it fetching articles and sending an email
- Check your inbox for the approval email

---

## Posting schedule

The cron runs **Monday, Wednesday, Friday at 8am UTC** (10am CET).

To change it, edit `render.yaml`:
```yaml
schedule: "0 8 * * 1,3,5"   # Mon/Wed/Fri 8am UTC
schedule: "0 7 * * *"        # Every day 7am UTC
schedule: "0 8 * * 1"        # Every Monday 8am UTC
```

---

## How to use the Telegram bot

Just open your bot and paste any URL:
- News article you spotted
- Instagram post URL
- Twitter/X link
- Any webpage

The bot replies with a confirmation. On the next scheduled run, the agent will
prioritise your submitted links over RSS feed stories.

---

## File structure

```
linkedin_agent/
├── main.py          # Agent: fetch news → pick story → write post → send email
├── server.py        # Flask: approval endpoints + Telegram webhook
├── requirements.txt # Python dependencies
├── render.yaml      # Render deployment config
└── README.md        # This file
```

---

## Monthly cost estimate

| Item | Cost |
|---|---|
| Render web service (free tier) | $0 |
| Render cron job (free tier) | $0 |
| Render disk 1GB | $0.25 |
| Claude API (~12 runs/month) | ~$0.50–1.50 |
| Gmail, Telegram | $0 |
| **Total** | **~$1–2/month** |

---

## Troubleshooting

**No email received:** Check Gmail spam, verify `EMAIL_APP_PASSWORD` is correct (no spaces), confirm 2FA is enabled on your Google account.

**LinkedIn post not going through:** Token may have expired (60 day limit). Re-run the OAuth flow to get a fresh token.

**Telegram bot not responding:** Re-register the webhook (Step 3). On Render free tier, the server sleeps after inactivity — send a message and wait 30 seconds for cold start.

**Agent picks irrelevant stories:** Add more specific RSS feeds or adjust the keyword list in `main.py`.
