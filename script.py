def resolve_job_company(self, linkedin_job_url: str) -> JobCompanyResult:
    job = self._collect_one(
        dataset_id=self.LINKEDIN_JOBS_DATASET_ID,
        target_url=linkedin_job_url,
    )

    job_title = self._required_string(job, "job_title")
    company_name = self._required_string(job, "company_name")
    linkedin_company_url = self._required_string(job, "company_url")

    company = self._collect_one(
        dataset_id=self.LINKEDIN_COMPANIES_DATASET_ID,
        target_url=linkedin_company_url,
    )

    company_website = self._required_string(company, "website")

    return JobCompanyResult(
        linkedin_job_url=linkedin_job_url,
        job_title=job_title,
        company_name=company_name,
        linkedin_company_url=linkedin_company_url,
        company_website=company_website,
    )