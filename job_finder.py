# PATCH VERSION: caraluzzi-lever-greenhouse-2026-08-01-v2
from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

from playwright.sync_api import (
    BrowserContext,
    Error as PlaywrightError,
    Frame,
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
    "clear filters",
    "sort by",
    "filter by",
    "all jobs",
)

DESCRIPTION_SIGNALS = (
    "responsibilities",
    "qualifications",
    "requirements",
    "job description",
    "about the role",
    "about this role",
    "what you'll do",
    "what you will do",
    "what we're looking for",
    "what we are looking for",
    "duties",
    "compensation",
    "salary",
    "benefits",
    "the role",
    "your impact",
)

JOB_METADATA_SIGNALS = (
    "full time",
    "full-time",
    "part time",
    "part-time",
    "remote",
    "hybrid",
    "on-site",
    "onsite",
    "posted",
    "salary",
    "per hour",
    "employment type",
    "location",
    "department",
)

APPLICATION_SIGNALS = (
    "submit application",
    "submit your application",
    "apply for this job",
    "upload resume",
    "upload your resume",
    "attach resume",
    "resume/cv",
    "cover letter",
    "candidate information",
    "personal information",
    "create account",
    "sign in to apply",
    "quick apply",
)

DISCOVERY_ACTION_TERMS = (
    "view open positions",
    "view openings",
    "see openings",
    "current openings",
    "current job listings",
    "search jobs",
    "view jobs",
    "open positions",
    "job listings",
    "explore jobs",
    "browse jobs",
    "find jobs",
    "view opportunities",
    "job opportunities",
    "join our team",
    "work with us",
    "start here",
    "explore opportunities",
    "see all jobs",
    "all open roles",
)

APPLICATION_ACTION_TERMS = (
    "apply",
    "apply now",
    "apply online",
    "apply for this job",
    "quick apply",
    "submit application",
    "submit your application",
    "login",
    "log in",
    "register",
    "sign in",
    "sign up",
    "upload resume",
    "submit resume",
)

BACKWARD_ACTION_TERMS = (
    "back to jobs",
    "back to job listings",
    "return to jobs",
    "return to listings",
    "all jobs",
    "view all jobs",
    "back",
)

UTILITY_ACTION_TERMS = (
    "create alert",
    "create job alert",
    "job alert",
    "sign up for job alerts",
    "quick apply with mygreenhouse",
    "mygreenhouse",
    "open menu",
    "toggle flyout",
    "toggle menu",
    "menu",
    "home",
    "homepage",
    "company website",
    "share",
    "save job",
    "print",
)

GENERIC_ROLE_TEXT = {
    "careers",
    "career",
    "jobs",
    "current jobs",
    "current openings",
    "job openings",
    "current job listings",
    "search jobs",
    "view jobs",
    "apply",
    "apply now",
    "apply online",
    "apply for this job",
    "join our team",
    "work with us",
    "learn more",
    "sales",
    "engineering",
    "operations",
    "marketing",
    "finance",
    "administration",
    "customer service",
    "product",
}

NON_ROLE_TITLE_TERMS = (
    "fraud",
    "fraudulent",
    "beware",
    "important",
    "privacy",
    "cookie",
    "terms",
    "about us",
    "life at",
    "why join",
    "our values",
    "our benefits",
    "benefits and perks",
)

HARD_EXCLUDED_LABELS = {
    "privacy",
    "privacy policy",
    "cookie policy",
    "cookie preferences",
    "set cookie preferences",
    "terms",
    "terms of use",
    "terms and conditions",
    "legal",
    "accessibility",
    "newsletter",
    "contact",
    "contact us",
    "investor relations",
    "social media",
    "create alert",
    "create job alert",
    "quick apply with mygreenhouse",
    "toggle flyout",
    "open menu",
}

HARD_EXCLUDED_URL_PARTS = (
    "/privacy",
    "/cookie",
    "/terms",
    "/legal",
    "/accessibility",
    "/contact",
    "/investor",
    "/login",
    "/sign-in",
    "/signin",
    "/register",
    "/job-alert",
    "/alerts",
)

APPLICATION_PATH_SUFFIXES = {
    "apply",
    "application",
    "apply-now",
    "job-application",
    "submit-application",
}

ACTION_SCORES = {
    "view open positions": 180,
    "view job": 170,
    "job details": 170,
    "view position": 165,
    "view jobs": 160,
    "search jobs": 160,
    "current openings": 155,
    "current job listings": 155,
    "job listings": 150,
    "open positions": 150,
    "view openings": 145,
    "see openings": 140,
    "view opportunities": 130,
    "job opportunities": 130,
    "start here": 75,
    "explore opportunities": 60,
    "join us": 55,
    "learn more": 20,
}

CLICKABLE_SELECTOR = (
    "a[href], button, [role='button'], [onclick], "
    "[data-href], [data-url], "
    "input[type='button'], input[type='submit'], "
    "[tabindex]:not([tabindex='-1'])"
)

COOKIE_ACCEPT_TEXT = re.compile(
    r"^(accept|accept all|allow all|agree|i agree|got it|ok|okay)$",
    re.IGNORECASE,
)

# Exploration is cheap; AI is used only for genuinely vague choices.
MAX_DEPTH = 4
MAX_TOTAL_ATTEMPTS = 10
MAX_DIRECT_JOB_LINKS = 3
MAX_DISCOVERY_ACTIONS = 3

MIN_DESCRIPTION_LENGTH = 400
STRONG_DESCRIPTION_LENGTH = 700


class JobFinderError(RuntimeError):
    """Raised when one specific open-job URL cannot be found."""


@dataclass(frozen=True)
class Candidate:
    index: int | None
    frame_url: str
    frame_name: str
    text: str
    role_title: str
    url: str
    context: str
    score: int
    kind: str

    def key(self, source_url: str) -> str:
        target = self.url or (
            f"{self.frame_url}|{self.index}|"
            f"{self.role_title}|{self.text[:80]}"
        )
        return f"{source_url}|{self.kind}|{target}"


@dataclass(frozen=True)
class Evidence:
    page_url: str
    content_url: str
    title: str
    heading: str
    summary: str

    direct_jobs: list[Candidate]
    discovery: list[Candidate]
    ambiguous_actions: list[Candidate]

    distinct_role_count: int
    prominent_role_count: int
    repeated_job_card_count: int
    filter_control_count: int

    description_length: int
    application_field_count: int
    application_form_count: int
    file_upload_count: int

    board_score: int
    detail_score: int
    application_score: int

    specific_role_title: str
    specific_role_evidence: bool
    specific_url_evidence: bool

    stage: str
    conclusive_individual: bool
    application_only: bool
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
    """Print one concise process line."""

    print(f"[{category}] {message}")


def clean_url(url: str) -> str:
    """Validate an HTTP(S) URL and remove its fragment."""

    cleaned = url.strip()

    if not cleaned:
        raise JobFinderError("No careers-page URL was provided.")

    if "://" not in cleaned:
        cleaned = f"https://{cleaned}"

    parsed = urlsplit(cleaned)

    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise JobFinderError("Invalid careers-page URL.")

    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path or "/",
            parsed.query,
            "",
        )
    )


