# Frontend Upload Flow — Direct-to-Storage Presigned Uploads

This guide is the complete reference for uploading files (avatars, course
thumbnails, lesson images, lesson videos, preview videos, subtitles, PDFs,
etc.) from the frontend of this platform.

**The whole flow in one picture:**

```
  Browser                          API (Django)                        Object Storage (S3-compatible)
     │                                    │                                      │
     │  1. POST /uploads/presign/         │                                      │
     │  (filename, content_type, folder,  │                                      │
     │   size, purpose, width, height…)   │                                      │
     │ ──────────────────────────────────▶│  2. boto3 signs URLs (10 min)       │
     │                                    │ ───────────────────────────────────▶│
     │ ◀──────────────────────────────────│◀─────────────────────────────────── │
     │  { upload_url, upload_headers,     │                                      │
     │    file_url, file_key,             │                                      │
     │    expires_in: 600 }               │                                      │
     │                                    │                                      │
     │  3. PUT raw bytes to upload_url    │      file bytes never touch Django    │
     │    with upload_headers unchanged   │                                      │
     │ ─────────────────────────────────────────────────────────────────────────▶│
     │ ◀─────────────────────────────────────────────────────────────────────────│
     │  4. play via file_url (GET, 10 min)│                                      │
     │  5. persist file_key (durable)     │                                      │
     │  6. later: POST /uploads/access/   │                                      │
     │     with file_key ────────────────▶│  7. fresh signed GET URL             │
     │ ◀──────────────────────────────────│ ───────────────────────────────────▶│
```

**Why this design?** There is deliberately **no multipart upload endpoint**.
A 500 MB lesson video streamed through Django would tie up a worker process
and hit proxy body-size limits. Instead the backend never touches file
bytes — it is only a *URL broker*: it validates your request, signs a
short-lived PUT URL, and hands you a short-lived GET URL to read the file
back. Browsers upload **directly to object storage** (DigitalOcean Spaces /
any S3-compatible bucket).

---

## 1. Endpoints

All endpoints are under the `api/v1` prefix and require an authenticated
request (the app `Authorization` header) **on the JSON API calls only** —
never on the direct storage PUT.

| Method | Endpoint | Purpose | Auth |
| --- | --- | --- | --- |
| `POST` | `/api/v1/uploads/presign/` | Request signed upload + read URLs | ✅ |
| `POST` | `/api/v1/uploads/access/` | Refresh a fresh read URL from a saved `file_key` | ✅ |

There is currently **no public delete endpoint** wired to a route
(`DeleteFileSerializer` and `StorageService.delete_file()` exist server-side).
Deletion of an uploaded object is performed by the backend.

---

## 2. Step 1 — Request a presigned upload URL

Call `POST /api/v1/uploads/presign/` **at the moment the user picks the file**
— the returned URL only lives for 10 minutes.

### Request body

| Field | Type | Required? | Notes |
| --- | --- | --- | --- |
| `filename` | string ≤255 | ✅ | Original file name with extension (e.g. `lesson-1.mp4`) |
| `content_type` | string | ✅ | MIME type. Must be in the allowed list (below) |
| `folder` | string | ✅ | One of the folder choices (below). Must **match** `purpose` for course media |
| `size` | integer | For course media: ✅ | Byte count. Signed into the PUT as `Content-Length`; oversized files are rejected *before* any bytes are sent |
| `purpose` | string | For course media: ✅ | Course-media preset: `COURSE_THUMBNAIL`, `LESSON_IMAGE`, `LESSON_VIDEO`, `COURSE_PREVIEW_VIDEO`, `SUBTITLE` |
| `width` | integer | Thumbnails & videos | Declared pixel width (min 1280 for thumbnails/videos) |
| `height` | integer | Thumbnails & videos | Declared pixel height (min 720 for thumbnails/videos) |
| `codec` | string | Videos | Must be `h264` |
| `duration_seconds` | integer | Preview videos only | Must be 60–120 |

**Allowed MIME types** (generic uploads):

```
image/jpeg  image/png  image/webp  image/gif
video/mp4   video/quicktime  video/webm
application/pdf
application/x-subrip  text/plain   (.srt subtitles)
```

**Folder choices:** `profiles`, `certificates`, `videos`, `courses`,
`thumbnails`, `jobs`, `chat`, `quotations`, `general`.

> ⚠️ **The #1 mistake:** sending `folder: "videos"` for a course video.
> Course media has a strict purpose→folder mapping (see §4). For a lesson
> video the folder must be `courses`. The legacy `videos` folder is only for
> generic uploads sent **without** a `purpose`.

