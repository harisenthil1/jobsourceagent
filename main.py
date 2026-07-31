from resolver import ResolverError, resolve_company_website


def main() -> None:
    linkedin_url = input(
        "Enter a LinkedIn job posting URL: "
    ).strip()

    if not linkedin_url:
        print("Error: No LinkedIn job URL provided.")
        return

    try:
        website = resolve_company_website(linkedin_url)
        print(website)
    except ResolverError as exc:
        print(f"Error: {exc}")


if __name__ == "__main__":
    main()