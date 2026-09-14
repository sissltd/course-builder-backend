# Document Import Frontend Implementation

This is the frontend contract for the course creation method **Import from a
document**. The backend creates a document-import job from an uploaded file,
returns a detected module/lesson tree, and creates the populated Draft course
when the creator confirms the reviewed structure.

## Accepted files

| Format | Extension | MIME type |
| --- | --- | --- |
| PDF | `.pdf` | `application/pdf` |
| DOCX | `.docx` | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` |
| TXT | `.txt` | `text/plain` |
| CSV | `.csv` | `text/csv` or `application/csv` |

Maximum file size: **20MB**.

Frontend validation messages:

- No file: `Please select a document to import.`
- Unsupported format: `Unsupported document format. Upload a PDF, DOCX, TXT, or CSV file.`
- File too large: `Document uploads cannot exceed 20MB.`
- Empty file: `The selected document is empty. Upload a document with content.`

## Step 1 — Presign and upload

Request a presigned upload URL:

```http
POST /api/v1/uploads/presign/
```

```json
{
  "filename": "python-outline.csv",
  "content_type": "text/csv",
  "folder": "course-imports",
  "purpose": "COURSE_DOCUMENT_IMPORT",
  "size": 2048
}
```

Upload the file bytes to `upload_url` with the exact returned
`upload_headers`.

Important: persist and pass `file_key` to the import API. Do not use
`file_url` as the durable reference; it is a temporary signed read URL and
expires.

## Step 2 — Start import

```http
POST /api/v1/course-imports/
```

```json
{
  "file_key": "uploads/course-imports/python-outline.csv",
  "filename": "python-outline.csv",
  "content_type": "text/csv",
  "size": 2048,
  "category": "6f9619ff-8b86-d011-b42d-00cf4fc964ff",
  "topic": null,
  "title": "Python Basics from CSV",
  "description": "Imported from a document.",
  "terms_accepted": true,
  "idempotency_key": "doc-import-20260914-001"
}
```

The response is a document-import job. It may already be
`READY_FOR_REVIEW` for TXT/CSV files.

## Step 3 — Poll progress

```http
GET /api/v1/course-imports/{jobId}/
```

Poll every 2–3 seconds. Stop polling on:

- `READY_FOR_REVIEW`
- `COMPLETED`
- `FAILED`
- `CANCELLED`

Statuses:

- `QUEUED`
- `PARSING`
- `MAPPING`
- `READY_FOR_REVIEW`
- `CONFIRMING`
- `COMPLETED`
- `FAILED`
- `CANCELLED`

Show `stage` and `progress` while polling. If `warnings` is non-empty, show
them above the review tree.

## Step 4 — Review detected structure

When status is `READY_FOR_REVIEW`, render:

```json
{
  "modules": [
    {
      "title": "Getting Started",
      "description": "",
      "order": 1,
      "lessons": [
        {
          "title": "Installing Python",
          "order": 1,
          "content": "Install Python and verify it from the terminal."
        }
      ]
    }
  ]
}
```

The frontend should allow editing module and lesson titles. Content can remain
read-only in v1 and be edited later inside the builder.

Block confirm until:

- at least one module exists
- every module has a title
- every module has at least one lesson
- every lesson has a title
- every lesson has content

## Step 5 — Confirm import

```http
POST /api/v1/course-imports/{jobId}/confirm/
```

```json
{
  "structure": {
    "course": {
      "title": "Python Basics from CSV",
      "description": "Imported from a document."
    },
    "modules": [
      {
        "title": "Getting Started",
        "description": "",
        "order": 1,
        "lessons": [
          {
            "title": "Installing Python",
            "order": 1,
            "content": "Install Python and verify it from the terminal."
          }
        ]
      }
    ]
  }
}
```

Success:

```json
{
  "course_id": "3f9a2e11-6b7c-4d2a-9e5f-1c8d4a7b2f30",
  "status": "DRAFT",
  "builder_url": "/courses/3f9a2e11-6b7c-4d2a-9e5f-1c8d4a7b2f30/builder"
}
```

Redirect to the returned `builder_url` or your frontend equivalent route.

## Backend errors to display

| Situation | Message |
| --- | --- |
| Wrong format | `Unsupported document format. Upload a PDF, DOCX, TXT, or CSV file.` |
| Wrong file key | `file_key must reference a course-imports upload.` |
| Extension/type mismatch | `Filename extension does not match the uploaded document type.` |
| Missing terms | `You must accept the category Terms and Conditions.` |
| Topic mismatch | `Topic does not belong to the selected category.` |
| Duplicate import | `You already have a document import in progress. Poll that job or cancel it before starting another.` |
| Cannot read file | `We could not read this document. Please upload a valid PDF, DOCX, TXT, or CSV file.` |
| Empty readable content | `No readable content was found in this document.` |
| Bad CSV | `This CSV could not be parsed. Check the file formatting and retry.` |
| CSV no rows | `This CSV has no content rows to import.` |
| CSV headers only | `This CSV only contains headers. Add content rows and retry.` |
| Confirm too early | `Import cannot be confirmed until parsing is ready for review.` |
| Already confirmed | `This import has already been confirmed.` |
| Cancelled job | `Cancelled imports cannot be confirmed.` |
| Failed job | `Failed imports cannot be confirmed. Retry with a new upload.` |

Parser warning:

`No clear section structure was detected, so the document was imported as one module and one lesson.`
