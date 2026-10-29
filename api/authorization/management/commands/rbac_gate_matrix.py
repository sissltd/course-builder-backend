import json
from pathlib import Path

from django.core.management.base import BaseCommand

from api.authorization.gate_matrix import build_unsaved_principals, compute_matrix

BASELINE_PATH = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "parity"
    / "gate_matrix_baseline.json"
)


class Command(BaseCommand):
    help = (
        "Write the view-gate matrix (who passes each endpoint's permission "
        "gate) to the parity baseline. Run only to record a deliberate change "
        "in access; the parity test fails on any undeclared difference."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--stdout",
            action="store_true",
            help="Print the matrix instead of overwriting the baseline file.",
        )

    def handle(self, *args, **options):
        payload = (
            json.dumps(compute_matrix(build_unsaved_principals()), indent=2) + "\n"
        )
        if options["stdout"]:
            self.stdout.write(payload)
            return
        BASELINE_PATH.write_text(payload)
        self.stdout.write(self.style.SUCCESS(f"Wrote {BASELINE_PATH}"))
