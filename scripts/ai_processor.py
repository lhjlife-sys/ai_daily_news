from __future__ import annotations

import json
import os
from collections import Counter
from typing import Any

from openai import OpenAI
from pydantic import BaseModel, Field
from tenacity import retry, stop_after_attempt, wait_exponential


class TokenUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    reasoning_tokens: int = 0
    cached_input_tokens: int = 0
    api_mode: str = ""
    response_id: str | None = None
    reasoning_summary: list[str] = Field(default_factory=list)


class SelectionResult(BaseModel):
    items: list["SelectedItemResult"] = Field(default_factory=list)
    token_usage: TokenUsage = Field(default_factory=TokenUsage)


class SelectedItemResult(BaseModel):
    index: int
    score: int = Field(ge=0, le=100)
    reason: str


class ProcessedItem(BaseModel):
    sig: str
    title: str
    url: str
    source_name: str
    published_at: str | None = None
    summary: str = ""
    translated_title: str
    translated_summary: str
    key_points: list[str] = Field(default_factory=list)
    why_it_matters: str | None = None


class PreferenceDecision(BaseModel):
    is_match: bool
    score: int = Field(ge=0, le=100)
    reason: str
    topics: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class TranslatedSelectedItem(BaseModel):
    sig: str
    title: str
    translated_title: str
    translated_summary: str
    key_points: list[str] = Field(default_factory=list)
    why_it_matters: str | None = None
    url: str
    source_name: str
    published_at: str | None = None
    selection_reason: str
    selection_score: int = Field(ge=0, le=100)


class TranslationResult(BaseModel):
    items: list[ProcessedItem] = Field(default_factory=list)
    token_usage: TokenUsage = Field(default_factory=TokenUsage)
    expected_count: int = 0
    returned_count: int = 0
    fallback_count: int = 0
    missing_sigs: list[str] = Field(default_factory=list)


SELECTION_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "index": {"type": "integer"},
                    "score": {"type": "integer", "minimum": 0, "maximum": 100},
                    "reason": {"type": "string"},
                },
                "required": ["index", "score", "reason"],
            },
        },
    },
    "required": ["items"],
}

TRANSLATION_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "sig": {"type": "string"},
                    "title": {"type": "string"},
                    "url": {"type": "string"},
                    "source_name": {"type": "string"},
                    "published_at": {"type": ["string", "null"]},
                    "summary": {"type": "string"},
                    "translated_title": {"type": "string"},
                    "translated_summary": {"type": "string"},
                    "key_points": {"type": "array", "items": {"type": "string"}},
                    "why_it_matters": {"type": ["string", "null"]},
                },
                "required": [
                    "sig",
                    "title",
                    "url",
                    "source_name",
                    "published_at",
                    "summary",
                    "translated_title",
                    "translated_summary",
                    "key_points",
                    "why_it_matters",
                ],
            },
        },
    },
    "required": ["items"],
}


def _client() -> OpenAI:
    return OpenAI()


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _token_usage_from_response(resp: Any, *, api_mode: str) -> TokenUsage:
    usage = _get(resp, "usage")
    if usage is None:
        return TokenUsage(api_mode=api_mode, response_id=_get(resp, "id"))

    input_details = _get(usage, "input_tokens_details") or _get(usage, "prompt_tokens_details")
    output_details = _get(usage, "output_tokens_details") or _get(usage, "completion_tokens_details")
    input_tokens = int(
        _get(usage, "input_tokens")
        or _get(usage, "prompt_tokens")
        or 0
    )
    output_tokens = int(
        _get(usage, "output_tokens")
        or _get(usage, "completion_tokens")
        or 0
    )
    total_tokens = int(_get(usage, "total_tokens") or input_tokens + output_tokens)
    reasoning_tokens = int(_get(output_details, "reasoning_tokens", 0) or 0)
    cached_input_tokens = int(_get(input_details, "cached_tokens", 0) or 0)
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        reasoning_tokens=reasoning_tokens,
        cached_input_tokens=cached_input_tokens,
        api_mode=api_mode,
        response_id=_get(resp, "id"),
        reasoning_summary=_reasoning_summary_from_response(resp),
    )


