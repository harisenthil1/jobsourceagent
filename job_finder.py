from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

from playwright.sync_api import (
    BrowserContext,
    Error as PlaywrightError,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

from job_ai_selector import (
    JobAISelectorError,
    MAX_AI_ACTIONS,
    select_next_job_action,
)


ATS_DOMAINS = (
    "jobs.lever.co",
    "jobs.ashbyhq.com",
    "greenhouse.io",
    "myworkdayjobs.com",
    "smartrecruiters.com",
    "applicantpro.com",
    "icims.com",
    "jobvite.com",
    "workable.com",
    "recruitee.com",
)

IGNORED_DOMAINS = (
    "facebook.com",
    "instagram.com",
    "youtube.com",
    "twitter.com",
    "x.com",
    "tiktok.com",
    "linkedin.com",
)

BOARD_SIGNALS = (
    "current job listings",
    "current openings",
    "job openings",
    "search jobs",
    "view jobs",
    "number of jobs",
    "employment type",
)

DETAIL_SIGNALS = (
    "responsibilities",
    "qualifications",
    "requirements",
    "job description",
    "about the role",
    "duties",
    "compensation",
    "salary",
    "benefits",
)

GENERIC_HEADINGS = (
    "careers",
    "jobs",
    "join our team",
    "work with us",
    "current job listings",
    "current openings",
    "job openings",
)

ACTION_SCORES = {
    "view jobs": 150,
    "search jobs": 150,
    "current openings": 145,
    "current job listings": 145,
    "job listings": 140,
    "open positions": 135,
    "view openings": 135,
    "see openings": 130,
    "view opportunities": 120,
    "job opportunities": 120,
    "view job": 145,
    "job details": 145,
    "apply now": 75,
    "apply online": 65,
    "start here": 55,
    "explore opportunities": 45,
    "join us": 40,
    "learn more": 20,
}

NEGATIVE_ACTIONS = (
    "login",
    "log in",
    "register",
    "sign in",
    "sign up",
    "submit resume",
    "upload resume",
    "contact",
    "newsletter",
    "partner",
    "reseller",
    "sales",
)

MAX_DEPTH = 3
MAX_TOTAL_ATTEMPTS = 8
MAX_ACTIONS_PER_PAGE = 5
MAX_DIRECT_JOB_LINKS = 3


class JobFinderError(RuntimeError):
    """Raised when a specific open-job URL cannot be found."""


@dataclass(frozen=True)
class Candidate:
    index: int | None
    text: str
    url: str
    context: str
    score: int
    kind: str

    def key(self, source_url: str) -> str:
        target = self.url or f"index:{self.index}"

        return (
            f"{source_url}|{self.kind}|"
            f"{target}|{self.text[:60]}"
        )


@dataclass(frozen=True)
class Evidence:
    url: str
    title: str
    heading: str
    summary: str
    direct_jobs: list[Candidate]
    actions: list[Candidate]
    board_score: int
    progress_score: int
    final_job_url: str | None
    signature: str


@dataclass
class State:
    attempts: int = 0
    visited: set[str] = field(default_factory=set)
    attempted: set[str] = field(default_factory=set)

    def can_try(self) -> bool:
        return self.attempts < MAX_TOTAL_ATTEMPTS


def log(category: str, message: str) -> None:
    """Print one concise process message."""

    print(f"[{category}] {message}")


def clean_url(url: str) -> str:
    """Validate a URL and remove its fragment."""

    cleaned = url.strip()

    if not cleaned:
        raise JobFinderError(
            "No careers-page URL was provided."
        )

    if "://" not in cleaned:
        cleaned = f"https://{cleaned}"

    parsed = urlsplit(cleaned)

    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
    ):
        raise JobFinderError(
            "Invalid careers-page URL."
        )

    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path or "/",
            parsed.query,
            "",
        )
    )


def hostname(url: str) -> str:
    """Return a normalized hostname."""

    return (
        urlsplit(url)
        .netloc
        .lower()
        .removeprefix("www.")
    )


def matches_domain(
    url: str,
    domains: tuple[str, ...],
) -> bool:
    """Check whether a URL belongs to one of the supplied domains."""

    host = hostname(url)

    return any(
        host == domain or host.endswith(f".{domain}")
        for domain in domains
    )


