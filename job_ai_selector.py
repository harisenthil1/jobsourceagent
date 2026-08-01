from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Literal

import openai
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field


MAX_AI_ACTIONS = 8


class JobAISelectorError(RuntimeError):
    """Raised when an AI job-navigation decision cannot be produced."""


class _ActionChoice(BaseModel):
    action_id: int = Field(
        description="Selected action ID, or 0 for none."
    )
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str


class _PageChoice(BaseModel):
    classification: Literal[
        "individual_job",
        "job_board",
        "application_only",
        "other",
    ]
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str


@dataclass(frozen=True)
class JobActionSelection:
    action_id: int | None
    confidence: float
    reason: str


@dataclass(frozen=True)
class JobPageClassification:
    classification: str
    confidence: float
    reason: str


ACTION_INSTRUCTIONS = """
You help a browser agent find one public, specific, currently open job
posting URL from a company's careers website.

Choose the action most likely to reveal a list of specific jobs or one
specific job-description page.

Rules:
- Prefer job-title links, View Job, Job Details, Current Openings,
  Search Jobs, View Opportunities, or a hiring-related Start Here.
- A general Apply Now button can be useful on a broad careers landing
  page if its context suggests it opens job listings.
- Avoid login, registration, final application submission, resume
  upload, contact, legal, privacy, cookie, newsletter, partnership,
  sales, and social links.
- Do not choose Apply when a job-title or View Job action is available.
- Select only a supplied action ID.
- Never invent or modify a URL.
- Return action_id 0 when none is useful.
- Keep the reason brief.
""".strip()


PAGE_INSTRUCTIONS = """
Classify the rendered page as:

- individual_job:
  One specific job is dominant and has a meaningful visible
  description.

- job_board:
  Several jobs have similar visual prominence.

- application_only:
  The page is mainly a form or login flow with no meaningful visible
  job description.

- other:
  None of the above.

Rules:
- A form below a full job description is still individual_job.
- A URL ending in /apply is only a hint. Judge visible content and
  layout.
- No meaningful description plus an application form means
  application_only.
- One dominant description plus small related-job links means
  individual_job.
- Several similarly prominent jobs or descriptions means job_board.
- Headings alone can mislead.
- Use description length, role prominence, repeated job structures,
  form evidence, and layout context.
- Keep the reason brief.
""".strip()


def _config(
    model: str | None,
) -> tuple[str, str]:
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

    return api_key, selected_model


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
    """Choose one of at most eight navigation actions."""

    if not actions:
        raise JobAISelectorError(
            "No navigation actions were supplied."
        )

    limited = actions[:MAX_AI_ACTIONS]

    numbered = [
        {
            "action_id": index,
            "text": str(
                action.get("text", "")
            )[:160],
            "url": str(
                action.get("url", "")
            )[:1000],
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
        for index, action in enumerate(
            limited,
            start=1,
        )
    ]

    payload = {
        "current_page": {
            "url": page_url,
            "title": page_title[:300],
            "main_heading": page_heading[:300],
            "summary": page_summary[:1200],
            "progress_score": progress_score,
        },
        "actions": numbered,
    }

    api_key, selected_model = _config(model)

    try:
        with OpenAI(api_key=api_key) as client:
            response = client.responses.parse(
                model=selected_model,
                instructions=ACTION_INSTRUCTIONS,
                input=json.dumps(
                    payload,
                    ensure_ascii=False,
                ),
                text_format=_ActionChoice,
            )

    except openai.OpenAIError as exc:
        raise JobAISelectorError(
            f"OpenAI request failed: {exc}"
        ) from exc

    parsed = response.output_parsed

    if parsed is None:
        raise JobAISelectorError(
            "OpenAI returned no structured action selection."
        )

    reason = (
        " ".join(parsed.reason.split()).strip()
        or "No reason provided."
    )

    if parsed.action_id == 0:
        return JobActionSelection(
            action_id=None,
            confidence=parsed.confidence,
            reason=reason,
        )

    if not 1 <= parsed.action_id <= len(limited):
        raise JobAISelectorError(
            "OpenAI selected an invalid action ID."
        )

    return JobActionSelection(
        action_id=parsed.action_id,
        confidence=parsed.confidence,
        reason=reason,
    )


def classify_job_page(
    *,
    page_url: str,
    page_title: str,
    page_heading: str,
    page_summary: str,
    layout_summary: dict[str, Any],
    model: str | None = None,
) -> JobPageClassification:
    """
    Classify a page only when deterministic layout evidence
    is ambiguous.
    """

    payload = {
        "page": {
            "url": page_url,
            "title": page_title[:300],
            "main_heading": page_heading[:300],
            "summary": page_summary[:1600],
        },
        "layout": layout_summary,
    }

    api_key, selected_model = _config(model)

    try:
        with OpenAI(api_key=api_key) as client:
            response = client.responses.parse(
                model=selected_model,
                instructions=PAGE_INSTRUCTIONS,
                input=json.dumps(
                    payload,
                    ensure_ascii=False,
                ),
                text_format=_PageChoice,
            )

    except openai.OpenAIError as exc:
        raise JobAISelectorError(
            f"OpenAI page classification failed: {exc}"
        ) from exc

    parsed = response.output_parsed

    if parsed is None:
        raise JobAISelectorError(
            "OpenAI returned no structured page classification."
        )

    reason = (
        " ".join(parsed.reason.split()).strip()
        or "No reason provided."
    )

    return JobPageClassification(
        classification=parsed.classification,
        confidence=parsed.confidence,
        reason=reason,
    )