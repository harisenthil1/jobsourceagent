from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit, urlunsplit

from playwright.sync_api import (
    Error as PlaywrightError,
    TimeoutError as PlaywrightTimeoutError,
    Page,
    sync_playwright,
)

from ai_selector import (
    AISelectorError,
    MAX_AI_CANDIDATES,
    select_careers_candidate,
)
from resolver import resolve_company_website


CAREER_TERMS = (
    "careers",
    "career",
    "jobs",
    "join us",
    "join our team",
    "work with us",
    "open positions",
    "open roles",
    "job opportunities",
    "opportunities",
    "we are hiring",
)

CONTEXT_TERMS = (
    "join",
    "team",
    "hiring",
    "employment",
    "talent",
    "positions",
    "roles",
    "opportunities",
    "work with",
    "work at",
    "build with",
)

NEGATIVE_TERMS = (
    "contact",
    "support",
    "investor",
    "privacy",
    "news",
    "blog",
    "partner",
    "sales",
    "customer",
)

ATS_DOMAINS = (
    "jobs.lever.co",
    "jobs.ashbyhq.com",
    "greenhouse.io",
    "myworkdayjobs.com",
    "smartrecruiters.com",
    "icims.com",
    "jobvite.com",
    "workable.com",
    "bamboohr.com",
    "recruitee.com",
)

IGNORED_DOMAINS = (
    "facebook.com",
    "instagram.com",
    "youtube.com",
    "twitter.com",
    "x.com",
    "tiktok.com",
)

COMMON_CAREER_PATHS = (
    "/careers",
    "/jobs",
    "/join-us",
    "/join-our-team",
    "/work-with-us",
    "/open-positions",
)

STRONG_RULE_SCORE = 100
MINIMUM_SCORE_MARGIN = 40
MAX_RULE_CANDIDATES = 8


class CareersError(RuntimeError):
    """Raised when a careers page cannot be found."""


@dataclass(frozen=True)
class LinkCandidate:
    """One possible careers-page link."""

    url: str
    text: str
    context: str
    score: int


def log(category: str, message: str) -> None:
    """Print a concise process message."""

    print(f"[{category}] {message}")


def remove_tracking(url: str) -> str:
    """Remove query parameters and fragments."""

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


def normalize_company_website(url: str) -> str:
    """
    Validate and normalize a company website URL.

    A missing scheme defaults to HTTPS.
    """

    cleaned = url.strip()

    if not cleaned:
        raise CareersError("No company website URL was provided.")

    if "://" not in cleaned:
        cleaned = f"https://{cleaned}"

    parsed = urlsplit(cleaned)

    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise CareersError("Invalid company website URL.")

    path = parsed.path or "/"

    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            path,
            "",
            "",
        )
    )


def host_name(url: str) -> str:
    """Return a normalized hostname."""

    return (
        urlsplit(url)
        .netloc
        .lower()
        .removeprefix("www.")
    )


def is_known_ats(url: str) -> bool:
    """Check whether a URL belongs to a known ATS."""

    host = host_name(url)

    return any(
        host == domain or host.endswith(f".{domain}")
        for domain in ATS_DOMAINS
    )


def is_ignored_url(url: str) -> bool:
    """Check whether a URL belongs to an irrelevant external site."""

    host = host_name(url)

    return any(
        host == domain or host.endswith(f".{domain}")
        for domain in IGNORED_DOMAINS
    )


def normalize_ats_board_url(url: str) -> str:
    """
    Convert some individual ATS job URLs into company job-board URLs.
    """

    parsed = urlsplit(url)
    host = parsed.netloc.lower()

    segments = [
        segment
        for segment in parsed.path.split("/")
        if segment
    ]

    if not segments:
        return remove_tracking(url)

    if host.endswith("jobs.lever.co"):
        path = f"/{segments[0]}"

    elif host.endswith("jobs.ashbyhq.com"):
        path = f"/{segments[0]}"

    elif "greenhouse.io" in host:
        path = f"/{segments[0]}"

    elif "myworkdayjobs.com" in host:
        lower_path = parsed.path.lower()
        job_marker = lower_path.find("/job/")

        if job_marker != -1:
            path = parsed.path[:job_marker]
        else:
            path = parsed.path

    else:
        return remove_tracking(url)

    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            path.rstrip("/"),
            "",
            "",
        )
    )


