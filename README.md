# RSS AI Digest Email Pipeline

[![Daily RSS AI Digest](https://github.com/wyivz/rss_AI_digest_email_pipeline/actions/workflows/daily-news.yml/badge.svg)](https://github.com/wyivz/rss_AI_digest_email_pipeline/actions/workflows/daily-news.yml)
![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)
![OpenAI](https://img.shields.io/badge/OpenAI-LLM-412991?logo=openai&logoColor=white)
![Resend](https://img.shields.io/badge/Email-Resend-000000)
![GitHub Actions](https://img.shields.io/badge/Automation-GitHub_Actions-2088FF?logo=githubactions&logoColor=white)
![License](https://img.shields.io/badge/License-GPL--3.0-blue)

**Language: English | [简体中文](README_zh-cn.md)**

An automated RSS-to-email digest pipeline that fetches curated RSS feeds, uses an LLM to select high-signal stories, translates and structures the selected items, renders a polished HTML digest, and sends it through Resend.

It can run unattended on GitHub Actions, with local dry-runs for prompt tuning, RSS debugging, and template iteration. This public repository keeps the checked-in workflow manual and dry-run by default.

This public edition is sanitized: real run logs, sent-item state, local outputs, and private automation history are intentionally excluded.

## Highlights

- **Batch LLM selection**: selects from a balanced candidate pool in one call instead of scoring every item one by one.
- **Cost-aware model split**: uses a stronger model for editorial selection and a cheaper model for translation.
- **Hard diversity controls**: caps selected items by source and topic cluster after the LLM step.
- **Pre-LLM exclusion filter**: removes obvious sports, entertainment, celebrity, lifestyle, film, music, and review content before selection.
- **Structured digest output**: generates translated title, factual summary, key points, and why-it-matters context.
- **Operational logs**: records prompts, fetch reports, selection metrics, output metrics, and selected items for auditability.
- **GitHub-native automation**: can commit sent state and run logs back to the repository after successful sends when explicitly enabled.

## Table Of Contents

- [How It Works](#how-it-works)
- [Current Defaults](#current-defaults)
- [Project Structure](#project-structure)
- [Local Setup](#local-setup)
- [Environment Variables](#environment-variables)
- [Run Locally](#run-locally)
- [Configuration](#configuration)
- [Selection And Diversity Controls](#selection-and-diversity-controls)
- [GitHub Actions](#github-actions)
- [Outputs And Logs](#outputs-and-logs)
- [Demo](#Demo)
- [Troubleshooting](#troubleshooting)
- [License](#license)

## How It Works

```text
RSS feeds
  -> fetch and normalize
  -> deduplicate against state and recent logs
  -> hard exclusion filter
  -> balanced source/topic candidate sampling
  -> LLM batch selection
  -> post-selection diversity caps
  -> LLM batch translation and structuring
  -> HTML rendering
  -> Resend email delivery
  -> optional state and run-log update
```

<img width="1472" height="1680" alt="optimized-rss-ai-email-pipeline" src="https://github.com/user-attachments/assets/a407f425-7550-4fb4-913d-4b5446a97321" />


### Pipeline steps:

1. Fetch RSS items from sources in `config/sources.yaml`.
2. Normalize and deduplicate items against `state/sent_items.json` and recent run logs.
3. Apply hard exclusion filters before calling the LLM.
4. Build a balanced candidate pool across sources and rough topic clusters.
5. Use an LLM selection step to choose the most relevant stories for the configured preference profile.
6. Enforce post-selection diversity caps for source and topic.
7. Use a separate LLM translation/summarization step to generate structured Chinese digest fields.
8. Render `out/daily_digest.html` with `templates/daily_digest.html.j2`.
9. Send the digest via Resend.
10. Write state, logs, and artifacts for observability and future deduplication.

## Current Defaults

| Setting | Default |
| --- | --- |
| OpenAI API mode | `responses` |
| Selection model | `gpt-4.1-mini` |
| Selection reasoning effort | unset |
| Translation model | `gpt-4.1-nano` |
| Target language | `zh-CN` |
| Max candidates | `50` |
| Max selected items | `10` |
| Minimum match score | `60` |
| Max items per source | `2` |
| Max items per topic cluster | `2` |
| Timezone | `Asia/Shanghai` |

## Project Structure

```text
config/
  prompts.yaml       LLM prompts for selection and translation
  settings.yaml      user preference profile
  sources.yaml       RSS source list

scripts/
  news_pipeline.py   main orchestration entrypoint
  rss_fetcher.py     RSS fetching and parsing
  ai_processor.py    OpenAI JSON calls and structured response models
  email_renderer.py  HTML rendering
  email_sender_resend.py
  state_store.py     sent-item deduplication state
  run_log_store.py   run logs and log-based deduplication

templates/
  daily_digest.html.j2

state/
  sent_items.json

logs/
  latest.json
  run-*.json

out/
  daily_digest.html
  processed_items.json
```

## Local Setup

Create a virtual environment and install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

On Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Environment Variables

Required for normal email delivery:

| Variable | Purpose |
| --- | --- |
| `OPENAI_API_KEY` | OpenAI API access |
| `RESEND_API_KEY` | Resend API access |
| `EMAIL_FROM` | Verified Resend sender |
| `EMAIL_TO` | Digest recipient |

Optional runtime controls:

| Variable | Default | Purpose |
| --- | --- | --- |
| `TARGET_LANGUAGE` | `zh-CN` | Output language |
| `TIMEZONE` | `Asia/Shanghai` | Rendered digest timezone |
| `MAX_CANDIDATES` | `50` | Max items sent to selection |
| `MAX_SELECTED` | `10` | Max final digest items |
| `MIN_MATCH_SCORE` | `60` | Minimum LLM selection score |
| `OPENAI_MODEL` | `gpt-4.1-nano` | Fallback model value |
| `OPENAI_API_MODE` | `responses` | OpenAI API surface: `responses` or `chat` |
| `SELECTION_MODEL` | `gpt-4.1-mini` | Editorial selection model |
| `SELECTION_REASONING_EFFORT` | empty | Optional selection reasoning effort for supported reasoning models: `none`, `minimal`, `low`, `medium`, `high`, or `xhigh` |
| `SELECTION_REASONING_SUMMARY` | empty | Optional Responses API reasoning summary for logs: `off`, `auto`, `concise`, or `detailed` |
| `TRANSLATION_MODEL` | `gpt-4.1-nano` | Translation and summary model |
| `TRANSLATION_BATCH_SIZE` | `3` | Items per translation call; lower values reduce missing translated items |
| `MAX_PER_SOURCE` | `2` | Source diversity cap |
| `MAX_PER_TOPIC_CLUSTER` | `2` | Topic diversity cap |
| `USER_PREFERENCE` | from `config/settings.yaml` | Override preference profile |
| `DRY_RUN` | empty | Set `DRY_RUN=1` to skip email and state update |
| `COMMIT_RUNTIME_STATE` | `0` in the public workflow | Set `COMMIT_RUNTIME_STATE=1` to let GitHub Actions commit generated state/logs |

## Run Locally

Dry-run:

```bash
DRY_RUN=1 python scripts/news_pipeline.py
```

Normal run:

```bash
python scripts/news_pipeline.py
```

Generated files:

- `out/daily_digest.html`
- `out/processed_items.json`
- `logs/latest.json`
- `logs/run-*.json`

Normal mode also updates:

- `state/sent_items.json`

## Configuration

### RSS Sources

Edit `config/sources.yaml` to add, remove, or rename RSS sources:

```yaml
- id: example
  name: "Example Feed"
  url: "https://example.com/rss.xml"
```

### User Preference

Edit `config/settings.yaml`:

```yaml
pipeline:
  user_preference: >
    MUST include: high-signal technology, science, business, and world news.
    PREFER: software engineering, AI research, hardware, security, finance, policy, and practical technical context.
    EXCLUDE: entertainment, sports, celebrity news, lifestyle, music/film reviews.
    When in doubt, prefer factual depth and broad reader relevance.
```

This text is passed into the selection step as the editorial preference profile.

### Prompts

Edit `config/prompts.yaml` to tune:

- `selection.system`
- `selection.user`
- `translation.system`
- `translation.user`

The translation prompt uses `{{ items_json }}` for batch input and asks the model to return:

- `translated_title`
- `translated_summary`
- `key_points`
- `why_it_matters`

## Selection And Diversity Controls

The pipeline uses both prompt-level guidance and Python-side hard constraints.

Before LLM selection:

- Excludes obvious off-preference items by keyword.
- Samples candidates across source/topic buckets to reduce source skew.

After LLM selection:

- Drops items below `MIN_MATCH_SCORE`.
- Enforces `MAX_PER_SOURCE`.
- Enforces `MAX_PER_TOPIC_CLUSTER`.
- Uses lightweight title-token overlap to reduce same-event duplicates.

Run logs include `selection_metrics` and `output_metrics`, including:

- excluded keyword count
- candidate source distribution
- candidate topic distribution
- selected source distribution
- selected topic distribution
- post-selection rejection counts
- summary sentence statistics

## GitHub Actions

Workflow file:

```text
.github/workflows/daily-news.yml
```

This public repository keeps the workflow manual by default (`workflow_dispatch`) and sets `DRY_RUN=1` unless repository variables override it. This prevents accidental publication of personal run state or logs.

Required GitHub Actions secrets:

- `OPENAI_API_KEY`
- `RESEND_API_KEY`
- `EMAIL_FROM`
- `EMAIL_TO`

Optional runtime controls can be set in GitHub repository **Settings -> Secrets and variables -> Actions -> Variables**. GitHub repository variables are not automatically exported to jobs; `.github/workflows/daily-news.yml` explicitly maps supported `vars.*` values into environment variables before running `scripts/news_pipeline.py`.

Supported repository variables include:

- `TARGET_LANGUAGE`, `TIMEZONE`, `MAX_CANDIDATES`, `MAX_SELECTED`
- `MIN_MATCH_SCORE`, `MAX_PER_SOURCE`, `MAX_PER_TOPIC_CLUSTER`
- `OPENAI_API_MODE`, `OPENAI_MODEL`
- `SELECTION_MODEL`, `OPENAI_SELECTION_MODEL`
- `SELECTION_REASONING_EFFORT`, `OPENAI_SELECTION_REASONING_EFFORT`
- `SELECTION_REASONING_SUMMARY`
- `TRANSLATION_MODEL`, `OPENAI_TRANSLATION_MODEL`, `TRANSLATION_BATCH_SIZE`
- `USER_PREFERENCE`, `EMAIL_SUBJECT`, `DRY_RUN`, `COMMIT_RUNTIME_STATE`

The workflow defaults are `MAX_CANDIDATES=80`, `MAX_SELECTED=15`, `OPENAI_API_MODE=responses`, `TARGET_LANGUAGE=zh-CN`, `TIMEZONE=Asia/Shanghai`, `DRY_RUN=1`, and `COMMIT_RUNTIME_STATE=0` when repository variables are not set.

The workflow checks out the repository, syncs latest `main`, installs dependencies, runs the pipeline, and uploads digest artifacts. It commits updated state/logs back to `main` only when `DRY_RUN` is not `1` and `COMMIT_RUNTIME_STATE=1`.

The state/log commit is rebased before pushing, which reduces failures when `main` changes while the job is running.




## Outputs And Logs

| Path | Purpose |
| --- | --- |
| `out/daily_digest.html` | Rendered email preview |
| `out/processed_items.json` | Structured digest items used for rendering |
| `logs/latest.json` | Most recent run log |
| `logs/run-*.json` | Historical run logs |
| `state/sent_items.json` | Sent signatures for deduplication |

`processed_items.json` is useful for recovery if a workflow run sends email but fails before committing state.

Run logs include LLM observability under `output_metrics.token_usage`, including the actual API mode, model names, input/output token counts, cached input tokens, reasoning tokens, response IDs, and optional selection reasoning summaries. `output_metrics.translation_integrity` records expected, returned, fallback, and missing translation counts. The email footer includes a compact selection/translation token summary.

## Demo
[digest_preview.html](https://github.com/user-attachments/files/27551916/digest_preview.html)


## Troubleshooting

### The workflow sends email but fails to push state

Check the uploaded artifact for `processed_items.json` and `daily_digest.html`. The workflow now rebases before pushing state/log updates, but manual recovery can still be done by adding sent item signatures to `state/sent_items.json`.

### Too many similar stories are selected

Tune `MAX_PER_SOURCE`, `MAX_PER_TOPIC_CLUSTER`, `MIN_MATCH_SCORE`, `pipeline.user_preference`, or `config/prompts.yaml`.

### The model ignores excluded categories

The pipeline has Python-side keyword filtering before LLM selection. If an unwanted class still appears, add the relevant keyword to the exclusion list in `scripts/news_pipeline.py` or strengthen `pipeline.user_preference`.

### The digest has too few items

Lower `MIN_MATCH_SCORE`, increase `MAX_CANDIDATES`, or relax `MAX_PER_SOURCE` / `MAX_PER_TOPIC_CLUSTER`.

## License

This project is licensed under the terms in `LICENSE`.