def _reasoning_summary_from_response(resp: Any) -> list[str]:
    summaries: list[str] = []
    for output in _get(resp, "output", []) or []:
        if _get(output, "type") != "reasoning":
            continue
        for item in _get(output, "summary", []) or []:
            text = _get(item, "text") or _get(item, "content") or ""
            if text:
                summaries.append(str(text))
    return summaries


def _response_text(resp: Any) -> str:
    output_text = _get(resp, "output_text")
    if output_text:
        return str(output_text)

    chunks: list[str] = []
    for output in _get(resp, "output", []) or []:
        for content in _get(output, "content", []) or []:
            text = _get(content, "text")
            if text:
                chunks.append(str(text))
    return "".join(chunks)


def _parse_json_content(content: str) -> Any:
    if not content:
        return {}
    return json.loads(content)


def _responses_text_format(schema_name: str, schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "format": {
            "type": "json_schema",
            "name": schema_name,
            "schema": schema,
            "strict": True,
        }
    }


def _chat_response_format(schema_name: str, schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": schema_name,
            "schema": schema,
            "strict": True,
        },
    }


def _normalize_api_mode(api_mode: str | None) -> str:
    value = (api_mode or "responses").strip().lower()
    if value not in {"responses", "chat"}:
        raise ValueError(f"Invalid OPENAI_API_MODE: {api_mode!r}. Expected responses or chat.")
    return value


def _normalize_reasoning_summary(reasoning_summary: str | None) -> str | None:
    value = (reasoning_summary or "").strip().lower()
    if value in {"", "off", "none", "0", "false"}:
        return None
    if value not in {"auto", "concise", "detailed"}:
        raise ValueError(
            f"Invalid SELECTION_REASONING_SUMMARY: {reasoning_summary!r}. "
            "Expected off, auto, concise, or detailed."
        )
    return value


def _responses_json(
    model: str,
    system: str,
    user: str,
    *,
    schema_name: str,
    schema: dict[str, Any],
    reasoning_effort: str | None = None,
    reasoning_summary: str | None = None,
) -> tuple[Any, TokenUsage]:
    client = _client()
    if not hasattr(client, "responses"):
        return _chat_json(
            model=model,
            system=system,
            user=user,
            schema_name=schema_name,
            schema=schema,
            reasoning_effort=reasoning_effort,
        )

    reasoning: dict[str, Any] = {}
    if reasoning_effort:
        reasoning["effort"] = reasoning_effort
    summary = _normalize_reasoning_summary(reasoning_summary)
    if summary:
        reasoning["summary"] = summary

    request: dict[str, Any] = {
        "model": model,
        "instructions": system,
        "input": user,
        "text": _responses_text_format(schema_name, schema),
    }
    if reasoning:
        request["reasoning"] = reasoning

    resp = client.responses.create(**request)
    return _parse_json_content(_response_text(resp)), _token_usage_from_response(resp, api_mode="responses")


