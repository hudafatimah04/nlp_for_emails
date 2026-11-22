# PRODUCT REQUIREMENTS DOCUMENT (PRD)
## NLP-Based Email and Attachment Processing Module
### Campus Opportunity Information Platform

**Document purpose:** Define the requirements, architecture, scope, and implementation plan for the NLP and AI processing module of the Campus Opportunity Information Platform.

**Current development stage:** Individual academic project demonstration.

**Primary responsibility:** Gmail integration, email and attachment reading, text extraction, NLP-based opportunity identification, information extraction, validation, and structured output generation.

---

# 1. PROJECT OVERVIEW

The Campus Opportunity Information Platform is a college-focused system designed to collect, process, and distribute information about opportunities such as:

- Hackathons
- Internships

The platform aims to reduce the manual effort involved in collecting opportunity announcements, understanding their eligibility requirements, organizing their information, and making them available to eligible students.

The NLP module is responsible for automatically reading institutional opportunity emails and their attachments, extracting useful information, and converting unstructured content into structured data that can be stored in the platform's relational database.

The main input sources are:

1. Email subject.
2. Email body.
3. PDF attachments.
4. DOCX attachments.
5. Scanned PDF documents requiring OCR.

The module must understand the content of these sources and extract the information required to create an opportunity record.

# 2. CURRENT DEMO PLAN: TWO GMAIL ACCOUNTS

At the current stage, the project does not have an official institutional email address dedicated to receiving opportunity announcements.

Before presenting the project to teachers and requesting institutional adoption, a working demonstration must be developed using two existing Gmail accounts.

## 2.1 Gmail Account 1: Educational Gmail Account

This is the student's existing educational Gmail account.

Its purpose in the demonstration is to act as the sender of opportunity announcements.

The student will:

- Find selected hackathon and internship announcements.
- Prepare or forward sample opportunity emails.
- Send these emails to the second Gmail account.
- Include relevant PDF or DOCX attachments.
- Act as the teacher or institutional sender for demonstration purposes.

The application does not need to access this account.

It only needs to identify this account as the sender when reading the received message from the monitored inbox.

## 2.2 Gmail Account 2: Personal Gmail Account (Monitored Inbox)

This is the student's normal personal Gmail account.

Its purpose is to act as the temporary dedicated opportunity-receiving inbox.

The Python application will obtain authorized, read-only access to this Gmail account through the Gmail API.

It will:

- Retrieve incoming emails.
- Read email subjects and bodies.
- Identify the sender.
- Retrieve email attachments.
- Extract text from supported documents.
- Pass the extracted content to the NLP pipeline.
- Process the opportunity information.
- Generate structured JSON output.

For the demonstration, the student will send selected opportunity announcements from the educational account to this personal Gmail account.

**Important:** The application must monitor the receiving account, not the sending account.

## 2.3 Future Institutional Implementation

If the teachers approve the demonstration, the institution can create a dedicated Gmail or Google Workspace email address for receiving hackathon and internship announcements.

For example:

opportunities@college.edu

The workflow will then become:

1. Teachers send opportunity announcements to the institutional opportunity email address.
2. The NLP application accesses that mailbox through authorized Gmail API access.
3. Incoming emails and attachments are retrieved.
4. The same extraction and NLP pipeline processes the content.
5. Structured opportunity information is sent to the platform backend.
6. The opportunity is stored in the database.

The future institutional email address will replace the temporary personal Gmail account.

The NLP pipeline should be designed so that changing the monitored mailbox does not require rewriting the entire processing system.

# 3. PROBLEM STATEMENT

Colleges frequently receive hackathon and internship announcements through institutional email accounts.

These announcements may contain important information distributed across:

- Email subject lines.
- Email body text.
- Attached PDF brochures.
- Word documents.
- Scanned documents.

The information is generally unstructured and may be presented in different formats.

Manually reading every email, opening attachments, identifying eligibility conditions, and entering the details into a database is time-consuming.

The proposed solution is an automated email and document processing module that reads opportunity announcements, extracts relevant information using NLP techniques, validates the extracted data, and prepares it for database storage.

# 4. OBJECTIVES

The primary objectives of this module are:

