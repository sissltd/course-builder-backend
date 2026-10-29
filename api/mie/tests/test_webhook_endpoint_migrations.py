"""The data migration that turns each account's webhook_url into its first
endpoint, run for real: back to before it, then forward again."""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

BEFORE = [("mie", "0009_webhookendpoint")]
AFTER = [("mie", "0011_require_endpoint_drop_webhook_url")]


class WebhookEndpointBackfillMigrationTests(TransactionTestCase):
    def _migrate(self, targets):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(targets)
        return executor.loader.project_state(targets).apps

    def tearDown(self):
        # Leave the reused test database at the latest schema whatever happens.
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    def test_each_account_gets_an_all_events_endpoint_and_keeps_its_events(self):
        apps = self._migrate(BEFORE)
        DeveloperAccount = apps.get_model("mie", "DeveloperAccount")
        CourseSubmission = apps.get_model("mie", "CourseSubmission")
        WebhookEvent = apps.get_model("mie", "WebhookEvent")
        account = DeveloperAccount.objects.create(
            email="old@studio.io", webhook_url="https://hooks.studio.io/old"
        )
        other = DeveloperAccount.objects.create(
            email="other@studio.io", webhook_url="https://hooks.studio.io/other"
        )
        submission = CourseSubmission.objects.create(
            developer=account, title="Rust", payload={"title": "Rust"}
        )
        event = WebhookEvent.objects.create(
            submission=submission, event_type="SUBMISSION_QUEUED", payload={}
        )

        apps = self._migrate(AFTER)
        WebhookEndpoint = apps.get_model("mie", "WebhookEndpoint")
        WebhookEvent = apps.get_model("mie", "WebhookEvent")

        endpoints = {
            row.developer_id: row for row in WebhookEndpoint.objects.all()
        }
        self.assertEqual(
            sorted(
                (row.url, row.all_events, row.event_types) for row in endpoints.values()
            ),
            [
                ("https://hooks.studio.io/old", True, []),
                ("https://hooks.studio.io/other", True, []),
            ],
        )
        self.assertEqual(
            WebhookEvent.objects.get(id=event.id).endpoint_id, endpoints[account.id].id
        )
        self.assertIn(other.id, endpoints)

    def test_the_reverse_restores_webhook_url(self):
        apps = self._migrate(AFTER)
        DeveloperAccount = apps.get_model("mie", "DeveloperAccount")
        WebhookEndpoint = apps.get_model("mie", "WebhookEndpoint")
        account = DeveloperAccount.objects.create(email="back@studio.io")
        WebhookEndpoint.objects.create(developer=account, url="https://hooks.studio.io/first")
        WebhookEndpoint.objects.create(developer=account, url="https://hooks.studio.io/second")

        apps = self._migrate(BEFORE)

        DeveloperAccount = apps.get_model("mie", "DeveloperAccount")
        self.assertEqual(
            DeveloperAccount.objects.get(id=account.id).webhook_url,
            "https://hooks.studio.io/first",
        )
