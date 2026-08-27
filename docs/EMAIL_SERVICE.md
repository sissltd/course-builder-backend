# Email Service Architecture

> End-to-end reference for how emails are composed, dispatched, and delivered across every environment. Written for Django (current), NestJS (upcoming Kafka + BullMQ migration), and any future stack.

---

## Table of Contents

1. [General Overview](#general-overview)
2. [Django Environment](#django-environment)
3. [NestJS Environment](#nestjs-environment)
4. [General Environment](#general-environment)

---

## General Overview

### What the email service does

Every transactional email in Feexeet — OTPs, invitations, password resets, payment confirmations, support updates — follows a single pipeline:

```
Caller (view / service / task)
        │
        ▼
  EmailService.send_<type>_email(...)       # compose context + pick template
        │
        ▼
  render_to_string("emails/<template>.html", context)   # Django template engine
        │
        ▼
  EmailService._send_email(subject, recipient, html)    # queue via Celery
        │
        ▼
  send_email_task.delay(...)                 # Celery @shared_task
        │
        ▼
  _dispatch_email(subject, recipient, html)  # route to active provider
        │
        ├──▶ _send_via_gmail(...)            # Django EmailMultiAlternatives (SMTP)
        └──▶ _send_via_resend(...)           # resend.Emails.send() API
```

### Key design decisions

| Decision | Rationale |
|---|---|
| **Static methods on a single service class** | No instantiation overhead; every email is a pure function of its inputs |
| **Celery for every send** | Email failures never block the request cycle; retries are automatic |
| **Provider switch via one env var** | `EMAIL_PROVIDER=gmail` or `resend` — zero code changes to swap |
| **HTML-only templates with plain-text fallback** | Gmail/Resend handle rendering; the SMTP fallback includes a `[Incompatible]` placeholder |
| **Service methods return `bool`** | Callers can log or react; they never have to catch exceptions |
| **No email log model** | Delivery status lives in Python `logging` and the `AuditLog` model (async via Celery) |

### Email types catalog

| Method | Template | Trigger |
|---|---|---|
| `send_verification_email` | `verification_email.html` | User registration |
| `send_welcome_email` | `welcome_email.html` | Post-registration |
| `send_password_reset_email` | `password_reset_email.html` | Password reset request |
| `send_password_changed_email` | `password_changed_email.html` | Password change |
| `send_otp_email` (Celery task) | `verification_email.html` | OTP login |
| `send_withdrawal_otp_email` | `withdrawal_otp_email.html` | Wallet withdrawal |
| `send_payment_confirmation_email` | `payment_confirmation_email.html` | Successful payment |
| `send_payout_sent_email` | `payout_sent_email.html` | Vendor payout |
| `send_account_deactivated_email` | `account_deactivated_email.html` | Account deactivation/deletion |
| `send_support_request_update_email` | `support_request_update_email.html` | Support ticket update |
| `send_host_account_created_email` | `host_account_created_email.html` | Host account creation |
| `send_admin_account_created_email` | `admin_account_created_email.html` | Admin role assignment |
| `send_staff_invitation_email` | `staff_invitation_email.html` | Internal team invite |
| `send_superadmin_invitation_email` | `superadmin_invitation_email.html` | SuperAdmin invite |
| `send_vendor_invitation_email` | `vendor_invitation_email.html` | Vendor onboarding |
| `send_occupant_invitation_email` | `occupant_invitation_email.html` | B2B occupant invite |
| `send_courier_org_invitation_email` | `courier_org_invitation_email.html` | Courier org onboarding |
| `send_company_invitation_email` | `company_invitation_email.html` | B2B company invite |
| `send_code_email` (verification codes) | `verification_code.html` | Logistics verification codes |

---

## Django Environment

### Directory layout

```
shared/
├── services/
│   └── email_service.py            # EmailService — 18 static send methods
├── tasks.py                        # Celery tasks: send_email_task, send_otp_email
├── celery/
│   └── celery_service.py           # CeleryService wrapper
├── verification_codes/
│   ├── models.py                   # VerificationCode model
│   ├── purposes.py                 # VerificationCodePurpose enum
│   ├── services.py                 # issue(), verify(), consume()
│   └── email_service.py            # send_code_email() — purpose-specific
├── email/
│   └── email.py                    # Legacy standalone Resend sender (deprecated)
├── templates/
│   └── emails/                     # 17 HTML email templates
│       ├── verification_email.html
│       ├── verification_code.html
│       ├── welcome_email.html
│       ├── password_reset_email.html
│       ├── password_changed_email.html
│       ├── withdrawal_otp_email.html
│       ├── payment_confirmation_email.html
│       ├── payout_sent_email.html
│       ├── account_deactivated_email.html
│       ├── admin_account_created_email.html
│       ├── staff_invitation_email.html
│       ├── superadmin_invitation_email.html
│       ├── company_invitation_email.html
│       ├── vendor_invitation_email.html
│       ├── occupant_invitation_email.html
│       ├── courier_org_invitation_email.html
│       └── support_request_update_email.html
└── constants/
    ├── authentication.py           # COMPANY_NAME, SUPPORT_EMAIL, FRONTEND_URL, etc.
    └── environ.py                  # DJANGO_ENV

config/
├── settings/
│   └── email.py                    # EMAIL_BACKEND, EMAIL_HOST, EMAIL_PROVIDER, etc.
└── celery.py                       # Celery app, beat schedule, autodiscovery
```

### Layer 1 — EmailService (`shared/services/email_service.py`)

Every public method follows the same shape:

```python
@staticmethod
def send_<type>_email(...):
    try:
        # [1] Build context dict with template variables
        context = {
            "FirstName": first_name,
            "user_name": user_name,
            "RecipientEmail": user_email,
            "company_name": COMPANY_NAME,
            "support_email": SUPPORT_EMAIL,
            # ... type-specific variables
        }

        # [2] Render the HTML template
        html_content = render_to_string("emails/<template>.html", context)

        # [3] Queue via Celery (non-blocking)
        response = EmailService._send_email(subject, user_email, html_content)
        return bool(response)

    except Exception as e:
        logger.error(f"Error sending <type> email: {e}")
        return False
```

The private `_send_email` method is the single funnel into Celery:

```python
@staticmethod
def _send_email(subject, recipient, html_content):
    from shared.tasks import send_email_task
    try:
        send_email_task.delay(subject, str(recipient), str(html_content))
        logger.info(f"Email queued for {recipient} with subject: {subject}")
        return True
    except Exception as e:
        logger.error(f"Failed to queue email for {recipient}. Error: {e}")
        return None
```

### Layer 2 — Celery tasks (`shared/tasks.py`)

Two tasks, both with retry logic:

```python
@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def send_email_task(self, subject, recipient, html_content):
    """General-purpose email task — every EmailService method routes here."""
    try:
        result = _dispatch_email(subject, recipient, html_content)
        return result
    except Exception as e:
        logger.error(f"Failed to send email to {recipient}. Error: {e}")
        raise self.retry(exc=e)

@shared_task(bind=True, max_retries=3, default_retry_delay=60, name="shared.send_otp_email")
def send_otp_email(self, email, otp):
    """OTP-specific task with dev/staging bypass."""
    try:
        # DEV/STAGING: log only, don't send
        if DJANGO_ENV in ("dev", "development", "pre-production"):
            logger.info(f"\n{'=' * 50}\n  OTP for {email}\n  Code: {otp}\n{'=' * 50}")
            return {"status": "logged", "env": DJANGO_ENV}

        # PROD: render template + dispatch
        from django.template.loader import render_to_string
        context = {"user_name": email, "verification_code": otp, ...}
        html_content = render_to_string("emails/verification_email.html", context)
        result = _dispatch_email(subject="Your Login code", recipient=email, html_content=html_content)
        return result
    except Exception as exc:
        logger.error(f"Failed to send OTP to {email}: {exc}")
        raise self.retry(exc=exc)
```

### Layer 3 — Provider dispatch (`_dispatch_email`)

The single routing function that reads `EMAIL_PROVIDER` from settings:

```python
def _dispatch_email(subject, recipient, html_content):
    provider = getattr(settings, "EMAIL_PROVIDER", "gmail")
    if provider == "resend":
        return _send_via_resend(subject, recipient, html_content)
    return _send_via_gmail(subject, recipient, html_content)
```

**Gmail (SMTP):**
```python
def _send_via_gmail(subject, recipient, html_content):
    from django.core.mail import EmailMultiAlternatives
    message = EmailMultiAlternatives(
        subject=subject,
        body="[Incompatible] Please open this email in an HTML-compatible mail client",
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[recipient],
    )
    message.attach_alternative(html_content, "text/html")
    message.send()
    return {"status": "sent", "provider": "gmail"}
```

**Resend (API):**
```python
def _send_via_resend(subject, recipient, html_content):
    import resend
    from decouple import config
    resend.api_key = config("RESEND_API_KEY")
    response = resend.Emails.send({
        "from": f"Feexeet <{settings.DEFAULT_FROM_EMAIL}>",
        "to": str(recipient),
        "subject": str(subject),
        "html": str(html_content),
    })
    return {"status": "sent", "provider": "resend", "id": response.get("id")}
```

### Layer 4 — CeleryService wrapper (`shared/celery/celery_service.py`)

Clean import surface for callers that don't want to import tasks directly:

```python
class CeleryService:
    @staticmethod
    def send_email(subject, recipient, html_content):
        return send_email_task.delay(subject, recipient, html_content)

    @staticmethod
    def send_otp_email(email, otp):
        return send_otp_email.delay(email, otp)
```

### Layer 5 — Verification code emails (`shared/verification_codes/`)

A purpose-driven subsystem for logistics codes (rider arrived, pickup confirmed, delivery verified):

```python
# send_code_email renders verification_code.html with purpose-specific copy
_PURPOSE_COPY = {
    VerificationCodePurpose.RIDER_ARRIVED: {
        "subject": "Confirm rider arrival",
        "action_line": "A rider has arrived at your store...",
    },
    # ...
}

def send_code_email(recipient_email, code, purpose) -> bool:
    copy = _PURPOSE_COPY.get(purpose, _DEFAULT_COPY)
    context = {"code": code, "purpose_label": str(purpose), "action_line": copy["action_line"], ...}
    html_content = render_to_string("emails/verification_code.html", context)
    return bool(EmailService._send_email(subject=copy["subject"], recipient=recipient_email, html_content=html_content))
```

### Configuration (`config/settings/email.py` + `.env`)

```python
EMAIL_PROVIDER = config("EMAIL_PROVIDER", default="gmail")   # "gmail" | "resend"
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = config("EMAIL_HOST", default="smtp.gmail.com")
EMAIL_PORT = config("EMAIL_PORT", default=587, cast=int)
EMAIL_USE_TLS = config("EMAIL_USE_TLS", default=True, cast=bool)
EMAIL_HOST_USER = config("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = config("EMAIL_HOST_PASSWORD", default="")
DEFAULT_FROM_EMAIL = config("DEFAULT_FROM_EMAIL", default="no-reply@feexeet.com")
```

| Variable | Purpose | Default |
|---|---|---|
| `EMAIL_PROVIDER` | `"gmail"` or `"resend"` | `gmail` |
| `RESEND_API_KEY` | Resend API key | (required for resend) |
| `EMAIL_HOST` | SMTP host | `smtp.gmail.com` |
| `EMAIL_PORT` | SMTP port | `587` |
| `EMAIL_USE_TLS` | TLS toggle | `True` |
| `EMAIL_HOST_USER` | SMTP username | (empty) |
| `EMAIL_HOST_PASSWORD` | SMTP password / app password | (empty) |
| `DEFAULT_FROM_EMAIL` | Sender address | `no-reply@feexeet.com` |
| `COMPANY_NAME` | Brand name in templates | `Feexeet` |
| `SUPPORT_EMAIL` | Support contact in footer | `support@feexeet.com` |
| `FRONTEND_URL` | Frontend base URL for links | `https://feexeet.com` |
| `DJANGO_ENV` | Environment gate for dev bypass | `development` |

### Template design system

All 17 templates share a consistent structure:

```
┌──────────────────────────────────────────┐
│  Header                                  │
│  ┌────────────────────────────────────┐  │
│  │  Feex eet  (logo)                  │  │
│  └────────────────────────────────────┘  │
├──────────────────────────────────────────┤
│  Content                                 │
│  ┌────────────────────────────────────┐  │
│  │  <h2> Subject line                 │  │
│  │  Hi {{ user_name }},              │  │
│  │  ...body copy...                   │  │
│  │  {{ code / link / action }}        │  │
│  │  Best regards,                     │  │
│  │  The {{ company_name }} Team       │  │
│  └────────────────────────────────────┘  │
├──────────────────────────────────────────┤
│  Footer                                  │
│  ┌────────────────────────────────────┐  │
│  │  © {% now "Y" %} {{ company_name }}│  │
│  │  Need help? {{ support_email }}    │  │
│  └────────────────────────────────────┘  │
└──────────────────────────────────────────┘
```

- **Font:** Inter (Google Fonts CDN)
- **Max width:** 600px
- **Colors:** `#202020` accents, white backgrounds, `#666666` footer text
- **Style:** Inline CSS in `<style>` blocks (not inlined for email clients)
- **Variables:** `{{ user_name }}`, `{{ FirstName }}`, `{{ company_name }}`, `{{ support_email }}`, `{{ RecipientEmail }}`

### Error handling & retries

| Layer | Behaviour |
|---|---|
| `EmailService.send_*` | try/except → returns `bool`, logs error, never raises |
| `EmailService._send_email` | try/except → returns `True`/`None`, logs error |
| `send_email_task` | 3 retries, 30s delay, `self.retry(exc=e)` on failure |
| `send_otp_email` | 3 retries, 60s delay, dev bypass logs OTP instead of sending |
| Caller services | Catch email failures separately (e.g. occupant invite) so email never blocks the primary operation |

### Testing

Global mock in `conftest.py`:

```python
@pytest.fixture(autouse=True)
def _patch_external_services():
    with (
        patch("shared.celery.celery_service.send_otp_email"),
        patch("shared.celery.celery_service.write_audit_log"),
        patch("shared.audit.audit_service.CeleryService"),
        patch("shared.tasks.send_email_task"),
        patch("shared.services.email_service.EmailService._send_email", return_value=True),
    ):
        yield
```

Per-test specificity:

```python
@pytest.fixture(autouse=True)
def _no_emails():
    with patch("api.authentication.services.staff_invitation_service.EmailService") as mock_email:
        yield mock_email

# Then:
_no_emails.send_superadmin_invitation_email.assert_called_once()
```

---

## NestJS Environment

> This section is a migration reference for the upcoming NestJS + Kafka + BullMQ rewrite. It maps Django concepts to their NestJS equivalents.

### Architecture mapping

| Django | NestJS equivalent |
|---|---|
| `EmailService` (static class) | `EmailService` (injectable `@Injectable()`) |
| `shared/tasks.py` (`@shared_task`) | BullMQ processor (`@Processor('email')`) |
| Celery broker (Redis) | Kafka (event bus) + BullMQ (job queue) |
| `_dispatch_email()` | `EmailProviderFactory` or strategy pattern |
| `render_to_string()` (Django templates) | `HandlebarsAdapter` or `MJML` (NestJS mailer) |
| `DJANGO_ENV` bypass | `ConfigService` read from `@nestjs/config` |
| `CeleryService.send_email()` | Event emitter: `this.eventEmitter.emit('email.send', payload)` |

### Recommended NestJS structure

```
src/
├── email/
│   ├── email.module.ts
│   ├── email.service.ts              # Injectable — composes payload, emits event
│   ├── email.processor.ts            # BullMQ @Processor — handles queue jobs
│   ├── email.providers/
│   │   ├── email-provider.interface.ts
│   │   ├── gmail.provider.ts
│   │   ├── resend.provider.ts
│   │   └── email-provider.factory.ts
│   ├── email.templates/              # Handlebars / MJML templates
│   │   ├── verification.hbs
│   │   ├── welcome.hbs
│   │   ├── password-reset.hbs
│   │   └── ...
│   ├── email.constants.ts
│   └── email.types.ts
├── kafka/
│   ├── kafka.module.ts
│   ├── kafka.producer.ts
│   └── kafka.consumer.ts
└── config/
    └── email.config.ts
```

### Kafka + BullMQ flow

```
Controller / Service
        │
        ▼
  EmailService.send<TYPE>(dto)            # compose payload, validate
        │
        ├──────────────────────────────┐
        ▼                              ▼
  Kafka event (audit/log)         BullMQ job (email:send)
        │                              │
        ▼                              ▼
  Kafka consumer                 BullMQ processor
  (analytics, logging)           (actual send)
                                     │
                                     ▼
                              EmailProviderFactory
                                     │
                                     ├──▶ GmailProvider (SMTP)
                                     └──▶ ResendProvider (API)
```

### Kafka topics (suggested)

| Topic | Purpose | Consumer |
|---|---|---|
| `email.send` | Trigger email delivery | BullMQ worker |
| `email.sent` | Delivery confirmation (webhook) | Analytics / Audit |
| `email.failed` | Delivery failure | Retry logic / alerting |
| `email.otp` | OTP-specific channel (priority queue) | Dedicated OTP worker |

### BullMQ job options

```typescript
// Priority queue — OTPs above everything else
await this.emailQueue.add('send', payload, {
  attempts: 3,
  backoff: { type: 'exponential', delay: 30000 },
  priority: priorityMap[emailType],  // OTP=1, transactional=5, marketing=10
  removeOnComplete: { age: 86400 },  // keep 24h
  removeOnFail: { age: 604800 },     // keep 7d for debugging
});
```

### Environment config

```typescript
// email.config.ts
export default () => ({
  email: {
    provider: process.env.EMAIL_PROVIDER || 'gmail',  // 'gmail' | 'resend'
    gmail: {
      host: process.env.EMAIL_HOST || 'smtp.gmail.com',
      port: parseInt(process.env.EMAIL_PORT || '587'),
      secure: false,
      auth: {
        user: process.env.EMAIL_HOST_USER,
        pass: process.env.EMAIL_HOST_PASSWORD,
      },
    },
    resend: {
      apiKey: process.env.RESEND_API_KEY,
    },
    from: process.env.DEFAULT_FROM_EMAIL || 'no-reply@feexeet.com',
    companyName: process.env.COMPANY_NAME || 'Feexeet',
    supportEmail: process.env.SUPPORT_EMAIL || 'support@feexeet.com',
    frontendUrl: process.env.FRONTEND_URL || 'https://feexeet.com',
  },
});
```

### Key migration notes

1. **Template portability** — The existing HTML templates are plain HTML + template variables. They can be dropped into Handlebars/MJML with minimal changes (replace `{{ var }}` syntax, which Handlebars already uses).
2. **Provider switch** — The `EMAIL_PROVIDER` env var pattern stays identical. The factory/strategy pattern in NestJS replaces the `_dispatch_email` if/else.
3. **Retry semantics** — BullMQ's `attempts` + `backoff` replaces Celery's `max_retries` + `default_retry_delay`. Exponential backoff is recommended over fixed delay.
4. **Dev bypass** — Move from `DJANGO_ENV` string comparison to `ConfigService.get('app.env')`. Same concept, cleaner access.
5. **Kafka vs Redis Streams** — If you only need email queuing (no event sourcing), BullMQ alone on Redis is sufficient. Kafka adds value when email events feed into analytics, audit trails, or cross-service choreography.

---

## General Environment

### Provider-agnostic email contract

Regardless of framework, the email service must satisfy this contract:

```
Input:
  - subject: string
  - recipient: string (email address)
  - html_content: string (rendered HTML)
  - metadata: { type, userId, environment }

Output:
  - status: 'queued' | 'sent' | 'failed'
  - provider: string
  - messageId: string (provider-specific)
  - error?: string

Guarantees:
  - At-least-once delivery (retry with backoff)
  - Provider-agnostic caller interface
  - Never blocks the calling thread/coroutine
  - Failures are logged + optionally persisted to audit
```

### Environment variable baseline

These variables must exist in every environment, regardless of stack:

| Variable | Required | Description |
|---|---|---|
| `EMAIL_PROVIDER` | Yes | Active provider identifier |
| `DEFAULT_FROM_EMAIL` | Yes | Sender address |
| `COMPANY_NAME` | Yes | Brand name for templates |
| `SUPPORT_EMAIL` | Yes | Footer support contact |
| `FRONTEND_URL` | Yes | Base URL for action links |
| `EMAIL_ENV` | Yes | Environment gate (`development` / `staging` / `production`) |
| `SMTP_HOST` | Provider-dependent | SMTP server hostname |
| `SMTP_PORT` | Provider-dependent | SMTP server port |
| `SMTP_USER` | Provider-dependent | SMTP username |
| `SMTP_PASSWORD` | Provider-dependent | SMTP password / app password |
| `RESEND_API_KEY` | Provider-dependent | Resend API key |

### Template variable standard

Every email template across every environment should accept these base variables:

| Variable | Type | Description |
|---|---|---|
| `user_name` | `string` | Full display name |
| `FirstName` | `string` | First name for greeting |
| `user_email` | `string` | Recipient email |
| `RecipientEmail` | `string` | Recipient email (alias) |
| `company_name` | `string` | Brand name |
| `support_email` | `string` | Support contact |
| `current_year` | `number` | Dynamic year for copyright |

Type-specific variables are added per template (e.g. `verification_code`, `reset_link`, `Amount`, `accept_url`).

### Error handling contract

| Level | Behaviour |
|---|---|
| **Provider failure** | Retry N times with exponential backoff, then mark as failed |
| **Template render failure** | Log error, return `false`, never send a broken email |
| **Queue failure** | Retry enqueue, log, surface to monitoring |
| **Caller impact** | Email failure must never block the originating operation |

### Monitoring checklist

Regardless of framework, these should be monitored:

- **Queue depth** — pending email jobs (Redis/BullMQ dashboard or `celery -A proj inspect active`)
- **Delivery rate** — sent vs failed per hour
- **Provider latency** — time from queue to delivery
- **Retry rate** — how often emails need retries
- **Template render errors** — logged separately from send errors

### Adding a new email type (any environment)

1. **Create the template** — copy an existing template, modify content and variables
2. **Add the service method** — `send_<type>_email()` following the existing pattern
3. **Register context variables** — list every template variable the method passes
4. **Write the test** — mock the send path, assert the method is called with correct args
5. **Trigger from the caller** — call the new method from the appropriate view/service/task
6. **Document** — add a row to the email types catalog table above

---

*Last updated: August 2026*
