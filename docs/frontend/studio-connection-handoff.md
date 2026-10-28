# Repair and hand off the Studio test connection

The Studio screenshot shows configuration types mixed together: a signing secret and the Studio incoming callback URL were saved under the API-key provider, no Demo API integration was configured, and the Demo API key was revoked locally. An approved Course Builder developer account cannot reveal its original API key: only its hash is retained. Reapproving an already approved account is deliberately rejected. Use the explicit administrator reissue workflow below rather than rejecting/reapproving the account or rotating Live credentials.

## 1. Platform administrator: identify the test account

Open the Course Builder MIE developer directory and select the approved account whose registered endpoint is:

`https://lab.soludeskscb.com/studio/api/webhooks/soludesk/demo`

Confirm its account UUID, email and callback. Use a distinct account from Live. The `scb_live_` prefix is the platform's key format for every approved developer; that prefix does not itself designate the Studio environment or guarantee sandbox isolation.

## 2. Deploy the reissue feature to the Course Builder test backend

This change adds no database migration. Follow the normal staging deployment process and verify its CI. This guide does not claim that the endpoint or management command is available before that deployment.

The administrator can choose either of the following methods.

### Private file export (recommended for an operator handoff)

Run inside the backend container using an active administrator with `mie.manage_console`:

```sh
python manage.py export_studio_connection \
  --account-id TEST_ACCOUNT_UUID \
  --actor-email AUTHORIZED_ADMIN_EMAIL \
  --api-base-url https://api.soludeskscb.com/api/v1/mie/v1/ \
  --studio-origin https://lab.soludeskscb.com \
  --environment demo \
  --output /tmp/studio-demo-connection.json
```

The command refuses an unapproved account, an unauthorized actor, a missing/mismatched callback, and an existing output filename. It rotates only the selected account's API key and signing secret. The new bundle is created with mode 0600; the terminal prints its path only. The old key and old signing secret stop working for that account. Coordinate any other receivers belonging to the same test account.

The JSON contains `environment`, `account_id`, `api_base_url`, `api_key`, `signing_secret`, and `webhook_url`. The API base above is the known platform URL; confirm with operations whether your test account should use a separate test backend origin before running the command.

Copy the file from the container into a restricted operator directory using `docker cp`, then use an encrypted file transfer or a password manager's secret-sharing feature to give it only to the Studio owner. Do not paste the bundle into an issue, PR, email, chat, screenshot or repository. Remove temporary copies after the owner has stored the credentials and verified the connection. Keep the normal secret-manager copy according to your retention policy.

### Administrator HTTP endpoint

Using the authorized administrator's normal platform authentication:

```http
POST /api/v1/mie/admin/developers/TEST_ACCOUNT_UUID/rotate-credentials/
Content-Type: application/json

{"confirm_account_id":"TEST_ACCOUNT_UUID"}
```

The response includes `one_time_api_key` and `signing_secret`, with `Cache-Control: no-store`. Capture it once into a secret manager and share it through the same restricted channel. Do not send the API key in the URL. Do not automatically retry this consequential rotation: check whether the first response was received before issuing another rotation.

## 3. Studio owner: register each value in the right place

| Value in bundle | Destination |
| --- | --- |
| `api_base_url` | Integrations & health → Demo → API endpoint |
| `api_key` | API services → Soludesk Course Builder → Register credential → Demo |
| `signing_secret` | API services → Soludesk webhook signing → Register credential → Demo |
| `webhook_url` | Course Builder test developer's webhook endpoint; not a Studio credential |

Remove the mistaken URL-as-secret entries. A valid test signing secret currently filed under the API-key provider must be replaced using the newly issued matching secret under **Soludesk webhook signing**. Keep only the intended active signing record for Demo, since Studio selects its most recently updated non-revoked signing credential.

Use **Check connection** on the API key after saving the Demo integration. It must return Verified. A registered signing secret has no generic “check API key” action: it is validated when Course Builder sends a timestamped HMAC-signed event. Do not expect a Registered signing-secret badge alone to become Verified.

## 4. Verify API access and webhook delivery separately

1. Check the Demo API key through Studio. The underlying `GET /me/` must return 200, and its account must be the test account.
2. Confirm the test developer's active webhook list contains the exact `/studio/api/webhooks/soludesk/demo` callback.
3. Ensure Studio's Demo signing secret equals that account's current `signing_secret` returned by authenticated `GET /me/`.
4. Perform a supervised test submission/review only when authorized. Inspect the platform's delivery status and Studio's incoming-event/audit views. A valid but unmapped event may be held for reconciliation; that is different from a signature rejection.
5. Confirm missing/wrong signatures receive 401 and that no paid build starts during connection-only verification.

Saving a key does not enable paid production. A Demo label does not prevent requests from reaching a real backend; use the correct test account and endpoint, and leave production disabled until the full approval/review handoff is verified.