1. Integrate with a Gmail account using secure authorization.
2. Automatically retrieve incoming opportunity-related emails.
3. Extract email subject, sender, body, and received timestamp.
4. Download supported email attachments.
5. Extract text from PDF and DOCX files.
6. Apply OCR to scanned PDF documents.
7. Identify whether an email describes a hackathon or internship.
8. Extract relevant opportunity details from email and attachment content.
9. Identify eligibility conditions, including academic year, CGPA, and eligible departments.
10. Convert extracted information into structured JSON.
11. Validate extracted information and handle missing or ambiguous values.
12. Preserve the original email and attachment metadata for traceability.
13. Prepare the extracted data for integration with the platform backend and relational database.

# 5. SCOPE OF THE NLP MODULE

## 5.1 Features Included in the Current Development

### A. Gmail Integration

- Authenticate with Gmail using Google OAuth 2.0.
- Request read-only mailbox permissions.
- Access the monitored Gmail inbox.
- Retrieve incoming messages.
- Read sender, subject, body, message identifier, and received timestamp.
- Retrieve email attachments.
- Support periodic checking for new emails.
- Avoid processing the same email repeatedly.

### B. Email Content Extraction

- Read plain-text email bodies.
- Extract readable text from HTML email bodies.
- Clean unnecessary formatting and whitespace.
- Preserve the original email metadata.
- Combine useful information from the email body with information extracted from attachments.

### C. Attachment Processing

Initial supported document formats:

- PDF
- DOCX

PDF processing:
- Extract selectable text using a PDF extraction library.
- Detect documents with little or no extractable text.
- Use OCR for scanned PDF pages.
- Preserve the extracted text for NLP processing.

DOCX processing:
- Extract paragraphs and relevant text from Word documents.

Unsupported formats should be identified and reported rather than silently ignored.

### D. NLP Processing

The NLP component must:

- Identify whether the content describes a hackathon or internship.
- Distinguish opportunity-related announcements from unrelated emails.
- Extract the opportunity title.
- Identify the organizer.
- Extract deadlines and event dates.
- Identify location and registration links.
- Extract fees where available.
- Identify whether participation is team-based.
- Extract eligibility conditions.
- Identify academic year requirements.
- Identify minimum CGPA requirements.
- Extract eligible department names or codes.

### E. Structured Data Generation

The extracted information must be converted into structured JSON.

The JSON structure must align with the existing relational database schema.

### F. Validation and Error Handling

The system must:

- Detect missing fields.
- Identify invalid or ambiguous dates.
- Detect conflicting information between the email and its attachment.
- Avoid inventing missing information.
- Represent unavailable values as null.
- Identify unsupported attachments.
- Report extraction errors.
- Preserve the source information for verification.

For uncertain or conflicting information, the system should flag the result for manual review rather than silently making assumptions.

## 5.2 Features Outside the Current NLP Responsibility

The following are outside the immediate scope of this module:

- Frontend development.
- Student authentication.
- Student profile management.
- Student opportunity dashboards.
- Student notifications.
- Student-to-student messaging.
- Team formation features.
- Storage of team compositions or team member lists.
- Database administration and complete backend development.
- Institutional Gmail account administration.

The NLP module must prepare the extracted information for the backend, but it does not own the implementation of these other platform features.

# 6. FUNCTIONAL REQUIREMENTS

## FR-01: Gmail Authentication

The system must authenticate with the monitored Gmail account through Google OAuth 2.0.

It must use the Gmail API with the gmail.readonly scope.

The application must not require or store the Gmail account password.

OAuth credentials and refresh tokens must be stored securely and must not be exposed in source code or logs.

## FR-02: Email Retrieval

The application must retrieve incoming messages from the monitored Gmail inbox.

For each message, it must obtain:

- Gmail message identifier.
- Sender email address.
- Subject.
- Email body.
- Received timestamp.
- Attachment metadata.

The application should support periodic inbox checking.

## FR-03: Email Filtering

The application should identify relevant opportunity announcements.

For the initial demonstration, an authorized sender allowlist can be configured.

The educational Gmail account used by the student will be an approved sender.

The system should also classify the email content so that unrelated messages are not incorrectly treated as opportunities.

Sender authorization and opportunity classification are separate checks.

## FR-04: Duplicate Detection

The application must not process the same email repeatedly.

The Gmail message identifier and email Message-ID header should be used as available identifiers.

The existing SOURCE_EMAIL table contains a unique message_identifier attribute for message-level duplicate detection.

## FR-05: Attachment Retrieval

The system must identify and download supported attachments from incoming emails.

It must retain:

- Original filename.
- File type.
- Source email association.
- Storage location.
- Extraction status.

