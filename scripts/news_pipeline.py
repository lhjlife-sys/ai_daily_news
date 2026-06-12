from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from dateutil import tz
import yaml
from dotenv import load_dotenv

# Allow `python scripts/news_pipeline.py` from repo root.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

load_dotenv(REPO_ROOT / ".env")

from scripts.ai_processor import TokenUsage, select_items, summarize_and_translate  # noqa: E402
from scripts.email_renderer import render_digest_html  # noqa: E402
from scripts.email_sender_resend import send_email_resend  # noqa: E402
from scripts.email_sender_smtp import send_email_smtp  # noqa: E402
from scripts.normalize import news_item_to_dict  # noqa: E402
from scripts.run_log_store import load_dedup_signatures_from_logs, trim_old_logs, write_run_log  # noqa: E402
from scripts.rss_fetcher import fetch_all, load_sources, sort_items_newest_first  # noqa: E402
from scripts.state_store import filter_new_items, load_state, mark_sent, save_state  # noqa: E402
from scripts.utils import getenv_int, getenv_str, utc_now_iso, write_json  # noqa: E402


def _log(msg: str) -> None:
    print(msg, flush=True)


def _load_prompts(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise RuntimeError(f"Invalid prompts config: {path}")
    return data


def _get_reasoning_effort(name: str, fallback_name: str | None = None) -> str | None:
    raw = os.getenv(name)
    if (raw is None or raw.strip() == "") and fallback_name:
        raw = os.getenv(fallback_name)
    if raw is None or raw.strip() == "":
        return None
    value = raw.strip().lower()
    allowed = {"none", "minimal", "low", "medium", "high", "xhigh"}
    if value not in allowed:
        raise RuntimeError(
            f"Invalid {name}: {raw!r}. "
            "Expected one of: none, minimal, low, medium, high, xhigh."
        )
    return value


def _get_api_mode(name: str) -> str:
    raw = getenv_str(name, "chat").strip().lower()
    if raw not in {"responses", "chat"}:
        raise RuntimeError(f"Invalid {name}: {raw!r}. Expected responses or chat.")
    return raw


def _send_digest_email(*, from_email: str, to_email: str, subject: str, html: str) -> dict:
    provider = getenv_str("EMAIL_PROVIDER", "smtp").strip().lower()
    if provider == "resend":
        return send_email_resend(from_email=from_email, to_email=to_email, subject=subject, html=html)
    return send_email_smtp(from_email=from_email, to_email=to_email, subject=subject, html=html)


def _get_reasoning_summary(name: str) -> str | None:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return None
    value = raw.strip().lower()
    if value in {"off", "none", "0", "false"}:
        return None
    if value not in {"auto", "concise", "detailed"}:
        raise RuntimeError(f"Invalid {name}: {raw!r}. Expected off, auto, concise, or detailed.")
    return value


EXCLUDE_KEYWORDS = (
    "sports",
    "sport",
    "nba",
    "nfl",
    "mlb",
    "nhl",
    "soccer",
    "football",
    "tennis",
    "cricket",
    "golf",
    "olympic",
    "entertainment",
    "celebrity",
    "celebrities",
    "lifestyle",
    "fashion",
    "music",
    "album",
    "concert",
    "film",
    "movie",
    "tv",
    "television",
    "review",
    "hollywood",
    "box office",
)

TOPIC_KEYWORDS = (
    ("semiconductors", ("chip", "chips", "semiconductor", "nvidia", "amd", "intel", "tsmc")),
    ("software", ("software", "developer", "programming", "cloud", "security", "cyber", "app")),
    ("finance", ("stock", "stocks", "market", "markets", "fed", "rates", "inflation", "earnings")),
    ("crypto", ("bitcoin", "crypto", "ethereum", "blockchain")),
    ("ai", ("ai", "artificial intelligence", "openai", "llm", "machine learning", "model")),
    ("geopolitics", ("china", "russia", "ukraine", "israel", "iran", "tariff", "trade", "war")),
    ("elections", ("election", "vote", "voters", "parliament", "congress", "president")),
    ("canada", ("canada", "canadian", "ottawa", "toronto", "vancouver")),
    ("science", ("science", "research", "study", "space", "nasa", "physics")),
    ("climate", ("climate", "weather", "emissions", "carbon", "energy")),
    ("health", ("health", "drug", "medicine", "disease", "vaccine")),
)

STOPWORDS = {
    "about",
    "after",
    "again",
    "against",
    "amid",
    "could",
    "from",
    "have",
    "into",
    "more",
    "over",
    "said",
    "says",
    "than",
    "that",
    "their",
    "this",
    "with",
    "will",
    "your",
}


def _item_search_text(item: dict) -> str:
    parts = [
        item.get("title", ""),
        item.get("source_name", ""),
        item.get("source_id", ""),
        item.get("category", ""),
        item.get("categories", ""),
        (item.get("content_text", "") or "")[:500],
    ]
    return " ".join(str(p) for p in parts if p).lower()


def _exclude_reason(item: dict) -> str | None:
    text = _item_search_text(item)
    for kw in EXCLUDE_KEYWORDS:
        pattern = r"(?<![a-z0-9])" + re.escape(kw.lower()) + r"(?![a-z0-9])"
        if re.search(pattern, text):
            return f"excluded_keyword:{kw}"
    return None


def _filter_excluded_items(items: list[dict]) -> tuple[list[dict], list[dict]]:
    kept: list[dict] = []
    excluded: list[dict] = []
    for item in items:
        reason = _exclude_reason(item)
        if reason:
            excluded.append({**item, "exclude_reason": reason})
        else:
            kept.append(item)
    return kept, excluded


def _title_tokens(item: dict) -> set[str]:
    text = f"{item.get('title', '')} {(item.get('content_text', '') or '')[:200]}".lower()
    return {t for t in re.findall(r"[a-z0-9]+", text) if len(t) > 3 and t not in STOPWORDS}


def _topic_cluster(item: dict) -> str:
    text = _item_search_text(item)
    for label, keywords in TOPIC_KEYWORDS:
        if any(kw in text for kw in keywords):
            return label
    tokens = sorted(_title_tokens(item))
    return "misc:" + "-".join(tokens[:3]) if tokens else "misc:unknown"


def _is_similar_topic(tokens: set[str], selected_token_sets: list[set[str]]) -> bool:
    if not tokens:
        return False
    for selected_tokens in selected_token_sets:
        if not selected_tokens:
            continue
        shared = len(tokens & selected_tokens)
        smaller = min(len(tokens), len(selected_tokens))
        if shared >= 3 or (smaller > 0 and shared / smaller >= 0.45):
            return True
    return False


def _balanced_candidate_sample(items: list[dict], limit: int) -> list[dict]:
    if limit <= 0:
        return []

    source_topic_groups: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    source_order: list[str] = []
    topic_order_by_source: dict[str, list[str]] = defaultdict(list)
    for item in items:
        source = str(item.get("source_name") or item.get("source_id") or "unknown")
        topic = _topic_cluster(item)
        if source not in source_topic_groups:
            source_order.append(source)
        if topic not in source_topic_groups[source]:
            topic_order_by_source[source].append(topic)
        source_topic_groups[source][topic].append(item)

    topic_pos_by_source = {source: 0 for source in source_order}
    sampled: list[dict] = []
    while len(sampled) < limit:
        progressed = False
        for source in source_order:
            topic_order = topic_order_by_source[source]
            if not topic_order:
                continue
            for _ in range(len(topic_order)):
                pos = topic_pos_by_source[source] % len(topic_order)
                topic = topic_order[pos]
                topic_pos_by_source[source] += 1
                bucket = source_topic_groups[source][topic]
                if bucket:
                    sampled.append(bucket.pop(0))
                    progressed = True
                    break
            if len(sampled) >= limit:
                break
        if not progressed:
            break
    return sampled


def _build_decision(*, is_match: bool, score: int, reason: str, confidence: float) -> dict:
    return {
        "is_match": is_match,
        "score": score,
        "reason": reason,
        "topics": [],
        "confidence": confidence,
    }


def _format_token_usage_summary(
    *,
    selection_model: str,
    selection_usage: TokenUsage,
    translation_model: str,
    translation_usage: TokenUsage,
) -> str:
    return (
        "本次运行 token 消耗："
        f"selection({selection_model}) input {selection_usage.input_tokens}, output {selection_usage.output_tokens}；"
        f"translation({translation_model}) input {translation_usage.input_tokens}, output {translation_usage.output_tokens}。"
    )


def _token_usage_log_payload(
    *,
    selection_model: str,
    selection_usage: TokenUsage,
    translation_model: str,
    translation_usage: TokenUsage,
) -> dict:
    return {
        "selection": {
            "model": selection_model,
            **selection_usage.model_dump(),
        },
        "translation": {
            "model": translation_model,
            **translation_usage.model_dump(),
        },
    }


def _translation_integrity_payload(result) -> dict:
    return {
        "expected_count": int(getattr(result, "expected_count", 0) or 0),
        "returned_count": int(getattr(result, "returned_count", 0) or 0),
        "fallback_count": int(getattr(result, "fallback_count", 0) or 0),
        "missing_sigs": list(getattr(result, "missing_sigs", []) or []),
    }


def _schedule_summary_from_github_event(*, timezone_name: str) -> str | None:
    if os.getenv("GITHUB_EVENT_NAME") != "schedule":
        return None
    event_path = os.getenv("GITHUB_EVENT_PATH")
    if not event_path:
        return None
    try:
        with open(event_path, "r", encoding="utf-8") as f:
            event = json.load(f)
    except Exception:
        return None

    cron = str(event.get("schedule") or "").strip()
    parts = cron.split()
    if len(parts) != 5:
        return None
    minute, hour, day_of_month, month, day_of_week = parts
    if not (minute.isdigit() and hour.isdigit()):
        return f"计划触发：{cron} UTC"
    if (day_of_month, month, day_of_week) != ("*", "*", "*"):
        return f"计划触发：{cron} UTC"

    local_tz = tz.gettz(timezone_name)
    if local_tz is None:
        return f"计划触发：{cron} UTC"
    try:
        import datetime as _dt

        utc_dt = _dt.datetime.now(tz=_dt.timezone.utc).replace(
            hour=int(hour),
            minute=int(minute),
            second=0,
            microsecond=0,
        )
        local_dt = utc_dt.astimezone(local_tz)
    except Exception:
        return f"计划触发：{cron} UTC"
    return f"计划触发：每日 {local_dt.strftime('%H:%M')} · {timezone_name}（cron: {cron} UTC）"


def _summary_sentence_count(text: str | None) -> int:
    if not text:
        return 0
    return len([s for s in re.split(r"[。！？.!?]+", text) if s.strip()])


def _sentence_stats(items: list[dict]) -> dict:
    counts = [_summary_sentence_count(it.get("translated_summary")) for it in items]
    if not counts:
        return {"min": 0, "max": 0, "avg": 0.0, "counts": []}
    return {
        "min": min(counts),
        "max": max(counts),
        "avg": round(sum(counts) / len(counts), 2),
        "counts": counts,
    }


def main() -> int:
    config_path = getenv_str("SOURCES_CONFIG", str(REPO_ROOT / "config" / "sources.yaml"))
    prompts_path = getenv_str("PROMPTS_CONFIG", str(REPO_ROOT / "config" / "prompts.yaml"))
    settings_path = getenv_str("SETTINGS_CONFIG", str(REPO_ROOT / "config" / "settings.yaml"))
    state_path = getenv_str("STATE_PATH", str(REPO_ROOT / "state" / "sent_items.json"))
    template_path = getenv_str("TEMPLATE_PATH", str(REPO_ROOT / "templates" / "daily_digest.html.j2"))
    logs_dir = getenv_str("LOGS_DIR", str(REPO_ROOT / "logs"))

    max_candidates = getenv_int("MAX_CANDIDATES", 50)
    max_selected = getenv_int("MAX_SELECTED", 10)
    target_language = getenv_str("TARGET_LANGUAGE", "zh-CN")
    timezone_name = getenv_str("TIMEZONE", "Asia/Shanghai")

    openai_model = getenv_str("OPENAI_MODEL", "gpt-4.1-nano")
    openai_api_mode = _get_api_mode("OPENAI_API_MODE")
    selection_model = getenv_str("SELECTION_MODEL", getenv_str("OPENAI_SELECTION_MODEL", "gpt-4.1-mini"))
    translation_model = getenv_str("TRANSLATION_MODEL", getenv_str("OPENAI_TRANSLATION_MODEL", openai_model))
    selection_reasoning_effort = _get_reasoning_effort(
        "SELECTION_REASONING_EFFORT",
        "OPENAI_SELECTION_REASONING_EFFORT",
    )
    selection_reasoning_summary = _get_reasoning_summary("SELECTION_REASONING_SUMMARY")
    min_match_score = getenv_int("MIN_MATCH_SCORE", 60)
    max_per_source = getenv_int("MAX_PER_SOURCE", 2)
    max_per_topic_cluster = getenv_int("MAX_PER_TOPIC_CLUSTER", 2)
    translation_batch_size = getenv_int("TRANSLATION_BATCH_SIZE", 3)

    dry_run = getenv_str("DRY_RUN", "").lower() in {"1", "true", "yes"}

    email_to = os.getenv("EMAIL_TO", "")
    email_from = os.getenv("EMAIL_FROM", "")

    sources = load_sources(config_path)
    prompts = _load_prompts(prompts_path)
    settings = _load_prompts(settings_path)
    pipeline_cfg = settings.get("pipeline", {})
    user_preference = getenv_str(
        "USER_PREFERENCE",
        str(
            pipeline_cfg.get(
                "user_preference",
                "Prefer AI tools, software engineering, ML research, product launches, and practical technical insights.",
            )
        ),
    )
    selection_cfg = prompts.get("selection", {})
    translation_cfg = prompts.get("translation", {})
    selection_system_prompt = str(selection_cfg.get("system", "")).strip()
    selection_user_template = str(selection_cfg.get("user", "")).strip()
    translation_system_prompt = str(translation_cfg.get("system", "")).strip()
    translation_user_template = str(translation_cfg.get("user", "")).strip()
    if not selection_system_prompt or not selection_user_template:
        raise RuntimeError("Invalid prompts config: selection prompts are required.")
    if not translation_system_prompt or not translation_user_template:
        raise RuntimeError("Invalid prompts config: translation prompts are required.")

    if not sources:
        raise RuntimeError(f"No sources configured in {config_path}")

    _log(f"Loaded {len(sources)} sources.")

    fetched_items, fetch_reports_models = fetch_all(sources)
    fetch_reports = [r.__dict__ for r in fetch_reports_models]
    items = sort_items_newest_first(fetched_items)
    normalized = [news_item_to_dict(i) for i in items]
    _log(f"Fetched {len(normalized)} total items.")
    if len(normalized) == 0:
        _log(f"[warn] no RSS items fetched. reports={fetch_reports}")

    state = load_state(state_path)
    new_items = filter_new_items(normalized, state)
    log_sigs = load_dedup_signatures_from_logs(logs_dir)
    new_items = [it for it in new_items if it.get("sig") not in log_sigs]
    _log(f"After dedup: {len(new_items)} new items.")

    eligible_items, excluded_items = _filter_excluded_items(new_items)
    candidates = _balanced_candidate_sample(eligible_items, max_candidates)
    _log(
        "Candidate filtering: "
        f"{len(excluded_items)} excluded by keyword, "
        f"{len(candidates)} balanced candidates from {len(eligible_items)} eligible items."
    )
    evaluation_logs: list[dict] = []
    selected: list[dict] = []
    translated: list[dict] = []
    selection_token_usage = TokenUsage(api_mode=openai_api_mode)
    translation_token_usage = TokenUsage(api_mode=openai_api_mode)
    translation_integrity: dict = {
        "expected_count": 0,
        "returned_count": 0,
        "fallback_count": 0,
        "missing_sigs": [],
    }
    selection_metrics: dict = {
        "excluded_keyword_count": len(excluded_items),
        "excluded_keyword_reasons": dict(Counter(it.get("exclude_reason", "unknown") for it in excluded_items)),
        "candidate_source_distribution": dict(Counter(str(it.get("source_name") or "unknown") for it in candidates)),
        "candidate_topic_distribution": dict(Counter(_topic_cluster(it) for it in candidates)),
        "post_selection_rejections": {},
    }

    if not candidates:
        processed = []
    else:
        selected_by_index: dict[int, dict] = {}
        selection_error: str | None = None
        try:
            selection_res = select_items(
                candidates,
                model=selection_model,
                max_selected=max_selected,
                topic_hint=user_preference,
                api_mode=openai_api_mode,
                reasoning_effort=selection_reasoning_effort,
                reasoning_summary=selection_reasoning_summary,
            )
            selection_token_usage = selection_res.token_usage
            for item in selection_res.items:
                selected_by_index[item.index] = {
                    "score": item.score,
                    "reason": item.reason,
                }
        except Exception as e:
            selection_error = str(e)
            _log(f"[warn] selection_failed: {selection_error}")

        decision_by_index: dict[int, dict] = {}
        for idx, it in enumerate(candidates):
            selected_meta = selected_by_index.get(idx)
            is_match = selected_meta is not None
            reason = (selected_meta or {}).get("reason", "not_selected_in_batch")
            score = int((selected_meta or {}).get("score", 0))
            if selection_error:
                reason = f"selection_failed: {selection_error}"
                score = 0
            decision_dict = _build_decision(
                is_match=is_match and selection_error is None,
                score=score if is_match and selection_error is None else 0,
                reason=reason,
                confidence=0.9 if is_match and selection_error is None else 0.0,
            )
            decision_by_index[idx] = decision_dict
            evaluation_logs.append(
                {
                    "sig": it.get("sig"),
                    "title": it.get("title"),
                    "url": it.get("url"),
                    "source_name": it.get("source_name"),
                    "published_at": it.get("published_at"),
                    "decision": decision_dict,
                }
            )

        rejection_counts: Counter[str] = Counter()
        source_counts: Counter[str] = Counter()
        topic_counts: Counter[str] = Counter()
        selected_token_sets: list[set[str]] = []
        selected_candidates = [
            (idx, candidates[idx], decision_by_index[idx])
            for idx in selected_by_index
            if idx in decision_by_index and int(decision_by_index[idx]["score"]) >= min_match_score
        ]
        selected_candidates.sort(key=lambda x: int(x[2]["score"]), reverse=True)
        for idx, it, decision_dict in selected_candidates:
            source = str(it.get("source_name") or it.get("source_id") or "unknown")
            topic = _topic_cluster(it)
            tokens = _title_tokens(it)
            rejection_reason = ""
            if len(selected) >= max_selected:
                rejection_reason = "post_selection_max_selected"
            elif source_counts[source] >= max_per_source:
                rejection_reason = "post_selection_source_cap"
            elif topic_counts[topic] >= max_per_topic_cluster or _is_similar_topic(tokens, selected_token_sets):
                rejection_reason = "post_selection_topic_cap"

            if rejection_reason:
                rejection_counts[rejection_reason] += 1
                decision_dict["is_match"] = False
                decision_dict["reason"] = f"{rejection_reason}: {decision_dict['reason']}"
                continue

            source_counts[source] += 1
            topic_counts[topic] += 1
            selected_token_sets.append(tokens)
            selected.append({**it, "selection": decision_dict})

        selection_metrics["post_selection_rejections"] = dict(rejection_counts)
        selection_metrics["selected_source_distribution"] = dict(Counter(str(it.get("source_name") or "unknown") for it in selected))
        selection_metrics["selected_topic_distribution"] = dict(Counter(_topic_cluster(it) for it in selected))

        _log(
            "Selected "
            f"{len(selected)} items after batch AI selection and hard caps "
            f"(source<= {max_per_source}, topic<= {max_per_topic_cluster})."
        )

        try:
            translation_res = summarize_and_translate(
                selected,
                model=translation_model,
                target_language=target_language,
                system_prompt=translation_system_prompt,
                user_prompt_template=translation_user_template,
                api_mode=openai_api_mode,
                batch_size=translation_batch_size,
            )
            batch_translated = translation_res.items
            translation_token_usage = translation_res.token_usage
            translation_integrity = _translation_integrity_payload(translation_res)
            translated = [
                {
                    "sig": it.sig,
                    "title": it.title,
                    "translated_title": it.translated_title,
                    "translated_summary": it.translated_summary,
                    "key_points": it.key_points,
                    "why_it_matters": it.why_it_matters,
                    "url": it.url,
                    "source_name": it.source_name,
                    "published_at": it.published_at,
                    "selection_reason": (selected[idx].get("selection", {}).get("reason", "") if idx < len(selected) else ""),
                    "selection_score": (selected[idx].get("selection", {}).get("score", 0) if idx < len(selected) else 0),
                }
                for idx, it in enumerate(batch_translated)
            ]
        except Exception as e:
            translation_integrity = {
                "expected_count": len(selected),
                "returned_count": 0,
                "fallback_count": len(selected),
                "missing_sigs": [str(it.get("sig") or "") for it in selected],
            }
            for it in selected:
                content = (it.get("content_text", "") or it.get("title", ""))[:500]
                translated.append(
                    {
                        "sig": it.get("sig"),
                        "title": it.get("title"),
                        "translated_title": it.get("title"),
                        "translated_summary": content,
                        "key_points": [],
                        "why_it_matters": None,
                        "url": it.get("url"),
                        "source_name": it.get("source_name"),
                        "published_at": it.get("published_at"),
                        "selection_reason": it.get("selection", {}).get("reason", ""),
                        "selection_score": it.get("selection", {}).get("score", 0),
                        "translation_error": str(e),
                    }
                )
        _log(f"Translated {len(translated)} selected items.")

        processed = [
            {
                "sig": it.get("sig"),
                "title": it.get("title"),
                "url": it.get("url"),
                "source_name": it.get("source_name"),
                "published_at": it.get("published_at"),
                "translated_title": it.get("translated_title"),
                "translated_summary": it.get("translated_summary"),
                "key_points": it.get("key_points", []),
                "why_it_matters": it.get("why_it_matters"),
            }
            for it in translated
        ]

    subject_date = utc_now_iso()[:10]
    subject = getenv_str("EMAIL_SUBJECT", f"Daily Digest · {subject_date}")
    schedule_summary = _schedule_summary_from_github_event(timezone_name=timezone_name)

    local_tz = tz.gettz(timezone_name)
    generated_at = utc_now_iso()
    if local_tz is not None:
        # Display in local tz, still store as ISO.
        try:
            import datetime as _dt

            generated_at = _dt.datetime.now(tz=local_tz).replace(microsecond=0).isoformat()
        except Exception:
            generated_at = utc_now_iso()

    html = render_digest_html(
        template_path=template_path,
        subject=subject,
        generated_at=generated_at,
        timezone=timezone_name,
        items=processed,
        token_usage_summary=_format_token_usage_summary(
            selection_model=selection_model,
            selection_usage=selection_token_usage,
            translation_model=translation_model,
            translation_usage=translation_token_usage,
        ),
        schedule_summary=schedule_summary,
    )

    output_metrics = {
        "selected_source_distribution": dict(Counter(str(it.get("source_name") or "unknown") for it in processed)),
        "selected_topic_distribution": dict(Counter(_topic_cluster(it) for it in selected)),
        "excluded_keyword_count": selection_metrics["excluded_keyword_count"],
        "summary_sentence_stats": _sentence_stats(processed),
        "translation_integrity": translation_integrity,
        "schedule_summary": schedule_summary,
        "token_usage": _token_usage_log_payload(
            selection_model=selection_model,
            selection_usage=selection_token_usage,
            translation_model=translation_model,
            translation_usage=translation_token_usage,
        ),
    }

    out_dir = REPO_ROOT / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    html_path = out_dir / "daily_digest.html"
    html_path.write_text(html, encoding="utf-8")
    write_json(out_dir / "processed_items.json", processed)
    _log(f"Rendered HTML: {html_path}")

    if dry_run:
        _log("DRY_RUN enabled: skipping email send and state update.")
        _log(f"Dry-run metrics: {output_metrics}")
        run_payload = {
            "run_at": utc_now_iso(),
            "mode": "dry_run",
            "counts": {
                "fetched": len(normalized),
                "deduped_candidates": len(candidates),
                "selected": len(selected),
                "translated": len(translated),
            },
            "config": {
                "sources_config": config_path,
                "prompts_config": prompts_path,
                "settings_config": settings_path,
                "model": openai_model,
                "openai_api_mode": openai_api_mode,
                "selection_model": selection_model,
                "selection_reasoning_effort": selection_reasoning_effort,
                "selection_reasoning_summary": selection_reasoning_summary,
                "translation_model": translation_model,
                "target_language": target_language,
                "user_preference": user_preference,
                "min_match_score": min_match_score,
                "max_candidates": max_candidates,
                "max_selected": max_selected,
                "max_per_source": max_per_source,
                "max_per_topic_cluster": max_per_topic_cluster,
                "translation_batch_size": translation_batch_size,
            },
            "selection_metrics": selection_metrics,
            "output_metrics": output_metrics,
            "prompts": {
                "selection_system": selection_system_prompt,
                "selection_user_template": selection_user_template,
                "translation_system": translation_system_prompt,
                "translation_user_template": translation_user_template,
            },
            "evaluation_results": evaluation_logs,
            "fetch_reports": fetch_reports,
            "selected_items": translated,
            "pushed_items": [],
        }
        run_log_path = write_run_log(logs_dir, run_payload)
        trim_old_logs(logs_dir, keep=120)
        _log(f"Wrote run log: {run_log_path}")
        return 0

    if not email_to or not email_from:
        raise RuntimeError("Missing EMAIL_TO or EMAIL_FROM (required unless DRY_RUN=1).")

    resp = _send_digest_email(from_email=email_from, to_email=email_to, subject=subject, html=html)
    _log(f"Email sent via {getenv_str('EMAIL_PROVIDER', 'smtp')}: {resp}")

    # Update state only on successful send.
    mark_sent(state, translated)
    save_state(state_path, state)
    _log(f"Updated state: {state_path}")

    run_payload = {
        "run_at": utc_now_iso(),
        "mode": "normal",
        "counts": {
            "fetched": len(normalized),
            "deduped_candidates": len(candidates),
            "selected": len(selected),
            "translated": len(translated),
            "pushed": len(translated),
        },
        "config": {
            "sources_config": config_path,
            "prompts_config": prompts_path,
            "settings_config": settings_path,
            "model": openai_model,
            "openai_api_mode": openai_api_mode,
            "selection_model": selection_model,
            "selection_reasoning_effort": selection_reasoning_effort,
            "selection_reasoning_summary": selection_reasoning_summary,
            "translation_model": translation_model,
            "target_language": target_language,
            "user_preference": user_preference,
            "min_match_score": min_match_score,
            "max_candidates": max_candidates,
            "max_selected": max_selected,
            "max_per_source": max_per_source,
            "max_per_topic_cluster": max_per_topic_cluster,
            "translation_batch_size": translation_batch_size,
        },
        "selection_metrics": selection_metrics,
        "output_metrics": output_metrics,
        "prompts": {
            "selection_system": selection_system_prompt,
            "selection_user_template": selection_user_template,
            "translation_system": translation_system_prompt,
            "translation_user_template": translation_user_template,
        },
        "evaluation_results": evaluation_logs,
        "fetch_reports": fetch_reports,
        "selected_items": translated,
        "pushed_items": translated,
        "email_response": resp,
    }
    run_log_path = write_run_log(logs_dir, run_payload)
    trim_old_logs(logs_dir, keep=120)
    _log(f"Wrote run log: {run_log_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