def is_ats(url: str) -> bool:
    """Check whether a URL belongs to a known recruiting platform."""

    return matches_domain(url, ATS_DOMAINS)


def looks_like_job_url(url: str) -> bool:
    """Conservatively identify an individual job URL."""

    parsed = urlsplit(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower().rstrip("/")

    parts = [
        part
        for part in path.split("/")
        if part
    ]

    if host.endswith(
        (
            "jobs.lever.co",
            "jobs.ashbyhq.com",
        )
    ):
        return len(parts) >= 2

    if "greenhouse.io" in host:
        return "/jobs/" in f"{path}/"

    if "myworkdayjobs.com" in host:
        return "/job/" in f"{path}/"

    if "applicantpro.com" in host:
        return (
            "/jobs/" in f"{path}/"
            and parts[-1:] not in [
                ["jobs"],
                ["search"],
            ]
        )

    match = re.search(
        (
            r"/(?:job|jobs|position|positions|"
            r"opening|openings)/([^/?#]+)"
        ),
        path,
    )

    return bool(
        match
        and match.group(1)
        not in {
            "search",
            "list",
            "all",
            "openings",
        }
    )


def find_jobposting_urls(
    value: Any,
) -> list[str]:
    """Recursively find JobPosting URLs in JSON-LD."""

    results: list[str] = []

    if isinstance(value, dict):
        raw_type = value.get("@type")

        types = (
            raw_type
            if isinstance(raw_type, list)
            else [raw_type]
        )

        if any(
            str(item).lower() == "jobposting"
            for item in types
        ):
            raw_url = value.get("url")

            if isinstance(raw_url, str):
                results.append(raw_url)

        for nested in value.values():
            results.extend(
                find_jobposting_urls(nested)
            )

    elif isinstance(value, list):
        for item in value:
            results.extend(
                find_jobposting_urls(item)
            )

    return results


def jsonld_job_urls(page: Page) -> list[str]:
    """Extract job URLs from JobPosting JSON-LD."""

    try:
        scripts = page.locator(
            "script[type='application/ld+json']"
        ).all_text_contents()

    except PlaywrightError:
        return []

    urls: list[str] = []
    jobposting_without_url = False

    for script in scripts:
        try:
            data = json.loads(script)

        except (TypeError, ValueError):
            continue

        found_urls = find_jobposting_urls(data)

        if found_urls:
            for raw_url in found_urls:
                url = clean_url(
                    urljoin(page.url, raw_url)
                )

                if url not in urls:
                    urls.append(url)

        elif "jobposting" in script.lower():
            jobposting_without_url = True

    if not urls and jobposting_without_url:
        urls.append(clean_url(page.url))

    return urls


def collect_elements(
    page: Page,
) -> list[dict[str, Any]]:
    """Collect links, buttons and embedded-frame destinations."""

    selector = (
        "a[href], button, [role='button'], "
        "input[type='button'], input[type='submit']"
    )

    try:
        elements = page.locator(
            selector
        ).evaluate_all(
            """
            elements => elements.map((element, index) => {
                const box = element.closest(
                    "article, section, li, tr, "
                    + ".job, .job-card, "
                    + "[class*='job'], "
                    + "[class*='career'], div"
                );

                return {
                    index,

                    text: (
                        element.innerText ||
                        element.value ||
                        element.getAttribute('aria-label') ||
                        element.getAttribute('title') ||
                        ''
                    ).trim(),

                    href: element.href || '',

                    disabled: Boolean(element.disabled),

                    context: (
                        box?.innerText ||
                        element.parentElement?.innerText ||
                        ''
                    ).trim().slice(0, 1000)
                };
            })
            """
        )

    except PlaywrightError:
        elements = []

    try:
        frames = page.locator(
            "iframe[src]"
        ).evaluate_all(
            """
            frames => frames.map(frame => ({
                index: null,

                text:
                    frame.getAttribute('title')
                    || 'Embedded jobs',

                href: frame.src || '',

                disabled: false,

                context: (
                    frame.parentElement?.innerText
                    || 'Embedded jobs'
                ).trim().slice(0, 1000)
            }))
            """
        )

    except PlaywrightError:
        frames = []

    return [
        *elements,
        *frames,
    ]


def job_link_score(
    text: str,
    url: str,
    context: str,
) -> int:
    """Score whether a link represents one specific job."""

    normalized_text = " ".join(
        text.lower().split()
    )

    context_lower = context.lower()

    score = (
        150
        if looks_like_job_url(url)
        else 0
    )

    if any(
        term in normalized_text
        for term in (
            "view job",
            "job details",
            "view position",
        )
    ):
        score += 100

    if re.search(
        (
            r"/(?:job|jobs|position|positions|"
            r"opening|openings)/"
        ),
        url.lower(),
    ):
        score += 55

    role_signals = (
        "full time",
        "part time",
        "posted:",
        "location",
        "salary",
        "per hour",
    )

    score += min(
        45,
        15 * sum(
            term in context_lower
            for term in role_signals
        ),
    )

    if (
        normalized_text
        in {
            "careers",
            "jobs",
            "view jobs",
            "search jobs",
            "apply now",
        }
        and not looks_like_job_url(url)
    ):
        score -= 80

    return score


def action_score(
    text: str,
    url: str,
    context: str,
) -> int:
    """Score whether an interaction may reveal specific jobs."""

    normalized_text = " ".join(
        text.lower().split()
    )

    context_lower = context.lower()

    score = sum(
        points
        for phrase, points in ACTION_SCORES.items()
        if phrase in normalized_text
    )

    if is_ats(url):
        score += 90

    if any(
        term in url.lower()
        for term in (
            "/jobs",
            "/job/",
            "/careers",
            "/openings",
            "/positions",
        )
    ):
        score += 60

    if any(
        term in context_lower
        for term in (
            "career",
            "hiring",
            "join our team",
            "current openings",
            "job listings",
        )
    ):
        score += 30

    if any(
        term in normalized_text
        for term in NEGATIVE_ACTIONS
    ):
        score -= 140

    if any(
        term in context_lower
        for term in (
            "partner",
            "reseller",
            "sales",
        )
    ):
        score -= 60

    if normalized_text in {
        "submit",
        "upload",
        "browse",
    }:
        score -= 180

    return score


def candidates(
    page: Page,
) -> tuple[
    list[Candidate],
    list[Candidate],
]:
    """Build direct-job candidates and general action candidates."""

    direct: dict[str, Candidate] = {}
    actions: dict[str, Candidate] = {}

    current_url = clean_url(page.url)

    for item in collect_elements(page):
        if item.get("disabled"):
            continue

        text = str(
            item.get("text", "")
        ).strip()

        context = str(
            item.get("context", "")
        ).strip()

        raw_href = str(
            item.get("href", "")
        ).strip()

        index = item.get("index")

        url = ""

        if raw_href:
            joined_url = urljoin(
                page.url,
                raw_href,
            )

            if joined_url.startswith(
                (
                    "http://",
                    "https://",
                )
            ):
                url = clean_url(joined_url)

                if matches_domain(
                    url,
                    IGNORED_DOMAINS,
                ):
                    continue

        if url and url != current_url:
            score = job_link_score(
                text=text,
                url=url,
                context=context,
            )

            if score >= 60:
                candidate = Candidate(
                    index=None,
                    text=text,
                    url=url,
                    context=context,
                    score=score,
                    kind="job",
                )

                previous = direct.get(url)

                if (
                    previous is None
                    or score > previous.score
                ):
                    direct[url] = candidate

        if text or url:
            candidate = Candidate(
                index=(
                    int(index)
                    if isinstance(index, int)
                    else None
                ),
                text=text,
                url=url,
                context=context,
                score=action_score(
                    text=text,
                    url=url,
                    context=context,
                ),
                kind="action",
            )

            key = (
                url
                or f"{index}:{text[:60]}"
            )

            previous = actions.get(key)

            if (
                previous is None
                or candidate.score > previous.score
            ):
                actions[key] = candidate

    direct_candidates = sorted(
        direct.values(),
        key=lambda item: item.score,
        reverse=True,
    )

    action_candidates = sorted(
        actions.values(),
        key=lambda item: item.score,
        reverse=True,
    )

    return (
        direct_candidates,
        action_candidates,
    )


def is_generic_heading(
    heading: str,
) -> bool:
    """Check whether a heading describes a general careers page."""

    normalized = " ".join(
        heading.lower().split()
    )

    return (
        not normalized
        or any(
            term in normalized
            for term in GENERIC_HEADINGS
        )
    )


def inspect(page: Page) -> Evidence:
    """Inspect a page and calculate progress toward one job URL."""

    page.wait_for_timeout(600)

    try:
        title = page.title().strip()

    except PlaywrightError:
        title = ""

    try:
        heading = page.locator(
            "h1"
        ).first.inner_text(
            timeout=2_000
        ).strip()

    except PlaywrightError:
        heading = ""

    try:
        body = " ".join(
            page.locator("body")
            .inner_text(timeout=5_000)
            .split()
        )

    except PlaywrightError:
        body = ""

    body_lower = body.lower()

    structured_urls = jsonld_job_urls(page)

    direct_jobs, actions = candidates(page)

    view_job_count = sum(
        "view job" in action.text.lower()
        for action in actions
    )

    apply_count = sum(
        "apply" in action.text.lower()
        for action in actions
    )

    board_score = 30 * sum(
        signal in body_lower
        for signal in BOARD_SIGNALS
    )

    board_score += min(
        80,
        max(
            0,
            view_job_count - 1,
        ) * 25,
    )

    board_score += min(
        80,
        max(
            0,
            len(direct_jobs) - 1,
        ) * 20,
    )

    if len(structured_urls) > 1:
        board_score += 120

    if is_ats(page.url):
        board_score += 20

    specific_score = 0

    if len(structured_urls) == 1:
        specific_score += 200

    if looks_like_job_url(page.url):
        specific_score += 120

    if not is_generic_heading(heading):
        specific_score += 35

    specific_score += min(
        90,
        18 * sum(
            signal in body_lower
            for signal in DETAIL_SIGNALS
        ),
    )

    if apply_count:
        specific_score += 30

    if view_job_count >= 2:
        specific_score -= 100

    if len(structured_urls) > 1:
        specific_score -= 120

    if board_score >= 100:
        specific_score -= 50

    final_url: str | None = None

    if len(structured_urls) == 1:
        final_url = structured_urls[0]

    elif (
        looks_like_job_url(page.url)
        and specific_score >= 120
        and len(direct_jobs) <= 2
    ):
        final_url = clean_url(page.url)

    elif (
        specific_score >= 150
        and board_score < 90
        and not is_generic_heading(heading)
    ):
        final_url = clean_url(page.url)

    progress_score = (
        max(0, board_score)
        + max(0, specific_score)
        + min(
            75,
            len(direct_jobs) * 15,
        )
    )

    summary = body[:1600]

    fingerprint = (
        f"{clean_url(page.url)}|"
        f"{title}|"
        f"{heading}|"
        f"{summary[:700]}|"
        f"{len(direct_jobs)}"
    )

    signature = hashlib.sha256(
        fingerprint.encode(
            "utf-8",
            errors="ignore",
        )
    ).hexdigest()

    return Evidence(
        url=clean_url(page.url),
        title=title,
        heading=heading,
        summary=summary,
        direct_jobs=direct_jobs,
        actions=actions,
        board_score=board_score,
        progress_score=progress_score,
        final_job_url=final_url,
        signature=signature,
    )


def progressed(
    before: Evidence,
    after: Evidence,
) -> bool:
    """Check whether navigation produced better job evidence."""

    return bool(
        after.final_job_url
        or after.progress_score
        >= before.progress_score + 15
        or after.board_score
        > before.board_score
        or len(after.direct_jobs)
        > len(before.direct_jobs)
        or (
            after.url != before.url
            and is_ats(after.url)
            and not is_ats(before.url)
        )
    )


def open_page(
    context: BrowserContext,
    url: str,
    root: bool = False,
) -> Page | None:
    """Open one URL in an isolated browser page."""

    page = context.new_page()

    try:
        response = page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=30_000,
        )

        if (
            response is not None
            and response.status >= 400
        ):
            if root:
                raise JobFinderError(
                    f"Careers page returned HTTP "
                    f"{response.status}."
                )

            page.close()
            return None

        page.wait_for_timeout(700)

        return page

    except (
        PlaywrightTimeoutError,
        PlaywrightError,
    ) as exc:
        page.close()

        if root:
            raise JobFinderError(
                f"Could not open careers page: {exc}"
            ) from exc

        return None


