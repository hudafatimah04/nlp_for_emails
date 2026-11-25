# NLP team handoff

Use [main.py](../main.py) for normal operation. The [README](../README.md#commands) lists single-message, dry-run, batch, watch, and cleanup commands. The agreed database design and open decisions are in the [database integration contract](database_integration_contract.md). This document describes the payload the current NLP client actually sends.

## Version 1.0 JSON

A delivery contains one object per opportunity. All sections and listed keys are emitted except the opt-in body. Unknown values stay JSON `null`. Complete examples are available for a [synthetic Hackathon](examples/hackathon-v1.json) and a [synthetic Internship](examples/internship-v1.json).

| Section | Fields and value rules |
| --- | --- |
| Root | `schema_version` is the string `"1.0"`. |
| `opportunity` | `type` and nonempty `title` are required for an eligible record. HTTP handoff currently supports `Hackathon` and `Internship`. `description`, `organizer`, `location`, and `registration_link` are strings or null. A link must be HTTP or HTTPS. `deadline`, `start_date`, and `end_date` are real calendar dates as `YYYY-MM-DD` or null. `fee` is a nonnegative number or null. Zero means free. `team_based` is true, false, or null. |
| `eligibility` | `min_year` and `max_year` are inclusive academic-year bounds, integers 1–4 or null. `min_cgpa` is 0–10 or null. `department_scope` is `ALL`, `SPECIFIC`, or null. `eligible_departments` is an array of separate provisional department codes or null. `SPECIFIC` requires at least one code. `ALL` has no department restrictions. An unstated restriction stays null. |
| `source` | `gmail_message_id`, `message_id_header`, `sender_email`, `subject`, `received_at`, `attachment_filename`, `attachment_file_type`, `attachment_storage_path`, `attachment_extraction_status`. Metadata is string or null, with received time in ISO 8601 UTC. The pipeline requires a valid Gmail ID before processing. Attachment fields describe one cited attachment when available. `--include-body` adds `email_body` as a string or null. |
| `processing` | `classification`, `status`, boolean `requires_review`, string arrays `review_reasons` and `missing_fields`, and arrays `conflicts`, `errors`, and `warnings`. Each validation issue has `field` (string or null), `code`, and `message`. Each conflict has `field` and `candidates`. Candidates carry `value`, `source`, `source_id`, and `line_number` (integer or null). Text candidates can include `span` and may include `matching_line_numbers` after repeated-quote recovery. |
| `evidence` | An object keyed by supported field. Each value has `source` and `line_number` (original one-based line or null). Visual evidence does not have a verified text line. |

- **`ready`** means suitable for handoff if its type is supported and attachment processing is complete. Optional nulls and non-blocking warnings are acceptable.
- **`needs_review`** means a significant extraction issue or material conflict. The pipeline saves the record locally and holds delivery.
- **`rejected`** means a mandatory field failed validation. The pipeline saves it under the rejected output directory and holds delivery.
- **`Other`** produces no opportunity record. A successful normal run marks the source as handled locally.
- **`Event`** can be extracted for local use. Backend support is pending. Expiration, display, and student-specific eligibility filtering belong downstream.

## Connect a receiver

Set `BACKEND_MODE=http` and `BACKEND_API_URL` to the staging ingestion URL. Set `BACKEND_API_TOKEN` if the receiver uses bearer authentication. No code edits or database credentials are needed on the NLP side.

The client uses `POST` with `Content-Type: application/json`, `Idempotency-Key: <gmail_message_id>:1`, and optional `Authorization: Bearer <token>`. The client accepts HTTP 2xx. It retries connection errors and HTTP 429, 500, 502, and 503 up to three total attempts. Today a 409 is a permanent failure. Agree on duplicate and durable-acceptance responses before deployment.

Mock is the default. It writes eligible payloads to `BACKEND_OUTBOX_DIR/ready/` and records a local mock delivery. It does not call an HTTP endpoint. Switching modes does not clear the ledger.

## Test a staging endpoint with synthetic JSON

From the repo root in PowerShell, after setting the staging URL and any required bearer token in the **shell environment**, send the bundled fixture directly. This test does not invoke Gmail, the LLM, or the NLP processed store. Change both the example filename and idempotency key to test the internship. Repeat with the same key to verify receiver-side deduplication.

```powershell
$headers = @{ 'Idempotency-Key' = 'fake-hackathon-001:1' }
if ($env:BACKEND_API_TOKEN) {
    $headers.Authorization = "Bearer $env:BACKEND_API_TOKEN"
}
Invoke-RestMethod -Method Post -Uri $env:BACKEND_API_URL -Headers $headers `
    -ContentType 'application/json' -InFile 'docs/examples/hackathon-v1.json'
```

Verify that the receiver persisted one opportunity and that repeating the request does not create another. The actual HTTP receiver has not yet been verified. Attachment bytes are not uploaded and the local path is not a hosted URL. Backend owners must agree on attachment storage, database mapping, and review handling. Frontend owners should use backend data for review or publication and downstream filtering. Saved-payload recovery without another LLM request remains future work.
