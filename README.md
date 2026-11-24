# Opportunity NLP

Opportunity NLP reads a monitored Gmail inbox for the Campus Opportunity Information Platform. It extracts text from announcements and attachments, classifies hackathons and internships, validates the extracted fields, and produces one JSON record per opportunity. You can inspect the records locally, write them to a mock outbox, or send them to an agreed REST endpoint.

**Status:** The NLP module is complete for the academic demo. The backend, platform database, and proposed admin interface are separate work outside this repository.

## What it does

```text
Gmail inbox            gmail_integration/authentication.py and email_reader.py
  -> Email parsing    gmail_integration/email_parser.py
  -> Attachments      gmail_integration/attachment_downloader.py
  -> Extraction/OCR   document_processing/document_extractor.py and ocr_processor.py
  -> Classification   nlp/opportunity_classifier.py
  -> Field extraction nlp/extraction_pipeline.py, information_extractor.py, and eligibility_extractor.py
  -> Validation       validation/data_validator.py
  -> JSON             output/json_formatter.py
  -> Backend handoff  integration/backend_client.py
```

`main.py` provides the CLI. `pipeline/run_pipeline.py` coordinates validation, local output, handoff, and delivery tracking for `run`.

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
python -m pytest -q
```

Read the [example payload in Output format](#output-format) to see what the NLP module produces.

**Full setup with Gmail** starts below.

## Prerequisites

- Python 3.10 or newer and Git.
- Tesseract OCR with English language data for scanned PDFs. A common Windows installation path is `C:/Program Files/Tesseract-OCR/tesseract.exe`. If the executable is outside PATH, set `TESSERACT_CMD` to its full path.
- A Google account with Gmail for the monitored inbox. For the two-account demo, use a separate approved sender account.

## Setup

Clone the repository using the URL shared by the project team. The URL below is a placeholder. If you already cloned it, start from the repository root and skip the first two commands.

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

## Configuration

Run from the repository root so the example relative paths resolve there.

| Setting | Purpose | Default when unset | Example |
| --- | --- | --- | --- |
| `GMAIL_CREDENTIALS_PATH` | Desktop OAuth client JSON | Unset. Required for Gmail. | `credentials.json` |
| `GMAIL_TOKEN_PATH` | Local OAuth token file in an existing writable directory | Unset. Required for Gmail. | `token.json` |
| `ALLOWED_SENDERS` | Gmail inbox sender filter | Empty list, which allows all inbox senders | `sender-one@example.com,sender-two@example.com` |
| `ATTACHMENT_DIR` | Local download directory | Unset. Required to save supported attachments. | `attachments` |
| `TESSERACT_CMD` | Tesseract executable | Unset. OCR uses `tesseract` on PATH. | `"C:/Program Files/Tesseract-OCR/tesseract.exe"` |
| `PROCESSED_DB_PATH` | Local SQLite file for processed emails and handoff history | `processed_emails.db` | `processed_emails.db` |
| `POLL_INTERVAL_SECONDS` | Polling interval for `run --watch` | Unset. The CLI uses 60 seconds. | `30` |
| `BACKEND_API_URL` | Destination URL for HTTP handoff | Unset. Required in HTTP mode. | `https://example.com/opportunities` |
| `BACKEND_MODE` | Handoff transport, `mock` or `http` | `mock` | `mock` |
| `BACKEND_OUTBOX_DIR` | Local mock handoff destination | `backend_outbox` | `backend_outbox` |
| `BACKEND_API_TOKEN` | Optional bearer token for HTTP handoff | Unset | Leave blank in mock mode. Supply the real value through the environment for HTTP mode. |

Set several allowed senders on one line with commas and no quotes, for example `ALLOWED_SENDERS=sender-one@example.com,sender-two@example.com`. The filter uses Gmail's `from:` search against sender information from the From header. It is a demo-level control and should be strengthened for production.

## Commands

Run each subcommand as `python main.py <command>`. All six accept `--max N`, a positive integer that defaults to 10. For `run --watch`, the cap applies per poll. Use `python main.py --help` or `python main.py <command> --help` for CLI help. `-h` is equivalent to `--help`.

| Command example | Purpose and flags beyond `--max` | Marks emails processed? |
| --- | --- | --- |
| `python main.py fetch --max 10` | List message metadata. `--show-body` adds up to 500 characters of the email body to console output. | No |
| `python main.py extract --max 10` | Download supported attachments and report extraction status. `--show-text` adds up to 200 extracted characters to console output. | No |
| `python main.py classify --max 10 --dry-run` | Classify the email and each attachment. `--explain` includes per-document category scores. `--reprocess` revisits processed emails. `--dry-run` skips writes to the processed store. | Yes unless dry-run or attachment processing fails. Uses the classification as its stored status. |
| `python main.py extract-fields --max 10` | Print extracted fields and evidence per opportunity. `--explain` includes evidence snippets of up to 80 characters. | No |
| `python main.py build-json --max 10` | Validate and write local JSON, including rejected records. `--out-dir PATH` defaults to `output_json/`. `--include-body` adds `source.email_body` to files. `--reprocess` replaces existing output files for those opportunities. | No |
| `python main.py run --max 10 --dry-run` | Process through handoff. `--out-dir PATH` defaults to `output_json/`. `--include-body` adds `source.email_body` to files and handoff payloads. `--reprocess` bypasses the processed-email check and still honours the handoff ledger for the current mode. `--dry-run` reports planned actions without writing JSON, sending, or updating delivery or processed records. `--watch` repeats polling until Ctrl+C. `--interval SECONDS` overrides the watch interval. | Yes when handling completes, unless dry-run. A failed handoff leaves the email retryable. |

