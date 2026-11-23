# NLP to backend database contract

## Agreed database design (not a proposal)

This is the existing agreed database design for the Campus Opportunity Information Platform. The NLP module's integration boundary is the backend REST API under PRD FR-11. The backend resolves references and owns database writes.

The attribute and key inventory below follows [PRD.md Section 13](../PRD.md#13-database-context-existing-er-model). A dash means Section 13 lists no separate constraint of that kind. The two explicitly nullable attributes are `SOURCE_EMAIL.message_identifier` and `OPPORTUNITY.attachment_id`. Other physical nullability decisions are **PENDING CONFIRMATION** where the PRD is silent.

| Entity | Attributes | Primary key | Foreign keys | Separate unique constraints |
| --- | --- | --- | --- | --- |
| `STUDENT` | `student_id`, `name`, `email`, `password_hash`, `department_code`, `academic_year`, `cgpa` | `student_id` | `department_code` → `DEPARTMENT.department_code` | `email` |
| `DEPARTMENT` | `department_code`, `department_name` | `department_code` | — | `department_name` |
| `OPPORTUNITY` | `opportunity_id`, `title`, `type_id`, `description`, `organizer`, `deadline`, `start_date`, `end_date`, `location`, `registration_link`, `fee`, `team_based`, `source_email_id`, `attachment_id` | `opportunity_id` | `type_id` → `OPPORTUNITY_TYPE.type_id`, `source_email_id` → `SOURCE_EMAIL.source_email_id`, `attachment_id` → `ATTACHMENT.attachment_id` | — |
| `OPPORTUNITY_TYPE` | `type_id`, `type_name` | `type_id` | — | `type_name` |
| `ELIGIBILITY_CRITERIA` | `eligibility_id`, `opportunity_id`, `min_year`, `max_year`, `min_cgpa`, `department_scope` | `eligibility_id` | `opportunity_id` → `OPPORTUNITY.opportunity_id` | `opportunity_id` |
| `ELIGIBILITY_DEPARTMENT` | `eligibility_id`, `department_code` | Composite `(eligibility_id, department_code)` | `eligibility_id` → `ELIGIBILITY_CRITERIA.eligibility_id`, `department_code` → `DEPARTMENT.department_code` | — |
| `SOURCE_EMAIL` | `source_email_id`, `message_identifier`, `sender_email`, `subject`, `email_body`, `received_at` | `source_email_id` | — | `message_identifier` |
| `ATTACHMENT` | `attachment_id`, `source_email_id`, `file_name`, `file_type`, `storage_path`, `extraction_status` | `attachment_id` | `source_email_id` → `SOURCE_EMAIL.source_email_id` | — |
| `NOTIFICATION` | `notification_id`, `student_id`, `opportunity_id`, `created_at`, `is_read` | `notification_id` | `student_id` → `STUDENT.student_id`, `opportunity_id` → `OPPORTUNITY.opportunity_id` | — |
| `TEAM_STATUS` | `student_id`, `opportunity_id`, `status`, `updated_at` | Composite `(student_id, opportunity_id)` | `student_id` → `STUDENT.student_id`, `opportunity_id` → `OPPORTUNITY.opportunity_id` | — |
| `TEAM_MESSAGE` | `message_id`, `opportunity_id`, `sender_student_id`, `receiver_student_id`, `message_text`, `sent_at` | `message_id` | `opportunity_id` → `OPPORTUNITY.opportunity_id`, `sender_student_id` and `receiver_student_id` → `STUDENT.student_id` | — |

## Schema authority and participation

[PRD.md](../PRD.md) Sections 13–15 are the only schema source in this repository. The conceptual ER diagram is authoritative for participation and notation. Section 15's Mermaid symbols show the multiplicities below. Any separate total or partial participation constraint, or database enforcement mechanism beyond what Sections 13–15 specify, is **PENDING CONFIRMATION**.

| Relationship | Right-side rows per left-side row | Left-side rows per right-side row |
| --- | --- | --- |
| DEPARTMENT to STUDENT | 0..many | 1 |
| OPPORTUNITY_TYPE to OPPORTUNITY | 0..many | 1 |
| SOURCE_EMAIL to ATTACHMENT | 0..many | 1 |
| SOURCE_EMAIL to OPPORTUNITY | 0..many | 1 |
| ATTACHMENT to OPPORTUNITY | 0..many | 0..1 |
| OPPORTUNITY to ELIGIBILITY_CRITERIA | 1 | 1 |
| ELIGIBILITY_CRITERIA to ELIGIBILITY_DEPARTMENT | 0..many | 1 |
| DEPARTMENT to ELIGIBILITY_DEPARTMENT | 0..many | 1 |
| STUDENT to NOTIFICATION | 0..many | 1 |
| OPPORTUNITY to NOTIFICATION | 0..many | 1 |
| STUDENT to TEAM_STATUS | 0..many | 1 |
| OPPORTUNITY to TEAM_STATUS | 0..many | 1 |
| STUDENT to TEAM_MESSAGE, sender | 0..many | 1 |
| STUDENT to TEAM_MESSAGE, receiver | 0..many | 1 |
| OPPORTUNITY to TEAM_MESSAGE | 0..many | 1 |

## Department mapping (PROVISIONAL)

The aliases below come from [config/departments.py](../config/departments.py). The canonical codes are **PROVISIONAL** until compared with the actual Supabase `DEPARTMENT` table. That comparison is **PENDING CONFIRMATION**.

| Canonical code | Configured aliases |
| --- | --- |
| `CSE` | computer science and engineering, computer science & engineering, computer science |
| `ISE` | information science and engineering, information science & engineering, information science |
| `ECE` | electronics and communication engineering, electronics & communication engineering |
| `EEE` | electrical and electronics engineering, electrical & electronics engineering |
| `ME` | MECH, mechanical engineering |
| `CV` | CE, CIVIL, civil engineering |
| `AIML` | AI/ML, AI & ML, artificial intelligence and machine learning |
| `IT` | information technology |
| `DS` | data science |
| `BT` | biotechnology |

Canonical codes sent by NLP must match `DEPARTMENT.department_code` exactly. Reject or hold records containing unknown codes. Never silently change or invent codes, and never auto-create missing departments.

## Entities not populated by NLP

The backend and application own these four entities. The NLP module never creates or populates their rows.

| Entity | Relationship to opportunities |
| --- | --- |
| `STUDENT` | Has no direct foreign key to `OPPORTUNITY`. Student department, academic year, and CGPA provide inputs for eligibility matching. Notifications, team status, and team messages reference students in the context of an opportunity. |
| `NOTIFICATION` | References the recipient `STUDENT` and the associated `OPPORTUNITY`. |
| `TEAM_STATUS` | Records a student's team-formation status for an opportunity, keyed by the student and opportunity pair. |
| `TEAM_MESSAGE` | References an opportunity and two student roles, sender and receiver. |

The notification trigger mechanism, whether backend code, a database function, or another mechanism, is **PENDING CONFIRMATION**. The rules and execution point for matching opportunity criteria to students are also **PENDING CONFIRMATION**.

## Backend write rules

- One email that produces **N opportunities** uses one shared `SOURCE_EMAIL` row, **N `OPPORTUNITY` rows**, and one `ELIGIBILITY_CRITERIA` row for each opportunity, even when every eligibility field is null. Whether `department_scope` can be null is **PENDING CONFIRMATION**.
- Create `ELIGIBILITY_DEPARTMENT` rows only for `SPECIFIC` scope, one row per code. Each code must already exist in `DEPARTMENT`. The backend must reject or hold a record with an unknown code and must never auto-create a department.
- NLP sends the type name. The backend resolves it against `OPPORTUNITY_TYPE.type_name` and stores the corresponding `type_id`. The allowed names are `Hackathon` and `Internship`.
- `OPPORTUNITY.attachment_id` is null for an email-only opportunity. For an attachment-based opportunity, set it to the backend ID of its source attachment.
- Store an `ATTACHMENT` row for each attachment classified `Other` for traceability, with no linked opportunity. The decision to retain `SOURCE_EMAIL` for emails that produce no opportunities is **PENDING CONFIRMATION**. That decision must account for the required source email link on any retained attachment.
- The backend must choose whether `SOURCE_EMAIL.message_identifier` holds the Gmail message ID or RFC Message-ID. This is **PENDING CONFIRMATION**. NLP's local `processed_emails.db` checks both identifiers and is separate from the backend's unique constraint.
- Preserve `fee: null` as unknown and `fee: 0` as free. Preserve `team_based: null` as unknown and `team_based: false` as explicitly individual participation.
- Database range checks for `min_year`, `max_year`, and `min_cgpa` are **PENDING CONFIRMATION**. How `STUDENT.password_hash` relates to Supabase Auth is **PENDING CONFIRMATION** for the backend developer.

## Current NLP output vs proposed backend request

The current NLP extraction command emits one JSON object per opportunity, with `opportunity`, `eligibility`, `source`, `processing`, and `evidence` sections. The final formatter must preserve that per-opportunity structure unless the team agrees on a later integration design.

The example below shows a **PROPOSED** future backend request with `source`, `attachments[]`, and `opportunities[]`. The request shape is **PENDING CONFIRMATION**. The mapping table uses envelope paths. For corresponding per-opportunity fields, drop the `opportunities[].` prefix. Envelope-only fields such as `attachments[]` and `attachment_ref` need the agreed integration design. In current output, `source.attachment_filename` is the original filename.

## Field mappings

Types for database fields follow Section 15's logical types. **Pending** in the nullable column means **PENDING CONFIRMATION** because Sections 13–15 leave physical column nullability unspecified. JSON nulls for unknown extracted values must retain that meaning. Required relationship references below describe the documented relationship or key.

| NLP field | Table.column | Type | Nullable | Notes |
| --- | --- | --- | --- | --- |
| `source.gmail_message_id` | `SOURCE_EMAIL.message_identifier` | string | Yes | Candidate identifier. Backend selection pending. |
| `source.message_id_header` | `SOURCE_EMAIL.message_identifier` | string | Yes | Alternative RFC Message-ID. Store only the agreed identifier in this unique column. |
| `source.sender_email` | `SOURCE_EMAIL.sender_email` | string | Pending | Parsed sender address. |
| `source.subject` | `SOURCE_EMAIL.subject` | string | Pending | Original subject when available. |
| `source.email_body` | `SOURCE_EMAIL.email_body` | text | Pending | Source metadata available from the parser. Transfer and retention policy pending. |
| `source.received_at` | `SOURCE_EMAIL.received_at` | datetime | Pending | ISO 8601 UTC in JSON. |
| `attachments[].filename` | `ATTACHMENT.file_name` | string | Pending | Original filename. Filenames may repeat. |
| `attachments[].file_type` | `ATTACHMENT.file_type` | string | Pending | Attachment file type. |
| `attachments[].storage_path` | `ATTACHMENT.storage_path` | string | Pending | Agreed storage location. Path ownership and format pending. |
| `attachments[].extraction_status` | `ATTACHMENT.extraction_status` | string | Pending | Extraction outcome. |
| `attachments[].classification` | NLP-side only / no DB column | string | No in example | `Other` still produces an attachment row. Classification and extraction status are separate. |
| `attachments[].ref` | Request-only / no DB column | string | No in example | Proposed reference unique within the request. |
| `opportunities[].attachment_ref` | `OPPORTUNITY.attachment_id` | int FK | Yes | Resolve the request reference to the inserted attachment row. Null for email-only. |
| `source.attachment_filename` in current per-opportunity output | `ATTACHMENT.file_name` | string | Pending | Source label only. Use a request-local reference to link rows when filenames repeat. |
| `opportunities[].opportunity.type` | `OPPORTUNITY_TYPE.type_name` → `OPPORTUNITY.type_id` | string → int FK | No for relationship | Look up the allowed type name. |
| `opportunities[].opportunity.title` | `OPPORTUNITY.title` | string | Pending | Extracted title. |
| `opportunities[].opportunity.description` | `OPPORTUNITY.description` | text | Pending | Extracted description. |
| `opportunities[].opportunity.organizer` | `OPPORTUNITY.organizer` | string | Pending | Extracted organizer. |
| `opportunities[].opportunity.deadline` | `OPPORTUNITY.deadline` | date | Pending | ISO `YYYY-MM-DD` in JSON. |
| `opportunities[].opportunity.start_date` | `OPPORTUNITY.start_date` | date | Pending | ISO `YYYY-MM-DD` in JSON. |
| `opportunities[].opportunity.end_date` | `OPPORTUNITY.end_date` | date | Pending | ISO `YYYY-MM-DD` in JSON. |
| `opportunities[].opportunity.location` | `OPPORTUNITY.location` | string | Pending | Extracted venue, location, or mode. |
| `opportunities[].opportunity.registration_link` | `OPPORTUNITY.registration_link` | string | Pending | Extracted URL. |
| `opportunities[].opportunity.fee` | `OPPORTUNITY.fee` | decimal | Pending | Null means unknown. Zero means free. |
| `opportunities[].opportunity.team_based` | `OPPORTUNITY.team_based` | boolean | Pending | Null means unknown. False means individual. |
| `opportunities[].eligibility.min_year` | `ELIGIBILITY_CRITERIA.min_year` | int | Pending | Range rules pending. |
| `opportunities[].eligibility.max_year` | `ELIGIBILITY_CRITERIA.max_year` | int | Pending | Range rules pending. |
| `opportunities[].eligibility.min_cgpa` | `ELIGIBILITY_CRITERIA.min_cgpa` | float | Pending | Range rules pending. |
| `opportunities[].eligibility.department_scope` | `ELIGIBILITY_CRITERIA.department_scope` | string | Pending | `ALL`, `SPECIFIC`, or unknown in NLP. |
| `opportunities[].eligibility.eligible_departments[]` | `ELIGIBILITY_DEPARTMENT.department_code` | string PK component, FK | No for key | One row per existing code under `SPECIFIC`. |
| `opportunities[].processing.classification` | NLP-side only / no DB column | string | No in opportunity output | Mirrors `opportunity.type` for an opportunity. |
| `opportunities[].processing.requires_review` | NLP-side only / no DB column | boolean | No in example | Insert or hold policy pending. |
| `opportunities[].processing.review_reasons` | NLP-side only / no DB column | array of strings | No in example | Reasons a human may need to review the result. |
| `opportunities[].processing.missing_fields` | NLP-side only / no DB column | array of strings | No in example | Names of null extracted fields. |
| `opportunities[].processing.conflicts` | NLP-side only / no DB column | array of objects | No in example | Field and candidate values with sources. |
| `opportunities[].processing.warnings` | NLP-side only / no DB column | array of strings | No in example | Extraction warnings. |
| `opportunities[].evidence` | NLP-side only / no DB column | object | No in example | Per-field source and line number. |

The backend supplies row IDs for `SOURCE_EMAIL`, `ATTACHMENT`, `OPPORTUNITY`, and `ELIGIBILITY_CRITERIA`. It links `ATTACHMENT.source_email_id` and `OPPORTUNITY.source_email_id` to the shared source row, `ELIGIBILITY_CRITERIA.opportunity_id` to its opportunity, and `ELIGIBILITY_DEPARTMENT.eligibility_id` to its criteria row. The last FK is part of the composite key with `department_code`.

## Supabase implementation requirements for the backend developer

Implement the 11 tables with the primary keys, foreign keys, composite keys, and unique constraints in the agreed design above. Enforce referential integrity for all listed FKs. The names in this table are the logical types in [PRD.md Section 15](../PRD.md#15-er-diagram-context). Their physical Supabase types and the final column names and nullability are **PENDING CONFIRMATION** where the PRD does not specify them.

| Table | Columns grouped by logical type |
| --- | --- |
| `STUDENT` | int: `student_id`, `academic_year`. string: `name`, `email`, `password_hash`, `department_code`. float: `cgpa`. |
| `DEPARTMENT` | string: `department_code`, `department_name`. |
| `OPPORTUNITY` | int: `opportunity_id`, `type_id`, `source_email_id`, `attachment_id`. string: `title`, `organizer`, `location`, `registration_link`. text: `description`. date: `deadline`, `start_date`, `end_date`. decimal: `fee`. boolean: `team_based`. |
| `OPPORTUNITY_TYPE` | int: `type_id`. string: `type_name`. |
| `ELIGIBILITY_CRITERIA` | int: `eligibility_id`, `opportunity_id`, `min_year`, `max_year`. float: `min_cgpa`. string: `department_scope`. |
| `ELIGIBILITY_DEPARTMENT` | int: `eligibility_id`. string: `department_code`. |
| `SOURCE_EMAIL` | int: `source_email_id`. string: `message_identifier`, `sender_email`, `subject`. text: `email_body`. datetime: `received_at`. |
| `ATTACHMENT` | int: `attachment_id`, `source_email_id`. string: `file_name`, `file_type`, `storage_path`, `extraction_status`. |
| `NOTIFICATION` | int: `notification_id`, `student_id`, `opportunity_id`. datetime: `created_at`. boolean: `is_read`. |
| `TEAM_STATUS` | int: `student_id`, `opportunity_id`. string: `status`. datetime: `updated_at`. |
| `TEAM_MESSAGE` | int: `message_id`, `opportunity_id`, `sender_student_id`, `receiver_student_id`. text: `message_text`. datetime: `sent_at`. |

- Link each opportunity to its source email. For attachment-based opportunities, resolve the optional attachment FK to a row under that same source email.
- Use exactly one eligibility criteria row per opportunity. Under `SPECIFIC` scope, represent departments as individual join rows keyed by `(eligibility_id, department_code)`.
- Reference the recipient and opportunity from each notification. In team messages, resolve the sender and receiver independently to student rows and link the message to its opportunity.
- Index scope is limited to documented PKs, unique constraints, and FK lookups. Beyond PKs, the unique keys are `STUDENT.email`, `DEPARTMENT.department_name`, `OPPORTUNITY_TYPE.type_name`, `SOURCE_EMAIL.message_identifier`, and `ELIGIBILITY_CRITERIA.opportunity_id`. The FK columns in the agreed design table are the only candidates for additional indexes in this contract. Confirm which FK lookup indexes are needed after checking existing key coverage. Those choices and any other index proposals are **PENDING CONFIRMATION**.
- Physical nullability where unspecified, FK `ON DELETE` behavior, and Supabase Auth and row-level security (RLS) choices are **PENDING CONFIRMATION**.

## Security

The NLP module sends JSON to the backend REST API and never needs database credentials. Keep credentials in environment variables and never commit them. Backend credentials, including Supabase service-role keys, belong only in backend environment variables. Keep secrets and email contents out of logs. This document must contain no real email addresses, message IDs, student information, or email text. Use placeholders or synthetic fixtures in examples.

## Example request

This proposed request groups one email, its attachments, and its opportunity results. The current extraction CLI emits one object per opportunity. The envelope makes source metadata and attachments classified `Other` available to the backend even when they have no opportunity result. Request-local references are transport metadata.

```json
{
  "source": {
    "gmail_message_id": "fake-gmail-message-001",
    "message_id_header": "<fake-message-001@example.com>",
    "sender_email": "sender@example.com",
    "subject": "Example Campus Hackathon",
    "email_body": "[Synthetic body placeholder]",
    "received_at": "2027-01-05T10:00:00Z"
  },
  "attachments": [
    {
      "ref": "attachment-1",
      "filename": "example-flyer.pdf",
      "file_type": "application/pdf",
      "storage_path": "example-storage/example-flyer.pdf",
      "extraction_status": "success",
      "classification": "Hackathon"
    },
    {
      "ref": "attachment-2",
      "filename": "example-reference.pdf",
      "file_type": "application/pdf",
      "storage_path": "example-storage/example-reference.pdf",
      "extraction_status": "success",
      "classification": "Other"
    }
  ],
  "opportunities": [
    {
      "attachment_ref": "attachment-1",
      "opportunity": {
        "type": "Hackathon",
        "title": "Example Campus Hackathon",
        "description": "[Synthetic description placeholder]",
        "organizer": "Example Student Club",
        "deadline": "2027-01-20",
        "start_date": "2027-01-25",
        "end_date": "2027-01-26",
        "location": null,
        "registration_link": "https://example.com/register",
        "fee": 0,
        "team_based": null
      },
      "eligibility": {
        "min_year": 2,
        "max_year": 4,
        "min_cgpa": 7.0,
        "department_scope": "SPECIFIC",
        "eligible_departments": ["CSE"]
      },
      "processing": {
        "classification": "Hackathon",
        "requires_review": true,
        "review_reasons": ["Location and participation format are unknown."],
        "missing_fields": ["location", "team_based"],
        "conflicts": [],
        "warnings": ["attachment example-reference.pdf ignored (not an opportunity document)"]
      },
      "evidence": {
        "title": {"source": "example-flyer.pdf", "line_number": 1},
        "deadline": {"source": "example-flyer.pdf", "line_number": 5},
        "min_cgpa": {"source": "example-flyer.pdf", "line_number": 8}
      }
    }
  ]
}
```

The example department code is illustrative and must resolve to an existing `DEPARTMENT` row before insertion.

## Future integration checklist

### Backend developer

- [ ] Export the Supabase `DEPARTMENT.department_code` values and share them for comparison with `config/departments.py`.
- [ ] Confirm physical column names, types, and nullability against the documented schema.
- [ ] Confirm the `OPPORTUNITY_TYPE` rows are exactly `Hackathon` and `Internship`.
- [ ] Agree with the NLP developer on the `message_identifier` choice, REST request shape, and attachment storage ownership.
- [ ] Agree on the policy for review-flagged records. Verify that rejected records produce no database writes.
- [ ] Verify backend field mappings and FK resolution for source emails, attachments, types, opportunities, criteria, and departments.

### NLP developer

- [ ] Compare exported department codes against `config/departments.py` and identify mismatches for team review.
- [ ] Review the agreed request shape and map each NLP field to its backend destination or NLP-only role. Verify the identifiers and department codes the backend uses for FK resolution.
- [ ] Test one email with one opportunity and one email with two opportunities.
- [ ] Test an unknown department code, a duplicate email, null fee versus zero fee, and a rejected record with the backend developer.
- [ ] Run an end-to-end test from Gmail through the backend REST API to the database and verify the resulting rows with the backend developer.

## Unresolved decisions

1. [ ] **PENDING CONFIRMATION** — Verify any participation or enforcement constraints beyond the Mermaid multiplicities against the authoritative conceptual ER diagram.
2. [ ] **PENDING CONFIRMATION** — Decide whether records with `requires_review: true` are inserted or held back.
3. [ ] **PENDING CONFIRMATION** — Decide where human review happens.
4. [ ] **PENDING CONFIRMATION** — Decide whether any review flag needs a schema change. Any change requires team agreement.
5. [ ] **PENDING CONFIRMATION** — Confirm `department_scope` nullability and the remaining physical column nullability where Sections 13–15 are silent.
6. [ ] **PENDING CONFIRMATION** — Decide whether to store `SOURCE_EMAIL` for emails without opportunities, including how required attachment links are handled.
7. [ ] **PENDING CONFIRMATION** — Choose Gmail message ID or RFC Message-ID for `SOURCE_EMAIL.message_identifier`.
8. [ ] **PENDING CONFIRMATION** — Define backend range and consistency checks for `min_year`, `max_year`, and `min_cgpa`.
9. [ ] **PENDING CONFIRMATION** — Backend developer to resolve `STUDENT.password_hash` in relation to Supabase Auth.
10. [ ] **PENDING CONFIRMATION** — Agree on the backend request shape, including whether it uses the proposed grouped envelope and how it references attachments.
11. [ ] **PENDING CONFIRMATION** — Agree on source email body transfer and retention, plus attachment storage ownership and path format.
12. [ ] **PENDING CONFIRMATION** — Compare the provisional NLP department codes and aliases against the actual Supabase `DEPARTMENT` export.
13. [ ] **PENDING CONFIRMATION** — Choose how notifications are triggered and where student eligibility matching runs. Agree on the matching rules.
14. [ ] **PENDING CONFIRMATION** — Confirm physical Supabase column names and types against the documented logical schema.
15. [ ] **PENDING CONFIRMATION** — Define FK `ON DELETE` behavior.
16. [ ] **PENDING CONFIRMATION** — Define Supabase Auth and RLS choices. The separate decision about `STUDENT.password_hash` is tracked in item 9.
17. [ ] **PENDING CONFIRMATION** — Confirm any needed FK lookup indexes after checking key coverage. Review any additional index proposals with the team.
18. [ ] **PENDING CONFIRMATION** — Confirm whether the backend will honor the `Idempotency-Key` header in the form `<gmail_message_id>:<n>`, where `n` is the opportunity's position within the email, starting at 1. If the backend does not honor the header, agree on how it will detect and reject duplicate opportunities.
19. [ ] **PENDING CONFIRMATION** — Define the response when the same source email arrives with a new opportunity. Because `SOURCE_EMAIL.message_identifier` is unique, the retry must reuse the existing `SOURCE_EMAIL` row and link the new opportunity to it instead of failing on a second source-row insert.
20. [ ] **PENDING CONFIRMATION** — Agree on the HTTP status codes for an accepted opportunity, a duplicate opportunity, and an invalid request.
