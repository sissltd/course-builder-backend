# Email Service Architecture

> The definitive reference for how this project composes, routes, and delivers email.
> Every claim below is verified against the codebase (file:line cited). Last updated: August 2026.

---

## One-paragraph summary

Every transactional email — signup verification, password reset, account-lockout alerts, staff
invitations, withdrawal OTPs, and withdrawal outcome notifications — follows one of two short
paths that both terminate in **synchronous delivery during the request cycle**:

```
Caller (view → service / serializer / webhook)
   │
   ├── auth / staff / lockout emails ──────► send_templated_email(receivers, subject, template_name, context)
   │
   └── wallet / webhook emails ───────────► Notification.emit_email_notification(...)   (send-only helper)
                                                │
                                                ▼
                                        send_templated_email(...)   api/notification/services/email_service.py:8
                                                │
                      renders "{template}.txt" + "{template}.html"  via Django template engine (:30-31)
                                                │
                        ┌───────────────────────┴───────────────────────┐
                        │                                               │
         EMAIL_PROVIDER ∈ {resend, cloudflare}                EMAIL_PROVIDER = smtp (default)
         AND no attachments/cc/bcc/reply_to                    OR advanced features present
                        │                                               │
                        ▼                                               ▼
              dispatch_email(...)                          EmailMultiAlternatives(...)
              shared/tasks.py:154                          → Django EMAIL_BACKEND
                        │                                   (smtp backend, or console when no creds)
                        ▼
   ┌────────────────────┼─────────────────────┐
   ▼                    ▼                     ▼
_send_via_gmail   _send_via_resend    _send_via_cloudflare
(Django SMTP)     (Resend REST API)   (Cloudflare Email Service REST API — HTTPS/443)
```