### Example — course lesson video

```js
const presignResponse = await api.post("/api/v1/uploads/presign/", {
  filename: file.name,
  content_type: file.type,          // "video/mp4"
  folder: "courses",                // NOT "videos"
  size: file.size,                  // required for course media
  purpose: "LESSON_VIDEO",
  width: mediaWidth,                // ≥ 1280
  height: mediaHeight,              // ≥ 720
  codec: "h264",                    // required
});
```

### Response body

```json
{
  "upload_url": "https://lon1.digitaloceanspaces.com/coursebuilder/uploads/courses/ecfcf1a69fa447018386fa9702e477f5.mp4?X-Amz-…",
  "upload_headers": {
    "Content-Type": "video/mp4",
    "x-amz-meta-upload-purpose": "LESSON_VIDEO",
    "x-amz-meta-width": "1920",
    "x-amz-meta-height": "1080",
    "x-amz-meta-codec": "h264"
  },
  "file_url": "https://lon1.digitaloceanspaces.com/coursebuilder/uploads/courses/ecfcf1a69fa447018386fa9702e477f5.mp4?X-Amz-…&X-Amz-Signature=…",
  "file_key": "uploads/courses/ecfcf1a69fa447018386fa9702e477f5.mp4",
  "expires_in": 600
}
```

| Field | What it is | Lifecycle |
| --- | --- | --- |
| `upload_url` | Presigned **PUT** URL — upload the bytes here | Expires in 10 min, single purpose |
| `upload_headers` | Headers that **must** be sent unchanged with the PUT | — |
| `file_url` | Temporary presigned **GET** URL for reading/playing the file right after upload | Expires in 10 min |
| `file_key` | **Durable** object key (`uploads/{folder}/{uuid}.{ext}`) | Permanent — **persist this** |
| `expires_in` | Seconds until the URLs expire (always 600) | — |

---

## 3. Step 2 — PUT the bytes straight to storage

```js
await fetch(upload_url, {
  method: "PUT",
  headers: upload_headers,   // exactly what the API returned
  body: file,                // the raw File/Blob
});
```

**Hard rules for this request:**

1. **Do not add the app `Authorization` header.** The signature lives in the
   URL query string (`X-Amz-Signature`). Sending extra headers that aren't in
   the signature is harmless, but anything you *change* breaks it.
2. **Send `upload_headers` unchanged.** The response headers are exactly the
   headers covered by the signature (`Content-Type` plus every
   `x-amz-meta-*` metadata value you declared). Do not rename, drop, or add
   your own `Content-Type`.
3. **Do not set a body of transformed data.** Send the original `File`/
   `Blob` so the browser's automatically-attached `Content-Length` equals the
   `size` you declared. `Content-Length` is part of the signature — if the
   real byte count differs from the declared `size`, the PUT is rejected.
4. **Don't route this through the API client.** It must go browser → storage,
   or your API base URL rewrites will strip the signed query parameters.

The declared media metadata (purpose, width, height, codec) is written to the
object as `x-amz-meta-*` tags during the PUT, so the backend can audit what
the client declared.

### Response

`fetch` resolves with **no body** on success (a `200 OK` from the storage
provider). Check `response.ok`; treat anything else as a failed upload
(see §9 troubleshooting).

---

## 4. Course media — purpose → folder rules

When `purpose` is present, the backend enforces the folder, content type,
extension, size, dimensions, codec and duration from this table:

| Purpose | Folder | Accepted types | Max size | Extra requirements |
| --- | --- | --- | ---: | --- |
| `COURSE_THUMBNAIL` | `thumbnails` | `image/jpeg`, `image/png` (`.jpg/.jpeg/.png`) | 5 MB | ≥ 1280×720, **exactly 16:9** |
| `LESSON_IMAGE` | `courses` | `image/jpeg/png/webp/gif` | 10 MB | — |
| `LESSON_VIDEO` | `courses` | `video/mp4` (`.mp4`) | 500 MB | ≥ 1280×720, codec `h264` |
| `COURSE_PREVIEW_VIDEO` | `courses` | `video/mp4` (`.mp4`) | 100 MB | ≥ 1280×720, codec `h264`, 60–120 s |
| `SUBTITLE` | `courses` | `application/x-subrip`, `text/plain` (`.srt`) | 20 MB | — |

**Rule details:**

