# Course Builder — Frontend Integration Guide

Response to the "Backend API Gaps" list. Each of the eight items is
answered below with the endpoint to call and the exact payload shape.

**Three of the eight already existed** and only needed to be found or
called differently. Three were genuinely missing and have been built. Two
rested on a premise that conflicts with the backend design and have been
resolved a different way — those two are the ones worth reading closely.

Every endpoint here is in Swagger at `/api/v1/docs/`.

---

## Summary

| #   | Item               | Status                      | What to do                                                   |
| --- | ------------------ | --------------------------- | ------------------------------------------------------------ |
| 1   | File upload        | **Already existed**         | Use `POST /uploads/presign/`, stop sending base64            |
| 2   | Bulk reorder       | **Built**                   | `PATCH .../modules/reorder/` and `.../lessons/reorder/`      |
| 3   | `version` field    | **Resolved differently**    | It is an FK — list `/course-versions/`, send the id          |
| 4   | Publish endpoint   | **Already existed**         | It is Admin-only; rename your button to "Submit for Review"  |
| 5   | Module lock/unlock | **Was unrouted, now fixed** | `POST .../modules/{id}/lock/` \| `/unlock/` \| `/heartbeat/` |
| 6   | Course preview     | **Built**                   | `GET /courses/{id}/preview/`                                 |
| 7   | Lesson rich text   | **Resolved differently**    | Use ContentBlocks, not `script` — mapping table below        |
| 8   | Category request   | **Built**                   | `POST /category-requests/`                                   |

---

## 1. File upload — already existed

There is no multipart endpoint and there should not be one: a 500MB video
through Django would tie up a worker and hit proxy body limits. The
existing flow uploads **browser → storage directly**, which is faster and
has no size ceiling imposed by our servers.

**Three steps:**

```js
// 1. Ask for a signed URL. Send `size` so an oversized file is rejected
//    now rather than after a long upload fails.
const presign = await api.post("/api/v1/uploads/presign/", {
  filename: file.name,
  content_type: file.type, // must be an allowed MIME type, see below
  folder: "thumbnails", // or 'courses' for lesson media
  size: file.size,
  purpose: "COURSE_THUMBNAIL",
  width: mediaWidth,
  height: mediaHeight,
});

// 2. PUT the bytes straight to storage. Note: no auth header here, the
//    signature is in the URL. Do not send this through our API.
await fetch(presign.data.upload_url, {
  method: "PUT",
  body: file,
  headers: presign.data.upload_headers,
});

// 3. Save the public object URL onto the resource.
await api.patch(`/api/v1/courses/${courseId}/`, {
  thumbnail_url: presign.data.file_url,
});
```

**Response from step 1:**

```json
{
  "upload_url": "https://...signed PUT URL...",
  "upload_headers": {
    "Content-Type": "image/png",
    "x-amz-meta-upload-purpose": "COURSE_THUMBNAIL",
    "x-amz-meta-width": "1280",
    "x-amz-meta-height": "720"
  },
  "file_url": "https://storage.../bucket/uploads/thumbnails/abc123.png",
  "file_key": "uploads/thumbnails/abc123.png",
  "expires_in": 600
}
```

**Limits and allowed types**

| Purpose | Limit | Accepted type and media requirements |
| --- | ---: | --- |
| `COURSE_THUMBNAIL` | 5 MB | JPEG/PNG, minimum 1280×720, exactly 16:9 |
| `LESSON_IMAGE` | 10 MB | JPEG/PNG/WebP/GIF |
| `LESSON_VIDEO` | 500 MB | H.264 MP4, minimum 1280×720 |
| `COURSE_PREVIEW_VIDEO` | 100 MB | H.264 MP4, minimum 1280×720, 60–120 seconds |
| `SUBTITLE` | 20 MB | `.srt` as `application/x-subrip` or `text/plain` |
| Generic document | 20 MB | `application/pdf` |

Folders now include `courses` (lesson media) and `thumbnails` (cover
images) alongside the existing ones.

