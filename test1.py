from careers import (
    CareersError,
    find_careers_page_from_website,
)


DEFAULT_TEST_WEBSITE = (
    "https://recruitingsite.staticdomains.app/"
)


def main() -> None:
    company_website = input(
        f"Enter company website: "
    ).strip()

    if not company_website:
        company_website = DEFAULT_TEST_WEBSITE

    try:
        careers_url = find_careers_page_from_website(
            company_website
        )

        print(f"\nResult: {careers_url}")

    except CareersError as exc:
        print(f"Error: {exc}")


if __name__ == "__main__":
    main()