def follow(
    context: BrowserContext,
    source_url: str,
    action: Candidate,
) -> Page | None:
    """Follow an action using its URL or browser click."""

    if action.url:
        return open_page(
            context,
            action.url,
        )

    if action.index is None:
        return None

    page = open_page(
        context,
        source_url,
    )

    if page is None:
        return None

    selector = (
        "a[href], button, [role='button'], "
        "input[type='button'], input[type='submit']"
    )

    pages_before = list(context.pages)

    try:
        locator = page.locator(
            selector
        ).nth(action.index)

        locator.scroll_into_view_if_needed(
            timeout=5_000
        )

        locator.click(
            timeout=10_000
        )

        page.wait_for_timeout(1_000)

        new_pages = [
            candidate
            for candidate in context.pages
            if candidate not in pages_before
        ]

        if new_pages:
            destination = new_pages[-1]

            try:
                destination.wait_for_load_state(
                    "domcontentloaded",
                    timeout=15_000,
                )

            except PlaywrightError:
                pass

            page.close()

            return destination

        return page

    except (
        PlaywrightTimeoutError,
        PlaywrightError,
    ):
        page.close()

        return None


def short_label(
    candidate: Candidate,
) -> str:
    """Create a concise console label."""

    text = " ".join(
        candidate.text.split()
    )

    if text:
        return text[:60]

    parsed = urlsplit(candidate.url)

    return (
        f"{parsed.netloc}{parsed.path}"
    )[:60]