## FR-06: Text Extraction

The system must extract text from:

- Email body.
- PDF documents.
- DOCX documents.
- Scanned PDF documents using OCR.

The extracted text must be made available to the NLP processing component.

## FR-07: Opportunity Classification

The NLP system must classify relevant content into:

- Hackathon.
- Internship.
- Other / Not an opportunity.

The system should not classify every incoming email as an opportunity.

## FR-08: Information Extraction

The NLP system must identify and extract the fields defined in Section 7.

Information may be present in the email body, an attachment, or both.

## FR-09: Structured Output

The NLP module must produce a JSON object containing the extracted opportunity information and source references.

## FR-10: Validation

The system must validate extracted dates, numeric values, eligibility conditions, and required information.

Missing or uncertain information must not be fabricated.

## FR-11: Backend Integration

After successful processing and validation, the module must make the structured information available to the backend through a defined interface, such as a REST API or agreed JSON handoff.

The backend is responsible for database insertion and subsequent platform operations.

# 7. INFORMATION TO BE EXTRACTED

The following fields must be identified by the NLP pipeline.

## 7.1 Opportunity Information

- opportunity_title
- opportunity_type
- description
- organizer
- deadline
- start_date
- end_date
- location
- registration_link
- fee
- team_based

## 7.2 Eligibility Information

- min_year
- max_year
- min_cgpa
- department_scope
- eligible_departments

The eligible departments must be represented as separate department values, not stored as one comma-separated database field.

For example:

CSE, ISE, ECE

must be converted into three separate department entries.

If an opportunity is open to all departments, department_scope should be ALL.

If it is restricted to selected departments, department_scope should be SPECIFIC.

## 7.3 Source Information

- source_email_id
- message_identifier
- sender_email
- subject
- email_body
- received_at
- attachment_id
- attachment_filename
- attachment_file_type
- attachment_storage_path
- extraction_status

The original email and attachment information must remain traceable to the extracted opportunity.

# 8. PROPOSED NLP PROCESSING PIPELINE

The proposed architecture is divided into independent processing stages.

## Stage 1: Gmail Authentication

Use Google OAuth 2.0 to authorize the application to read the monitored Gmail account.

Suggested technology:
- Gmail API.
- Google API Python client.
- OAuth 2.0.

## Stage 2: Email Retrieval

Retrieve new messages using the Gmail API.

Read:
- Sender.
- Subject.
- Body.
- Timestamp.
- Message identifier.
- Attachment references.

## Stage 3: Email Filtering

Check whether the email is from an authorized sender.

For the demonstration, the educational Gmail address will be configured as an approved sender.

Then inspect the content to determine whether it is related to a hackathon or internship.

## Stage 4: Attachment Download

Download supported PDF and DOCX attachments.

Save them in an organized local or configured storage location.

## Stage 5: Document Text Extraction

Suggested libraries:

- PyMuPDF for PDF text extraction.
- python-docx for DOCX extraction.
- Tesseract OCR for scanned PDFs.

The system should first attempt normal PDF text extraction.

If the document contains scanned pages without selectable text, OCR should be used.

## Stage 6: Text Preprocessing

Clean extracted text by:

- Removing unnecessary whitespace.
- Normalizing formatting.
- Handling line breaks.
- Removing irrelevant document artifacts where appropriate.
- Preserving meaningful dates, numbers, links, and eligibility statements.

## Stage 7: NLP Classification

Identify the opportunity category.

Possible approaches:
- Rule-based keyword detection.
- spaCy-based NLP processing.
- A language-model-assisted extraction approach, if appropriate and available.

The implementation should use an approach that can be explained, demonstrated, and tested academically.

## Stage 8: Information Extraction

Extract the opportunity and eligibility fields.

The email body and attachment text should be treated as complementary sources.

If the same field appears in both sources, the system should compare the values.

If they conflict, it should flag the field for review rather than automatically choosing an unsupported value.

## Stage 9: Validation

Check:
- Date formats.
- Deadline consistency.
- Numeric CGPA values.
- Academic year ranges.
- Department names and codes.
- Registration URLs.
- Missing mandatory information.

## Stage 10: JSON Generation

Convert the validated information into the agreed JSON structure.

## Stage 11: Backend Handoff

Send the JSON and associated source metadata to the backend.

The backend will map the data to the existing database tables.

# 9. EXAMPLE INPUT AND EXPECTED OUTPUT

## 9.1 Sample Incoming Email

