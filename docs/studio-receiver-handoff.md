# Studio receiver handoff

Both review settings use `https://api.soludeskscb.com/api/v1/mie/v1/studio/reviews/`.
Uploads use `/api/v1/mie/v1/studio/uploads/`; both require an approved MIE developer credential.
These are implemented locally; public availability and a real end-to-end handoff are unverified.

## Dependency and rollout order

Include the existing uncommitted topic-identity, receipt, upload, webhook and course-push changes together.
MIE migrations 0012–0014 and courses migration 0022 are required, including their existing predecessors.
The platform staged review flow must be enabled for delivery. Topic approval reserves the course UUID;
text approval assigns Studio video production. Do not substitute reviewer approval routes for delivery.

Deploy the compatible receiver before the Studio sender. The upload descriptor now requires `sha256`,
`size` and `content_type`. Studio computes SHA-256 after any caption conversion, on the exact PUT bytes.
Course Builder stores this descriptor, verifies the stored object's length, type and SHA-256 before
returning a persisted upload or accepting video, and fails closed for legacy grants without a descriptor.
Legacy in-flight grants need fresh asset IDs and uploads; do not fabricate checksums or edit receipts.
The sender update is `server/platform-media.mjs` in the paired Studio backend repository.

Video transcripts have a dedicated read-only lesson API field `video_transcript`; approved text scripts
are preserved. Captions populate the existing `video_script_file` reference. The receiver validates
these fields through a Studio-specific serializer; legacy MIE video requests retain their old schema.
Confirm private-storage playback URL refresh and frontend use in the deployed journey.

## Verification and limits

Local regression: receiver, approval identities, legacy course push, staged video and storage tests.
Object storage is mocked. Actual S3-compatible storage behavior, playback, deployment and publication
are not verified by those tests. Storage verification streams each referenced object and can add latency
for large courses; measure this before production rollout. Existing presigned PUT grants remain usable
until expiry, so immutable/versioned storage is still needed for guarantees against post-check overwrites.
No deployment, paid generation, credential rotation or publication is included in this change.
