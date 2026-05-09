from __future__ import annotations

import os
from typing import Any

import resend


def send_email_resend(*, from_email: str, to_email: str, subject: str, html: str) -> dict[str, Any]:
    api_key = os.getenv("RESEND_API_KEY")
    if not api_key:
        raise RuntimeError("Missing RESEND_API_KEY")

    resend.api_key = api_key
    resp = resend.Emails.send(
        {
            "from": from_email,
            "to": [to_email],
            "subject": subject,
            "html": html,
        }
    )
    return dict(resp or {})
