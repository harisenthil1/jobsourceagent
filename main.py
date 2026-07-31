from careers import (
    CareersError,
    find_careers_page_from_linkedin,
)
from job_finder import (
    JobFinderError,
    find_one_job_post,
)
from resolver import ResolverError


def main() -> None:
    linkedin_job_url = input(
        "Enter a LinkedIn job posting URL: "
    ).strip()

    if not linkedin_job_url:
        print(
            "Error: No LinkedIn job URL provided."
        )
        return

    try:
        careers_url = (
            find_careers_page_from_linkedin(
                linkedin_job_url
            )
        )

        print(
            f"\n[stage] Careers page: "
            f"{careers_url}"
        )

        job_url = find_one_job_post(
            careers_url
        )

        print(f"\nResult: {job_url}")

    except (
        ResolverError,
        CareersError,
        JobFinderError,
    ) as exc:
        print(f"Error: {exc}")


if __name__ == "__main__":
    main()