def url_identity(url: str) -> str:
    """Return a normalized URL for equality checks."""

    parsed = urlsplit(clean_url(url))

    return urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower().removeprefix("www."),
            parsed.path.rstrip("/") or "/",
            "",
            "",
        )
    )


def hostname(url: str) -> str:
    """Return a normalized hostname."""

    return urlsplit(url).netloc.lower().removeprefix("www.")


def matches_domain(url: str, domains: tuple[str, ...]) -> bool:
    """Check whether a URL belongs to one of the supplied domains."""

    host = hostname(url)
    return any(host == domain or host.endswith(f".{domain}") for domain in domains)


def is_ats(url: str) -> bool:
    """Check whether a URL belongs to a known recruiting platform."""

    return matches_domain(url, ATS_DOMAINS)


def is_application_url(url: str) -> bool:
    """Return whether the URL clearly enters an application flow."""

    if not url:
        return False

    parsed = urlsplit(url)
    parts = [part.lower() for part in parsed.path.split("/") if part]

    if parts and parts[-1] in APPLICATION_PATH_SUFFIXES:
        return True

    query = parse_qs(parsed.query.lower())
    return any(key in query for key in ("apply", "application"))


def application_parent_url(url: str) -> str | None:
    """Derive a likely job-description URL from an application URL."""

    parsed = urlsplit(clean_url(url))
    parts = [part for part in parsed.path.split("/") if part]

    if not parts or parts[-1].lower() not in APPLICATION_PATH_SUFFIXES:
        return None

    path = "/" + "/".join(parts[:-1])

    if path == "/":
        return None

    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def looks_like_job_url(url: str) -> bool:
    """Conservatively recognize a URL for one specific job."""

    if not url or is_application_url(url):
        return False

    parsed = urlsplit(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower().rstrip("/")
    parts = [part for part in path.split("/") if part]

    if host.endswith(("jobs.lever.co", "jobs.ashbyhq.com")):
        return len(parts) >= 2

    if "greenhouse.io" in host:
        if re.search(r"/jobs/\d+(?:[-/]|$)", path):
            return True
        query = parse_qs(parsed.query)
        return bool(query.get("gh_jid"))

    if "myworkdayjobs.com" in host:
        return "/job/" in f"{path}/"

    if "applicantpro.com" in host:
        return bool(re.search(r"/jobs/[^/]+", path)) and not path.endswith(
            ("/jobs", "/jobs/search")
        )

    if "smartrecruiters.com" in host:
        return len(parts) >= 2 and parts[-1] not in {"jobs", "search"}

    if "workable.com" in host:
        return "/j/" in f"{path}/"

    if "recruitee.com" in host:
        return "/o/" in f"{path}/"

    match = re.search(
        r"/(?:job|jobs|position|positions|opening|openings)/([^/?#]+)",
        path,
    )

    return bool(
        match
        and match.group(1)
        not in {"search", "list", "all", "openings", "jobs", "positions"}
    )


def normalized_text(value: str) -> str:
    """Normalize text for rules."""

    return " ".join(value.lower().split())


def is_specific_role_text(text: str) -> bool:
    """Return whether text plausibly names one particular role."""

    normalized = normalized_text(text)

    if not normalized or len(normalized) > 180:
        return False

    if normalized in GENERIC_ROLE_TEXT:
        return False

    if any(term in normalized for term in NON_ROLE_TITLE_TERMS):
        return False

    if normalized in {
        "view job",
        "job details",
        "view position",
        "apply",
        "apply now",
        "apply online",
        "apply for this job",
        "back to jobs",
    }:
        return False

    return 1 < len(normalized.split()) <= 18


def is_homepage_url(url: str) -> bool:
    """Return whether a URL points to a site's root page."""

    if not url:
        return False

    parsed = urlsplit(url)
    return parsed.path in {"", "/"} and not parsed.query


def hard_excluded(text: str, url: str, context: str) -> bool:
    """Remove legal, social, login, alerts, menus, and utility actions."""

    label = normalized_text(text)
    url_lower = url.lower()
    context_lower = context.lower()

    if label in HARD_EXCLUDED_LABELS:
        return True

    if any(part in url_lower for part in HARD_EXCLUDED_URL_PARTS):
        return True

    if url and matches_domain(url, IGNORED_DOMAINS):
        return True

    if label.startswith(("mailto:", "tel:")):
        return True

    if any(
        label == phrase or label.startswith(f"{phrase} ")
        for phrase in UTILITY_ACTION_TERMS
    ):
        return True

    if "alert" in label and "job" in label:
        return True

    if "mygreenhouse" in label:
        return True

    if "login" in label or "sign in" in label or "register" in label:
        return True

    is_cookie_control = (
        label
        in {
            "accept",
            "accept all",
            "allow all",
            "agree",
            "i agree",
            "reject",
            "reject all",
            "manage preferences",
        }
        and ("cookie" in context_lower or "consent" in context_lower)
    )

    return is_cookie_control


def is_backward_action(candidate: Candidate) -> bool:
    """Return whether an action goes back to a list or earlier page."""

    label = normalized_text(candidate.text)
    return any(
        label == phrase or label.startswith(f"{phrase} ")
        for phrase in BACKWARD_ACTION_TERMS
    )


def is_application_action(candidate: Candidate) -> bool:
    """Return whether an action enters an application or account flow."""

    # On a broad careers landing page, a general "Apply now" control may
    # actually be the route to the company's job board. Discovery takes
    # precedence unless the destination is explicitly an application URL.
    if not is_application_url(candidate.url) and is_discovery_action(candidate):
        return False

    if is_application_url(candidate.url):
        return True

    label = normalized_text(candidate.text)

    return any(
        label == phrase or label.startswith(f"{phrase} ")
        for phrase in APPLICATION_ACTION_TERMS
    )


def is_discovery_action(candidate: Candidate) -> bool:
    """Return whether an action is likely to reveal job listings."""

    if is_application_url(candidate.url) or is_backward_action(candidate):
        return False

    label = normalized_text(candidate.text)
    context = candidate.context.lower()
    url_lower = candidate.url.lower()

    if any(phrase in label for phrase in DISCOVERY_ACTION_TERMS):
        return True

    if any(
        fragment in url_lower
        for fragment in ("/careers", "/openings", "/positions", "/jobs")
    ):
        return not looks_like_job_url(candidate.url)

    general_apply = label in {"apply", "apply now", "apply online"}
    has_specific_role = is_specific_role_text(candidate.role_title)
    careers_context = any(
        phrase in context
        for phrase in (
            "career",
            "join our team",
            "job openings",
            "open positions",
            "current openings",
            "employment opportunities",
        )
    )

    return general_apply and not has_specific_role and careers_context


def wait_for_dynamic_content(page: Page) -> None:
    """Wait briefly for JavaScript and iframe content to stabilize."""

    try:
        page.wait_for_load_state("domcontentloaded", timeout=8_000)
    except PlaywrightError:
        pass

    last_count = -1
    stable_rounds = 0

    for round_number in range(6):
        total = 0

        for frame in list(page.frames):
            try:
                total += frame.locator(CLICKABLE_SELECTOR).count()
            except PlaywrightError:
                pass

        if total == last_count and total > 0:
            stable_rounds += 1
        else:
            stable_rounds = 0

        if stable_rounds >= 2 and round_number >= 2:
            break

        last_count = total
        page.wait_for_timeout(350)


def dismiss_cookie_banner(page: Page) -> bool:
    """Dismiss a cookie banner without consuming a search attempt."""

    selector = "button, [role='button'], input[type='button'], input[type='submit']"

    script = """
    elements => elements.map((element, index) => {
        const style = getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        const container = element.closest(
            "[id*='cookie' i], [class*='cookie' i], "
            + "[id*='consent' i], [class*='consent' i], "
            + "[aria-label*='cookie' i]"
        );

        return {
            index,
            visible:
                style.display !== 'none'
                && style.visibility !== 'hidden'
                && rect.width > 0
                && rect.height > 0,
            text: (
                element.innerText
                || element.value
                || element.getAttribute('aria-label')
                || ''
            ).trim(),
            cookieContext: Boolean(container)
        };
    })
    """

    for frame in list(page.frames):
        try:
            items = frame.locator(selector).evaluate_all(script)
        except PlaywrightError:
            continue

        for item in items:
            if not item.get("visible") or not item.get("cookieContext"):
                continue

            label = str(item.get("text", "")).strip()

            if not COOKIE_ACCEPT_TEXT.match(label):
                continue

            try:
                frame.locator(selector).nth(int(item["index"])).click(timeout=3_000)
                page.wait_for_timeout(300)
                log("cookie", "Dismissed cookie banner.")
                return True
            except PlaywrightError:
                pass

    return False


def snapshot_frame(frame: Frame) -> dict[str, Any]:
    """Collect compact DOM, layout, form, and clickable evidence."""

    try:
        return frame.evaluate(
            """
            () => {
                const visible = element => {
                    const style = getComputedStyle(element);
                    const rect = element.getBoundingClientRect();
                    return (
                        style.display !== 'none'
                        && style.visibility !== 'hidden'
                        && rect.width > 0
                        && rect.height > 0
                    );
                };

                const rawText = element => (
                    element?.innerText
                    || element?.textContent
                    || ''
                );

                const normalized = value => (
                    value || ''
                ).replace(/\\s+/g, ' ').trim();

                const text = element => normalized(rawText(element));

                const findSemanticContainer = element => {
                    let current = element.parentElement;
                    let best = element.parentElement;

                    for (let depth = 0; current && depth < 8; depth += 1) {
                        const currentText = text(current);
                        const hasHeading = Boolean(
                            current.querySelector('h1, h2, h3, h4')
                        );
                        const jobishClass = /job|position|opening|posting|vacancy/i.test(
                            String(current.className || '')
                        );

                        if (
                            (hasHeading || jobishClass)
                            && currentText.length >= 15
                            && currentText.length <= 1800
                        ) {
                            best = current;

                            if (hasHeading && currentText.length >= 30) {
                                return current;
                            }
                        }

                        current = current.parentElement;
                    }

                    return best || element.parentElement;
                };

                const findRoleTitle = container => {
                    if (!container) {
                        return '';
                    }

                    const selectors = [
                        "[class*='job-title' i]",
                        "[class*='position-title' i]",
                        "[class*='opening-title' i]",
                        "[data-testid*='job-title' i]",
                        'h1',
                        'h2',
                        'h3',
                        'h4'
                    ];

                    for (const selector of selectors) {
                        for (const candidate of container.querySelectorAll(selector)) {
                            if (!visible(candidate)) {
                                continue;
                            }

                            const value = text(candidate);

                            if (value && value.length <= 180) {
                                return value;
                            }
                        }
                    }

                    return '';
                };

                const clickables = [
                    ...document.querySelectorAll(%s)
                ].map((element, index) => {
                    const container = findSemanticContainer(element);
                    const rect = element.getBoundingClientRect();

                    return {
                        index,
                        visible: visible(element),
                        disabled: Boolean(element.disabled),
                        tag: element.tagName.toLowerCase(),
                        text: normalized(
                            element.innerText
                            || element.value
                            || element.getAttribute('aria-label')
                            || element.getAttribute('title')
                            || ''
                        ),
                        roleTitle: findRoleTitle(container),
                        href: (
                            element.href
                            || element.getAttribute('data-href')
                            || element.getAttribute('data-url')
                            || ''
                        ),
                        context: text(container).slice(0, 1600),
                        fontSize: parseFloat(getComputedStyle(element).fontSize) || 0,
                        width: rect.width,
                        height: rect.height
                    };
                }).filter(item => item.visible);

                const mainCandidates = [
                    ...document.querySelectorAll("main, [role='main'], article")
                ].filter(visible).sort(
                    (first, second) => text(second).length - text(first).length
                );

                const main = mainCandidates[0] || document.body;
                const mainText = text(main);

                const headings = [
                    ...document.querySelectorAll('h1, h2, h3, h4')
                ].filter(visible).map(element => {
                    const rect = element.getBoundingClientRect();
                    const style = getComputedStyle(element);

                    return {
                        text: text(element).slice(0, 220),
                        tag: element.tagName.toLowerCase(),
                        fontSize: parseFloat(style.fontSize) || 0,
                        width: rect.width,
                        height: rect.height,
                        top: rect.top
                    };
                });

                const descriptionTerms = [
                    'responsibilities',
                    'qualifications',
                    'requirements',
                    'job description',
                    'about the role',
                    'about this role',
                    "what you'll do",
                    'what you will do',
                    "what we're looking for",
                    'what we are looking for',
                    'duties',
                    'compensation',
                    'salary',
                    'benefits',
                    'the role',
                    'your impact'
                ];

                const descriptionBlocks = [];

                for (const heading of document.querySelectorAll(
                    'h1, h2, h3, h4, strong'
                )) {
                    if (!visible(heading)) {
                        continue;
                    }

                    const headingText = text(heading).toLowerCase();

                    if (!descriptionTerms.some(term => headingText.includes(term))) {
                        continue;
                    }

                    let container = heading.closest(
                        'section, article, li, [class*="description" i], div'
                    ) || heading.parentElement;

                    while (
                        container?.parentElement
                        && text(container).length < 250
                    ) {
                        container = container.parentElement;
                    }

                    if (!container || !visible(container)) {
                        continue;
                    }

                    descriptionBlocks.push({
                        heading: text(heading).slice(0, 160),
                        textLength: text(container).length,
                        snippet: text(container).slice(0, 1400)
                    });
                }

                const roleElements = [
                    ...document.querySelectorAll(
                        "h1, h2, h3, h4, "
                        + "[class*='job-title' i], "
                        + "[class*='position-title' i], "
                        + "[class*='opening-title' i], "
                        + "[data-testid*='job-title' i]"
                    )
                ].filter(visible).map(element => {
                    const rect = element.getBoundingClientRect();
                    const style = getComputedStyle(element);
                    const container = findSemanticContainer(element);

                    return {
                        text: text(element).slice(0, 220),
                        tag: element.tagName.toLowerCase(),
                        className: String(element.className || '').slice(0, 240),
                        fontSize: parseFloat(style.fontSize) || 0,
                        width: rect.width,
                        height: rect.height,
                        top: rect.top,
                        context: text(container).slice(0, 900)
                    };
                });

                const fieldDescriptor = element => {
                    const labels = element.labels
                        ? [...element.labels].map(text).join(' ')
                        : '';

                    return normalized([
                        labels,
                        element.name,
                        element.id,
                        element.placeholder,
                        element.getAttribute('aria-label'),
                        element.autocomplete
                    ].filter(Boolean).join(' ')).toLowerCase();
                };

                // Only identity/contact/resume fields establish a real job
                // application form. Broad terms such as city, state, website,
                // and location are intentionally excluded because they commonly
                // appear in job filters and unrelated forms.
                const strongApplicationFieldTerms = [
                    'full name', 'first name', 'last name', 'email', 'e-mail',
                    'phone', 'mobile', 'resume', 'curriculum vitae', 'cv',
                    'cover letter', 'work authorization', 'authorized to work',
                    'sponsorship'
                ];

                const supportingApplicationFieldTerms = [
                    'linkedin', 'portfolio', 'address', 'postal', 'zip',
                    'veteran', 'gender', 'race', 'disability'
                ];

                const filterFieldTerms = [
                    'search', 'filter', 'department', 'location', 'city',
                    'state', 'employment type', 'job type', 'category',
                    'team', 'sort', 'keyword'
                ];

                const applicationContextTerms = [
                    'submit application', 'submit your application',
                    'apply for this job', 'candidate information',
                    'personal information', 'attach resume', 'upload resume',
                    'resume/cv', 'cover letter', 'quick apply'
                ];

                const formSubmitText = form => normalized([
                    ...form.querySelectorAll(
                        "button, input[type='submit'], input[type='button'], [role='button']"
                    )
                ].filter(visible).map(element => (
                    element.innerText
                    || element.value
                    || element.getAttribute('aria-label')
                    || ''
                )).join(' ')).toLowerCase();

                let filterControlCount = 0;
                let applicationFieldCount = 0;
                let applicationFormCount = 0;
                let fileUploadCount = 0;
                let applicationTextLength = 0;

                const formGroups = new Map();
                const fields = [
                    ...document.querySelectorAll(
                        "input:not([type='hidden']), textarea, select"
                    )
                ].filter(visible);

                for (const field of fields) {
                    const descriptor = fieldDescriptor(field);
                    const type = (field.type || '').toLowerCase();
                    const form = field.closest('form');
                    const group = form || field.parentElement;

                    if (!group) {
                        continue;
                    }

                    const isFile = type === 'file';
                    const isStrongIdentityField = (
                        isFile
                        || type === 'email'
                        || type === 'tel'
                        || strongApplicationFieldTerms.some(
                            term => descriptor.includes(term)
                        )
                    );
                    const isSupportingField = supportingApplicationFieldTerms.some(
                        term => descriptor.includes(term)
                    );
                    const isFilter = (
                        type === 'search'
                        || filterFieldTerms.some(term => descriptor.includes(term))
                    );

                    // A field that looks like a filter stays a filter unless it
                    // also has unmistakable identity/contact/resume semantics.
                    if (isFilter && !isStrongIdentityField) {
                        filterControlCount += 1;
                        continue;
                    }

                    if (!formGroups.has(group)) {
                        formGroups.set(group, {
                            strong: 0,
                            supporting: 0,
                            files: 0,
                            fields: 0
                        });
                    }

                    const record = formGroups.get(group);
                    record.fields += 1;

                    if (isStrongIdentityField) {
                        record.strong += 1;
                    } else if (isSupportingField) {
                        record.supporting += 1;
                    }

                    if (isFile) {
                        record.files += 1;
                    }
                }

                for (const [container, record] of formGroups.entries()) {
                    const containerText = text(container).toLowerCase();
                    const submitText = container.matches('form')
                        ? formSubmitText(container)
                        : containerText;
                    const hasApplicationContext = applicationContextTerms.some(
                        term => containerText.includes(term) || submitText.includes(term)
                    );

                    const isRealApplicationForm = (
                        (hasApplicationContext && record.strong >= 2)
                        || (record.files >= 1 && record.strong >= 2)
                        || record.strong >= 3
                    );

                    if (!isRealApplicationForm) {
                        continue;
                    }

                    applicationFormCount += 1;
                    applicationFieldCount += record.strong + record.supporting;
                    fileUploadCount += record.files;
                    applicationTextLength += text(container).length;
                }

                const jobCardSelectors = [
                    "article[class*='job' i]",
                    "li[class*='job' i]",
                    "[class*='job-card' i]",
                    "[class*='job-listing' i]",
                    "[class*='opening-card' i]",
                    "[data-job-id]",
                    "[data-testid*='job-card' i]"
                ].join(',');

                const jobCards = [
                    ...document.querySelectorAll(jobCardSelectors)
                ].filter(visible).filter(element => text(element).length >= 20);

                return {
                    frameUrl: location.href,
                    mainText: mainText.slice(0, 60000),
                    headings,
                    descriptionBlocks,
                    roleElements,
                    clickables,
                    filterControlCount,
                    applicationFieldCount,
                    applicationFormCount,
                    fileUploadCount,
                    applicationTextLength,
                    jobCardCount: jobCards.length
                };
            }
            """
            % json.dumps(CLICKABLE_SELECTOR)
        )

    except PlaywrightError:
        return {
            "frameUrl": frame.url,
            "mainText": "",
            "headings": [],
            "descriptionBlocks": [],
            "roleElements": [],
            "clickables": [],
            "filterControlCount": 0,
            "applicationFieldCount": 0,
            "applicationFormCount": 0,
            "fileUploadCount": 0,
            "applicationTextLength": 0,
            "jobCardCount": 0,
        }


def extract_jobposting_records(value: Any) -> list[dict[str, Any]]:
    """Recursively extract JobPosting JSON-LD records."""

    records: list[dict[str, Any]] = []

    if isinstance(value, dict):
        raw_type = value.get("@type")
        types = raw_type if isinstance(raw_type, list) else [raw_type]

        if any(str(item).lower() == "jobposting" for item in types):
            raw_description = html.unescape(
                re.sub(r"<[^>]+>", " ", str(value.get("description", "")))
            )
            description = " ".join(raw_description.split())

            records.append(
                {
                    "url": str(value.get("url", "")),
                    "title": str(value.get("title", "")),
                    "description_length": len(description),
                }
            )

        for nested in value.values():
            records.extend(extract_jobposting_records(nested))

    elif isinstance(value, list):
        for item in value:
            records.extend(extract_jobposting_records(item))

    return records


def jsonld_records(page: Page) -> list[dict[str, Any]]:
    """Read JobPosting structured data from all frames."""

    found: dict[tuple[str, str], dict[str, Any]] = {}

    for frame in list(page.frames):
        try:
            scripts = frame.locator(
                "script[type='application/ld+json']"
            ).all_text_contents()
        except PlaywrightError:
            continue

        for script in scripts:
            try:
                data = json.loads(script)
            except (TypeError, ValueError):
                continue

            for record in extract_jobposting_records(data):
                raw_url = record["url"]
                base_url = frame.url or page.url
                record["url"] = clean_url(urljoin(base_url, raw_url)) if raw_url else clean_url(base_url)

                key = (record["url"], record["title"])
                previous = found.get(key)

                if previous is None or record["description_length"] > previous["description_length"]:
                    found[key] = record

    return list(found.values())


def role_score(text: str, context: str, font_size: float, tag: str) -> int:
    """Score whether heading text plausibly names one specific role."""

    if not is_specific_role_text(text):
        return -100

    score = 0
    context_lower = context.lower()

    if any(term in context_lower for term in JOB_METADATA_SIGNALS):
        score += 5

    if tag == "h1":
        score += 5
    elif tag in {"h2", "h3"}:
        score += 3

    if font_size >= 26:
        score += 3
    elif font_size >= 20:
        score += 2

    return score


def collect_role_evidence(
    snapshots: list[tuple[Frame, dict[str, Any]]],
) -> tuple[str, int, list[dict[str, Any]]]:
    """Return the dominant role title and number of similarly prominent roles."""

    roles: dict[str, dict[str, Any]] = {}

    for _, snapshot in snapshots:
        for item in snapshot.get("roleElements", []):
            text = " ".join(str(item.get("text", "")).split())
            context = str(item.get("context", ""))
            font_size = float(item.get("fontSize", 0) or 0)
            width = float(item.get("width", 0) or 0)
            height = float(item.get("height", 0) or 0)
            tag = str(item.get("tag", "")).lower()

            score = role_score(text, context, font_size, tag)

            if score < 4:
                continue

            prominence = (
                max(font_size, 10)
                * (1 + min(width, 1000) / 1000)
                * (1 + min(height, 180) / 360)
                * (1.25 if tag == "h1" else 1.1 if tag in {"h2", "h3"} else 1.0)
            )

            candidate = {
                "text": text,
                "context": " ".join(context.split())[:300],
                "font_size": round(font_size, 1),
                "prominence": round(prominence, 2),
                "score": score,
            }

            key = text.lower()
            previous = roles.get(key)

            if previous is None or prominence > float(previous["prominence"]):
                roles[key] = candidate

    ordered = sorted(
        roles.values(),
        key=lambda item: (float(item["prominence"]), int(item["score"])),
        reverse=True,
    )

    if not ordered:
        return "", 0, []

    top = float(ordered[0]["prominence"])
    similar_count = sum(
        float(item["prominence"]) >= top * 0.75 for item in ordered
    )

    return str(ordered[0]["text"]), similar_count, ordered[:8]


def job_candidate_score(
    *,
    text: str,
    role_title: str,
    url: str,
    context: str,
) -> int:
    """Score a clickable element as a direct specific-job candidate."""

    if not url or is_application_url(url):
        return -100

    label = normalized_text(text)
    score = 0

    if looks_like_job_url(url):
        score += 180

    if any(phrase in label for phrase in ("view job", "job details", "view position")):
        score += 100

    if is_specific_role_text(text):
        score += 50

    if is_specific_role_text(role_title):
        score += 65

    if any(term in context.lower() for term in JOB_METADATA_SIGNALS):
        score += 25

    if label in {"apply", "apply now", "apply online"}:
        if looks_like_job_url(url) and is_specific_role_text(role_title):
            score += 55
        else:
            score -= 40

    if is_backward_label(label):
        return -100

    return score


def is_backward_label(label: str) -> bool:
    """Return whether normalized text is backward navigation."""

    return any(
        label == phrase or label.startswith(f"{phrase} ")
        for phrase in BACKWARD_ACTION_TERMS
    )


def action_score(text: str, role_title: str, url: str, context: str) -> int:
    """Score a non-final navigation action."""

    label = normalized_text(text)
    score = sum(points for phrase, points in ACTION_SCORES.items() if phrase in label)

    if is_ats(url):
        score += 70

    if any(fragment in url.lower() for fragment in ("/jobs", "/careers", "/openings", "/positions")):
        score += 45

    if any(
        phrase in context.lower()
        for phrase in ("career", "hiring", "current openings", "job listings")
    ):
        score += 20

    if is_specific_role_text(role_title):
        score += 20

    if is_application_url(url):
        score -= 100

    return score


def build_candidates(
    page: Page,
    snapshots: list[tuple[Frame, dict[str, Any]]],
) -> tuple[list[Candidate], list[Candidate], list[Candidate]]:
    """Build direct-job, discovery, and ambiguous candidates from all frames."""

    direct: dict[str, Candidate] = {}
    discovery: dict[str, Candidate] = {}
    ambiguous: dict[str, Candidate] = {}

    page_identity = url_identity(page.url)

    for frame, snapshot in snapshots:
        frame_url = str(snapshot.get("frameUrl") or frame.url or page.url)
        frame_name = frame.name or ""

        for item in snapshot.get("clickables", []):
            if item.get("disabled"):
                continue

            text = str(item.get("text", "")).strip()
            role_title = str(item.get("roleTitle", "")).strip()
            context = str(item.get("context", "")).strip()
            raw_href = str(item.get("href", "")).strip()
            index = item.get("index")
            url = ""

            if raw_href:
                joined = urljoin(frame_url or page.url, raw_href)

                if joined.startswith(("http://", "https://")):
                    url = clean_url(joined)

            if hard_excluded(text, url, context):
                continue

            if url and url_identity(url) == page_identity:
                continue

            if url and is_homepage_url(url):
                label = normalized_text(text)
                if not any(term in label for term in ("jobs", "careers", "openings")):
                    continue

            candidate_index = int(index) if isinstance(index, int) else None

            direct_score = job_candidate_score(
                text=text,
                role_title=role_title,
                url=url,
                context=context,
            )

            if direct_score >= 100:
                candidate = Candidate(
                    index=candidate_index,
                    frame_url=frame_url,
                    frame_name=frame_name,
                    text=text,
                    role_title=role_title,
                    url=url,
                    context=context,
                    score=direct_score,
                    kind="job",
                )
                key = url or f"{frame_url}|{candidate_index}|{role_title}|{text}"
                previous = direct.get(key)

                if previous is None or candidate.score > previous.score:
                    direct[key] = candidate

                continue

            candidate = Candidate(
                index=candidate_index,
                frame_url=frame_url,
                frame_name=frame_name,
                text=text,
                role_title=role_title,
                url=url,
                context=context,
                score=action_score(text, role_title, url, context),
                kind="action",
            )

            if is_backward_action(candidate) or is_application_action(candidate):
                continue

            key = url or f"{frame_url}|{candidate_index}|{role_title}|{text}"

            if is_discovery_action(candidate):
                previous = discovery.get(key)

                if previous is None or candidate.score > previous.score:
                    discovery[key] = candidate

                continue

            if candidate.score <= 0:
                continue

            previous = ambiguous.get(key)

            if previous is None or candidate.score > previous.score:
                ambiguous[key] = candidate

    return (
        sorted(direct.values(), key=lambda item: item.score, reverse=True),
        sorted(discovery.values(), key=lambda item: item.score, reverse=True),
        sorted(ambiguous.values(), key=lambda item: item.score, reverse=True),
    )


def measure_description(
    *,
    snapshots: list[tuple[Frame, dict[str, Any]]],
    records: list[dict[str, Any]],
    specific_url_evidence: bool,
    specific_role_title: str,
) -> tuple[int, str, str]:
    """Estimate visible job-description length and its owning frame URL."""

    explicit_length = 0
    explicit_url = ""
    longest_main = ""
    longest_url = ""
    application_text_length = 0

    for _, snapshot in snapshots:
        frame_url = str(snapshot.get("frameUrl", ""))
        main_text = " ".join(str(snapshot.get("mainText", "")).split())

        if len(main_text) > len(longest_main):
            longest_main = main_text
            longest_url = frame_url
            application_text_length = int(
                snapshot.get("applicationTextLength", 0) or 0
            )

        for block in snapshot.get("descriptionBlocks", []):
            block_length = int(block.get("textLength", 0) or 0)

            if block_length > explicit_length:
                explicit_length = block_length
                explicit_url = frame_url

    jsonld_length = max(
        (int(record.get("description_length", 0) or 0) for record in records),
        default=0,
    )

    heuristic_length = 0

    if specific_url_evidence and specific_role_title:
        heuristic_length = max(0, len(longest_main) - application_text_length)

    description_length = max(explicit_length, jsonld_length, heuristic_length)

    if jsonld_length >= max(explicit_length, heuristic_length) and len(records) == 1:
        owner_url = str(records[0].get("url", "")) or longest_url
    elif explicit_length >= heuristic_length:
        owner_url = explicit_url or longest_url
    else:
        owner_url = longest_url

    return description_length, owner_url, longest_main


def inspect(page: Page) -> Evidence:
    """Inspect the main document and every iframe without navigating."""

    snapshots = [(frame, snapshot_frame(frame)) for frame in list(page.frames)]
    records = jsonld_records(page)

    direct_jobs, discovery, ambiguous_actions = build_candidates(page, snapshots)

    specific_role_title, similarly_prominent_roles, role_samples = collect_role_evidence(
        snapshots
    )

    filter_controls = sum(
        int(snapshot.get("filterControlCount", 0) or 0)
        for _, snapshot in snapshots
    )
    application_fields = sum(
        int(snapshot.get("applicationFieldCount", 0) or 0)
        for _, snapshot in snapshots
    )
    application_forms = sum(
        int(snapshot.get("applicationFormCount", 0) or 0)
        for _, snapshot in snapshots
    )
    file_uploads = sum(
        int(snapshot.get("fileUploadCount", 0) or 0)
        for _, snapshot in snapshots
    )
    repeated_job_cards = sum(
        int(snapshot.get("jobCardCount", 0) or 0)
        for _, snapshot in snapshots
    )

    distinct_role_titles = {
        normalized_text(candidate.role_title or candidate.text)
        for candidate in direct_jobs
        if is_specific_role_text(candidate.role_title or candidate.text)
    }
    distinct_role_titles.update(
        normalized_text(str(sample.get("text", "")))
        for sample in role_samples
        if is_specific_role_text(str(sample.get("text", "")))
    )
    distinct_role_count = len(distinct_role_titles)

    specific_url_evidence = looks_like_job_url(page.url) or len(records) == 1

    description_length, owner_url, longest_main = measure_description(
        snapshots=snapshots,
        records=records,
        specific_url_evidence=specific_url_evidence,
        specific_role_title=specific_role_title,
    )

    content_url = clean_url(owner_url) if owner_url.startswith(("http://", "https://")) else clean_url(page.url)

    if looks_like_job_url(content_url):
        specific_url_evidence = True

    top_context = role_samples[0]["context"] if role_samples else ""
    role_has_metadata = any(
        term in top_context.lower() for term in JOB_METADATA_SIGNALS
    )

    specific_role_evidence = bool(
        is_specific_role_text(specific_role_title)
        and (
            specific_url_evidence
            or role_has_metadata
            or len(records) == 1
        )
    )

    all_text = " ".join(
        " ".join(str(snapshot.get("mainText", "")).split())
        for _, snapshot in snapshots
    )
    lower = all_text.lower()

    board_score = (
        30 * sum(signal in lower for signal in BOARD_SIGNALS)
        + min(180, len(direct_jobs) * 18)
        + min(120, distinct_role_count * 20)
        + min(80, filter_controls * 20)
        + min(100, repeated_job_cards * 15)
        + (100 if len(records) > 1 else 0)
    )

    detail_score = (
        (80 if description_length >= MIN_DESCRIPTION_LENGTH else 0)
        + (80 if description_length >= STRONG_DESCRIPTION_LENGTH else 0)
        + (80 if specific_role_evidence else 0)
        + (50 if specific_url_evidence else 0)
        + (30 if similarly_prominent_roles <= 1 and specific_role_title else 0)
    )

    application_signal_count = sum(
        signal in lower for signal in APPLICATION_SIGNALS
    )
    application_score = (
        min(120, application_fields * 20)
        + min(50, file_uploads * 40)
        + min(60, application_signal_count * 15)
        + (30 if is_application_url(content_url) else 0)
    )

    # A specific ATS job URL with a substantial description is final even when
    # the application form is rendered below it or related jobs appear smaller.
    conclusive_individual = bool(
        specific_url_evidence
        and specific_role_evidence
        and description_length >= MIN_DESCRIPTION_LENGTH
        and similarly_prominent_roles <= 2
    )

    # Non-standard company-hosted detail pages can also qualify, but require
    # stronger text evidence and little board structure.
    if not conclusive_individual:
        conclusive_individual = bool(
            specific_role_evidence
            and description_length >= STRONG_DESCRIPTION_LENGTH
            and similarly_prominent_roles <= 1
            and len(direct_jobs) <= 1
            and repeated_job_cards <= 1
            and filter_controls == 0
        )

    has_real_application_form = bool(
        application_forms >= 1
        and application_fields >= 2
        and (
            file_uploads >= 1
            or application_signal_count >= 1
            or is_application_url(content_url)
        )
    )

    has_board_structure = bool(
        len(direct_jobs) >= 2
        or distinct_role_count >= 2
        or similarly_prominent_roles >= 2
        or repeated_job_cards >= 2
        or filter_controls >= 2
        or len(records) > 1
    )

    # Application-only is deliberately terminal only when the page has a real
    # identity/contact/resume form and no useful route toward a job description.
    # This protects careers landing pages such as Caraluzzi's while preserving
    # Lever/Greenhouse application-form backtracking.
    application_only = bool(
        not conclusive_individual
        and description_length < MIN_DESCRIPTION_LENGTH
        and has_real_application_form
        and not direct_jobs
        and not discovery
    )

    if conclusive_individual:
        stage = "detail"
    elif has_board_structure or board_score >= 100:
        stage = "board"
    elif application_only:
        stage = "application"
    else:
        stage = "landing"

    final_job_url: str | None = None

    if conclusive_individual:
        if len(records) == 1 and records[0].get("url") and not is_application_url(
            str(records[0]["url"])
        ):
            final_job_url = clean_url(str(records[0]["url"]))
        else:
            final_job_url = content_url

    try:
        title = page.title().strip()
    except PlaywrightError:
        title = ""

    heading = specific_role_title
    summary = longest_main[:1800]

    signature_source = (
        f"{url_identity(page.url)}|{url_identity(content_url)}|"
        f"{title}|{heading}|{summary[:700]}|{stage}|"
        f"{len(direct_jobs)}|{len(discovery)}"
    )
    signature = hashlib.sha256(
        signature_source.encode("utf-8", errors="ignore")
    ).hexdigest()

    return Evidence(
        page_url=clean_url(page.url),
        content_url=content_url,
        title=title,
        heading=heading,
        summary=summary,
        direct_jobs=direct_jobs,
        discovery=discovery,
        ambiguous_actions=ambiguous_actions,
        distinct_role_count=distinct_role_count,
        prominent_role_count=similarly_prominent_roles,
        repeated_job_card_count=repeated_job_cards,
        filter_control_count=filter_controls,
        description_length=description_length,
        application_field_count=application_fields,
        application_form_count=application_forms,
        file_upload_count=file_uploads,
        board_score=board_score,
        detail_score=detail_score,
        application_score=application_score,
        specific_role_title=specific_role_title,
        specific_role_evidence=specific_role_evidence,
        specific_url_evidence=specific_url_evidence,
        stage=stage,
        conclusive_individual=conclusive_individual,
        application_only=application_only,
        final_job_url=final_job_url,
        signature=signature,
    )


def stage_rank(stage: str) -> int:
    """Return directional progress rank."""

    return {
        "application": -1,
        "landing": 0,
        "board": 1,
        "detail": 2,
    }.get(stage, 0)


def compare_progress(before: Evidence, after: Evidence) -> tuple[bool, str]:
    """Compare two states directionally rather than by raw link count."""

    if after.conclusive_individual:
        return True, "one dominant job description found"

    if after.application_only:
        return False, "reached an application-only page"

    old_rank = stage_rank(before.stage)
    new_rank = stage_rank(after.stage)

    if new_rank > old_rank:
        return True, f"advanced from {before.stage} to {after.stage}"

    if new_rank < old_rank:
        return False, f"regressed from {before.stage} to {after.stage}"

    if after.stage == "board":
        if len(after.direct_jobs) > len(before.direct_jobs):
            return (
                True,
                f"job links {len(before.direct_jobs)} -> {len(after.direct_jobs)}",
            )

        if after.board_score > before.board_score + 25:
            return True, "job-board evidence increased"

    if after.stage == "landing":
        if not is_ats(before.content_url) and is_ats(after.content_url):
            return True, "reached a recruiting platform"

        if after.board_score > before.board_score + 40:
            return True, "job evidence increased"

    changed_url = url_identity(after.content_url) != url_identity(before.content_url)

    if changed_url and after.detail_score > before.detail_score + 40:
        return True, "job-description evidence increased"

    return False, "job evidence did not improve"


def open_page(
    context: BrowserContext,
    url: str,
    root: bool = False,
) -> Page | None:
    """Open a URL in an isolated page and wait once for dynamic content."""

    page = context.new_page()

    try:
        response = page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=30_000,
        )

        if response is not None and response.status >= 400:
            if root:
                raise JobFinderError(
                    f"Careers page returned HTTP {response.status}."
                )

            page.close()
            return None

        wait_for_dynamic_content(page)

        if dismiss_cookie_banner(page):
            wait_for_dynamic_content(page)

        return page

    except (PlaywrightTimeoutError, PlaywrightError) as exc:
        page.close()

        if root:
            raise JobFinderError(f"Could not open careers page: {exc}") from exc

        return None