def score_link(
    text: str,
    href: str,
    context: str,
) -> int:
    """Score how likely a link is to lead to careers."""

    text_lower = text.lower()
    href_lower = href.lower()
    context_lower = context.lower()

    score = 0

    for term in CAREER_TERMS:
        hyphenated_term = term.replace(" ", "-")
        underscored_term = term.replace(" ", "_")

        if term in text_lower:
            score += 80

        if (
            hyphenated_term in href_lower
            or underscored_term in href_lower
        ):
            score += 60

        if term in context_lower:
            score += 20

    if is_known_ats(href):
        score += 100

    if any(
        term in context_lower
        for term in CONTEXT_TERMS
    ):
        score += 15

    if any(
        term in text_lower
        for term in NEGATIVE_TERMS
    ):
        score -= 50

    return score


def collect_candidates(page: Page) -> list[LinkCandidate]:
    """
    Collect possible links from the current page.

    Zero-score links remain available because AI may understand vague
    wording that deterministic rules do not understand.
    """

    raw_links = page.locator("a[href]").evaluate_all(
        """
        elements => elements.map(element => {
            const container = element.closest(
                "footer, nav, section, article, li, div"
            );

            return {
                text: (
                    element.innerText ||
                    element.getAttribute("aria-label") ||
                    element.getAttribute("title") ||
                    ""
                ).trim(),

                href: element.href || "",

                context: (
                    container?.innerText ||
                    element.parentElement?.innerText ||
                    ""
                ).trim().slice(0, 1000)
            };
        })
        """
    )

    candidates_by_url: dict[str, LinkCandidate] = {}

    for item in raw_links:
        href = str(item.get("href", "")).strip()
        text = str(item.get("text", "")).strip()
        context = str(item.get("context", "")).strip()

        if not href.startswith(("http://", "https://")):
            continue

        if is_ignored_url(href):
            continue

        normalized_url = remove_tracking(href)

        if is_known_ats(normalized_url):
            normalized_url = normalize_ats_board_url(
                normalized_url
            )

        score = score_link(
            text=text,
            href=href,
            context=context,
        )

        candidate = LinkCandidate(
            url=normalized_url,
            text=text,
            context=context,
            score=score,
        )

        existing = candidates_by_url.get(normalized_url)

        if existing is None or score > existing.score:
            candidates_by_url[normalized_url] = candidate

    return sorted(
        candidates_by_url.values(),
        key=lambda candidate: candidate.score,
        reverse=True,
    )


def rules_are_confident(
    candidates: list[LinkCandidate],
) -> bool:
    """Determine whether the rule system has one obvious winner."""

    positive_candidates = [
        candidate
        for candidate in candidates
        if candidate.score > 0
    ]

    if not positive_candidates:
        return False

    best = positive_candidates[0]

    if best.score < STRONG_RULE_SCORE:
        return False

    if len(positive_candidates) == 1:
        return True

    second_best = positive_candidates[1]

    return (
        best.score - second_best.score
        >= MINIMUM_SCORE_MARGIN
    )


def prepare_ai_candidates(
    candidates: list[LinkCandidate],
    attempted_urls: set[str],
) -> list[LinkCandidate]:
    """
    Select fewer than ten candidates for AI.

    Previously attempted and strongly negative links are excluded.
    """

    eligible = [
        candidate
        for candidate in candidates
        if (
            candidate.url not in attempted_urls
            and candidate.score > -50
        )
    ]

    return eligible[:MAX_AI_CANDIDATES]


def candidate_label(candidate: LinkCandidate) -> str:
    """Create a short candidate label for console output."""

    text = " ".join(candidate.text.split())

    if text:
        return text[:60]

    parsed = urlsplit(candidate.url)

    return f"{parsed.netloc}{parsed.path}"[:60]