def _chat_json(
    model: str,
    system: str,
    user: str,
    *,
    schema_name: str,
    schema: dict[str, Any],
    reasoning_effort: str | None = None,
) -> tuple[Any, TokenUsage]:
    client = _client()
    request: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if _use_json_object_mode():
        # DeepSeek JSON Output: https://api-docs.deepseek.com/guides/json_mode
        request["response_format"] = {"type": "json_object"}
    else:
        request["response_format"] = _chat_response_format(schema_name, schema)
    if reasoning_effort:
        request["reasoning_effort"] = reasoning_effort
    else:
        request["temperature"] = 0.2
    resp = client.chat.completions.create(**request)
    content = resp.choices[0].message.content or "{}"
    return _parse_json_content(content), _token_usage_from_response(resp, api_mode="chat")


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
def _complete_json(
    model: str,
    system: str,
    user: str,
    *,
    schema_name: str,
    schema: dict[str, Any],
    api_mode: str = "responses",
    reasoning_effort: str | None = None,
    reasoning_summary: str | None = None,
) -> tuple[Any, TokenUsage]:
    if _normalize_api_mode(api_mode) == "chat":
        return _chat_json(
            model=model,
            system=system,
            user=user,
            schema_name=schema_name,
            schema=schema,
            reasoning_effort=reasoning_effort,
        )
    return _responses_json(
        model=model,
        system=system,
        user=user,
        schema_name=schema_name,
        schema=schema,
        reasoning_effort=reasoning_effort,
        reasoning_summary=reasoning_summary,
    )


def _render_prompt(template: str, vars_map: dict[str, str]) -> str:
    out = template
    for k, v in vars_map.items():
        out = out.replace("{{ " + k + " }}", v).replace("{{" + k + "}}", v)
    return out


def _combine_token_usage(usages: list[TokenUsage], *, api_mode: str) -> TokenUsage:
    response_ids = [u.response_id for u in usages if u.response_id]
    summaries: list[str] = []
    for usage in usages:
        summaries.extend(usage.reasoning_summary)
    return TokenUsage(
        input_tokens=sum(u.input_tokens for u in usages),
        output_tokens=sum(u.output_tokens for u in usages),
        total_tokens=sum(u.total_tokens for u in usages),
        reasoning_tokens=sum(u.reasoning_tokens for u in usages),
        cached_input_tokens=sum(u.cached_input_tokens for u in usages),
        api_mode=api_mode,
        response_id=", ".join(response_ids) if response_ids else None,
        reasoning_summary=summaries,
    )


def evaluate_item_preference(
    item: dict,
    *,
    model: str,
    system_prompt: str,
    user_prompt_template: str,
    user_preference: str,
    api_mode: str = "responses",
) -> PreferenceDecision:
    user = _render_prompt(
        user_prompt_template,
        {
            "user_preference": user_preference,
            "item_json": json.dumps(item, ensure_ascii=False),
        },
    )
    data, _usage = _complete_json(
        model=model,
        system=system_prompt,
        user=user,
        schema_name="preference_decision",
        schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "is_match": {"type": "boolean"},
                "score": {"type": "integer", "minimum": 0, "maximum": 100},
                "reason": {"type": "string"},
                "topics": {"type": "array", "items": {"type": "string"}},
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            },
            "required": ["is_match", "score", "reason", "topics", "confidence"],
        },
        api_mode=api_mode,
    )
    return PreferenceDecision.model_validate(data)


def translate_selected_item(
    item: dict,
    *,
    model: str,
    system_prompt: str,
    user_prompt_template: str,
    target_language: str,
    api_mode: str = "responses",
) -> TranslatedSelectedItem:
    user = _render_prompt(
        user_prompt_template,
        {
            "target_language": target_language,
            "item_json": json.dumps(item, ensure_ascii=False),
        },
    )
    data, _usage = _complete_json(
        model=model,
        system=system_prompt,
        user=user,
        schema_name="translated_selected_item",
        schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "sig": {"type": "string"},
                "title": {"type": "string"},
                "translated_title": {"type": "string"},
                "translated_summary": {"type": "string"},
                "key_points": {"type": "array", "items": {"type": "string"}},
                "why_it_matters": {"type": ["string", "null"]},
                "url": {"type": "string"},
                "source_name": {"type": "string"},
                "published_at": {"type": ["string", "null"]},
                "selection_reason": {"type": "string"},
                "selection_score": {"type": "integer", "minimum": 0, "maximum": 100},
            },
            "required": [
                "sig",
                "title",
                "translated_title",
                "translated_summary",
                "key_points",
                "why_it_matters",
                "url",
                "source_name",
                "published_at",
                "selection_reason",
                "selection_score",
            ],
        },
        api_mode=api_mode,
    )
    return TranslatedSelectedItem.model_validate(data)