def find_frame(page: Page, candidate: Candidate) -> Frame | None:
    """Locate the frame that originally produced a candidate."""

    for frame in list(page.frames):
        if candidate.frame_name and frame.name == candidate.frame_name:
            return frame

        if candidate.frame_url and frame.url:
            try:
                if url_identity(frame.url) == url_identity(candidate.frame_url):
                    return frame
            except JobFinderError:
                if frame.url == candidate.frame_url:
                    return frame

    return None


def find_locator(frame: Frame, candidate: Candidate):
    """Locate a previously collected clickable after recreating its page."""

    base = frame.locator(CLICKABLE_SELECTOR)

    if candidate.text:
        exact = base.filter(
            has_text=re.compile(
                rf"^\s*{re.escape(candidate.text.strip())}\s*$",
                re.IGNORECASE,
            )
        )

        try:
            if exact.count() > 0:
                return exact.first
        except PlaywrightError:
            pass

    if candidate.index is not None:
        return base.nth(candidate.index)

    return None


def follow(
    context: BrowserContext,
    source_url: str,
    candidate: Candidate,
) -> Page | None:
    """Follow a direct URL or click an element inside its original frame."""

    if candidate.url:
        return open_page(context, candidate.url)

    if candidate.index is None:
        return None

    page = open_page(context, source_url)

    if page is None:
        return None

    frame = find_frame(page, candidate)

    if frame is None:
        page.close()
        return None

    locator = find_locator(frame, candidate)

    if locator is None:
        page.close()
        return None

    pages_before = list(context.pages)

    try:
        locator.scroll_into_view_if_needed(timeout=5_000)
        locator.click(timeout=10_000)
        page.wait_for_timeout(700)

        new_pages = [item for item in context.pages if item not in pages_before]

        if new_pages:
            destination = new_pages[-1]

            try:
                destination.wait_for_load_state("domcontentloaded", timeout=12_000)
            except PlaywrightError:
                pass

            page.close()
            wait_for_dynamic_content(destination)

            if dismiss_cookie_banner(destination):
                wait_for_dynamic_content(destination)

            return destination

        wait_for_dynamic_content(page)

        if dismiss_cookie_banner(page):
            wait_for_dynamic_content(page)

        return page

    except (PlaywrightTimeoutError, PlaywrightError):
        page.close()
        return None


