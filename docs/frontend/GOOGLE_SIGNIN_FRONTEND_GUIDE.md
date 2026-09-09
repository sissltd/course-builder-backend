# Google Sign-In & Login — Frontend Integration Guide

How the frontend integrates Google sign-in (login **and** signup) with the
Course Builder platform. The backend verifies Google ID tokens and mints the
platform's own JWT pair — the frontend never handles passwords or Google
tokens beyond passing the ID token to the API.

**Audience:** frontend engineers integrating with the deployed API.

---

## 0. How it works (read this first)

```
┌────────────┐   Google OAuth consent   ┌──────────────┐
│  Frontend  │ ───────────────────────▶ │    Google    │
│  (GIS SDK) │ ◀─────────────────────── │              │
└────────────┘     ID token (JWT)       └──────────────┘
      │
      │ POST /api/v1/auth/login/google/   { "id_token": "..." }
      ▼
┌────────────────────┐   verifies signature/issuer/audience/email  ┌─────────┐
│  Django backend    │ ──────────────────────────────────────────▶ │ Google  │
│                    │  (against GOOGLE_OAUTH_CLIENT_IDS)          │  certs  │
└────────────────────┘
      │
      │ 200 → { access, refresh, user, role, workspace }
      │ 400 "No account is linked…" → frontend shows signup step
      ▼
  Frontend stores tokens, redirects to workspace home
```

- The **login** endpoint (`/auth/login/google/`) only signs in *existing*
  accounts — it never creates one.
- The **signup** endpoint (`/auth/signup/google/`) creates a new account
  (201) or links the Google identity to an existing eligible account (200),
  and returns tokens immediately (no separate email-verification step).
- New Google accounts are created **active** with an unusable password; the
  role is forced server-side by the URL you call (creator vs reviewer).
- Google can activate an existing `PENDING_VERIFICATION` account after it
  verifies the email. Suspended, deactivated, and other inactive accounts are
  refused and must be resolved through support instead.

---

## 1. Prerequisites

| # | Item | Owner |
| --- | --- | --- |
| 1 | A Google Cloud **OAuth 2.0 Web** client (client ID + secret) | Frontend / platform admin |
| 2 | The web client ID added to the backend's `GOOGLE_OAUTH_CLIENT_IDS` env var | Backend |
| 3 | Backend CORS allowing your frontend origin (via `CORS_ALLOWED_ORIGINS`) | Backend |
| 4 | API base URL, e.g. `https://<api-domain>/api/v1` | — |

**Endpoints used**

| Endpoint | Purpose |
| --- | --- |
| `POST /api/v1/auth/login/google/` | Sign in an existing Course Creator with Google |
| `POST /api/v1/auth/reviewer/login/google/` | Same view, reviewer alias (role read from account, not URL) |
| `POST /api/v1/auth/signup/google/` | Create/link a Course Creator account with Google |
| `POST /api/v1/auth/reviewer/signup/google/` | Create/link a Creator Reviewer account with Google |
| `POST /api/v1/auth/token/refresh/` | Swap the refresh token for a fresh access token |

---

## 2. Google Cloud Console setup (one-time)

1. Create an **OAuth 2.0 Web** credential (console.cloud.google.com →
   APIs & Services → Credentials).
2. **Authorized JavaScript origins:** add every frontend origin, e.g.
   `https://course-builder-frontend-lovat.vercel.app` and
   `http://localhost:3000`.
3. **Authorized redirect URIs:** only required if you use the server-side
   OAuth flow (NextAuth, see §8). Add
   `` `<frontend-url>/api/auth/callback/google` ``.
4. **Backend:** add the client ID to `GOOGLE_OAUTH_CLIENT_IDS`
   (comma-separated). Tokens with any other audience are rejected with
   `Invalid Google credential.`

---

## 3. Frontend environment variables

