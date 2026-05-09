from __future__ import annotations

import json
from collections import Counter
from typing import Any

from openai import OpenAI
from pydantic import BaseModel, Field
from tenacity import retry, stop_after_attempt, wait_exponential


class SelectionResult(BaseModel):
    items: list["SelectedItemResult"] = Field(default_factory=list)


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


def _client() -> OpenAI:
    return OpenAI()


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
def _chat_json(model: str, system: str, user: str) -> Any:
    client = _client()
    resp = client.chat.completions.create(
        model=model,
        temperature=0.2,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        response_format={"type": "json_object"},
    )
    content = resp.choices[0].message.content or "{}"
    return json.loads(content)


def _render_prompt(template: str, vars_map: dict[str, str]) -> str:
    out = template
    for k, v in vars_map.items():
        out = out.replace("{{ " + k + " }}", v).replace("{{" + k + "}}", v)
    return out


def evaluate_item_preference(
    item: dict,
    *,
    model: str,
    system_prompt: str,
    user_prompt_template: str,
    user_preference: str,
) -> PreferenceDecision:
    user = _render_prompt(
        user_prompt_template,
        {
            "user_preference": user_preference,
            "item_json": json.dumps(item, ensure_ascii=False),
        },
    )
    data = _chat_json(model=model, system=system_prompt, user=user)
    return PreferenceDecision.model_validate(data)


def translate_selected_item(
    item: dict,
    *,
    model: str,
    system_prompt: str,
    user_prompt_template: str,
    target_language: str,
) -> TranslatedSelectedItem:
    user = _render_prompt(
        user_prompt_template,
        {
            "target_language": target_language,
            "item_json": json.dumps(item, ensure_ascii=False),
        },
    )
    data = _chat_json(model=model, system=system_prompt, user=user)
    return TranslatedSelectedItem.model_validate(data)


def select_items(
    candidates: list[dict],
    *,
    model: str = "gpt-4.1-nano",
    max_selected: int = 10,
    topic_hint: str | None = None,
) -> SelectionResult:
    source_counts = Counter(it.get("source_name", it.get("source_id", "")) for it in candidates)
    slim = []
    for i, it in enumerate(candidates):
        source_name = it.get("source_name", it.get("source_id", ""))
        slim.append(
            {
                "i": i,
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

    data = _chat_json(model=model, system=system, user=user)
    try:
        res = SelectionResult.model_validate(data)
    except Exception:
        res = SelectionResult(items=[])

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
) -> list[ProcessedItem]:
    # Batch to reduce API calls.
    payload = []
    for it in items:
        payload.append(
            {
                "sig": it.get("sig"),
                "title": it.get("title"),
                "url": it.get("url"),
                "source_name": it.get("source_name"),
                "published_at": it.get("published_at"),
                "content": (it.get("content_text", "") or "")[:2000],
            }
        )

    if system_prompt and user_prompt_template:
        system = system_prompt
        user = _render_prompt(
            user_prompt_template,
            {
                "target_language": target_language,
                "items_json": json.dumps(payload, ensure_ascii=False),
                # Compatibility key if older templates still use item_json.
                "item_json": json.dumps(payload, ensure_ascii=False),
            },
        )
    else:
        system = (
            "You are a professional news analyst and translator. "
            "Translate and structure items for a Chinese daily digest. "
            "Return strict JSON only."
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
    data = _chat_json(model=model, system=system, user=user)
    raw_items = data.get("items", [])
    out: list[ProcessedItem] = []
    if isinstance(raw_items, list):
        for r in raw_items:
            try:
                out.append(ProcessedItem.model_validate(r))
            except Exception:
                continue
    return out