**Critical fact:** all active email sends are **synchronous and inline**. The Celery email tasks
still exist in `shared/tasks.py` but currently have **zero callers** (see [Celery tasks](#celery-tasks-dormant)).

---

## Table of Contents

1. [What sends email (the complete inventory)](#what-sends-email-the-complete-inventory)
2. [Composition — `send_templated_email`](#composition--send_templated_email)
3. [Routing — `dispatch_email` and the providers](#routing--dispatch_email-and-the-providers)
4. [Configuration & environment variables](#configuration--environment-variables)
5. [Templates catalog](#templates-catalog)
6. [Token & link generation](#token--link-generation)
7. [Celery tasks (dormant)](#celery-tasks-dormant)
8. [Failure handling & monitoring](#failure-handling--monitoring)
9. [Testing the pipeline](#testing-the-pipeline)
10. [Known dead code & gotchas](#known-dead-code--gotchas)

---

## What sends email (the complete inventory)

### Synchronous senders (the entire active system)

| Trigger | Call site | Template | Subject | Context |
|---|---|---|---|---|
| Signup verification | `_send_signup_verification_email` — `api/authentication/services/authentication_service.py:357` (fired from `signup` via `transaction.on_commit`, :138) | `emails/email_verification` | `Verify your email` | `first_name`, `verification_link`, `expiry_minutes` |
| Resend verification / reset link | `resend_otp` — `authentication_service.py:170` (:183) | `emails/email_verification` / `emails/password_reset` | same as above | same as above |
| Forgot password | `forgot_password` — `authentication_service.py:333` | `emails/password_reset` | `Reset your password` | `first_name`, `reset_link`, `expiry_minutes` |
| Password reset completed | `reset_password` — `authentication_service.py:185` | `emails/password_reset_confirmation` | `Your password was changed` | `first_name` |
| Password changed (signed-in) | `change_password` — `authentication_service.py:206` | `emails/password_reset_confirmation` | `Your password was changed` | `first_name` |
| Email address change request | `request_email_change` — `authentication_service.py:234` (:260) | `emails/email_change_confirmation` | `Confirm your new email address` | `first_name`, `confirmation_link` |
| Staff invitation | `_send_staff_invitation_email` — `api/authentication/services/staff_service.py:375` (fired from `invite_staff` via `transaction.on_commit`, :125/:194) | `emails/staff_invitation` | `You've been invited to join the team` | `first_name`, `invited_by_name`, `role_label`, `invitation_link`, `expiry_minutes` |
| Account temporarily locked | `_register_failed_attempt` (5th failure) — `api/authentication/serializers/login_serializer.py:179` | `emails/account_locked` | `Your account was temporarily locked` | `first_name`, `lock_minutes` |
| Withdrawal OTP | `api/wallet/services/wallet_service.py:334` | `emails/withdrawal_otp` | `Confirm your withdrawal` | `first_name`, `code`, `amount`, `expiry_minutes` |
| Withdrawal reversal failed | `api/wallet/tasks.py:134` | `emails/failed_withdrawal` | `Failed Withdrawal Notification` | `first_name`, `amount` |
| Withdrawal succeeded (webhook) | `api/webhooks/services/paystack_webhook_services.py:108` | `emails/successful_withdrawal` | `Successful Withdrawal Notification` | `first_name`, `amount` |
| Withdrawal failed (webhook) | `paystack_webhook_services.py:154` | `emails/failed_withdrawal` | `Failed Withdrawal Notification` | `first_name`, `amount` |
| Withdrawal succeeded (webhook) | `api/webhooks/services/flutterwave_webhook_services.py:116` | `emails/successful_withdrawal` | `Successful Withdrawal Notification` | `first_name`, `amount` |
| Withdrawal failed (webhook) | `flutterwave_webhook_services.py:163` | `emails/failed_withdrawal` | `Failed Withdrawal Notification` | `first_name`, `amount` |

The four auth email types log `auth_email_sent` / `auth_email_failed` through the shared
wrapper `_queue_auth_email` — `authentication_service.py:37-75` — which sends **synchronously**
and never raises (failures are logged only).

### Admin diagnostic endpoint

`TestEmailView` — `api/platform/views.py:347-451`, mounted at `POST /api/v1/test-email/`.
Permission: `IsAuthenticated` + `IsAdminOrSuperAdminRole`. It calls `dispatch_email` directly
(`:426`) — bypassing `send_templated_email` — with the authed user's address as the recipient when
`email` is omitted, and returns HTTP 502 with the provider's error message when the send fails.

### `Notification.emit_email_notification` — send-only helper

`api/notification/models.py:158-199`. A classmethod used as a static email helper — it **creates
no database rows** (the `Notification.type` choices `SMS/EMAIL/IN_APP/PUSH` are not exercised by
the email path; only `emit_in_app_notification` writes `IN_APP` rows).

- `receivers` accepts a `User`, a user pk, a raw email string, or a list/tuple/set of any mix; each
  is resolved by `_resolve_email` (`:202`): `User → .email`, a string containing `@` is used as-is,
  anything else is treated as a pk and looked up.
- Requires at least one receiver, else `ValidationError` (`:180`).
- Passes everything through to `send_templated_email(...)` (`:188-199`), including
  `from_email` / `cc_emails` / `bcc_emails` / `reply_to` / `attachments` / `fail_silently`.
- `metadata` is accepted but only used by the in-app path.

### Dead-zone check

No caller outside of auth/staff/login/wallet/webhooks sends email. MIE notifications go out as
signed **webhooks**, not email. `api/courses`, `api/reviews`, `api/catalog`, `api/users`,
`api/collaborators`, `api/kyc` only use `emit_in_app_notification`.

---

## Composition — `send_templated_email`

`api/notification/services/email_service.py:8-63`. Signature:

```python
send_templated_email(*, receivers, subject, template_name, context=None,
                     from_email=None, cc_emails=None, bcc_emails=None,
                     reply_to=None, attachments=None, fail_silently=False) -> int
```

1. Renders **both** parts from the same template basename (`:30-31`):
   `render_to_string(f"{template_name}.txt")` and `render_to_string(f"{template_name}.html")`.
   Both files must exist (see [template catalog](#templates-catalog)).
2. Reads `EMAIL_PROVIDER` (`:32`). If it is `resend` or `cloudflare` **and** no advanced features
   (`attachments`, `cc_emails`, `bcc_emails`, `reply_to`) are present → delegates to
   `dispatch_email(...)` and returns `1`.
3. Otherwise (default `smtp` provider, or advanced features present) → builds an
   `EmailMultiAlternatives` directly (`:49-63`) with the text body, `.attach_alternative(html, "text/html")`,
   optional cc/bcc/reply-to, then attachment(s), and sends through Django's `EMAIL_BACKEND`.

> Note: under the default `smtp` provider, **every email goes through Django's SMTP/console backend**,
> never through `dispatch_email`. `dispatch_email` is only used for the `resend`/`cloudflare`
> providers and the direct `TestEmailView` call.

---

## Routing — `dispatch_email` and the providers

`shared/tasks.py`. The single switch point:

| `EMAIL_PROVIDER` | Handled at | Function | Transport |
|---|---|---|---|
| `smtp` (default) / `gmail` / anything else | `shared/tasks.py:197-207` | `_send_via_gmail` (`.16`) | Django `EmailMultiAlternatives` → `EMAIL_BACKEND` |
| `resend` | `shared/tasks.py:176-183` | `_send_via_resend` (`.52`) | `resend.Emails.send` (Resend REST API) |
| `cloudflare` | `shared/tasks.py:185-195` | `_send_via_cloudflare` (`.91`) | `httpx` → Cloudflare Email Service REST API (HTTPS) |

### `_send_via_gmail` (`shared/tasks.py:16`)

Django `EmailMultiAlternatives` with text body + `text/html` alternative, plus cc/bcc/reply_to and
attachments. Sends through `EMAIL_BACKEND`. Returns `{"status": "sent", "provider": "gmail"}`.

### `_send_via_resend` (`shared/tasks.py:52`)

`resend.Emails.send` with `from = "{COMPANY_NAME} <{from_email | DEFAULT_FROM_EMAIL}>"`, `to`,
`subject`, `text`, `html`. Uses `RESEND_API_KEY`. Returns
`{"status": "sent", "provider": "resend", "id": <message id>}`.

### `_send_via_cloudflare` (`shared/tasks.py:91`)

Cloudflare Email Service's **structured REST endpoint** — delivery over HTTPS/443, no SMTP egress:

```
POST https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/email/sending/send
Authorization: Bearer {CLOUDFLARE_API_TOKEN}
Content-Type: application/json
```

Request payload (field names per Cloudflare's REST API — note `from` uses `address`, not `email`):

```json
{
  "from":    {"address": "<DEFAULT_FROM_EMAIL>", "name": "<COMPANY_NAME>"},
  "to":      ["recipient@example.com"],
  "subject": "...",
  "text":    "plain-body",
  "html":    "<p>html-body</p>",
  "cc":      ["..."],     // optional
  "bcc":     ["..."],     // optional
  "reply_to": "..."       // optional (single address)
}
```

- Timeout: `httpx.Timeout(10.0, read=30.0)` — connects fast, but won't hang the request.
- Success (HTTP 200) → returns `{"status": "sent", "provider": "cloudflare", "id": result.message_id}`.
- Non-200 → raises `RuntimeError` carrying the first Cloudflare error
  (`{code}: {message}`, extracted by `_cloudflare_error_message`, `.79`), e.g. `HTTP 403: 10102: Token lacks permission to send`.
- Transport failure (DNS/connect/read) → wrapped as `RuntimeError(f"Cloudflare send transport error: ...")`.

**Prerequisites for `cloudflare`:**
- `CLOUDFLARE_API_TOKEN` with the **Email Sending: Edit** permission; it is a *Bearer* token (the SMTP
  password for the same account is a different credential — SMTP uses username `api_token`).
- `DEFAULT_FROM_EMAIL`'s domain must be **onboarded for Email Sending** on the account that owns the token,
  otherwise the API rejects with `403` (`10105 not_entitled` / `10203 sending_disabled`).

---

## Configuration & environment variables

`config/settings/smtp.py` (imported from `config/settings/__init__.py`).

| Variable | Default | Meaning |
|---|---|---|
| `EMAIL_PROVIDER` | `smtp` | `smtp` (Django SMTP backend) \| `resend` (Resend REST) \| `cloudflare` (Cloudflare REST) |
| `EMAIL_HOST_USER` | `""` | SMTP username. **Empty ⇒ `EMAIL_BACKEND = console` and nothing is actually sent.** |
| `EMAIL_HOST_PASSWORD` | `""` | SMTP password / app password |
| `DEFAULT_FROM_EMAIL` | `""` (settings) | Sender address — also in `shared/constants/authentication.py` (`no_reply@coursebuilder.com`) |
| `EMAIL_HOST` | `smtp.cloudflare.com` | SMTP host (only used when `EMAIL_HOST_USER` is set) |
| `EMAIL_PORT` | `587` | SMTP port (Cloudflare SMTP requires `465` + `EMAIL_USE_SSL=True`) |
| `EMAIL_USE_TLS` | `True` | STARTTLS toggle |
| `EMAIL_USE_SSL` | `False` | Implicit-TLS toggle — set `True` for port 465 |
| `EMAIL_TIMEOUT` | `30` | Per-socket-op cap Django's SMTP backend applies, so a hung provider fails fast instead of stalling past Cloudflare's 120 s proxy read timeout (HTTP 524) |
| `CLOUDFLARE_API_TOKEN` | `""` | Bearer token for Cloudflare Email Service REST (Email Sending: Edit) |
| `CLOUDFLARE_ACCOUNT_ID` | `""` | Cloudflare account id that owns the token/domain |

Email-token settings live in `config/settings/authentication.py`:

| Variable | Default |
|---|---|
| `EMAIL_TOKEN_BYTES` | `32` |
| `EMAIL_TOKEN_EXPIRY_MINUTES` | `60` |
| `EMAIL_TOKEN_MAX_ATTEMPTS` | `5` |
| `EMAIL_TOKEN_RESEND_COOLDOWN_SECONDS` | `60` |
| `FRONTEND_URL` | `http://localhost:3000` (also in `shared/constants/authentication.py`) |

Branding/contact constants — `shared/constants/authentication.py`:
`COMPANY_NAME` (default `CourseBuilder`), `SUPPORT_EMAIL` (default `support@coursebuilder.com`),
`FRONTEND_URL` (default `https://coursebuilder.com`).

---

## Templates catalog

Loader: `config/settings/commons.py:50-65` — `TEMPLATES["DIRS"] = [BASE_DIR / "templates"]` plus
`APP_DIRS: True`, so `render_to_string("emails/<name>.html")` resolves under `templates/emails/`.

Every active template is a pair (`<name>.html` + `<name>.txt`) rendered by `send_templated_email`.
Context variables actually used by the templates: `first_name`, `verification_link`, `expiry_minutes`,
`reset_link`, `confirmation_link`, `invited_by_name`, `role_label`, `invitation_link`,
`lock_minutes`, `code`, `amount`.

| Template | Used by |
|---|---|
| `account_locked` | login_serializer.py (lockout alert) |
| `email_change_confirmation` | authentication_service.py (email change) |
| `email_verification` | authentication_service.py (signup verification) |
| `failed_withdrawal` | wallet/tasks.py, paystack_webhook_services.py, flutterwave_webhook_services.py |
| `password_reset` | authentication_service.py |
| `password_reset_confirmation` | authentication_service.py |
| `staff_invitation` | staff_service.py |
| `successful_withdrawal` | paystack_webhook_services.py, flutterwave_webhook_services.py |
| `withdrawal_otp` | wallet_service.py |
| `category_request_approved` | **no caller — unused** |

---

## Token & link generation

Verification/reset links are built by `api/authentication/utils/links.py`
(`build_verification_link`, used in `authentication_service.py:20, :358-376`) against
`FRONTEND_URL`. Token lifecycle is governed by the `EMAIL_TOKEN_*` settings (above) and the endpoints
`POST /api/v1/auth/verification-token/`-style flows documented in `docs/postman_collection.md`.

---

## Celery tasks (dormant)

The `@shared_task`s below are registered (forced-imported by `config/celery.py:15-19`) but
**none are currently enqueued anywhere** — a deliberate design decision: critical auth emails send
synchronously because Celery delivery proved unreliable on Dokploy's Redis topology.

| Task | Decorator (shared/tasks.py) | Retries / delay | Notes |
|---|---|---|---|
| `send_templated_email_task` | `@shared_task(bind=True, max_retries=3, default_retry_delay=30)` — `.211` | 3 / 30 s | Renders template, then `send_templated_email`; raises if `sent_count < 1`; logs `auth_email_sent/retrying/failed` with `task_id`. |
| `send_email_task` | `@shared_task(bind=True, max_retries=3, default_retry_delay=30)` — `.295` | 3 / 30 s | `dispatch_email`; text fallback `f"{subject}\n\n{html_content}"`; logs `email_sent/retrying/failed`. |
| `send_otp_email` | `@shared_task(bind=True, max_retries=3, default_retry_delay=60, name="shared.send_otp_email")` — `.355` | 3 / 60 s | Dev gate: when `DJANGO_ENV ∈ {"dev","development","pre-production"}` it only logs the OTP (`:367-381`). **Prod path renders `emails/verification_email` which does not exist — would fail (see gotchas).** |
| `create_audit_log_task` | `@shared_task(bind=True, max_retries=2, default_retry_delay=10, name="shared.create_audit_log")` — `.437` | 2 / 10 s | Writes `AuditLog`; no callers. |
| `write_audit_log` | `@shared_task(bind=True, max_retries=5, default_retry_delay=30, name="shared.write_audit_log")` — `.466` | 5 / 30 s | Writes `AuditLog`; reachable only via the unused `CeleryService`. |

`shared/celery/celery_service.py:27-60` (`CeleryService.send_email`, `send_otp_email`,
`write_audit_log`) is an unused convenience wrapper. If a single shared Redis instance is ever
restored, the `.delay()` calls can be re-enabled — no other change required.

---

## Failure handling & monitoring

- **Synchronous senders never propagate email failures to the caller.** `_queue_auth_email`,
  `_send_staff_invitation_email`, and the lockout email all `try/except` and log; the primary
  operation (signup, invite, reset) always succeeds regardless of email outcome.
- `send_templated_email` returns `fail_silently=True` only on the `EmailMultiAlternatives` path;
  `dispatch_email` callers (e.g. `TestEmailView`) do surface the provider error.
- Cloudflare provider errors carry the Cloudflare error code via `RuntimeError`; Resend errors
  propagate from the `resend` SDK.
- **Log tags** (grep-able, filterable in Docker logs):
  - `auth_email_sent` / `auth_email_failed` — auth + staff + lockout paths (also `auth_email_retrying` from the dormant task).
  - `email_sent` / `email_retrying` / `email_failed` — dormant `send_email_task`.
  - `celery_service_email_sent` / `celery_service_email_failed` — unused `CeleryService`.

---

## Testing the pipeline

- **Live delivery check (staging):** `POST /api/v1/test-email/` with
  `{"email": "qa@...", "subject": "...", "message": "..."}` returns `{"status": "sent"}`
  synchronously, or `502` with the provider's error. This is the definitive end-to-end proof.
- **Unit tests:** `api/platform/tests/test_email_providers.py` — pins `dispatch_email` routing and
  `_send_via_cloudflare`'s payload/headers, error-detail extraction, and transport-error wrapping
  (no DB needed, `SimpleTestCase`).
- **Connection checker:** `devscripts/test_smtp.py` — standalone SMTP connectivity probe.
- CI runs `python manage.py test`; local runs need postgres for the DB-bound suites.

---

## Known dead code & gotchas

1. **`shared/services/email_service.py` (legacy `EmailService`) is dead code.** It is the old
   "Feexeet" scaffold — its `send_*_email` methods reference templates that do not exist
   (`welcome_email`, `password_reset_email`, etc.), and nothing imports it. Do not treat it as the pipeline.
2. **`send_otp_email`'s production path is broken-on-arrival**: it renders `emails/verification_email`,
   which does not exist (the real signup template is `emails/email_verification`). The worker would
   retry forever. It's dormant, so nothing hits it today.
3. **Default config sends nothing.** With `EMAIL_PROVIDER=smtp` and an empty `EMAIL_HOST_USER`,
   `EMAIL_BACKEND` is the console backend — every email is printed to stdout, not delivered.
   Provider switches to `resend`/`cloudflare` bypass `EMAIL_BACKEND` entirely.
4. **Cloudflare SMTP vs REST are different ports/credentials.** SMTP = `smtp.mx.cloudflare.net:465`
   (implicit TLS only), username literal `api_token`, password = an API token. REST = port 443 with a
   Bearer token + account id. Both require the from-domain to be onboarded for Email Sending.
5. **Outbound SMTP may be firewalled** (staging currently rejects 465/587). That is exactly why
   `EMAIL_PROVIDER=cloudflare` exists — it needs only outbound HTTPS/443, which is already open.
6. `Notification.type` enum `EMAIL` is not used by the email path; `emit_email_notification` writes no rows.