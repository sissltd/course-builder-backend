# Studio credential handoff verification

Scope: `fix/studio-demo-credential-handoff`, based on `b66403d`; exact implementation commit and CI run are recorded in the PR. Environment: isolated `scb_credentials` PostgreSQL test database; no platform credential was rotated and no deployment was performed by this implementation.

| Control | Source / implementation | Verification | Remaining dependency / owner |
| --- | --- | --- | --- |
| P1 discoverability | Generated OpenAPI describes the admin rotation request/response | Schema validation and CI | Deployment of matching docs; platform engineering |
| P2 MCP | No MCP service added by this narrowly scoped change | No pass claimed | Product-wide mandatory MCP operation remains outside this increment; platform engineering |
| P3 mobile | JSON administrator endpoint and operator export | API tests only | Admin UI/mobile integration and product offline/performance controls; frontend engineering |
| P4 isolation | Selected developer row locked; existing permission service gates access | Other account's API key remains valid after rotation | Existing product database tenant-isolation controls not certified by this test; platform engineering |
| P5 domain boundaries | Existing developer/key/activity services; no direct credential writes in the view | Unauthorized actor, wrong confirmation and suspended account denied | Product-wide catalogue/App Spec controls; platform engineering |
| P6 reliability | Atomic key/signing-secret change, exclusive private output file, old file never overwritten | 304 MIE tests and 213 subtests passed locally, including four new rotation/export journeys | Exact-head CI and deployment verification; platform/operations |
| P7 secret handling | Permission check, no-store response, 0600 export, secret-free audit details | Old key rejected, new key accepted, stdout contains neither secret | Secret-manager handoff and coordinated receiver update by authorized test account administrator |

This component verification is not a claim of whole-product P1–P7 compliance. See the [operator handoff guide](../frontend/studio-connection-handoff.md).