def ordered_actions(
    evidence: Evidence,
    state: State,
) -> list[Candidate]:
    """Order actions using rules or AI when ambiguous."""

    direct_urls = {
        candidate.url
        for candidate in evidence.direct_jobs
    }

    choices = [
        action
        for action in evidence.actions
        if (
            action.score > -120
            and action.url not in direct_urls
            and action.key(evidence.url)
            not in state.attempted
        )
    ][:MAX_AI_ACTIONS]

    if not choices:
        return []

    positive = [
        action
        for action in choices
        if action.score > 0
    ]

    if positive:
        second_score = (
            positive[1].score
            if len(positive) > 1
            else -999
        )

        if (
            positive[0].score >= 130
            and positive[0].score
            - second_score >= 35
        ):
            best = positive[0]

            log(
                "rules",
                (
                    f'Trying strong action '
                    f'"{short_label(best)}".'
                ),
            )

            remaining = [
                item
                for item in choices
                if item != best
            ]

            return [
                best,
                *remaining,
            ][:MAX_ACTIONS_PER_PAGE]

    payload = [
        {
            "text": action.text,
            "url": action.url,
            "context": action.context,
            "score": action.score,
            "element_type": "clickable",
        }
        for action in choices
    ]

    ordered: list[Candidate] = []

    try:
        selection = select_next_job_action(
            page_url=evidence.url,
            page_title=evidence.title,
            page_heading=evidence.heading,
            page_summary=evidence.summary,
            progress_score=evidence.progress_score,
            actions=payload,
        )

        if selection.action_id is not None:
            chosen = choices[
                selection.action_id - 1
            ]

            reason = " ".join(
                selection.reason.split()
            )[:120]

            log(
                "ai",
                (
                    f'Chose "{short_label(chosen)}" '
                    f"({selection.confidence:.0%}): "
                    f"{reason}"
                ),
            )

            ordered.append(chosen)

        else:
            log(
                "ai",
                "No useful action selected.",
            )

    except JobAISelectorError as exc:
        log(
            "ai",
            f"Skipped: {exc}",
        )

    ordered.extend(
        item
        for item in choices
        if item not in ordered
    )

    return ordered[:MAX_ACTIONS_PER_PAGE]


