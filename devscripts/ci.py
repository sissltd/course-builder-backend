"""Run the same gates CI runs, locally, before you push.

Needs a local Postgres + Redis and the same .env as normal development
(see docker-compose.yaml / .env.example).
"""
import subprocess
import sys

GATES = [
    (["ruff", "check", "."], True),
    (["ruff", "format", "--check", "."], False),
    (["python", "manage.py", "makemigrations", "--check", "--dry-run"], True),
    (["python", "manage.py", "spectacular", "--file", "/tmp/openapi-schema.yaml", "--validate"], True),
    (["python", "manage.py", "test"], True),
]


def main() -> int:
    failed = False
    for command, blocking in GATES:
        print(f"\n$ {' '.join(command)}")
        result = subprocess.run(command)
        if result.returncode != 0:
            label = "FAILED" if blocking else "FAILED (informational)"
            print(f"-- {label}: {' '.join(command)}")
            failed = failed or blocking
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