Sender: student.education@example.com

Subject: National Hackathon 2026 – Registrations Open

Email body:

Registrations are now open for National Hackathon 2026.

Students from the 2nd, 3rd, and 4th years with a minimum CGPA of 7.0 are eligible.

Eligible departments include CSE, ISE, and ECE.

The registration deadline is 15 December 2026.

The event will take place from 20 December 2026 to 22 December 2026.

Please refer to the attached brochure for further information.

Attachment:

national_hackathon_2026.pdf

## 9.2 Expected Structured Output

```json
{
  "opportunity": {
    "title": "National Hackathon 2026",
    "type": "Hackathon",
    "description": "Registrations are open for National Hackathon 2026.",
    "organizer": null,
    "deadline": "2026-12-15",
    "start_date": "2026-12-20",
    "end_date": "2026-12-22",
    "location": null,
    "registration_link": null,
    "fee": null,
    "team_based": null
  },
  "eligibility": {
    "min_year": 2,
    "max_year": 4,
    "min_cgpa": 7.0,
    "department_scope": "SPECIFIC",
    "eligible_departments": [
      "CSE",
      "ISE",
      "ECE"
    ]
  },
  "source": {
    "sender_email": "student.education@example.com",
    "subject": "National Hackathon 2026 – Registrations Open",
    "attachment_filename": "national_hackathon_2026.pdf"
  },
  "processing": {
    "classification": "Hackathon",
    "status": "extracted",
    "requires_review": true,
    "review_reasons": [
      "Organizer, location, registration link, fee and team-based status were not identified."
    ]
  }
}
```

This is an illustrative example. In the actual implementation, the source metadata should include the Gmail message identifier and attachment identifiers.

The system must not assume that a missing value is false or that a value is available simply because it is common for that type of opportunity.

# 10. PROPOSED TECHNOLOGY STACK

The implementation is expected to use Python.

### Gmail Integration
- Gmail API.
- Google OAuth 2.0.
- Google API Python client.

### Document Processing
- PyMuPDF.
- python-docx.
- Tesseract OCR.
- pytesseract.

### NLP and Information Extraction
- spaCy.
- Python regular expressions.
- Rule-based extraction.
- Optional language-model-assisted extraction.

### Data Formatting
- Python dictionaries.
- JSON.

### Backend Communication
- REST API.
- Python requests or an appropriate backend client.

### Development and Testing
- Python virtual environment.
- VS Code.
- Git.
- Automated Python tests.

The final technology selection may be refined during implementation, but the architecture must remain modular.

# 11. PROPOSED SOFTWARE ARCHITECTURE

The codebase should separate Gmail access, document processing, NLP, validation, and output generation.

Suggested directory structure:

```text
opportunity_nlp/
│
├── gmail_integration/
│   ├── authentication.py
│   ├── email_reader.py
│   ├── email_parser.py
│   └── attachment_downloader.py
│
├── document_processing/
│   ├── pdf_extractor.py
│   ├── docx_extractor.py
│   └── ocr_processor.py
│
├── preprocessing/
│   └── text_cleaner.py
│
├── nlp/
│   ├── opportunity_classifier.py
│   ├── information_extractor.py
│   └── eligibility_extractor.py
│
├── validation/
│   └── data_validator.py
│
├── output/
│   └── json_formatter.py
│
├── integration/
│   └── backend_client.py
│
├── tests/
│   ├── test_email_reader.py
│   ├── test_document_processing.py
│   ├── test_classifier.py
│   ├── test_extraction.py
│   └── test_validation.py
│
├── config/
│   └── settings.py
│
├── main.py
├── requirements.txt
├── .env.example
└── README.md
```

The exact structure can be adjusted during implementation.

The main processing flow should be:

Gmail API → Email Reader → Attachment Downloader → Document Text Extraction → Text Preprocessing → NLP Classification → Information Extraction → Validation → JSON Formatter → Backend API.

# 12. ERROR HANDLING AND EDGE CASES

The application must consider the following situations:

1. Email contains no attachment.
2. Email contains multiple attachments.
3. Email contains an unsupported file format.
4. PDF contains selectable text.
5. PDF contains scanned images.
6. PDF contains both scanned and selectable pages.
7. Attachment is corrupted or password-protected.
8. Email body is empty.
9. Opportunity details are distributed between the body and PDF.
10. Email contains multiple opportunity announcements.
11. Email is unrelated to hackathons or internships.
12. The same email is received or encountered more than once.
13. Important fields are missing.
14. Email and PDF contain conflicting information.
15. OCR produces incomplete or incorrect text.
16. Gmail API authentication expires or access is revoked.
17. Gmail API temporarily becomes unavailable.

