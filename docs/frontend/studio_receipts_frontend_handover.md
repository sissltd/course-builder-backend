# Studio Receipt Callbacks — Frontend Handover

What changed: Course Builder now sends Soludesk Studio a signed callback
(contract `studio.receipt.v1`) each time a Studio delivery is safely stored.
Studio can then mark that delivery as received on its side.

**No breaking changes, and almost nothing to build.** This is backend to
backend. No new endpoint is for the frontend to call. Three small
documentation changes reach your screens (Part 2).

Read [mie_course_delivery_frontend_handover.md](mie_course_delivery_frontend_handover.md)
first if you haven't. This only covers what is new.

---

## Part 1 — The logic

### What a receipt is

Studio is an MIE developer account that sends us three kinds of delivery:

| Kind | Studio sends it to | We store |
|---|---|---|
| `topic` | `POST /mie/v1/submissions/` | the course idea |
| `text` | `POST /mie/v1/studio/reviews/` (`kind: text`) | the course text, into review |
| `video` | `POST /mie/v1/studio/reviews/` (`kind: video`) | the lesson videos, captions and transcripts |

Once one of these is stored, we tell Studio "received". It's only a
confirmation that we have it. **It doesn't approve, reject or publish
anything.** Review decisions still happen in the existing review screens,
and Studio still learns about them through the existing webhooks.

### How it flows

```
Studio sends a delivery with three extra headers
  X-Studio-Delivery-Id      Studio's job id
  X-Studio-Payload-SHA256   hash of the exact bytes it sent
  X-Studio-Content-Revision the revision (text and video only)
        │
        ▼
We check the headers, then store the delivery and its receipt together
  (if anything fails, neither is stored: 400 or 409, nothing to clean up)
        │
        ▼
Every minute, a background job sends due receipts to Studio's private server,
signed, over verified TLS
        │
        ├─ Studio says OK (2xx)        → DELIVERED, done
        ├─ Studio is busy or down      → retried after 1m, 5m, 15m, 1h
        └─ Studio refuses it, or the
           retries run out             → HELD, waiting for a person
```

### Who sees what

- **Only the Studio account gets receipts.** For every other developer,
  nothing changes. Any `X-Studio-*` headers they send are ignored.
- **The callback target is private.** It sits on the internal network, so
  no browser can reach it, and the frontend never talks to it.
- **Held receipts are handled in Django admin**, under *MIE → Studio
  Delivery Receipts*. There, ops can filter by status and requeue them,
  keeping the same receipt id. There's no admin API or screen in the
  frontend for this. Ask before building one; it would need new endpoints.

---

## Part 2 — What you need to do

### 1. The developer docs page has a new `studio.receipts` key

`GET /api/v1/mie/v1/documentation/` (and the PDF) gains one key in the
`studio` section:

| Key | Shape | Render as |
|---|---|---|
| `studio.receipts` | list of strings | Bullets, after `uploads`. |

The rendering table in the course delivery handover (step 6) already lists
it. If your page renders known keys only, add `receipts` with the heading
**"Receipts"**.

### 2. Two endpoint entries mention the Studio headers

In the same docs response, two endpoint entries changed:
- **"Submit a course idea":** the 400 and 409 rows now include the Studio
  header errors (`studio_delivery_conflict`), and there's one new note.
- **"Studio: deliver course text or video for review":** the `auth` line
  now names the three Studio headers, the 400 and 409 rows include their
  errors, and there's one new note.

These are text changes only, so they render the way you already render
endpoint entries. Nothing to do unless you hard-coded those texts.

### 3. Swagger: three new optional headers

On `POST /mie/v1/submissions/` and `POST /mie/v1/studio/reviews/`, Swagger
now documents optional headers:
- `X-Studio-Delivery-Id` and `X-Studio-Payload-SHA256` on both endpoints
- `X-Studio-Content-Revision` on the reviews endpoint only

If you generate a client from the whole schema, the two methods gain
optional header parameters. **Don't send them from the frontend.** They
only mean something for Studio's own account.

---

## Summary

| Area | Change | Breaking? | Frontend work |
|---|---|---|---|
| Developer docs | `studio.receipts` key | No — additive | Render it, if you whitelist keys |
| Developer docs | Studio header errors and notes on two endpoint entries | No — text only | None |
| Swagger | Optional `X-Studio-*` headers on two endpoints | No — optional | None (never send them) |
| Admin | Held receipts in Django admin only | — | None (ask before building a screen) |
| Studio receipt server | Private, backend to backend | — | None |
