from __future__ import annotations

from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape


def render_digest_html(
    *,
    template_path: str | Path,
    subject: str,
    generated_at: str,
    timezone: str,
    items: list[dict],
    token_usage_summary: str | None = None,
    schedule_summary: str | None = None,
) -> str:
    template_path = Path(template_path)
    env = Environment(
        loader=FileSystemLoader(str(template_path.parent)),
        autoescape=select_autoescape(enabled_extensions=("html", "xml", "j2")),
    )
    tmpl = env.get_template(template_path.name)
    return tmpl.render(
        subject=subject,
        generated_at=generated_at,
        timezone=timezone,
        items=items,
        token_usage_summary=token_usage_summary,
        schedule_summary=schedule_summary,
        now=datetime.utcnow(),
    )