The application must log processing failures without exposing credentials or sensitive mailbox content.

# 13. DATABASE CONTEXT: EXISTING ER MODEL

The NLP module must work with the existing database design of the Campus Opportunity Information Platform.

The current database design contains:

- 11 entities.
- 60 attributes.
- 15 relationships.

The database is context for the NLP module. The current responsibility is to produce data that fits this schema, not to redesign the entire database.

## 13.1 STUDENT

Attributes:
- student_id (Primary Key)
- name
- email (Unique)
- password_hash
- department_code (Foreign Key)
- academic_year
- cgpa

Purpose:

Stores student information used by the platform's eligibility-matching functionality.

The NLP module does not create or manage student records.

## 13.2 DEPARTMENT

Attributes:
- department_code (Primary Key)
- department_name (Unique)

Purpose:

Stores the departments available in the institution.

The NLP module uses department codes when preparing opportunity eligibility data.

## 13.3 OPPORTUNITY

Attributes:
- opportunity_id (Primary Key)
- title
- type_id (Foreign Key)
- description
- organizer
- deadline
- start_date
- end_date
- location
- registration_link
- fee
- team_based
- source_email_id (Foreign Key)
- attachment_id (Nullable Foreign Key)

Purpose:

Stores the main opportunity information extracted from incoming emails and attachments.

This is the central entity populated using the NLP module's output.

## 13.4 OPPORTUNITY_TYPE

Attributes:
- type_id (Primary Key)
- type_name (Unique)

Allowed opportunity types:
- Hackathon
- Internship

Purpose:

Identifies the category of an opportunity.

## 13.5 ELIGIBILITY_CRITERIA

Attributes:
- eligibility_id (Primary Key)
- opportunity_id (Unique Foreign Key)
- min_year
- max_year
- min_cgpa
- department_scope

Purpose:

Stores the academic eligibility conditions associated with an opportunity.

Each opportunity has one eligibility record in the existing design.

## 13.6 ELIGIBILITY_DEPARTMENT

Attributes:
- eligibility_id (Composite Primary Key component and Foreign Key)
- department_code (Composite Primary Key component and Foreign Key)

Composite Primary Key:
(eligibility_id, department_code)

Purpose:

Stores the eligible departments for a particular opportunity's eligibility criteria.

Each eligible department is represented by a separate row.

## 13.7 SOURCE_EMAIL

Attributes:
- source_email_id (Primary Key)
- message_identifier (Unique, nullable)
- sender_email
- subject
- email_body
- received_at

Purpose:

Stores original email information for source traceability.

The Gmail message identifier or appropriate email Message-ID value should be used to help prevent duplicate processing.

## 13.8 ATTACHMENT

Attributes:
- attachment_id (Primary Key)
- source_email_id (Foreign Key)
- file_name
- file_type
- storage_path
- extraction_status

Purpose:

Stores attachment metadata and extraction status.

The actual document can be stored in configured file storage, while the database retains its metadata and storage path.

## 13.9 NOTIFICATION

Attributes:
- notification_id (Primary Key)
- student_id (Foreign Key)
- opportunity_id (Foreign Key)
- created_at
- is_read

Purpose:

Stores notifications generated for eligible students.

The NLP module does not directly manage student notifications.

## 13.10 TEAM_STATUS

Attributes:
- student_id (Composite Primary Key component and Foreign Key)
- opportunity_id (Composite Primary Key component and Foreign Key)
- status
- updated_at

Composite Primary Key:
(student_id, opportunity_id)

Purpose:

Stores each student's team-formation status for a particular opportunity.

The platform does not store actual team compositions or member lists.

The NLP module does not manage this entity.

## 13.11 TEAM_MESSAGE

Attributes:
- message_id (Primary Key)
- opportunity_id (Foreign Key)
- sender_student_id (Foreign Key to STUDENT)
- receiver_student_id (Foreign Key to STUDENT)
- message_text
- sent_at

Purpose:

Stores persistent messages exchanged between students in relation to opportunities.

The NLP module does not manage this entity.

# 14. EXISTING ER RELATIONSHIPS AND CARDINALITIES

The following relationships form the existing ER model.

