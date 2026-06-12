# AI Daily Digest (Local)

Fetch AI news from RSS, summarize with DeepSeek, render HTML, send via QQ SMTP.

## Quick Start

```powershell
cd D:\ai_study\ai_daily
.\run.ps1
start out\daily_digest.html
```

## Configuration (.env)

- OPENAI_API_KEY - DeepSeek API key
- SELECTION_MODEL / TRANSLATION_MODEL - default deepseek-v4-flash
- DRY_RUN=1 - preview only, no email
- SMTP_* / EMAIL_* - QQ mail SMTP (auth code, not login password)

## Send Email

1. Enable QQ mail SMTP and get auth code
2. Fill SMTP_USER, SMTP_PASSWORD, EMAIL_FROM, EMAIL_TO in .env
3. Set DRY_RUN=0
4. Run .\run.ps1

## Schedule

```powershell
.\setup_task.ps1
```

Creates Windows task AI_Daily_Digest at 09:00 daily.