def looks_like_careers_page(page: Page) -> bool:
    """Verify that the current page contains career information."""

    current_url = page.url.lower()

    if is_known_ats(current_url):
        return True

    try:
        title = page.title().lower()
    except PlaywrightError:
        title = ""

    try:
        body_text = (
            page.locator("body")
            .inner_text(timeout=5_000)
            .lower()[:30_000]
        )
    except PlaywrightError:
        body_text = ""

    combined = f"{current_url} {title} {body_text}"

    strong_signals = (
        "open positions",
        "open roles",
        "job openings",
        "search jobs",
        "view jobs",
        "join our team",
        "work with us",
        "current opportunities",
        "available positions",
        "current openings",
    )

    signal_count = sum(
        signal in combined
        for signal in strong_signals
    )

    try:
        application_link_count = page.locator(
            "a[href*='job'], "
            "a[href*='career'], "
            "a:has-text('Apply'), "
            "button:has-text('Apply')"
        ).count()
    except PlaywrightError:
        application_link_count = 0

    return (
        signal_count >= 1
        and application_link_count >= 1
    )


def try_page(page: Page, url: str) -> str | None:
    """Open and verify one possible careers-page URL."""

    try:
        response = page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=25_000,
        )

        if (
            response is not None
            and response.status >= 400
        ):
            return None

        page.wait_for_timeout(750)

        if looks_like_careers_page(page):
            return remove_tracking(page.url)

    except (
        PlaywrightTimeoutError,
        PlaywrightError,
    ):
        return None

    return None


def try_ai_selection(
    page: Page,
    company_website: str,
    page_title: str,
    candidates: list[LinkCandidate],
    attempted_urls: set[str],
) -> str | None:
    """Ask AI to choose an ambiguous link, then verify it."""

    ai_candidates = prepare_ai_candidates(
        candidates=candidates,
        attempted_urls=attempted_urls,
    )

    if not ai_candidates:
        return None

    log(
        "ai",
        f"Reviewing {len(ai_candidates)} ambiguous links.",
    )

    ai_input = [
        {
            "url": candidate.url,
            "text": candidate.text,
            "context": candidate.context,
            "score": candidate.score,
        }
        for candidate in ai_candidates
    ]

    try:
        selection = select_careers_candidate(
            company_website=company_website,
            page_title=page_title,
            candidates=ai_input,
        )

    except AISelectorError as exc:
        log("ai", f"Skipped: {exc}")
        return None

    if selection.url is None:
        log("ai", "AI found no suitable careers link.")
        return None

    selected_text = (
        " ".join(selection.text.split())[:50]
        or selection.url
    )

    short_reason = " ".join(
        selection.reason.split()
    )[:120]

    log(
        "ai",
        (
            f'Chose "{selected_text}" '
            f"({selection.confidence:.0%}): "
            f"{short_reason}"
        ),
    )

    attempted_urls.add(selection.url)

    verified_url = try_page(
        page=page,
        url=selection.url,
    )

    if verified_url:
        log("verified", "AI selection passed verification.")
        return verified_url

    log("ai", "AI selection failed verification.")

    return None