def explore(
    context: BrowserContext,
    page: Page,
    evidence: Evidence,
    depth: int,
    state: State,
) -> str | None:
    """Explore a page using bounded navigation and backtracking."""

    if evidence.signature in state.visited:
        return None

    state.visited.add(evidence.signature)

    log(
        "inspect",
        (
            f"Depth {depth}: "
            f"progress {evidence.progress_score}, "
            f"{len(evidence.direct_jobs)} job links."
        ),
    )

    if evidence.final_job_url:
        log(
            "found",
            "Specific job page verified.",
        )

        return evidence.final_job_url

    if (
        depth >= MAX_DEPTH
        or not state.can_try()
    ):
        return None

    for candidate in evidence.direct_jobs[
        :MAX_DIRECT_JOB_LINKS
    ]:
        if not state.can_try():
            break

        key = candidate.key(evidence.url)

        if key in state.attempted:
            continue

        state.attempted.add(key)
        state.attempts += 1

        log(
            "try",
            (
                f'Opening job candidate '
                f'"{short_label(candidate)}".'
            ),
        )

        branch = open_page(
            context,
            candidate.url,
        )

        if branch is None:
            continue

        try:
            branch_evidence = inspect(branch)

            if not progressed(
                evidence,
                branch_evidence,
            ):
                log(
                    "backtrack",
                    (
                        "Job link did not improve "
                        "job evidence."
                    ),
                )

                continue

            result = explore(
                context=context,
                page=branch,
                evidence=branch_evidence,
                depth=depth + 1,
                state=state,
            )

            if result:
                return result

        finally:
            if not branch.is_closed():
                branch.close()

    for action in ordered_actions(
        evidence,
        state,
    ):
        if not state.can_try():
            break

        key = action.key(evidence.url)

        if key in state.attempted:
            continue

        state.attempted.add(key)
        state.attempts += 1

        log(
            "act",
            (
                f'Following '
                f'"{short_label(action)}".'
            ),
        )

        branch = follow(
            context=context,
            source_url=evidence.url,
            action=action,
        )

        if branch is None:
            log(
                "backtrack",
                "Action could not be completed.",
            )

            continue

        try:
            branch_evidence = inspect(branch)

            if not progressed(
                evidence,
                branch_evidence,
            ):
                log(
                    "backtrack",
                    (
                        "Action did not improve "
                        "job evidence."
                    ),
                )

                continue

            log(
                "progress",
                (
                    f"Score "
                    f"{evidence.progress_score} -> "
                    f"{branch_evidence.progress_score}."
                ),
            )

            result = explore(
                context=context,
                page=branch,
                evidence=branch_evidence,
                depth=depth + 1,
                state=state,
            )

            if result:
                return result

        finally:
            if not branch.is_closed():
                branch.close()

    return None


