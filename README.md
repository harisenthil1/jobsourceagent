# Job Source Agent

A Python application that starts with a LinkedIn job posting URL and finds:

- The company website
- The company careers page
- One specific open job posting URL

The project uses browser automation, rule-based link scoring, limited AI assistance, and backtracking.

---

## How It Works

### 1. Receive a LinkedIn Job URL

The program asks the user to enter a LinkedIn job posting URL.

```text
Enter a LinkedIn job posting URL:
https://www.linkedin.com/jobs/view/1234567890/
```

---

### 2. Resolve the Company Website

`resolver.py` sends the LinkedIn job URL to a LinkedIn crawler API.

The returned LinkedIn data is used to identify the company and its official website.

```text
LinkedIn job URL
        ↓
Company information
        ↓
Official company website
```

Example:

```text
https://www.linkedin.com/jobs/view/4423657972/
        ↓
US Mobile
        ↓
https://www.usmobile.com/
```

---

### 3. Find the Careers Page

`careers.py` opens the company website using Playwright.

It searches the page for links such as:

- Careers
- Jobs
- Join our team
- Work with us
- Open positions

The program uses the following order:

1. Look for an obvious careers link using rules.
2. Use AI only when several links are ambiguous.
3. Try common paths such as `/careers` and `/jobs`.
4. Verify that the selected page is related to hiring.

Example:

```text
https://www.usmobile.com/
        ↓
https://www.usmobile.com/careers
```

If the website blocks the headless browser, the program retries with a visible browser.

---

### 4. Inspect the Careers Page

`job_finder.py` opens the careers page and determines what kind of page it is.

The page may be:

- A general careers landing page
- A job board containing multiple openings
- A page describing one specific job
- An application-only form
- A login or unrelated page

The program also inspects embedded iframes because some companies load their jobs inside another website.

---

### 5. Explore Obvious Job Buttons

When the page contains an obvious button such as:

- View open positions
- View jobs
- Search jobs
- Current openings

the program follows it without using AI.

Example:

```text
Company careers page
        ↓
View open positions
        ↓
Greenhouse job board
```

The new page is inspected to determine whether it contains better job-related information.

---

### 6. Find Job Listings

When the program reaches a job board, it collects links for specific positions.

It can detect jobs from:

- Normal links
- Job cards
- Apply buttons connected to a specific job
- ATS websites such as Greenhouse or Lever
- Embedded iframes
- `JobPosting` structured data

Example:

```text
Senior Backend Engineer
Product Designer
Account Manager
Software Engineer
```

The program selects one job candidate and opens it.

The challenge only requires one active job posting, so it does not need to collect every available job.

---

### 7. Verify the Job Description Page

The program checks whether the new page is primarily about one specific job.

A valid final page normally contains:

- One prominent job title
- A meaningful job description
- Responsibilities or duties
- Qualifications or requirements
- Location or employment information

An application form may appear below the description. That is still considered a valid job-description page.

```text
Job title
Job location
Job description
Responsibilities
Qualifications
Application form
```

The program returns the page when one job description clearly dominates the page.

---

### 8. Reject Application-Only Pages

The program does not return a page that mainly contains:

- Resume upload fields
- Name and email fields
- Login forms
- Registration forms
- Submit application controls
- Very little or no job description

Example:

```text
Submit your application
Resume/CV
Full name
Email
Phone
```

If the program reaches an application-only page, it checks the parent URL or goes back to the previous page.

---

### 9. Backtrack When Necessary

Every explored page is compared with the previous page.

The intended direction is:

```text
Company website
        ↓
Careers page
        ↓
Job board
        ↓
Specific job description
```

The program backtracks when it reaches:

- An application-only page
- A login page
- A company homepage
- A legal or privacy page
- A page with less useful job information
- A page it has already visited

This prevents the browser from continuing through irrelevant links.

---

### 10. Return the Result

When a valid job-description page is found, the program returns:

- Company website
- Careers page URL
- Specific job posting URL

Example:

```text
[website] https://insurify.com/

[stage] Careers page:
https://insurify.com/company/careers/

[found] Verified one dominant job description.

Result:
https://job-boards.greenhouse.io/insurify/jobs/123456
```

---

## Main Files

### `main.py`

Runs the complete workflow.

```text
LinkedIn URL
→ Company website
→ Careers page
→ Job posting
```

### `resolver.py`

Uses LinkedIn job data to resolve the company website.

### `careers.py`

Finds and verifies the company careers page.

### `ai_selector.py`

Uses AI only when the careers-page links are ambiguous.

### `job_finder.py`

Explores the careers page, finds job listings, verifies one job description, and handles backtracking.

### `job_ai_selector.py`

Uses AI only when the next job-navigation action cannot be selected reliably with rules.

### `test.py`

Tests the company-website-to-careers-page stage directly.

### `test_job.py`

Tests the careers-page-to-job-posting stage directly.

---

## Rule-Based Logic and AI

The project prefers deterministic rules whenever possible.

Rules handle clear links such as:

```text
Careers
View open positions
Search jobs
View job
Job details
```

AI is used only for vague options such as:

```text
Start here
Discover more
Explore opportunities
Take the next step
```

This keeps the workflow more predictable and reduces unnecessary API usage.

---

## Browser Modes

The browser first runs in headless mode.

```text
Headless browser
        ↓
Success → continue
        ↓
Blocked or HTTP 403
        ↓
Retry with visible browser
```

The visible-browser fallback is useful for websites that block automated headless browsers.

---

## Environment Variables

Create a `.env` file containing:

```env
LINKEDIN_API_KEY=your_linkedin_crawler_api_key
OPENAI_API_KEY=your_openai_api_key
OPENAI_MODEL=gpt-5-mini
```

---

## Running the Project

Install the Python dependencies and Playwright browser:

```bash
pip install -r requirements.txt
playwright install chromium
```

Run the complete workflow:

```bash
python main.py
```

Test only the careers-page finder:

```bash
python test.py
```

Test only the job-post finder:

```bash
python test_job.py
```

---

## Project Flow

```text
LinkedIn job posting
        ↓
Resolve company website
        ↓
Open company website
        ↓
Find careers page
        ↓
Inspect careers page
        ↓
Follow job-discovery buttons
        ↓
Find a job board
        ↓
Open one specific position
        ↓
Verify the job description
        ↓
Return the job posting URL
```