# Inventory Optimization & Lead Generation Pipeline

This repository contains the backend systems for a two-part B2B inventory optimization and lead matching pipeline:
1. **Multi-Agent Electrical Component Application Finder**: Maps specific surplus components (e.g. SSDs, LPDDR5, resistors) to their potential industrial applications using an iterative, audited multi-agent flow.
2. **Scalable Lead Scraping & Synthesis Engine**: Automatically harvests, scrapes, classifies, and database-persists B2B lead companies (MSMEs) in target manufacturing/hardware sectors at scale (100k+ leads).

---

## 1. Multi-Agent Component Application Finder

Maps surplus electrical components to every possible system assembly, functional hardware slot, and vertical market.

### System Architecture

```
                  ┌──────────────────────┐
                  │   User Input / API   │
                  └──────────┬───────────┘
                             │
                             ▼
               ┌────────────────────────────┐
               │    SpecsExtractorAgent     │ (Extracts part number, package, specs)
               └─────────────┬──────────────┘
                             │
                             ▼
         ┌────────────────────────────────────────┐
         │    ApplicationDomainSpecialistAgent    │ (Checks Consumer, Enterprise, Automotive,
         └─────────────┬──────────────────────────┘  Industrial, Medical, Defense, Telecom)
                       │
                       ▼
               ┌────────────────────────────┐
               │       SynthesisAgent       │ (Compiles full detailed Markdown report)
               └─────────────┬──────────────┘
                             │
                             ▼
               ┌────────────────────────────┐
               │    QualityAssuranceAgent   │ ◄──┐ (Audits for gaps / missing detail)
               └─────────────┬──────────────┘    │
                             │                   │
                     [Approved?]                 │ (If rejected, loops up to max_refinement
                       ├─── No ──────────────────┘  times with audit recommendations)
                       └─── Yes ───► [Final Report Output]
```

#### CLI Usage:
Run component specifications analysis to compile a markdown engineering report:
```bash
python cli.py "Samsung K3LKBKB0BM-MGC8 LPDDR5 16GB" --output report.md
```

#### FastAPI Server Usage:
Start the local API server:
```bash
python main.py
```
Interact with the API Swagger UI at `http://localhost:8000/docs`.

---

## 2. Scalable Lead Scraping & Synthesis Engine

An asynchronous, database-driven pipeline to ingest, crawl, synthesize, and classify lead companies. Integrated with **MySQL** to support high-throughput, concurrent crawling of 100,000+ MSME targets.

### Processing Pipeline Workflow

```mermaid
graph TD
    A[Raw Lead Ingestion / JSON] -->|Bulk Import| B[(MySQL Database / leads)]
    B -->|Fetch Queue| C[Scraping Orchestrator]
    C -->|Rotate Proxies| D[Playwright Crawler]
    C -->|Fallback Search| E[Yahoo Search / Google Serper API]
    D & E -->|Raw Content / Snippets| F[Llama-3.3-70b-versatile / Groq API]
    F -->|Synthesized Profile| G[Classification / Sector Check]
    G -->|Update Lead Info & Flags| B
    B -->|Quality-Filtered Export| H[Final Matching JSON File]
```

### Database Schema
Results are persisted row-by-row in MySQL (`buyer_db`). Data fields use native MySQL `JSON` columns to store structured arrays:
*   `cin_number` (VARCHAR): Primary key.
*   `company_name` (VARCHAR)
*   `website`, `canonical_url` (VARCHAR)
*   `company_description` (TEXT)
*   `emails`, `phones`, `addresses`, `offerings` (JSON fields)
*   `crawl_status` (VARCHAR): Tracks queue state (`pending`, `crawled`, `synthesized`, `failed`).
*   `is_pure_software_only` (BOOLEAN): Flags strictly digital services/software companies.
*   `is_hardware_related` (BOOLEAN): Flags physical hardware, electronic, electrical, component, or machinery manufacturers/distributors.

---

## Setup & Prerequisites (MySQL Engine)