- `purpose` is **required** whenever `folder` is `courses` or `thumbnails`.
- If `purpose` is present but its folder doesn't match, the request is
  rejected — there is no fallback folder.
- `size` is required for every purpose upload.
- Width/height are validated as **declared values** — the backend signs them
  into object metadata for later QA; it does not inspect the pixels at
  presign time. Send real values so the registration/QA flow can audit them.

**Generic (non-course) uploads** — no `purpose`, any folder, category limits:

| Category | MIME prefix | Max size |
| --- | --- | --- |
| Image | `image/*` | 10 MB |
| Video | `video/*` | 500 MB |
| Documents / text | `application/pdf`, `text/*` | 20 MB |

Note: `video/quicktime` (`.mov`) and `video/webm` are allowed *only* as
generic uploads (no `purpose`); all course purposes require H.264 MP4.

---

## 5. Steps 3–4 — Play immediately & persist the durable key

**Play right after upload (optional but fine):**

```js
videoElement.src = presignResponse.data.file_url;
```

`file_url` is a working presigned GET URL for the just-uploaded object
(HTML5 video can seek on it via Range requests). **But it expires after 10
minutes** — never store it as the permanent media reference.

**Persist the durable reference:**

```js
// Save file_key — the only value that outlives the presigned URLs.
const { file_key, file_url } = presignResponse.data;
await api.patch(`/api/v1/courses/${courseId}/`, {
  thumbnail_url: file_url,      // or store the object key as appropriate
});
```

Course fields `thumbnail_url` and `preview_video_url` are writable on
`PATCH /api/v1/courses/{id}/`; a dedicated
`POST /api/v1/courses/{course_pk}/thumbnail/` route also exists for covers.
(Internally, `file_key` is the value that should be treated as durable —
the backend can resolve a fresh URL from it at any time.)

---

## 6. Step 5 — Refresh the read URL before later playback

Because the bucket is **private**, every read goes through a fresh signed
GET URL. Whenever playback starts (or resumes) after the stored URL has
expired, mint a new one from the persisted key:

```js
const accessResponse = await api.post("/api/v1/uploads/access/", {
  file_key,                     // e.g. "uploads/courses/ecfcf1a69fa447018386fa9702e477f5.mp4"
});

videoElement.src = accessResponse.data.file_url;
// { "file_url": "https://…signed…", "expires_in": 600 }
```

The access endpoint accepts either a raw `file_key` or a full object URL
(server-side the key is extracted from the path). Response: `file_url`
(temporary GET URL) and `expires_in` (600).

**Rule of thumb:** store `file_key` at save time; call `/uploads/access/`
at *playback* time — never cache the returned URL for long.

---

## 7. Worked examples

### Avatar (generic image, no purpose)

```js
const presign = await api.post("/api/v1/uploads/presign/", {
  filename: "me.png",
  content_type: "image/png",
  folder: "profiles",
  size: file.size,
});
await fetch(presign.data.upload_url, {
  method: "PUT",
  headers: presign.data.upload_headers,
  body: file,
});
await api.patch("/api/v1/users/me/", { avatar_url: presign.data.file_key });
```

### Course cover thumbnail (16:9, ≥ 1280×720, JPEG/PNG only)

```js
const presign = await api.post("/api/v1/uploads/presign/", {
  filename: "cover.png",
  content_type: "image/png",
  folder: "thumbnails",          // purpose maps here
  size: file.size,
  purpose: "COURSE_THUMBNAIL",
  width: 1920,
  height: 1080,                  // exactly 16:9
});
await fetch(presign.data.upload_url, {
  method: "PUT",
  headers: presign.data.upload_headers,
  body: file,
});
await api.patch(`/api/v1/courses/${courseId}/`, {
  thumbnail_url: presign.data.file_url,
});
```

### Lesson video (H.264 MP4, ≤ 500 MB, ≥ 1280×720)

```js
const presign = await api.post("/api/v1/uploads/presign/", {
  filename: "lesson-4.mp4",
  content_type: "video/mp4",
  folder: "courses",             // NOT "videos"
  size: file.size,
  purpose: "LESSON_VIDEO",
  width: 1920,
  height: 1080,
  codec: "h264",
});
await fetch(presign.data.upload_url, {
  method: "PUT",
  headers: presign.data.upload_headers,
  body: file,
});

// save durable reference
await api.patch(`/api/v1/courses/${courseId}/lessons/${lessonId}/`, {
  video_key: presign.data.file_key,
});

// later, before playback:
const access = await api.post("/api/v1/uploads/access/", {
  file_key: presign.data.file_key,
});
videoElement.src = access.data.file_url;
```