def search_for_job(
    careers_url: str,
    headless: bool,
) -> str:
    """Search for one job using one browser mode."""

    mode = (
        "headless"
        if headless
        else "visible"
    )

    log(
        "browser",
        f"Searching for a job in {mode} mode.",
    )

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=headless,
                channel="chromium",
            )

            context = browser.new_context(
                viewport={
                    "width": 1440,
                    "height": 1000,
                }
            )

            root: Page | None = None

            try:
                root = open_page(
                    context,
                    careers_url,
                    root=True,
                )

                if root is None:
                    raise JobFinderError(
                        "Could not open the careers page."
                    )

                result = explore(
                    context=context,
                    page=root,
                    evidence=inspect(root),
                    depth=0,
                    state=State(),
                )

                if result:
                    return result

            finally:
                if (
                    root is not None
                    and not root.is_closed()
                ):
                    root.close()

                context.close()
                browser.close()

    except JobFinderError:
        raise

    except (
        PlaywrightTimeoutError,
        PlaywrightError,
    ) as exc:
        raise JobFinderError(
            f"Browser error while finding a job: {exc}"
        ) from exc

    raise JobFinderError(
        "A specific open-job URL was not found "
        "within the search limits."
    )


def find_one_job_post(
    careers_page_url: str,
) -> str:
    """Return one specific open-job URL from a careers page."""

    careers_url = clean_url(
        careers_page_url
    )

    log(
        "jobs",
        careers_url,
    )

    try:
        return search_for_job(
            careers_url=careers_url,
            headless=True,
        )

    except JobFinderError as headless_error:
        log(
            "browser",
            (
                f"Headless job search failed: "
                f"{headless_error}"
            ),
        )

        log(
            "browser",
            "Retrying job search visibly.",
        )

        return search_for_job(
            careers_url=careers_url,
            headless=False,
        )