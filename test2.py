from job_finder import (
    JobFinderError,
    find_one_job_post,
)


def main() -> None:
    careers_page_url = input(
        "Enter a careers-page URL: "
    ).strip()

    if not careers_page_url:
        print(
            "Error: No careers-page URL provided."
        )
        return

    try:
        job_url = find_one_job_post(
            careers_page_url
        )

        print(f"\nResult: {job_url}")

    except JobFinderError as exc:
        print(f"Error: {exc}")


if __name__ == "__main__":
    main()