**Notes**

- `upload_url` expires after **10 minutes**. Request it when the user
  picks the file, not when the form loads.
- `purpose` and `size` are required for course media. `width` and `height`
  are also required for thumbnails and videos; videos require `codec: "h264"`,
  and previews require `duration_seconds`.
- Send every returned `upload_headers` entry unchanged. Storage CORS must allow
  `Content-Type` and `x-amz-meta-*`; do not add the application auth header.
- Course fields `thumbnail_url` and `preview_video_url` are already
  writable on `PATCH /courses/{id}/`. There is also
  `POST /courses/{course_pk}/thumbnail/` if you want the dedicated route.

---

## 2. Bulk reorder — built

```
PATCH /api/v1/courses/{course_pk}/modules/reorder/
PATCH /api/v1/courses/{course_pk}/modules/{module_pk}/lessons/reorder/
```

```json
{
  "order": [
    { "id": "uuid-1", "order": 1 },
    { "id": "uuid-2", "order": 2 },
    { "id": "uuid-3", "order": 3 }
  ]
}
```

Returns **200** with the full list in its new order, so you can replace
local state from the response rather than re-fetching.

**You must list every sibling, not just the ones that moved.** A partial
list returns 400 naming the missing ids. This is deliberate: the database
enforces one item per position, and a partial reorder would collide.

Other rules:

- Order values must be distinct → 400 otherwise.
- The course must be `DRAFT` → 400 otherwise.
- Both reorder endpoints respect module edit locks → **423** if another
  user holds a lock. For module reorder that means _any_ module in the
  course; for lesson reorder, the parent module. Reordering is not a way
  around a lock.
- The whole reorder is one transaction — it fully applies or not at all,
  so a rejected request never leaves a half-sorted outline.

---

## 3. `version` — it is a foreign key, not a string

The builder's Version step assumed a free-text field. `Course.version`
points at a canonical `CourseVersion` table so every course publishes
under a shared, controlled label. Hardcoded `v1.0 / v1.1 / v2.0` options
in the client would not match real rows.

**Populate the picker from the API:**

```
GET /api/v1/course-versions/
```

```json
[{ "id": "3f2a1b4c-...", "label": "1.0", "is_active": true }]
```

Not paginated. Frozen versions are excluded, so anything returned is safe
to select.

**Then send the id:**

```
PATCH /api/v1/courses/{id}/
{ "version": "3f2a1b4c-..." }
```

The choice is honoured at publish time. If no version is set, the lowest
active label is used automatically — so the step is optional.

---

## 4. Publish — already existed, but not for creators

`POST /api/v1/courses/{id}/publish/` exists. It requires the **Admin**
role and the course must already be `APPROVED`. A creator calling it gets 403.

**Rename the builder's "Publish course" button to "Submit for Review"**
and point it at:

```
POST /api/v1/courses/{id}/submit/
```

That is your Option A, and the backend settles it — publishing is the end
of the review pipeline, not a creator action:

```
DRAFT → SUBMITTED → IN_REVIEW → QA_VERIFICATION → APPROVED → PUBLISHED
                                                             ↑ admin only
```

---

## 5. Module lock/unlock — the endpoints existed but were unreachable

`lock`, `unlock` and `heartbeat` were fully implemented, with a lock
service and expiry behind them, but the module routes were registered by
hand and the action routes were never declared. They are routed now.

```
POST /api/v1/courses/{course_pk}/modules/{id}/lock/
POST /api/v1/courses/{course_pk}/modules/{id}/unlock/
POST /api/v1/courses/{course_pk}/modules/{id}/heartbeat/
```

All three return the module, including `locked_by`, `lock_expires_at`,
`is_locked`.

**You get `heartbeat` for free — use it.** Locks expire. While a user is
actively editing a module, call `heartbeat` periodically (well inside the
expiry window) or their lock lapses mid-edit and someone else can take it.

- Locking a module someone else holds → **423 Locked**.
- `heartbeat` without holding the lock → **423 Locked**.
- `unlock` is a no-op if not locked, so it is safe to call on unmount.