1. DEPARTMENT — HAS — STUDENT: One-to-Many.
2. OPPORTUNITY_TYPE — CLASSIFIES — OPPORTUNITY: One-to-Many.
3. SOURCE_EMAIL — CONTAINS — ATTACHMENT: One-to-Many.
4. SOURCE_EMAIL — PROVIDES — OPPORTUNITY: One-to-Many.
5. ATTACHMENT — SUPPORTS — OPPORTUNITY: One-to-Many. One attachment may support multiple opportunities; an opportunity can have zero or one primary attachment.
6. OPPORTUNITY — HAS — ELIGIBILITY_CRITERIA: One-to-One.
7. ELIGIBILITY_CRITERIA — SPECIFIES — ELIGIBILITY_DEPARTMENT: One-to-Many.
8. DEPARTMENT — ALLOWED IN — ELIGIBILITY_DEPARTMENT: One-to-Many.
9. STUDENT — RECEIVES — NOTIFICATION: One-to-Many.
10. OPPORTUNITY — GENERATES — NOTIFICATION: One-to-Many.
11. STUDENT — MAINTAINS — TEAM_STATUS: One-to-Many.
12. OPPORTUNITY — HAS — TEAM_STATUS: One-to-Many.
13. STUDENT — SENDS — TEAM_MESSAGE: One-to-Many.
14. STUDENT — RECEIVES — TEAM_MESSAGE: One-to-Many.
15. OPPORTUNITY — HAS MESSAGES — TEAM_MESSAGE: One-to-Many.

The TEAM_STATUS entity resolves the conceptual many-to-many association between STUDENT and OPPORTUNITY.

The ELIGIBILITY_DEPARTMENT entity resolves the conceptual many-to-many association between ELIGIBILITY_CRITERIA and DEPARTMENT.

# 15. ER DIAGRAM CONTEXT

The following Mermaid representation provides Claude with a structural overview of the existing relational schema.

```mermaid
erDiagram

    DEPARTMENT ||--o{ STUDENT : has
    OPPORTUNITY_TYPE ||--o{ OPPORTUNITY : classifies

    SOURCE_EMAIL ||--o{ ATTACHMENT : contains
    SOURCE_EMAIL ||--o{ OPPORTUNITY : provides

    ATTACHMENT o|--o{ OPPORTUNITY : supports

    OPPORTUNITY ||--|| ELIGIBILITY_CRITERIA : has

    ELIGIBILITY_CRITERIA ||--o{ ELIGIBILITY_DEPARTMENT : specifies
    DEPARTMENT ||--o{ ELIGIBILITY_DEPARTMENT : allowed_in

    STUDENT ||--o{ NOTIFICATION : receives
    OPPORTUNITY ||--o{ NOTIFICATION : generates

    STUDENT ||--o{ TEAM_STATUS : maintains
    OPPORTUNITY ||--o{ TEAM_STATUS : has

    STUDENT ||--o{ TEAM_MESSAGE : sends
    STUDENT ||--o{ TEAM_MESSAGE : receives
    OPPORTUNITY ||--o{ TEAM_MESSAGE : has_messages

    DEPARTMENT {
        string department_code PK
        string department_name UK
    }

    STUDENT {
        int student_id PK
        string name
        string email UK
        string password_hash
        string department_code FK
        int academic_year
        float cgpa
    }

    OPPORTUNITY_TYPE {
        int type_id PK
        string type_name UK
    }

    SOURCE_EMAIL {
        int source_email_id PK
        string message_identifier UK
        string sender_email
        string subject
        text email_body
        datetime received_at
    }

    ATTACHMENT {
        int attachment_id PK
        int source_email_id FK
        string file_name
        string file_type
        string storage_path
        string extraction_status
    }

    OPPORTUNITY {
        int opportunity_id PK
        string title
        int type_id FK
        text description
        string organizer
        date deadline
        date start_date
        date end_date
        string location
        string registration_link
        decimal fee
        boolean team_based
        int source_email_id FK
        int attachment_id FK
    }

    ELIGIBILITY_CRITERIA {
        int eligibility_id PK
        int opportunity_id FK_UK
        int min_year
        int max_year
        float min_cgpa
        string department_scope
    }

    ELIGIBILITY_DEPARTMENT {
        int eligibility_id PK, FK
        string department_code PK, FK
    }

    NOTIFICATION {
        int notification_id PK
        int student_id FK
        int opportunity_id FK
        datetime created_at
        boolean is_read
    }

    TEAM_STATUS {
        int student_id PK, FK
        int opportunity_id PK, FK
        string status
        datetime updated_at
    }

    TEAM_MESSAGE {
        int message_id PK
        int opportunity_id FK
        int sender_student_id FK
        int receiver_student_id FK
        text message_text
        datetime sent_at
    }
```