def search_company_website(
    company_website: str,
    headless: bool,
) -> str:
    """
    Search a company website once using the requested browser mode.

    This is a lower-level function. Most callers should use
    find_careers_page_from_website().
    """

    mode = "headless" if headless else "visible"

    log("browser", f"Searching in {mode} mode.")

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=headless,
                channel="chromium",
            )

            page = browser.new_page(
                viewport={
                    "width": 1440,
                    "height": 1000,
                }
            )

            try:
                response = page.goto(
                    company_website,
                    wait_until="domcontentloaded",
                    timeout=30_000,
                )

                if (
                    response is not None
                    and response.status >= 400
                ):
                    raise CareersError(
                        f"Company website returned HTTP "
                        f"{response.status}."
                    )

                page.wait_for_timeout(750)

                try:
                    page_title = page.title()
                except PlaywrightError:
                    page_title = ""

                page.evaluate(
                    """
                    window.scrollTo(
                        0,
                        document.body.scrollHeight
                    )
                    """
                )

                page.wait_for_timeout(1_000)

                candidates = collect_candidates(page)

                positive_candidates = [
                    candidate
                    for candidate in candidates
                    if candidate.score > 0
                ]

                log(
                    "scan",
                    (
                        f"Found {len(candidates)} links; "
                        f"{len(positive_candidates)} likely candidates."
                    ),
                )

                attempted_urls: set[str] = set()

                if rules_are_confident(candidates):
                    best_candidate = positive_candidates[0]

                    log(
                        "rules",
                        (
                            f'Trying "'
                            f'{candidate_label(best_candidate)}".'
                        ),
                    )

                    attempted_urls.add(best_candidate.url)

                    result = try_page(
                        page=page,
                        url=best_candidate.url,
                    )

                    if result:
                        log(
                            "verified",
                            "Rule match passed verification.",
                        )
                        return result

                    log(
                        "rules",
                        "Strong rule match failed verification.",
                    )

                else:
                    log(
                        "rules",
                        "No single high-confidence match.",
                    )

                ai_result = try_ai_selection(
                    page=page,
                    company_website=company_website,
                    page_title=page_title,
                    candidates=candidates,
                    attempted_urls=attempted_urls,
                )

                if ai_result:
                    return ai_result

                remaining_candidates = [
                    candidate
                    for candidate in positive_candidates
                    if candidate.url not in attempted_urls
                ][:MAX_RULE_CANDIDATES]

                if remaining_candidates:
                    log(
                        "rules",
                        (
                            "Testing "
                            f"{len(remaining_candidates)} "
                            "remaining candidates."
                        ),
                    )

                for candidate in remaining_candidates:
                    attempted_urls.add(candidate.url)

                    result = try_page(
                        page=page,
                        url=candidate.url,
                    )

                    if result:
                        log(
                            "verified",
                            "Rule candidate passed verification.",
                        )
                        return result

                log(
                    "fallback",
                    "Trying common careers paths.",
                )

                for path in COMMON_CAREER_PATHS:
                    candidate_url = urljoin(
                        company_website,
                        path,
                    )

                    candidate_url = remove_tracking(
                        candidate_url
                    )

                    if candidate_url in attempted_urls:
                        continue

                    result = try_page(
                        page=page,
                        url=candidate_url,
                    )

                    if result:
                        log(
                            "verified",
                            "Common path passed verification.",
                        )
                        return result

            finally:
                browser.close()

    except CareersError:
        raise

    except PlaywrightTimeoutError as exc:
        raise CareersError(
            "Company website timed out while loading."
        ) from exc

    except PlaywrightError as exc:
        raise CareersError(
            f"Browser error: {exc}"
        ) from exc

    raise CareersError("Careers page was not found.")


def find_careers_page_from_website(
    company_website: str,
) -> str:
    """
    Find a careers page directly from a company website.

    This function bypasses LinkedIn and Bright Data. It is useful for
    development tests and as the independent careers-discovery stage.
    """

    normalized_website = normalize_company_website(
        company_website
    )

    log("website", normalized_website)

    try:
        return search_company_website(
            company_website=normalized_website,
            headless=True,
        )

    except CareersError as headless_error:
        log(
            "browser",
            f"Headless attempt failed: {headless_error}",
        )

        log(
            "browser",
            "Retrying with a visible browser.",
        )

        return search_company_website(
            company_website=normalized_website,
            headless=False,
        )


def find_careers_page_from_linkedin(
    linkedin_job_url: str,
    api_key: str | None = None,
) -> str:
    """
    Run the complete LinkedIn-to-careers-page workflow.
    """

    log("resolve", "Resolving company website.")

    company_website = resolve_company_website(
        linkedin_job_url=linkedin_job_url,
        api_key=api_key,
    )

    return find_careers_page_from_website(
        company_website
    )


# Compatibility alias for code using the previous function name.
find_careers_page = find_careers_page_from_linkedin