def short_label(candidate: Candidate) -> str:
    """Create a concise label for logs."""

    role_title = " ".join(candidate.role_title.split())
    text = " ".join(candidate.text.split())

    if role_title and text and role_title.lower() != text.lower():
        return f"{role_title} — {text}"[:72]

    if role_title:
        return role_title[:72]

    if text:
        return text[:72]

    parsed = urlsplit(candidate.url)
    return f"{parsed.netloc}{parsed.path}"[:72]


def choose_ambiguous_action(evidence: Evidence) -> Candidate | None:
    """Use one AI call to choose one genuinely ambiguous action."""

    choices = evidence.ambiguous_actions[:MAX_AI_ACTIONS]

    if not choices:
        return None

    payload = [
        {
            "text": candidate.role_title or candidate.text,
            "url": candidate.url,
            "context": candidate.context,
            "score": candidate.score,
            "element_type": "clickable",
        }
        for candidate in choices
    ]

    try:
        result = select_next_job_action(
            page_url=evidence.content_url,
            page_title=evidence.title,
            page_heading=evidence.heading,
            page_summary=evidence.summary,
            progress_score=evidence.board_score + evidence.detail_score,
            actions=payload,
        )
    except JobAISelectorError as exc:
        log("ai", f"Skipped: {exc}")
        return None

    if result.action_id is None:
        log("ai", "No useful ambiguous action selected.")
        return None

    chosen = choices[result.action_id - 1]
    reason = " ".join(result.reason.split())[:120]

    log(
        "ai",
        f'Chose "{short_label(chosen)}" '
        f"({result.confidence:.0%}): {reason}",
    )

    return chosen