```bash
# Public — safe to expose (used by the Google Identity Services SDK)
NEXT_PUBLIC_GOOGLE_CLIENT_ID=<your-web-client-id>.apps.googleusercontent.com

# Server-side only (NextAuth OAuth dance)
GOOGLE_CLIENT_ID=<same id>
GOOGLE_CLIENT_SECRET=<secret>

# API base
NEXT_PUBLIC_DJANGO_API_URL=https://<api-domain>/api/v1
```

---

## 4. Load Google Identity Services (GIS)

Add the SDK to your auth pages:

```html
<script src="https://accounts.google.com/gsi/client" async defer></script>
```

Initialize it once on the login page and render the Google button:

```js
window.google.accounts.id.initialize({
  client_id: NEXT_PUBLIC_GOOGLE_CLIENT_ID,
  callback: handleCredentialResponse, // receives { credential: "<id_token>" }
});

window.google.accounts.id.renderButton(
  document.getElementById("google-signin-button"),
  { type: "standard", theme: "outline", size: "large", text: "continue_with" }
);
```

`handleCredentialResponse(response.credential)` gives you the **ID token**.
Send it to the backend — never decode it client-side for anything security
sensitive; the backend re-verifies signature, issuer, expiry, and audience.

---

## 5. Step A — Log in an existing account

```js
const res = await fetch(`${API_URL}/auth/login/google/`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ id_token: response.credential }),
});

const body = await res.json();
if (res.ok) {
  // body.access, body.refresh, body.user, body.role, body.workspace
  storeTokens(body);
  redirectToWorkspace(body.workspace);
} else {
  handleAuthError(res.status, body.errors); // see §9
}
```

**200 response shape:**

```json
{
  "access": "eyJhbGciOiJIUzI1NiJ9.creator-access-token",
  "refresh": "eyJhbGciOiJIUzI1NiJ9.creator-refresh-token",
  "user": {
    "id": "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d",
    "email": "jane.doe@example.com",
    "first_name": "Jane",
    "last_name": "Doe",
    "country": "NG",
    "role": "COURSE_CREATOR",
    "is_active": true,
    "status": "ACTIVE",
    "has_completed_onboarding": false
  },
  "role": "COURSE_CREATOR",
  "workspace": "creator_studio"
}
```

---

## 6. Step B — Sign up a new account

The login call can return **400** with:

```json
{
  "errors": [
    {
      "type": "validation_error",
      "code": "invalid",
      "message": "No account is linked to this Google identity. Please sign up first.",
      "field_name": "id_token"
    }
  ]
}
```

When that happens, route the user to a signup step that:

1. **Re-obtains a fresh ID token** (render the GIS button again, or keep the
   credential from step 5 — it's still valid, but a fresh one is safer for a
   form that may sit open).
2. **Prefills** `first_name`/`last_name`/`email` — you may decode them from
   the ID token's payload for display only (`given_name`, `family_name`,
   `email`), and let the user edit the names.
3. **Collects** a `country` (ISO 3166-1 **alpha-2**, e.g. `NG`, `US`) and a
   mandatory `terms_accepted: true` checkbox.
4. Calls the signup endpoint:

```js
const res = await fetch(`${API_URL}/auth/signup/google/`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    id_token: freshCredential,
    first_name: "Jane",
    last_name: "Doe",
    country: "NG",            // 2-letter ISO code, required
    terms_accepted: true,     // must be true
  }),
});
```

**Responses:** `201` = new account created (Course Creator), `200` = existing
eligible account linked and signed in. Both return the same `access` /
`refresh` / `user` / `role` / `workspace` payload as login. Store tokens and
redirect exactly as in Step A.

**Reviewer signup:** call `/auth/reviewer/signup/google/` — the role is
forced to `CREATOR_REVIEWER` server-side and the workspace becomes
`creator_review_dashboard`.

---

## 7. Sessions, refresh, and post-login routing

**Store the tokens** (`access` short-lived, `refresh` long-lived) in
whatever your session layer uses (httpOnly cookie preferred; localStorage is
acceptable for a pure SPA).

**Silent refresh** — when an API call returns `401`:

```js
const res = await fetch(`${API_URL}/auth/token/refresh/`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ refresh: storedRefreshToken }),
});
// 200 → { "access": "…" } — replace the stored access token and retry.
```

**Route by `workspace`** — never hardcode a single post-login redirect; the
backend tells you which dashboard the account belongs to:

| `workspace` | Role | Suggested route |
| --- | --- | --- |
| `creator_studio` | `COURSE_CREATOR` | `/dashboard` |
| `creator_review_dashboard` | `CREATOR_REVIEWER` | `/reviewer` |

**Logout:** call `POST /api/v1/auth/logout/` (revokes the session server-side)
with the access token, then clear local tokens. `POST /auth/logout-all/`
revokes every session for the user.

---

## 8. NextAuth.js option (matches the deployed app)

The deployed frontend (`course-builder-frontend-lovat.vercel.app`) uses
NextAuth — its `/auth/login?callbackUrl=…` redirect is NextAuth's sign-in
pattern. Integration outline:

1. Configure `GoogleProvider` (`GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`)
   with a JWT session strategy; set `pages.signIn = "/auth/login"`.
2. In the `jwt` callback, when `account.provider === "google"`, exchange
   `account.id_token` for the Django JWT pair via `/auth/login/google/` and
   store `{ access, refresh, user, role, workspace }` on the session token.
3. On the "no account" `400`, set `pendingSignup` on the session and redirect
   to `/auth/signup`.
4. The signup page obtains a fresh token with GIS (§4), calls
   `/auth/signup/google/`, then establishes the session with a pass-through
   credentials sign-in carrying the Django tokens (NextAuth never mints its
   own tokens).
5. Expose the payload via `useSession()` and route with `workspace` (§7).
6. Protect dashboards with NextAuth middleware; guard `/auth/signup` so it
   redirects away when the session isn't pending signup.

---

## 9. Error handling reference

All endpoints return the same error envelope (rendered by
`drf-standardized-errors`):

```json
{
  "errors": [
    {
      "type": "validation_error",
      "code": "invalid",
      "message": "Invalid Google credential.",
      "field_name": "id_token"
    }
  ]
}
```

| Status | `message` | What it means / frontend action |
| --- | --- | --- |
| `400` | `Invalid Google credential.` | Token expired, wrong audience, bad signature, or account not eligible (e.g. staff role). Have the user re-try the Google flow. |
| `400` | `No account is linked to this Google identity. Please sign up first.` | New user → route to signup step (§6). |
| `400` | `This account is not active. Please contact support.` | Suspended/deactivated account → show support message. |
| `400` | `Too many failed attempts. Try again in X minute(s).` | Account temporarily locked → show lockout message. |
| `400` | `You must accept the Terms and Conditions and Privacy Policy.` | `terms_accepted` was false on signup. |
| `503` | `Google authentication is temporarily unavailable.` | Backend has no `GOOGLE_OAUTH_CLIENT_IDS` configured → show "try again later". |
| `429` | `Request was throttled. …` | Rate limit hit (per IP for anonymous). Respect `Retry-After` header. |

**Anti-enumeration note:** password login deliberately returns one generic
`Invalid email or password.` for unknown email / wrong password / locked
account. The Google endpoints return specific messages only after Google has
already verified the identity, so surfacing them verbatim is fine.

---

## 10. Security checklist

- [ ] Only send the ID token to the backend; never to third parties.
- [ ] The backend (not the client) is the source of truth: it re-verifies
      signature, issuer, expiry, audience, and `email_verified`.
- [ ] Treat `access`/`refresh` like passwords (httpOnly cookies, no
      console.log, no URL params).
- [ ] Don't prefill trust: decode token claims for *display* only; editable
      fields (`first_name`, `last_name`, `country`) are re-validated by the
      backend.
- [ ] Use `workspace` from the API response for routing — the client must
      never infer role/workspace from the token.
- [ ] Redirect URIs and JS origins locked to real frontend domains.