Dry runs can still download attachments. `classify`, `build-json`, and `run` do not print email bodies or attachment text. `extract-fields` prints extracted fields and can include short source snippets with `--explain`. The raw text previews in `fetch` and `extract` require their respective opt-in flags. For repeated polling, use `python main.py run --max 10 --watch --interval 30`.

## Output format

`build-json` and `run` produce one JSON object per opportunity, with the filename `<gmail_message_id>__<n>.json` where numbering starts at 1. One email can produce several opportunities with the same source message ID. `run` writes rejected records under the `rejected/` subdirectory of the output directory. The grouped backend envelope in the contract is a proposal.

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
    "status": "needs_review",
    "requires_review": true,
    "review_reasons": ["Missing fields: location."],
    "missing_fields": ["location"],
    "conflicts": [],
    "errors": [],
    "warnings": [
      {
        "field": "location",
        "code": "missing_recommended",
        "message": "A recommended field is missing."
      }
    ]
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
| `opportunity.type`, `title` | Strings | `Hackathon` or `Internship`, and the extracted title | Yes in rejected records |
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
| `needs_review` | Extraction or validation found a warning, conflict, missing recommended field, or other review concern. Optional-field errors can also produce this status. |
| `rejected` | The required title or type is missing or invalid. |

| Issue type | Codes emitted by the validator |
| --- | --- |
| Warnings | `missing_recommended`, `deadline_passed`, `deadline_after_start`, `unknown_department`, `scope_mismatch`, `extraction_warning` |
| Errors | `missing_required`, `invalid_title`, `invalid_type`, `invalid_text`, `invalid_date`, `end_before_start`, `invalid_url`, `invalid_fee`, `invalid_team_based`, `invalid_cgpa`, `invalid_year_range`, `invalid_departments` |

`deadline_after_start` leaves the dates intact and flags them for review. `end_before_start` clears both event dates. Conflicts and multiple-opportunity explanations can also appear as prose in `review_reasons`.

## Backend integration

- **Mock mode** writes eligible payloads to `BACKEND_OUTBOX_DIR/ready/` or `BACKEND_OUTBOX_DIR/review/` according to the review flag. This split is a placeholder for the team's pending insert-or-hold decision.
- **HTTP mode** POSTs JSON to `BACKEND_API_URL` with a 10-second timeout, `Content-Type: application/json`, and `Idempotency-Key: <gmail_message_id>:<n>`. It sends `Authorization: Bearer <token>` only if `BACKEND_API_TOKEN` is set. Redirects are disabled.
- The current client accepts `2xx`. It retries connection errors and `429`, `500`, `502`, and `503`, with at most three total attempts and one- and two-second backoff delays. Other `4xx` responses, including `409`, are permanent failures. Other unexpected statuses fail without retry. Rejected NLP records are never sent.
- `processed_emails.db` is local SQLite bookkeeping, separate from the platform database. The processed-email store skips messages already handled by Gmail ID or RFC Message-ID. The handoff ledger records successful deliveries by idempotency key and mode. On retry, a recorded delivery is skipped in the same mode. Switching modes does not require clearing the ledger. Use `run --reprocess` to revisit emails already marked processed. A failure between remote acceptance and the local write can still cause a repeat request.
- Backend duplicate handling and source-email reuse are **PENDING CONFIRMATION**. Start with the [unresolved decisions](docs/database_integration_contract.md#unresolved-decisions) and [integration checklist](docs/database_integration_contract.md#future-integration-checklist) in the [database integration contract](docs/database_integration_contract.md). The department codes in [config/departments.py](config/departments.py) are **PROVISIONAL** and must be compared with the actual `DEPARTMENT` table before integration.

## Project structure

```text
opportunity_nlp/
  config/                Environment settings, validation rules, department aliases
  docs/                  Database contract and PRD coverage audit
  document_processing/   PDF, DOCX, and OCR extraction
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

With the virtual environment active, run `python -m pytest -q`. Gmail and backend HTTP calls are mocked. The real OCR test needs a working Tesseract installation and is skipped if the executable is unavailable. The [PRD coverage audit](docs/prd_coverage.md) distinguishes offline coverage from live acceptance gaps.

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
- Separate attachments can yield separate opportunities. Several announcements inside one document are not split into separate records.
- OCR can introduce errors. The module does not reliably detect wrong-but-plausible extracted values.
- Gmail polling recovery after a prolonged outage has not been verified live. The From-header sender filter is a demo control.
- Testing-mode Gmail refresh tokens expire after about seven days. [Google OAuth token expiration](https://developers.google.com/identity/protocols/oauth2#expiration)
- Admin review and approval workflows remain proposals.

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
