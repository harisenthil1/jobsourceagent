from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import openai
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field


# Always fewer than 10 candidates.
MAX_AI_ACTIONS = 8


class JobAISelectorError(RuntimeError):
    """Raised when AI cannot choose the next navigation action."""


class _ActionChoiceSchema(BaseModel):
    action_id: int = Field(
        description=(
            "The selected action ID. Use 0 when none of the actions "
            "is likely to lead toward a specific open job."
        )
    )
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str


@dataclass(frozen=True)
class JobActionSelection:
    """Validated AI decision returned to job_finder.py."""

    action_id: int | None
    confidence: float
    reason: str


SYSTEM_INSTRUCTIONS = """
You help a browser agent find one public, specific, currently open job
description URL from a company's careers website.

The current page might be:
- a careers landing page,
- a page that already lists jobs,
- a separate job board,
- an individual job-description page,
- a generic application form,
- a login page,
- or an irrelevant page.

Choose the action most likely to reveal either:
1. a list of specific open jobs, or
2. one specific job-description page.

Rules:
- Prefer actions such as View Job, Job Details, Current Openings,
  Search Jobs, View Opportunities, or a hiring-related Start Here.
- A general Apply Now button can be useful on a careers landing page
  when its context suggests that it opens the company's job board.
- Avoid Login, Register, Sign In, Submit Resume, Upload Resume,
  Contact, newsletter, partnership, reseller, sales, and social links.
- Do not choose a final application or account-creation action when
  a specific job-description page is already available.
- Use the surrounding context, destination URL, and rule score.
- Select only an action ID that was supplied.
- Never invent or alter a URL.
- Return action_id 0 when none of the supplied actions is useful.
- Keep the reason brief.
""".strip()


def select_next_job_action(
    *,
    page_url: str,
    page_title: str,
    page_heading: str,
    page_summary: str,
    progress_score: int,
    actions: list[dict[str, Any]],
    model: str | None = None,
) -> JobActionSelection:
    """Ask OpenAI to select one navigation action."""

    if not actions:
        raise JobAISelectorError(
            "No navigation actions were supplied to the AI selector."
        )

    limited_actions = actions[:MAX_AI_ACTIONS]

    load_dotenv()

    api_key = os.getenv("OPENAI_API_KEY")

    if not api_key:
        raise JobAISelectorError(
            "OPENAI_API_KEY is missing from the .env file."
        )

    selected_model = (
        model
        or os.getenv("OPENAI_MODEL")
        or "gpt-5-mini"
    )

    numbered_actions: list[dict[str, Any]] = []

    for action_id, action in enumerate(
        limited_actions,
        start=1,
    ):
        numbered_actions.append(
            {
                "action_id": action_id,
                "text": str(action.get("text", ""))[:160],
                "url": str(action.get("url", ""))[:1000],
                "nearby_context": str(
                    action.get("context", "")
                )[:600],
                "rule_score": int(
                    action.get("score", 0)
                ),
                "element_type": str(
                    action.get("element_type", "")
                )[:40],
            }
        )

    request_data = {
        "current_page": {
            "url": page_url,
            "title": page_title[:300],
            "main_heading": page_heading[:300],
            "summary": page_summary[:1200],
            "progress_score": progress_score,
        },
        "actions": numbered_actions,
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
                text_format=_ActionChoiceSchema,
            )

    except openai.OpenAIError as exc:
        raise JobAISelectorError(
            f"OpenAI request failed: {exc}"
        ) from exc

    parsed = response.output_parsed

    if parsed is None:
        raise JobAISelectorError(
            "OpenAI did not return a structured action selection."
        )

    reason = " ".join(parsed.reason.split()).strip()

    if not reason:
        reason = "No reason provided."

    if parsed.action_id == 0:
        return JobActionSelection(
            action_id=None,
            confidence=parsed.confidence,
            reason=reason,
        )

    if not 1 <= parsed.action_id <= len(limited_actions):
        raise JobAISelectorError(
            "OpenAI selected an invalid action ID."
        )

    return JobActionSelection(
        action_id=parsed.action_id,
        confidence=parsed.confidence,
        reason=reason,
    )