def explore_candidate(
    *,
    context: BrowserContext,
    source_evidence: Evidence,
    candidate: Candidate,
    depth: int,
    state: State,
) -> str | None:
    """Follow one branch, assess progress, and backtrack when necessary."""

    if not state.can_try():
        return None

    key = candidate.key(source_evidence.page_url)

    if key in state.attempted:
        return None

    state.attempted.add(key)
    state.attempts += 1

    log("act", f'Following "{short_label(candidate)}".')

    branch = follow(
        context=context,
        source_url=source_evidence.page_url,
        candidate=candidate,
    )

    if branch is None:
        log("backtrack", "Action could not be completed.")
        return None

    try:
        next_evidence = inspect(branch)
        improved, reason = compare_progress(source_evidence, next_evidence)

        if not improved:
            log("backtrack", f"{reason.capitalize()}.")
            return None

        log("progress", f"{reason.capitalize()}.")

        return explore(
            context=context,
            page=branch,
            evidence=next_evidence,
            depth=depth + 1,
            state=state,
        )

    finally:
        if not branch.is_closed():
            branch.close()


def explore(
    context: BrowserContext,
    page: Page,
    evidence: Evidence,
    depth: int,
    state: State,
) -> str | None:
    """Explore deterministically first and use AI only for one vague action."""

    if evidence.signature in state.visited:
        return None

    state.visited.add(evidence.signature)

    log(
        "inspect",
        (
            f"Depth {depth}: {evidence.stage}; "
            f"jobs={len(evidence.direct_jobs)}, "
            f"discover={len(evidence.discovery)}, "
            f"roles={evidence.distinct_role_count}, "
            f"prominent={evidence.prominent_role_count}, "
            f"description={evidence.description_length}, "
            f"app_forms={evidence.application_form_count}, "
            f"app_fields={evidence.application_field_count}."
        ),
    )

    # The goal has been reached. Do not click Apply, Back to jobs, alerts,
    # account links, logos, menus, or any other controls on this page.
    if evidence.conclusive_individual and evidence.final_job_url:
        log("found", "Verified one dominant job description.")
        return evidence.final_job_url

    if depth >= MAX_DEPTH or not state.can_try():
        return None

    # Useful forward routes are explored before trusting an application-only
    # label. This is a safety net for mixed careers pages that contain short
    # role blurbs or unrelated forms alongside real job/discovery links.
    for candidate in evidence.direct_jobs[:MAX_DIRECT_JOB_LINKS]:
        result = explore_candidate(
            context=context,
            source_evidence=evidence,
            candidate=candidate,
            depth=depth,
            state=state,
        )

        if result:
            return result

    # A landing page should follow obvious discovery actions with no AI call.
    for candidate in evidence.discovery[:MAX_DISCOVERY_ACTIONS]:
        log("rules", f'Strong discovery action: "{short_label(candidate)}".')

        result = explore_candidate(
            context=context,
            source_evidence=evidence,
            candidate=candidate,
            depth=depth,
            state=state,
        )

        if result:
            return result

    if evidence.application_only:
        parent = application_parent_url(evidence.content_url)
        parent_key = f"parent:{url_identity(parent)}" if parent else ""

        if (
            parent
            and state.can_try()
            and parent_key not in state.attempted
        ):
            state.attempted.add(parent_key)
            state.attempts += 1
            log("backtrack", "Application-only page; checking parent job URL.")

            branch = open_page(context, parent)

            if branch is not None:
                try:
                    result = explore(
                        context=context,
                        page=branch,
                        evidence=inspect(branch),
                        depth=depth + 1,
                        state=state,
                    )

                    if result:
                        return result
                finally:
                    if not branch.is_closed():
                        branch.close()

        return None

    # Only one ambiguous AI-selected action is tried. We do not append and click
    # every remaining control on the page.
    chosen = choose_ambiguous_action(evidence)

    if chosen is not None:
        return explore_candidate(
            context=context,
            source_evidence=evidence,
            candidate=chosen,
            depth=depth,
            state=state,
        )

    return None