### 1. Database Configuration
Before running database operations, verify that a local MySQL server is running. Create a `.env` configuration file in the project root:
```env
DB_HOST=localhost
DB_USER=root
DB_PASSWORD=Shubh1234qwer
DB_NAME=buyer_db
DB_PORT=3306
GROQ_API_KEY=gsk_your_groq_key_here
SERPER_API_KEY=your_serper_key_here
```
*Note: The script automatically initializes the database and creates/migrates the leads table on launch.*

---

## CLI Execution Guide

The Scraping Engine `scrape_leads_scalable.py` supports modular options to manage the database queue, run batch jobs, or execute crawls:

### 1. Ingesting Raw Active Leads
Ingest MCA raw company data (e.g. from HSN/NIC filters) into the MySQL pending queue:
```bash
python scrape_leads_scalable.py --import-json all_active_leads.json
```

### 2. Ingesting/Restoring Already Scraped Leads
To skip crawling leads you have already processed, import them directly. This marks their status as `synthesized` and protects search quotas:
```bash
python scrape_leads_scalable.py --import-scraped scraped_active_leads.json
```

### 3. Check Crawl & Synthesis Queue Statistics
Check the queue status distribution in the MySQL table:
```bash
python scrape_leads_scalable.py --stats
```

### 4. Running the Scraper Queue
Process pending leads in the database using multithreaded workers. It resolves the official domain, crawls internal contact pages, and synthesizes/classifies profiles using the Groq API:
```bash
python scrape_leads_scalable.py --crawl --limit 50 --max-workers 5
```
*   `--limit`: Number of pending leads to process in this run.
*   `--max-workers`: Concurrency level (number of parallel crawling threads).
*   `--max-pages`: Maximum pages to crawl per website (default: 5).
*   `--serper-key`: Google Serper API Key (bypasses browser engine search limits).

### 5. Quality-Filtered Export to JSON
Export completed leads back into a JSON file for consumption by the matching engine. This command automatically filters out leads flagged as pure software (`is_pure_software_only = 1`) or not hardware-related (`is_hardware_related = 0`):
```bash
python scrape_leads_scalable.py --export-json finalized_hardware_leads.json --only-scraped
```

---

## Scaling to 100k+ Leads: OpenAI Batch API

To scale to 100,000+ leads cost-effectively (reducing LLM costs by 50% and avoiding Groq/live API rate-limits), use the **OpenAI Batch API** integration commands:

### 1. Export Batch Request File
Generates a `.jsonl` file containing structured prompt requests for all pending leads:
```bash
python scrape_leads_scalable.py --export-batch my_openai_batch.jsonl --limit 5000
```
Upload this `.jsonl` file to the OpenAI Batch dashboard for asynchronous execution (typically resolved under 24 hours at a 50% discount).

### 2. Import Completed Batch Response File
Once OpenAI processes the batch and you download the output `.jsonl` file, ingest the responses directly back into MySQL:
```bash
python scrape_leads_scalable.py --import-batch batch_responses_completed.jsonl
```
This updates descriptions, emails, phones, offerings, and software/hardware classification flags in MySQL and transitions their queue status to `synthesized`.

---

## Key Features & Quality Control

### 1. Boilerplate Email Filtration
To prevent database pollution from hidden theme/developer credits (e.g. `impallari@gmail.com` in Google fonts references):
- The engine uses custom heuristics (`is_valid_email` and `filter_generic_emails_if_custom_exists`) that automatically discard generic email providers (like `@gmail.com`, `@yahoo.com`, `@hotmail.com`) if one or more **custom-domain matching emails** are found on the lead's domain.

### 2. Domain Blocklists & Search Filtering
- The crawler strictly avoids scraping directories like IndiaMart, ZaubaCorp, Tofler, JustDial, LinkedIn, and Facebook.
- Official website candidates are dynamically scored using token matching against the target company name to ensure high-accuracy website discovery.

### 3. Anti-Blocking & Proxies
- Run crawls with proxy rotation using the residential proxy option:
  ```bash
  python scrape_leads_scalable.py --crawl --limit 50 --proxy-url "http://username:password@proxy.example.com:8080"
  ```
- Alternatively, save `PROXY_URL` in your `.env` configuration file to run silently.