def select_items(
    candidates: list[dict],
    *,
    model: str = "gpt-4.1-nano",
    max_selected: int = 10,
    topic_hint: str | None = None,
    api_mode: str = "responses",
    reasoning_effort: str | None = None,
    reasoning_summary: str | None = None,
) -> SelectionResult:
    source_counts = Counter(it.get("source_name", it.get("source_id", "")) for it in candidates)
    slim = []
    for i, it in enumerate(candidates):
        source_name = it.get("source_name", it.get("source_id", ""))
        slim.append(
            {
                "index": i,   # 原来是 "i": i
                "title": it.get("title", ""),
                "source": source_name,
                "source_count": source_counts.get(source_name, 0),
                "url": it.get("url", ""),
                "published_at": it.get("published_at"),
                "content": (it.get("content_text", "") or "")[:500],
            }
        )

    system = (
        "You are an editor selecting high-signal news for a daily digest. "
        "Return strict JSON only."
    )
    hint = f"Topic focus: {topic_hint}\n" if topic_hint else ""
    user = (
        f"{hint}"
        f"Select up to {max_selected} items from the list below.\n"
        "Hard constraints:\n"
        "- No more than 2 articles about the same event or topic cluster\n"
        "- Prefer diversity across topics (geopolitics, tech, finance, science, etc.)\n"
        "- Exclude entertainment, sports, celebrity, lifestyle content\n"
        "- Prefer source diversity; avoid selecting more than 2 items from the same source when alternatives exist\n"
        "Criteria: relevance, novelty, informational density.\n"
        "Return JSON with key: items (array). Each item must include:\n"
        "- index (int)\n"
        "- score (0-100)\n"
        "- reason (one sentence explaining why this item was selected)\n\n"
        f"Items: {json.dumps(slim, ensure_ascii=False)}"
    )

    data, token_usage = _complete_json(
        model=model,
        system=system,
        user=user,
        schema_name="selection_result",
        schema=SELECTION_RESPONSE_SCHEMA,
        api_mode=api_mode,
        reasoning_effort=reasoning_effort,
        reasoning_summary=reasoning_summary,
    )
    try:
        res = SelectionResult.model_validate(data)
    except Exception:
        res = SelectionResult(items=[])
    res.token_usage = token_usage

    # sanitize
    clean_items: list[SelectedItemResult] = []
    seen_indices: set[int] = set()
    for item in res.items:
        if item.index in seen_indices:
            continue
        if 0 <= item.index < len(candidates):
            clean_items.append(item)
            seen_indices.add(item.index)
    res.items = clean_items[:max_selected]
    return res


