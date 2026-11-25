# Opportunity NLP

Opportunity NLP reads a monitored Gmail inbox for the Campus Opportunity Information Platform. It extracts text from announcements and attachments, classifies hackathons, internships, and events, validates the extracted fields, and produces one JSON record per opportunity. You can inspect the records locally, write them to a mock outbox, or send them to an agreed REST endpoint.

**Handoff status:** The extraction pipeline is ready for team integration. The actual HTTP receiver, platform database, and frontend are separate work.

## What it does

```text
Gmail inbox            gmail_integration/authentication.py and email_reader.py
  -> Email parsing    gmail_integration/email_parser.py
  -> Attachments      gmail_integration/attachment_downloader.py
  -> Extraction/OCR   document_processing/document_extractor.py and ocr_processor.py
  -> LLM extraction   nlp/llm_extractor.py (Gemini or Groq, with shared evidence checks)
  -> Validation       validation/data_validator.py
  -> JSON             output/json_formatter.py
  -> Backend handoff  integration/backend_client.py
```

**`main.py` is the single entry point.** Teammates do not run individual Python modules. `main()` parses commands and creates the provider budget. For `run`, `_run_command()` selects or polls Gmail messages, `_run_email()` loads each message and opens its storage workspace, and `_build_units()` prepares the attachments and calls the shared extractor. `pipeline/run_pipeline.py::process_email()` validates, formats, saves, and hands off eligible records, then records their outcomes.

## Responsibilities

| NLP module | Backend teammate |
| --- | --- |
| Reads Gmail, extracts and structures supported opportunities, preserves source and field evidence, and flags uncertain records. Tracks processed emails and handoffs locally. | Owns REST ingestion, platform database writes, backend duplicate handling, notifications, review workflow, admin access, and the proposed admin UI. |

`processing.requires_review` is only a flag in the output. It does not assign a reviewer or start an approval workflow.

## Quick start without Gmail

From the repository root in PowerShell, install the dependencies and run the offline tests. The tests use synthetic messages and mocked service calls.

```powershell
python -m venv venv
. .\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pytest tests -q
```