This is a relational-schema overview for context. The established conceptual ER design remains the authoritative reference for entity participation and notation.

# 16. HOW THE NLP MODULE CONNECTS TO THE DATABASE

The main data flow is:

1. A new email is retrieved from Gmail.
2. Its information is stored as a SOURCE_EMAIL record.
3. Its attachments are identified and stored as ATTACHMENT records.
4. Text is extracted from the email and attachments.
5. The NLP module identifies an opportunity.
6. The extracted opportunity information is prepared for OPPORTUNITY.
7. The academic eligibility information is prepared for ELIGIBILITY_CRITERIA.
8. Eligible department codes are prepared as individual ELIGIBILITY_DEPARTMENT records.
9. The backend inserts the validated records into the database.

Important mapping rules:

- One SOURCE_EMAIL can contain multiple ATTACHMENT records.
- One SOURCE_EMAIL can provide multiple OPPORTUNITY records.
- One OPPORTUNITY has one ELIGIBILITY_CRITERIA record.
- Multiple eligible departments are stored using ELIGIBILITY_DEPARTMENT.
- SOURCE_EMAIL and ATTACHMENT preserve source traceability.

The NLP module should not introduce unnecessary database entities or change the existing schema without team agreement.

# 17. NON-FUNCTIONAL REQUIREMENTS

## Security

- Use OAuth 2.0.
- Use read-only Gmail access.
- Never store Gmail passwords.
- Protect OAuth refresh tokens.
- Keep credentials out of Git repositories.
- Avoid logging confidential email contents.

## Reliability

- Handle Gmail API failures.
- Handle unsupported and corrupted attachments.
- Avoid duplicate processing.
- Preserve original source information.
- Report failures clearly.

## Maintainability

- Keep modules independent.
- Use reusable Python functions.
- Separate configuration from processing logic.
- Provide meaningful error messages.
- Document installation and execution.

## Extensibility

The architecture must support replacing the temporary personal Gmail inbox with an institutional mailbox in the future.

It should also be possible to add more supported document types and improve extraction methods without rewriting the entire application.

## Explainability

The system should be demonstrable to teachers.

It should be possible to show:
- Which email was processed.
- Which attachments were read.
- What text was extracted.
- Which opportunity type was identified.
- Which fields were extracted.
- Which fields were missing or uncertain.
- What JSON was generated.

# 18. DEMONSTRATION AND ACCEPTANCE CRITERIA

The current project demonstration will use the two Gmail accounts.

The following tests should be completed.

### Test 1: Basic email processing

Send a hackathon email from the educational account to the monitored personal Gmail account.

Expected result:
- The application retrieves the email.
- It extracts the subject and body.
- It identifies the email as a hackathon announcement.
- It produces structured JSON.

### Test 2: Email with PDF attachment

Send an internship email with a PDF brochure.

Expected result:
- The email is retrieved.
- The PDF is downloaded.
- Text is extracted from the PDF.
- Email and attachment content are combined for NLP processing.
- Internship details are extracted.

### Test 3: Scanned PDF

Send an email containing a scanned PDF brochure.

Expected result:
- Normal text extraction is attempted.
- OCR is used when necessary.
- Extracted text is passed to the NLP pipeline.

### Test 4: Missing information

Send an email where the deadline or CGPA is not specified.

Expected result:
- The missing value is represented as null.
- The application does not invent a value.
- The missing information is identified for review.

### Test 5: Unrelated email

Send an unrelated email.

Expected result:
- It is not incorrectly saved as an opportunity.

### Test 6: Duplicate email

Run the application multiple times against the same inbox.

Expected result:
- The same Gmail message is not processed repeatedly.

### Test 7: Multiple attachments

Send an email containing multiple supported documents.

Expected result:
- Each supported attachment is retrieved and processed.
- Source relationships are preserved.

### Test 8: Conflicting information

Send an email where the body and attachment contain different deadlines.

Expected result:
- The system identifies the conflict.
- It flags the extracted opportunity for manual review.
- It does not silently select an unsupported deadline.

# 19. IMPLEMENTATION ROADMAP

