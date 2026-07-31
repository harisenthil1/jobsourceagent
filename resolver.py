from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from dotenv import load_dotenv


BRIGHT_DATA_API_URL = "https://api.brightdata.com/datasets/v3/scrape"

LINKEDIN_JOBS_DATASET_ID = "gd_lpfll7v5hcqtkxl6l"
LINKEDIN_COMPANIES_DATASET_ID = "gd_l1vikfnt1wgvvqz95w"


class ResolverError(RuntimeError):
    """Raised when the LinkedIn job or company cannot be resolved."""


def validate_linkedin_job_url(url: str) -> None:
    """Ensure the input resembles an individual LinkedIn job URL."""

    parsed = urlsplit(url)

    is_linkedin = (
        parsed.netloc == "linkedin.com"
        or parsed.netloc.endswith(".linkedin.com")
    )

    is_job_url = parsed.path.startswith("/jobs/")

    if not is_linkedin or not is_job_url:
        raise ResolverError(
            "Input must be a LinkedIn job URL, such as "
            "https://www.linkedin.com/jobs/view/1234567890/"
        )


def remove_url_tracking(url: str) -> str:
    """Remove query parameters and fragments from a URL."""

    parsed = urlsplit(url)

    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            "",
            "",
        )
    )


def get_api_key(api_key: str | None = None) -> str:
    """
    Return an explicitly supplied API key or load it from .env.

    This lets another Python file either:
    1. pass the key directly, or
    2. rely on LINKEDIN_API_KEY in .env.
    """

    if api_key:
        return api_key

    load_dotenv()

    environment_key = os.getenv("LINKEDIN_API_KEY")

    if not environment_key:
        raise ResolverError(
            "LINKEDIN_API_KEY is missing. Add it to your .env file "
            "or pass api_key directly."
        )

    return environment_key


def scrape_one(
    client: httpx.Client,
    api_key: str,
    dataset_id: str,
    target_url: str,
) -> dict[str, Any]:
    """Request one structured record from Bright Data."""

    try:
        response = client.post(
            BRIGHT_DATA_API_URL,
            params={
                "dataset_id": dataset_id,
                "format": "json",
                "include_errors": "true",
            },
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=[
                {
                    "url": target_url,
                }
            ],
        )
    except httpx.RequestError as exc:
        raise ResolverError(
            f"Could not connect to Bright Data: {exc}"
        ) from exc

    if response.is_error:
        raise ResolverError(
            f"Bright Data returned HTTP {response.status_code}:\n"
            f"{response.text[:1000]}"
        )

    try:
        data = response.json()
    except ValueError as exc:
        raise ResolverError(
            f"Bright Data returned invalid JSON:\n"
            f"{response.text[:1000]}"
        ) from exc

    if isinstance(data, dict) and "snapshot_id" in data:
        raise ResolverError(
            "Bright Data is still processing the request. "
            f"Snapshot ID: {data['snapshot_id']}"
        )

    if isinstance(data, list):
        if not data:
            raise ResolverError(
                "Bright Data returned an empty result list."
            )

        record = data[0]

    elif isinstance(data, dict):
        record = data

    else:
        raise ResolverError(
            f"Unexpected response type: {type(data).__name__}"
        )

    if not isinstance(record, dict):
        raise ResolverError(
            "The returned record is not a JSON object."
        )

    error_message = (
        record.get("error")
        or record.get("error_message")
        or record.get("message")
    )

    if error_message:
        raise ResolverError(
            f"Bright Data returned an error: {error_message}"
        )

    return record


def resolve_company_website(
    linkedin_job_url: str,
    api_key: str | None = None,
) -> str:
    """Return the official company website for a LinkedIn job posting."""

    validate_linkedin_job_url(linkedin_job_url)

    resolved_api_key = get_api_key(api_key)

    timeout = httpx.Timeout(
        timeout=75.0,
        connect=15.0,
    )

    with httpx.Client(
        timeout=timeout,
        follow_redirects=True,
    ) as client:
        job = scrape_one(
            client=client,
            api_key=resolved_api_key,
            dataset_id=LINKEDIN_JOBS_DATASET_ID,
            target_url=linkedin_job_url,
        )

        linkedin_company_url = job.get("company_url")

        if not linkedin_company_url:
            raise ResolverError(
                "LinkedIn job did not contain a company URL."
            )

        linkedin_company_url = remove_url_tracking(
            linkedin_company_url
        )

        company = scrape_one(
            client=client,
            api_key=resolved_api_key,
            dataset_id=LINKEDIN_COMPANIES_DATASET_ID,
            target_url=linkedin_company_url,
        )

    company_website = company.get("website")

    if not isinstance(company_website, str) or not company_website.strip():
        raise ResolverError(
            "Company website was not found."
        )

    return company_website.strip()