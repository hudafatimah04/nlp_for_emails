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