---

## 6. Course preview — built

```
GET /api/v1/courses/{id}/preview/
```

```json
{
  "preview_url": "https://app.../courses/{id}/preview?token=eyJhbGciOi...",
  "expires_at": "2026-09-01T12:15:00Z",
  "expires_in": 900
}
```

The token in the URL **is** the authorisation — the link works for someone
who is not signed in, so it can be shared with a reviewer. It grants read
access to this one course and nothing else, carries no user identity, and
allows no writes.

It expires after **15 minutes**. Generate a fresh link each time the
Preview button is pressed rather than caching it. Nothing on the course
changes — the course is not made public, the link simply stops working.

---

## 7. Lesson rich text — use ContentBlocks, not `script`

`script` is **narration copy** governed by a 500–1500 word rule. Putting
editor HTML in it will fail validation and corrupts what reviewers and QA
read.

The lesson body already has a purpose-built model: `LessonContentBlock`,
with 11 block types and full CRUD. That is what the "General" panel is
meant to write to.

**Save the whole body in one request:**

```
PUT /api/v1/courses/{course_pk}/modules/{module_pk}/lessons/{lesson_pk}/content-blocks/bulk/
```

```json
[
  { "order": 1, "block_type": "HEADING_1", "text_content": "Introduction" },
  {
    "order": 2,
    "block_type": "PARAGRAPH",
    "text_content": "This lesson covers..."
  },
  { "order": 3, "block_type": "IMAGE", "media_url": "https://cdn/..." }
]
```

Full replace inside one transaction: blocks absent from the payload are
deleted, an empty list clears the body, and block ids are not stable
across saves. If any block is invalid the entire save is rejected and the
previous body survives.

**TipTap → block_type mapping**

| TipTap node       | `block_type`    | Payload field  |
| ----------------- | --------------- | -------------- |
| `heading` level 1 | `HEADING_1`     | `text_content` |
| `heading` level 2 | `HEADING_2`     | `text_content` |
| `paragraph`       | `PARAGRAPH`     | `text_content` |
| `orderedList`     | `NUMBERED_LIST` | `text_content` |
| `bulletList`      | `BULLETED_LIST` | `text_content` |
| `blockquote`      | `BLOCKQUOTE`    | `text_content` |
| `horizontalRule`  | `DIVIDER`       | _(neither)_    |
| `image`           | `IMAGE`         | `media_url`    |
| `video`           | `VIDEO`         | `media_url`    |
| iframe embed      | `EMBED`         | `media_url`    |
| quiz              | `QUIZ`          | `quiz` (id)    |

Each block carries exactly one payload: prose blocks use `text_content`,
media blocks use `media_url`, `DIVIDER` uses neither. Sending both, or
neither, is a 400.

Structured blocks are what make QA checks, search indexing and the
published snapshot work — an HTML blob would be opaque to all three.

---

## 8. Category request — built

```
POST /api/v1/category-requests/
{ "name": "Data Science", "description": "Courses about data analysis and ML." }
```

```json
{
  "id": "uuid",
  "name": "Data Science",
  "status": "PENDING",
  "resulting_category": null,
  "reviewed_at": null
}
```

`GET /api/v1/category-requests/` lists the caller's own requests; admins
see all. `description` is optional and carries onto the created category.

The category does **not** exist until an admin approves — do not add it to
any picker while `status` is `PENDING`. The creator is emailed on
approval, and `resulting_category` is populated then.

Admin-only: `POST /category-requests/{id}/approve/` (requires
`creator_price`) and `POST /category-requests/{id}/reject/`.

---

## Two things that will bite you

**Empty-state ordering.** Reorder requires the complete sibling list. If
your drag library only reports the moved item, rebuild the full array from
local state before sending.

**Lock heartbeats.** If you wire up `lock` but not `heartbeat`, editors
will silently lose their lock partway through a long edit. Call
`heartbeat` on an interval while the editor is open, and `unlock` on
close or unmount.
