"""Access is decided by stored permissions, never by role-class gates.

The role classes (IsAdminRole, require_role, allowed_roles, ...) were
replaced by `Perm(...)` and `permission_service`. A gate written against a
role would ignore the Roles & Permissions screen entirely, so this fails if
one reappears anywhere in application code.
"""

import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

FORBIDDEN = re.compile(
    r"\b(HasRole|require_role|allowed_roles|Is(Admin|AdminOrSuperAdmin|SuperAdmin|CourseCreator|"
    r"PublicCourseCreator|CreatorReviewer|QaReviewer|AiReviewer)Role|CanManageCategories|"
    r"CanManageAchievements|CanDecideMieIdeas)\b"
)
SCANNED = ("api", "shared", "config", "core")


class NoRoleGatesTests(SimpleTestCase):
    def test_no_role_class_gates_in_application_code(self):
        base = Path(settings.BASE_DIR)
        offenders = []
        for top in SCANNED:
            for path in (base / top).rglob("*.py"):
                if (
                    "migrations" in path.parts
                    or path.resolve() == Path(__file__).resolve()
                ):
                    continue
                for number, line in enumerate(path.read_text().splitlines(), start=1):
                    if FORBIDDEN.search(line):
                        offenders.append(
                            f"{path.relative_to(base)}:{number}: {line.strip()}"
                        )
        self.assertEqual(offenders, [])