### Course preview video (adds 60–120 s duration rule, 100 MB cap)

```js
const presign = await api.post("/api/v1/uploads/presign/", {
  filename: "preview.mp4",
  content_type: "video/mp4",
  folder: "courses",
  size: file.size,
  purpose: "COURSE_PREVIEW_VIDEO",
  width: 1920,
  height: 1080,
  codec: "h264",
  duration_seconds: 90,          // 60–120
});
```

### Subtitle file

```js
const presign = await api.post("/api/v1/uploads/presign/", {
  filename: "captions.srt",
  content_type: "application/x-subrip",   // or "text/plain"
  folder: "courses",
  size: file.size,
  purpose: "SUBTITLE",
});
```

---

## 8. Frontend best practices

1. **Presign when the file is picked**, not when the form/page loads — the
   URL dies after 10 minutes. If the user idles past the window, presign
   again (it's cheap).
2. **Validate early on the client** to avoid a long upload that fails at the
   end: show the size limit from §4 and reject wrong types/extensions before
   presigning. The server re-checks everything anyway.
3. **Send `size` always.** It's required for course media and lets the
   server refuse an oversized file *before* you waste a 500 MB upload.
4. **Progress & cancellation:** `fetch` PUTs don't report progress by
   default. Use `XMLHttpRequest` (upload `progress` event) or
   `fetch` + streams when you need a progress bar. If the user cancels or the
   PUT fails midway, simply presign again and retry — orphaned partial
   objects are harmless.
5. **Multiple files:** presign per file — each `file_key` is unique
   (`uuid`), so there are no collisions even for same-named files.
6. **Don't store `file_url`.** Persist `file_key` and mint fresh URLs at
   playback via `/uploads/access/`.
7. **`x-amz-meta-*` CORS:** the storage bucket's CORS must allow your
   frontend origin plus `Content-Type` and `x-amz-meta-*` on `PUT`, or the
   browser will block the direct upload. See README "Storage and S3".
8. **Never proxy the PUT through the API** — that reintroduces the worker /
   body-size problem direct upload exists to solve.

---

## 9. Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `400 … must use the 'courses' folder` | Sent a course `purpose` with `folder: "videos"` | Use the folder from §4 (`courses` / `thumbnails`) |
| `400 purpose is required for course and thumbnail uploads` | `folder` is `courses`/`thumbnails` but no `purpose` | Add the matching `purpose` |
| `400 … accepts only: video/mp4` | WebM/MOV sent as a course video | Re-encode to H.264 MP4 (or upload generically without `purpose`) |
| `400 … requires one of these file extensions` | MIME/extension mismatch (e.g. `text/plain` content-type with a `.txt` name for a subtitle) | Use `.srt` for subtitles; keep extension consistent with content type |
| `400 … files cannot exceed XMB` | `size` over the purpose cap | Validate size client-side before upload |
| `400 … requires a minimum resolution of 1280x720` / `must use a 16:9 aspect ratio` | Declared `width`/`height` too small or wrong ratio | Send actual dimensions |
| `400 … requires the H264 codec` | `codec` missing or not `h264` | Declare `codec: "h264"` |
| `400 … must be between 60 and 120 seconds` | Preview video duration out of range | Only presign previews after measuring duration |
| `400 File type 'x' is not allowed` | MIME not in the allowed list | Normalize `file.type` before sending |
| `403` from the storage PUT | URL expired, or a signed header was modified / `Content-Length` differs from declared `size` | Presign again; send `upload_headers` byte-for-byte and the untouched `File` |
| Browser CORS error on the PUT | Bucket CORS doesn't allow the origin / `x-amz-meta-*` headers | Fix storage CORS config (not the API) |
| Signed URL rejected when sent through the API client | Client stripped query params or added headers | `fetch(upload_url)` directly, not via `api.*` |

---

## 10. Related documentation

- `docs/SCB_FRONTEND_GUIDE.md` — creator flow guide (uploads section + reorder,
  lesson endpoints)
- `README.md` → "Storage and S3" — bucket configuration and CORS requirements
- `shared/services/storage_service.py` — `request_upload`,
  `generate_presigned_get`, `delete_file`, `COURSE_UPLOAD_RULES`
- `shared/serializers/storage_serializer.py` — request/response field definitions
- `shared/uploads/views.py` — `UploadPresignView`, `UploadAccessView`
