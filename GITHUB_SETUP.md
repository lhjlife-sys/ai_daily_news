# GitHub Actions Setup Checklist

Push completed. Configure secrets at:
https://github.com/lhjlife-sys/ai_daily_news/settings/secrets/actions

## Required Secrets

| Name | Value |
|------|-------|
| OPENAI_API_KEY | DeepSeek API key |
| SMTP_USER | QQ email address |
| SMTP_PASSWORD | QQ mail auth code (16 chars) |
| EMAIL_FROM | Same as SMTP_USER |
| EMAIL_TO | Recipient email |

## Optional Variables

https://github.com/lhjlife-sys/ai_daily_news/settings/variables/actions

Workflow defaults are already set in `.github/workflows/daily-news.yml`:
- DRY_RUN=0, COMMIT_RUNTIME_STATE=1
- OPENAI_API_MODE=chat, deepseek-v4-flash models
- cron: 00:00 UTC = 08:00 Beijing, 12:00 UTC = 20:00 Beijing (twice daily)

## Test

1. Actions -> Daily RSS AI Digest -> Run workflow
2. Expect log: `Selected N items`, `Email sent via smtp`
3. Next day ~08:00 and ~20:00 auto run (may delay 5-15 min)

## Local

Keep local `.env` with `DRY_RUN=1` to avoid duplicate emails.