def summarize_and_translate(
    items: list[dict],
    *,
    model: str = "gpt-4.1-nano",
    target_language: str = "zh-CN",
    system_prompt: str | None = None,
    user_prompt_template: str | None = None,
    api_mode: str = "responses",
    batch_size: int = 3,
) -> TranslationResult:
    batch_size = max(1, batch_size)
    if system_prompt and user_prompt_template:
        system = system_prompt
    else:
        system = (
            "You are a professional news analyst and translator. "
            "Translate and structure items for a Chinese daily digest. "
            "Return strict JSON only."
        )

    def build_payload(batch_items: list[dict]) -> list[dict]:
        payload: list[dict] = []
        for it in batch_items:
            payload.append(
                {
                    "sig": str(it.get("sig") or ""),
                    "title": str(it.get("title") or ""),
                    "url": str(it.get("url") or ""),
                    "source_name": str(it.get("source_name") or ""),
                    "published_at": it.get("published_at"),
                    "content": (it.get("content_text", "") or "")[:2000],
                }
            )
        return payload

    def build_user(payload: list[dict]) -> str:
        if system_prompt and user_prompt_template:
            return _render_prompt(
                user_prompt_template,
                {
                    "target_language": target_language,
                    "items_json": json.dumps(payload, ensure_ascii=False),
                    # Compatibility key if older templates still use item_json.
                    "item_json": json.dumps(payload, ensure_ascii=False),
                },
            )
        user = (
            f"Process these news items for a digest in {target_language}.\n"
            "For each item, produce:\n"
            f"- translated_title: translate title to {target_language}.\n"
            f"- translated_summary: 5-8 factual sentences in {target_language} with key numbers/names.\n"
            f"- key_points: exactly 3 concise fact bullets in {target_language}, each under 60 characters.\n"
            f"- why_it_matters: one sentence in {target_language} on broader significance.\n"
            "Return JSON object with key items: an array, preserving input order. Each element must include:\n"
            "sig, title, url, source_name, published_at, summary, translated_title, translated_summary, key_points, why_it_matters.\n\n"
            f"Input items: {json.dumps(payload, ensure_ascii=False)}"
        )
        return user

    def translate_payload(payload: list[dict]) -> tuple[list[ProcessedItem], TokenUsage]:
        data, token_usage = _complete_json(
            model=model,
            system=system,
            user=build_user(payload),
            schema_name="translation_result",
            schema=TRANSLATION_RESPONSE_SCHEMA,
            api_mode=api_mode,
        )
        raw_items = data.get("items", [])
        out: list[ProcessedItem] = []
        if not isinstance(raw_items, list):
            return out, token_usage
        for r in raw_items:
            try:
                out.append(ProcessedItem.model_validate(r))
            except Exception:
                continue
        return out, token_usage

    def fallback_item(it: dict) -> ProcessedItem:
        content = (it.get("content_text", "") or it.get("title", "") or "")[:500]
        return ProcessedItem(
            sig=str(it.get("sig") or ""),
            title=str(it.get("title") or ""),
            url=str(it.get("url") or ""),
            source_name=str(it.get("source_name") or ""),
            published_at=it.get("published_at"),
            summary="",
            translated_title=str(it.get("title") or ""),
            translated_summary=str(content),
            key_points=[],
            why_it_matters=None,
        )

    translated_by_sig: dict[str, ProcessedItem] = {}
    usages: list[TokenUsage] = []
    returned_count = 0
    fallback_count = 0
    missing_sigs: list[str] = []

    for start in range(0, len(items), batch_size):
        batch = items[start : start + batch_size]
        payload = build_payload(batch)
        translated_batch, usage = translate_payload(payload)
        usages.append(usage)
        returned_count += len(translated_batch)
        for translated_item in translated_batch:
            if translated_item.sig and translated_item.sig not in translated_by_sig:
                translated_by_sig[translated_item.sig] = translated_item

        missing_payload = [p for p in payload if p["sig"] not in translated_by_sig]
        for missing in missing_payload:
            original = next((it for it in batch if str(it.get("sig") or "") == missing["sig"]), missing)
            retry_items, retry_usage = translate_payload([missing])
            usages.append(retry_usage)
            returned_count += len(retry_items)
            retry_item = next((it for it in retry_items if it.sig == missing["sig"]), None)
            if retry_item is not None:
                translated_by_sig[missing["sig"]] = retry_item
                continue
            missing_sigs.append(missing["sig"])
            translated_by_sig[missing["sig"]] = fallback_item(original)
            fallback_count += 1

    ordered_items = [
        translated_by_sig[str(it.get("sig") or "")]
        for it in items
        if str(it.get("sig") or "") in translated_by_sig
    ]
    return TranslationResult(
        items=ordered_items,
        token_usage=_combine_token_usage(usages, api_mode=api_mode),
        expected_count=len(items),
        returned_count=returned_count,
        fallback_count=fallback_count,
        missing_sigs=missing_sigs,
    )
