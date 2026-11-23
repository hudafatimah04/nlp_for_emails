# Opportunity NLP

Python inbox reader for the Campus Opportunity Information Platform. See [PRD.md](PRD.md) for the specification.

## Setup

From the repository root in PowerShell, create a virtual environment, install dependencies, and copy the configuration template.

```powershell
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Enable the Gmail API in your Google Cloud project and create an OAuth client for a desktop app. In `.env`, set `GMAIL_CREDENTIALS_PATH` to the downloaded OAuth client JSON and `GMAIL_TOKEN_PATH` to a local token file path with an existing writable parent directory. Keep both files out of Git. Set `ALLOWED_SENDERS` to a comma-separated list of email addresses, or leave it blank to fetch from all senders in the inbox.

## Fetch recent emails

```powershell
.\venv\Scripts\python.exe main.py fetch --max 10
```

When OAuth opens your browser, sign in to the Gmail account that receives the opportunity emails. The command prints one JSON summary per message, including attachment metadata. Add `--show-body` to include the first 500 body characters in each summary.

Run the offline tests with `.\venv\Scripts\python.exe -m pytest`.

## Extract attachment text

Set `ATTACHMENT_DIR` in `.env` to a local directory such as `attachments`. Install Tesseract with English language data for scanned PDFs. If the executable is outside your PATH, set `TESSERACT_CMD` in `.env` to its full path.

```powershell
.\venv\Scripts\python.exe main.py extract --max 10
```

The command prints one JSON summary per attachment, including unsupported files and extraction failures. Add `--show-text` to include up to 200 characters of extracted text. Downloads use per-message directories and add a numeric suffix when a filename already exists.

## Classify inbox messages

```powershell
.\venv\Scripts\python.exe main.py classify --max 10
```

Classification uses the subject, email body, and extracted attachment text. Output includes the decision and matched rule terms. The local SQLite store uses `PROCESSED_DB_PATH`, which defaults to `processed_emails.db`. A match on either the Gmail ID or the RFC Message-ID skips the email. Use `--reprocess` to bypass this check or `--dry-run` to skip database writes. Dry runs can still download attachments. Messages with incomplete attachment processing remain eligible for retry.

## Project structure

- `gmail_integration/` handles Gmail access and attachments.
- `document_processing/` extracts document text.
- `nlp/` classifies opportunities.
- `storage/` records processed emails in SQLite.
- `tests/` covers the pipeline offline.
