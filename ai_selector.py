from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import openai
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel


# Must remain below 10, as requested.
MAX_AI_CANDIDATES = 8


class AISelectorError(RuntimeError):
    """Raised when the AI candidate selector cannot complete."""


class _SelectionSchema(BaseModel):
    """
    Structured response returned by OpenAI.

    candidate_id:
        1 through the number of supplied candidates.
        0 means that none of the candidates appears to be a careers page.
    """

    candidate_id: int
    confidence: float
    reason: str


@dataclass(frozen=True)
class AISelection:
    """Validated selection returned to careers.py."""

    url: str | None
    text: str
    confidence: float
    reason: str


SYSTEM_INSTRUCTIONS = """
You select the hyperlink most likely to lead to a company's careers page,
jobs page, hiring portal, or official applicant-tracking-system job board.

Use the link text, URL, nearby page context, and rule score.

Important rules:
- Select only one of the candidate IDs supplied.
- Never invent or modify a URL.
- A direct Workday, Ashby, Greenhouse, Lever, or other hiring board is valid.
- Reject sales, customer support, partnership, investor, news, social-media,
  contact, and general business-opportunity links.
- Creative wording such as "Start here", "Begin your journey", or
  "Build with us" can represent careers when the nearby context discusses
  employment, joining a team, roles, talent, hiring, or working at the company.
- Return candidate_id 0 when none of the candidates reasonably represents
  careers or employment.
- Keep the reason brief.
""".strip()


def select_careers_candidate(
    company_website: str,
    page_title: str,
    candidates: list[dict[str, Any]],
    model: str | None = None,
) -> AISelection:
    """
    Ask OpenAI to choose the most likely careers link.

    At most MAX_AI_CANDIDATES candidates are sent.

    Each candidate should contain:
        url
        text
        context
        score
    """

    if not candidates:
        raise AISelectorError(
            "No link candidates were supplied to the AI selector."
        )

    limited_candidates = candidates[:MAX_AI_CANDIDATES]

    load_dotenv()

    api_key = os.getenv("OPENAI_API_KEY")

    if not api_key:
        raise AISelectorError(
            "OPENAI_API_KEY is missing from the .env file."
        )

    selected_model = (
        model
        or os.getenv("OPENAI_MODEL")
        or "gpt-5-mini"
    )

    numbered_candidates: list[dict[str, Any]] = []

    for candidate_id, candidate in enumerate(
        limited_candidates,
        start=1,
    ):
        numbered_candidates.append(
            {
                "candidate_id": candidate_id,
                "text": str(candidate.get("text", ""))[:150],
                "url": str(candidate.get("url", "")),
                "nearby_context": str(
                    candidate.get("context", "")
                )[:500],
                "rule_score": int(candidate.get("score", 0)),
            }
        )

    request_data = {
        "company_website": company_website,
        "homepage_title": page_title,
        "candidates": numbered_candidates,
    }

    try:
        with OpenAI(api_key=api_key) as client:
            response = client.responses.parse(
                model=selected_model,
                instructions=SYSTEM_INSTRUCTIONS,
                input=json.dumps(
                    request_data,
                    ensure_ascii=False,
                ),
                text_format=_SelectionSchema,
            )

    except openai.OpenAIError as exc:
        raise AISelectorError(
            f"OpenAI request failed: {exc}"
        ) from exc

    parsed = response.output_parsed

    if parsed is None:
        raise AISelectorError(
            "OpenAI did not return a structured selection."
        )

    if not 0.0 <= parsed.confidence <= 1.0:
        raise AISelectorError(
            "OpenAI returned an invalid confidence value."
        )

    reason = parsed.reason.strip()

    if not reason:
        reason = "No reason provided."

    if parsed.candidate_id == 0:
        return AISelection(
            url=None,
            text="",
            confidence=parsed.confidence,
            reason=reason,
        )

    if not 1 <= parsed.candidate_id <= len(
        limited_candidates
    ):
        raise AISelectorError(
            "OpenAI selected an invalid candidate ID."
        )

    chosen_candidate = limited_candidates[
        parsed.candidate_id - 1
    ]

    return AISelection(
        url=str(chosen_candidate["url"]),
        text=str(chosen_candidate.get("text", "")),
        confidence=parsed.confidence,
        reason=reason,
    )