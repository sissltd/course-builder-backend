# Review mode for each course

`PlatformSettings.staged_review_flow_enabled` is the default for future first
submissions. `Course.staged_review_flow` is read-only and freezes on first successful
submission. Existing courses, including drafts, receive their current mode during
migration, so a default change cannot reinterpret an old review seat or video rule.
Null is allowed only for not-yet-submitted new drafts and compatibility detection.
The default switch still rejects any unsnapshotted in-flight course.

Apply courses 0023 (snapshot/backfill), users 0048 (audit action choice), and platform
0023 (help metadata). Back up and restore a copy first. Record all existing Course,
ReviewAssignment and ReviewAction fingerprints. Rehearse migration on the copy,
checking snapshot values and identical fingerprints excluding the added field.
Deploy the API, all workers and Beat before activation. Verify no old worker remains.

An admin can PATCH `/api/v1/platform-settings/` with
`{"staged_review_flow_enabled": true}`. Existing permission checks still apply.
Settings responses include `course_review_flow_snapshots_enabled: true`; course list
and detail responses include the saved `staged_review_flow` value. The operational
alternative is:

    python manage.py set_course_review_default --mode text-first --actor-id <active-permitted-admin-uuid> --reason "Owner approved text-first defaults after preserving existing review flows."

The command uses the same guarded settings service and appends a configuration audit
record. It does not publish, approve, return to draft, archive or change any course.
Verify the old course/assignment/action fingerprints again after activation. Then
retry only the intended new text submission using a fresh reconciled delivery job.
The new platform course should have a true snapshot and enter normal text review.

Before activation, the previous image can run against the additive schema. After
activation, do not roll back to code that reads only the global flag while staged
courses remain in flight. Keep compatible code or complete an explicit reconciliation.
Do not reverse the backfill or remove snapshots to force a switch.