def search_for_job(careers_url: str, headless: bool) -> str:
    """Search for one specific job in one browser mode."""

    mode = "headless" if headless else "visible"
    log("browser", f"Searching for a job in {mode} mode.")

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=headless,
                channel="chromium",
            )
            context = browser.new_context(
                viewport={"width": 1440, "height": 1000}
            )
            root: Page | None = None

            try:
                root = open_page(context, careers_url, root=True)

                if root is None:
                    raise JobFinderError("Could not open the careers page.")

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
                if root is not None and not root.is_closed():
                    root.close()

                context.close()
                browser.close()

    except JobFinderError:
        raise
    except (PlaywrightTimeoutError, PlaywrightError) as exc:
        raise JobFinderError(
            f"Browser error while finding a job: {exc}"
        ) from exc

    raise JobFinderError(
        "A specific open-job URL was not found within the search limits."
    )


def find_one_job_post(careers_page_url: str) -> str:
    """Return one specific open-job description URL from a careers page."""

    careers_url = clean_url(careers_page_url)
    log("jobs", careers_url)

    try:
        return search_for_job(
            careers_url=careers_url,
            headless=True,
        )
    except JobFinderError as exc:
        log("browser", f"Headless job search failed: {exc}")
        log("browser", "Retrying job search visibly.")

        return search_for_job(
            careers_url=careers_url,
            headless=False,
        )