Read the [example payload in Output format](#output-format) to see what the NLP module produces.

**Full setup with Gmail** starts below.

## Prerequisites

- Python 3.11 or newer and Git. Dependencies are listed in [requirements.txt](requirements.txt), including the Gmail, Gemini and Groq SDKs, PyMuPDF, Pillow, pytesseract, python-docx, Pydantic, requests, and pytest.
- Install Tesseract OCR with English data. On macOS use `brew install tesseract`; on Debian or Ubuntu use `sudo apt install tesseract-ocr tesseract-ocr-eng`. On Windows install a Tesseract distribution and add its executable folder to PATH, or set `TESSERACT_CMD` to the executable path on your machine. Check `tesseract --version` and `tesseract --list-langs` for `eng`.
- A Google account with Gmail for the monitored inbox. For the two-account demo, use a separate approved sender account.

## Setup

Clone the team repository. If you already cloned it, start at the repository root and skip the first two commands.

```powershell
git clone https://github.com/hudafatimah04/nlp_for_emails.git
Set-Location nlp_for_emails
python -m venv venv
. .\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

The shell should show `(venv)` before you run the Python commands. On macOS or Linux, use `python3 -m venv venv`, `source venv/bin/activate`, and `cp .env.example .env`. After activation, the same `python` commands apply.

## Google Cloud and Gmail API setup

1. Create or select a project in the Google Cloud console.
2. Enable the **Gmail API** in that project.
3. Configure the OAuth consent screen in Google Auth Platform. Choose **External**, keep the app in **Testing**, and add the monitored Gmail account under **Test users**.
4. Create an OAuth client with application type **Desktop app**. Download the client JSON, save it as `credentials.json` in the repository root, and configure the credentials and token paths in `.env`.
5. Run `python main.py fetch --max 1`. On first use, sign in to the monitored inbox in the browser and approve the requested access. If Google displays “Google hasn't verified this app” or a similar warning, use **Advanced** to continue only after confirming it is your own test project.

The client requests only the Gmail read-only scope `https://www.googleapis.com/auth/gmail.readonly` and stores no Google password. After consent it writes the token to `GMAIL_TOKEN_PATH`, such as `token.json` with the example settings below. An External app in Testing receives a refresh token that expires after about seven days for this scope. If reauthorisation is needed, stop the command, delete the local `token.json` at the configured path, and run `fetch` again. [Gmail quickstart](https://developers.google.com/workspace/gmail/api/quickstart/python) · [Consent and test users](https://developers.google.com/workspace/guides/configure-oauth-consent) · [Unverified-app warning](https://developers.google.com/workspace/gmail/api/troubleshoot-authentication-authorization) · [Refresh token expiration](https://developers.google.com/identity/protocols/oauth2#expiration)

## LLM API keys

Create a Gemini key through [Google AI Studio](https://aistudio.google.com/apikey) and a Groq key through the [Groq console](https://console.groq.com/keys). Put them in the ignored local `.env` as `GEMINI_API_KEY` and `GROQ_API_KEY`. Each teammate uses their own authorised credentials. The offline tests and synthetic backend examples require neither key.

## Configuration

Run from the repository root so the example relative paths resolve there.

| Setting | Purpose | Default when unset | Example |
| --- | --- | --- | --- |
| `GMAIL_CREDENTIALS_PATH` | Desktop OAuth client JSON | Unset. Required for Gmail. | `credentials.json` |
| `GMAIL_TOKEN_PATH` | Local OAuth token file in an existing writable directory | Unset. Required for Gmail. | `token.json` |
| `ALLOWED_SENDERS` | Gmail inbox sender filter | Empty list, which allows all inbox senders | `sender-one@example.com,sender-two@example.com` |
| `ATTACHMENT_DIR` | Local download directory | Unset. Required to save supported attachments. | `attachments` |
| `TESSERACT_CMD` | Tesseract executable | Unset. OCR uses `tesseract` on PATH. | Path on your machine if Tesseract is outside PATH |
| `PROCESSED_DB_PATH` | Local SQLite file for processed emails and handoff history | `processed_emails.db` | `processed_emails.db` |
| `POLL_INTERVAL_SECONDS` | Polling interval for `run --watch` | Unset. The CLI uses 60 seconds. | `30` |
| `BACKEND_API_URL` | Destination URL for HTTP handoff | Unset. Required in HTTP mode. | `https://example.com/opportunities` |
| `BACKEND_MODE` | Handoff transport, `mock` or `http` | `mock` | `mock` |
| `BACKEND_OUTBOX_DIR` | Local mock handoff destination | `backend_outbox` | `backend_outbox` |
| `BACKEND_API_TOKEN` | Optional bearer token for HTTP handoff | Unset | Leave blank in mock mode. Supply the real value through the environment for HTTP mode. |

Set several allowed senders on one line with commas and no quotes, for example `ALLOWED_SENDERS=sender-one@example.com,sender-two@example.com`. The filter uses Gmail's `from:` search against sender information from the From header. It is a demo-level control and should be strengthened for production.

## Commands

Run commands from the project root with the virtual environment active:

```text
python main.py run --message-id MESSAGE_ID
python main.py run --message-id MESSAGE_ID --dry-run --reprocess
python main.py run --max 10
python main.py run --watch --max 10 --interval 60
python main.py cleanup --dry-run
```

These cover normal single-message processing, a single-message preview, batch processing, Gmail monitoring, and cleanup preview. Replace `MESSAGE_ID` with the selected Gmail ID. A selected ID bypasses the sender-filtered inbox search. Keep `BACKEND_MODE=mock` for local integration. A dry run still calls the configured LLM provider.

Inbox commands accept `--max N` with a default of 10. In watch mode it applies per poll. Use `python main.py --help` or `python main.py <command> --help` for CLI help. `-h` is equivalent to `--help`.

| Command example | Purpose and flags beyond `--max` | Marks emails processed? |
| --- | --- | --- |
| `python main.py fetch --max 10` | List message metadata. `--show-body` adds up to 500 characters of the email body to console output. | No |
| `python main.py extract --max 10` | Download supported attachments and report extraction status. `--show-text` adds up to 200 extracted characters to console output. | No |
| `python main.py classify --max 10` | Preview the LLM classification of the email and its attachments together. `--explain` shows evidence locations. `--reprocess` revisits processed emails. | No |
| `python main.py extract-fields --max 10` | Print extracted fields and evidence per opportunity. `--explain` includes evidence snippets of up to 80 characters. | No |
| `python main.py build-json --max 10` | Validate and write local JSON, including rejected records. `--out-dir PATH` defaults to `output_json/`. `--include-body` adds `source.email_body` to files. `--reprocess` replaces existing output files for those opportunities. | No |
| `python main.py run --max 10 --dry-run` | Process through handoff. `--out-dir PATH` defaults to `output_json/`. `--include-body` adds `source.email_body` to files and handoff payloads. `--reprocess` bypasses the processed-email check and still honours the handoff ledger for the current mode. `--dry-run` reports planned actions without writing JSON, sending, or updating delivery or processed records. `--watch` polls until interrupted, stopped by quota in an explicit mode, or the command budget is spent. `--interval SECONDS` overrides the watch interval. | Yes when handling completes, unless dry-run. A failed handoff leaves the email retryable. |

Dry runs can still download attachments. `classify`, `build-json`, and `run` do not print email bodies or attachment text. `extract-fields` prints extracted fields and can include short source snippets with `--explain`. The raw text previews in `fetch` and `extract` require their respective opt-in flags. **Watch budget:** The request budget is shared by all emails and polls in the same process. It never resets each poll. With defaults, auto has up to three Gemini attempts and three Groq attempts available over the whole command. Explicit modes have up to three attempts for their selected provider. `--llm-limit` adds a combined cap. The watcher exits on command-budget exhaustion or interruption, and explicit provider modes also stop on provider quota errors. Restart it as needed. Watch mode polls a bounded recent inbox window and has no Gmail push subscription or pending-delivery replay worker.

Set `LLM_PROVIDER=auto` (the default) and configure `GEMINI_API_KEY` and `GROQ_API_KEY` in your local `.env`. Auto tries Gemini first and can use Groq after an unavailable service, timeout, quota, invalid structured response, or unsupported Gemini request size. A usable Gemini result, including Other, null optional fields, or a material conflict, stays with Gemini. Explicit `gemini` and `groq` modes use that provider alone. The default models are `gemini-3.5-flash-lite` and `qwen/qwen3.8-27b`. Dry runs still make LLM requests. The timeout defaults to 60 seconds. Set `LLM_TIMEOUT_SECONDS` to override it for both providers. Gemini still honors `GEMINI_TIMEOUT_SECONDS` when the shared setting is unset.

The NLP commands send the cleaned subject and body, extracted attachment text, and supported visuals in one request. Gemini accepts JPEG/PNG and selected scanned or layout-heavy PDFs directly. Groq sends JPEG/PNG and renders every page of a selected PDF locally, with the attachment ID and original page number. Groq allows up to three images or rendered pages per request. Local resolution and encoded size checks still apply. Groq token estimates are advisory, and Groq enforces the actual rate and context limits. Set `GROQ_MAX_COMPLETION_TOKENS` to configure the independent completion cap (default 3,072). Text PDFs and DOCX normally use local extraction. Visual evidence is model-reported and has no verified text line in JSON. Unsupported or oversized inputs are reported as incomplete.

Each provider has a command budget, set by `GEMINI_REQUEST_LIMIT` and `GROQ_REQUEST_LIMIT` (default 3 each). Set `--llm-limit N` to impose an additional combined cap. `--llm-attempts` defaults to one attempt per provider per email and accepts up to three. The `--gemini-limit` and `--gemini-attempts` aliases remain available. Auto cools down a provider after service failures or a quota response, using `LLM_PROVIDER_COOLDOWN_SECONDS` (default 60). Local rolling request guardrails use each provider's `*_REQUESTS_PER_MINUTE` and `*_REQUESTS_PER_DAY` settings. They are command-local and provider limits still apply across commands and accounts. Explicit modes stop on quota errors. For one actual attempt in a dry run, select an explicit provider and use `python main.py run --message-id MESSAGE_ID --dry-run --reprocess --llm-limit 1 --llm-attempts 1`.

`Hackathon`, `Internship`, and `Event` use the same validation, version 1.0 JSON, and handoff rules. The backend teammate must support all three types. Other emails produce no opportunity file. Use `--reprocess` to revisit Events already marked processed under the previous local-only policy.

## Output format

`build-json` and `run` produce at most one opportunity per email, with the filename `<gmail_message_id>__1.json`. Extracted values from the email and supporting attachments are combined. Conflicting values are cleared and their candidates are retained for review. `run` writes rejected records under the `rejected/` subdirectory of the output directory. The grouped backend envelope in the contract is a proposal.

### Synthetic example

```json
{
  "schema_version": "1.0",
  "opportunity": {
    "type": "Hackathon",
    "title": "Example Campus Hackathon",
    "description": "Synthetic opportunity description.",
    "organizer": "Example Computing Club",
    "deadline": "2027-01-20",
    "start_date": "2027-01-25",
    "end_date": "2027-01-26",
    "location": null,
    "registration_link": "https://example.com/register",
    "fee": 0,
    "team_based": true
  },
  "eligibility": {
    "min_year": 2,
    "max_year": 4,
    "min_cgpa": 7.0,
    "department_scope": "SPECIFIC",
    "eligible_departments": ["CSE", "ISE"]
  },
  "source": {
    "gmail_message_id": "fake-msg-001",
    "message_id_header": "<fake-msg-001@example.com>",
    "sender_email": "sender@example.com",
    "subject": "Example Campus Hackathon",
    "received_at": "2027-01-05T10:00:00+00:00",
    "attachment_filename": "example-flyer.pdf",
    "attachment_file_type": "application/pdf",
    "attachment_storage_path": "attachments/fake-msg-001/example-flyer.pdf",
    "attachment_extraction_status": "success"
  },
  "processing": {
    "classification": "Hackathon",
    "status": "ready",
    "requires_review": false,
    "review_reasons": [],
    "missing_fields": ["location"],
    "conflicts": [],
    "errors": [],
    "warnings": []
  },
  "evidence": {
    "title": {"source": "example-flyer.pdf", "line_number": 1},
    "deadline": {"source": "example-flyer.pdf", "line_number": 4}
  }
}
```

| Field | Type | Meaning | Nullable |
| --- | --- | --- | --- |
| `schema_version` | String | Current payload version, `1.0` | No |
| `opportunity.type`, `title` | Strings | `Hackathon`, `Internship`, or `Event`, and the extracted title | Yes in rejected records |
| `opportunity.description`, `organizer`, `location`, `registration_link` | Strings | Extracted description, organiser, venue or mode, and HTTP(S) URL | Yes |
| `opportunity.deadline`, `start_date`, `end_date` | Date strings | ISO `YYYY-MM-DD` | Yes |
| `opportunity.fee` | Number | Registration fee | Yes |
| `opportunity.team_based` | Boolean | Team-based or individual participation | Yes |
| `eligibility.min_year`, `max_year` | Integers | Academic year bounds | Yes |
| `eligibility.min_cgpa` | Number | Minimum GPA on the configured 0–10 scale | Yes |
| `eligibility.department_scope` | String | `ALL` or `SPECIFIC` | Yes |
| `eligibility.eligible_departments` | Array of strings | Separate canonical department codes. An empty array holds no codes. | Yes, for example after a conflict clears the value |
| `source.gmail_message_id`, `message_id_header`, `sender_email`, `subject` | Strings | Gmail identifier, RFC Message-ID, sender, and subject | Yes if unavailable |
| `source.received_at` | Timestamp string | ISO 8601 UTC received time | Yes |
| `source.attachment_filename`, `attachment_file_type`, `attachment_storage_path`, `attachment_extraction_status` | Strings | Metadata for the associated attachment | Yes, including for email-only opportunities |
| `source.email_body` | String | Email body included with `--include-body`. Omitted by default. | Yes when included |
| `processing.classification`, `status` | Strings | NLP category and validation result | Classification can be null if unavailable. Status is required. |
| `processing.requires_review` | Boolean | Flag for human review | No |
| `processing.review_reasons`, `missing_fields` | Arrays of strings | Review explanations and missing field names | No |
| `processing.conflicts` | Array of objects | Conflicting fields and their candidate values and sources | No |
| `processing.errors`, `warnings` | Arrays of objects | Issues with `field`, `code`, and `message`. `field` can be null for a general warning. | No |
| `evidence` | Object | Available field references with `source` and `line_number` in extracted text | No |

`null` means unknown or unavailable. Unknown fields use JSON null and are never represented by `"N/A"` or an empty string. A fee of `0` means free, while a null fee is unknown. A null `team_based` means unknown and `false` means individual participation. The opportunity and eligibility column names in the proposed database mapping can be checked against the contract.

| Status | Meaning |
| --- | --- |
| `ready` | Validation found no reason to flag the opportunity. |
| `needs_review` | A material conflict, significant validation issue, or explicit extraction concern requires review. Missing optional values, visual evidence, and ordinary extraction warnings do not trigger review by themselves. |
| `rejected` | The required title or type is missing or invalid. |

| Issue type | Codes emitted by the validator |
| --- | --- |
| Warnings | `deadline_passed`, `deadline_after_start`, `unknown_department`, `scope_mismatch`, `extraction_warning`. `missing_recommended` is supported if recommended fields are configured. |
| Errors | `missing_required`, `invalid_title`, `invalid_type`, `invalid_text`, `invalid_date`, `end_before_start`, `invalid_url`, `invalid_fee`, `invalid_team_based`, `invalid_cgpa`, `invalid_year_range`, `invalid_departments` |

`deadline_after_start` leaves the dates intact and flags them for review. `end_before_start` clears both event dates. Material conflicts can appear in `review_reasons`.

## Backend integration

Set `BACKEND_MODE=http` and `BACKEND_API_URL` to the agreed receiver URL. Supply `BACKEND_API_TOKEN` only when the receiver requires bearer authentication. No NLP code edits or database credentials are needed. See the [team handoff](docs/team_handoff.md) for the exact version 1.0 contract, three complete example records, and a direct synthetic HTTP test without Gmail or LLM calls.

- **Automatic handoff** sends only `ready` records. The pipeline writes `needs_review` records to local JSON and reports `held_for_review`.
- **Mock mode** writes eligible payloads to `BACKEND_OUTBOX_DIR/ready/`.
- **HTTP mode** POSTs JSON to `BACKEND_API_URL` with a 10-second timeout, `Content-Type: application/json`, and `Idempotency-Key: <gmail_message_id>:<n>`. It sends `Authorization: Bearer <token>` only if `BACKEND_API_TOKEN` is set. Redirects are disabled.
- The current client accepts `2xx`. It retries connection errors and `429`, `500`, `502`, and `503`, with at most three total attempts and one- and two-second backoff delays. Other `4xx` responses, including `409`, are permanent failures. Other unexpected statuses fail without retry. Rejected NLP records are never sent.
- `processed_emails.db` is local SQLite bookkeeping, separate from the platform database. The processed-email store skips messages already handled by Gmail ID or RFC Message-ID. The handoff ledger records successful deliveries by idempotency key and mode. On retry, a recorded delivery is skipped in the same mode. Switching modes does not require clearing the ledger. Use `run --reprocess` to revisit emails already marked processed. A failure between remote acceptance and the local write can still cause a repeat request.
- Backend duplicate handling and source-email reuse are **PENDING CONFIRMATION**. Start with the [unresolved decisions](docs/database_integration_contract.md#unresolved-decisions) and [integration checklist](docs/database_integration_contract.md#future-integration-checklist) in the [database integration contract](docs/database_integration_contract.md). The department codes in [config/departments.py](config/departments.py) are **PROVISIONAL** and must be compared with the actual `DEPARTMENT` table before integration.

## Project structure

```text
opportunity_nlp/
  config/                Environment settings, validation rules, department aliases
  docs/                  Database contract and PRD coverage audit
  document_processing/   PDF, DOCX, image OCR, and bounded ZIP extraction
  gmail_integration/     Gmail authentication, parsing, and attachment downloads
  integration/           Mock outbox and HTTP handoff
  nlp/                   Classification, extraction, and evidence
  output/                JSON formatting and local file writes
  pipeline/              End-to-end run orchestration
  preprocessing/         Reserved text-cleaner module
  sample_emails/          Synthetic email and document-text fixtures
  storage/               Local SQLite processed-email store and handoff ledger
  tests/                 Offline pytest suite
  validation/            Opportunity and eligibility validation
```

## Testing

The extractor supports PDF and DOCX documents, JPEG and PNG images through Tesseract, and ZIP attachments containing those types. ZIP processing skips nested archives and unsupported files and applies size and entry limits.

With the virtual environment active, run `python -m pytest tests -q`. Gmail and backend HTTP calls are mocked. The real OCR test needs a working Tesseract installation and is skipped if the executable is unavailable. The [PRD coverage audit](docs/prd_coverage.md) distinguishes offline coverage from live acceptance gaps.

## Demo guide

Use synthetic messages and keep `BACKEND_MODE=mock`. To reset a demo using the default paths, stop any running watcher and delete `processed_emails.db`, `backend_outbox/`, and `output_json/`. Back up any demo artifacts you want to keep before resetting. If you configured different paths, check those locations first. Deleting the SQLite file clears both the processed-email history and the local handoff ledger.

1. From the approved demo sender, send a synthetic opportunity email with a supported attachment to the monitored inbox.
2. Run `python main.py fetch --max 10` to check the inbox filter. Run `python main.py extract --max 10` to show the attachment's extraction status.
3. Run `python main.py classify --max 10 --dry-run --explain` for diagnostics without marking emails processed.
4. Run `python main.py run --max 10 --dry-run` to preview the handoff, then run `python main.py run --max 10` to write the JSON and populate the mock outbox.
5. Run `python main.py run --max 10` again against the same inbox without `--reprocess`. Check the `skipped_as_duplicates` tally and that previously processed records are not handed off again.

## Security

Keep credentials in environment variables or ignored local files. Git ignores `.env`, `credentials.json`, `token.json`, `attachments/`, `output_json/`, `backend_outbox/`, and `*.db`. Custom output or credential locations may require separate protection. Never commit real email data, downloaded attachments, tokens, or student information.

The NLP module never needs database credentials. Operational logs and default run summaries exclude email bodies, attachment text, and tokens. Explicit preview flags and body-inclusive JSON output can expose source data to the person running the command. Keep those artifacts private.

## Known limitations

- Department codes are provisional. Backend duplicate handling and source-row reuse are pending.
- The pipeline produces at most one opportunity per email. It sends related attachments together for one semantic decision.
- OCR can introduce errors. The module does not reliably detect wrong-but-plausible extracted values.
- Gmail polling recovery after a prolonged outage has not been verified live. The From-header sender filter is a demo control.
- Testing-mode Gmail refresh tokens expire after about seven days. [Google OAuth token expiration](https://developers.google.com/identity/protocols/oauth2#expiration)
- The actual HTTP receiver has not been verified. Attachment bytes are not uploaded. Replaying a saved payload without repeating LLM extraction and building the admin review workflow remain integration work.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| `ModuleNotFoundError` | Activate the virtual environment and confirm the prompt shows `(venv)`. Then run `python -m pip install -r requirements.txt`. |
| Google login says access denied | Make sure the monitored account is listed as a test user for the External app and is the account selected in the browser. |
| Token expired or was revoked | Stop the watcher, delete the file at `GMAIL_TOKEN_PATH`, and run `python main.py fetch --max 1` to reauthorise. |
| Tesseract not found | Install Tesseract with English language data. Add it to PATH or set `TESSERACT_CMD` to the executable's full path. |
| Every email says `skipped (already processed)` | That is the `classify` duplicate-skip message. If you want to inspect those emails again without writing processed status, use `python main.py classify --reprocess --dry-run`. |
| `already_delivered` after `--reprocess` | The handoff ledger already has that opportunity in the current mode. Reprocessing bypasses only the processed-email skip check. |
| PowerShell cannot find `run` | Use `python main.py run` from the repository root with the virtual environment active. |

### Local artifact retention

Run `python main.py cleanup --dry-run` to list eligible files and their size.
Run with `--apply` to remove the listed classes of expired managed artifacts.
Preview downloads use isolated workspaces under `runtime_storage/` and are
cleaned up when the operation exits. Completed pre-delivery failures are
eligible after `FAILED_RETENTION_DAYS` (default 30). Managed temporary
artifacts eligible for a later sweep use `TEMP_RETENTION_HOURS` (default 24).
Provider telemetry for processing commands rotates at approximately 1 MB per
file, keeping up to three rotated copies per command. Closed telemetry logs
are eligible after `LOG_RETENTION_DAYS` (default 14).

The cleanup command preserves historical folders, unfinished workspaces
without an ownership manifest, review artifacts, and files associated with
pending or accepted deliveries. HTTP acceptance alone does not confirm
persistence or that the backend can access attachments independently.