Development should take place incrementally.

## Phase 1: Gmail Integration

- Configure Google Cloud project.
- Enable Gmail API.
- Configure OAuth consent and credentials.
- Authenticate with the monitored Gmail account.
- Retrieve email subject, sender, body, timestamp and message identifier.
- Test using emails sent from the educational account.

Deliverable: A working Gmail reader.

## Phase 2: Attachment Processing

- Download PDF attachments.
- Download DOCX attachments.
- Extract selectable PDF text.
- Extract DOCX text.
- Integrate OCR for scanned PDFs.

Deliverable: A working email and document text extraction module.

## Phase 3: NLP Classification

- Identify hackathon announcements.
- Identify internship announcements.
- Identify unrelated messages.
- Test classification using different sample emails.

Deliverable: Opportunity classification module.

## Phase 4: Information Extraction

- Extract opportunity details.
- Extract eligibility criteria.
- Extract department information.
- Handle missing fields.
- Combine email and attachment content.

Deliverable: Structured opportunity extraction module.

## Phase 5: Validation and JSON Generation

- Validate extracted values.
- Detect conflicting information.
- Generate schema-aligned JSON.
- Test different email and attachment formats.

Deliverable: Validated JSON output.

## Phase 6: Backend Integration

- Agree on the API contract with the backend teammate.
- Send validated JSON to the backend.
- Map fields to the existing database.
- Verify that records can be inserted and retrieved.

Deliverable: Integrated NLP processing module.

## Phase 7: Demonstration

- Prepare representative hackathon emails.
- Prepare representative internship emails.
- Include PDF and scanned PDF examples.
- Demonstrate the entire Gmail-to-JSON processing pipeline.
- Show the resulting database records if backend integration is ready.

Deliverable: Working end-to-end academic demonstration.

# 20. FINAL DEFINITION OF SUCCESS

The NLP module will be considered successful when:

1. It can securely access the monitored Gmail account.
2. It can retrieve incoming opportunity emails.
3. It can identify the sender and email metadata.
4. It can download and process PDF and DOCX attachments.
5. It can extract text from scanned PDFs using OCR.
6. It can identify hackathon and internship announcements.
7. It can extract the opportunity and eligibility fields.
8. It can identify missing and conflicting information.
9. It can generate structured JSON aligned with the existing database.
10. It can avoid duplicate processing.
11. It can preserve source email and attachment traceability.
12. It can be demonstrated using the student's two Gmail accounts.
13. Its monitored mailbox can later be replaced with an institutional opportunity email account without redesigning the NLP pipeline.

---

# 21. INSTRUCTIONS FOR CLAUDE: IMPLEMENTATION GUIDANCE

You are assisting with the development of the NLP and AI processing module of the Campus Opportunity Information Platform.

The primary task is NOT to build the entire platform.

The immediate responsibility is the Gmail-to-NLP processing pipeline.

Understand the following architecture before implementing anything:

- The educational Gmail account is the sender in the current demonstration.
- The personal Gmail account is the monitored receiving inbox.
- The Python application must access the receiving inbox using Gmail API and OAuth 2.0.
- Incoming emails and supported attachments must be retrieved and processed.
- The email body and attachment content must be used together when extracting opportunity details.
- The NLP system must classify Hackathons and Internships and extract the relevant fields.
- The extracted information must be validated and converted into JSON.
- The output must align with the existing 11-entity, 60-attribute, 15-relationship database design described above.
- The existing database schema must not be unnecessarily redesigned.
- The future dedicated institutional Gmail account must be supported by the same architecture.

**Development instructions:**

1. First inspect the available project files and existing code.
2. Explain the proposed architecture before implementation.
3. Start with Gmail OAuth authentication and email retrieval.
4. Test Gmail integration before proceeding to document processing.
5. Implement PDF, DOCX and OCR extraction separately.
6. Implement classification and information extraction incrementally.
7. Use realistic sample emails for testing.
8. Build validation and duplicate detection into the pipeline.
9. Keep credentials secure.
10. Explain each module, its purpose, dependencies and execution steps.
11. Write automated tests.
12. Do not claim that a feature works until it has actually been tested.
13. Do not invent missing opportunity details.
14. Do not introduce new database entities without discussing the change first.

The goal is to build a reliable, modular, understandable and demonstrable NLP processing system that can eventually be adopted by the institution.

**Begin by proposing the project structure and implementing Phase 1: Gmail authentication and email retrieval.**