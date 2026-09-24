"""Every permission the platform knows, how it is shown, and who holds it by default.

This is the single source of truth for permissions:

* `PERMISSIONS` - each codename's label, group and rules. A stored grant whose
  codename is not registered here is ignored by the resolver.
* `PERMISSION_GROUPS` - display order for the Roles & Permissions screen. The
  design's chip groups come first, then groups for capabilities the design
  does not show but that still need gating.
* `SYSTEM_ROLE_DEFAULT_GRANTS` - what each built-in role holds on a fresh
  database. The seed migration carries a frozen copy of this table; a drift
  test keeps the two equal, so a new codename cannot ship without a backfill
  migration.

The default grants reproduce exactly who each role-class gate admitted before
permissions were stored, so seeding them changes nobody's access.
"""

from dataclasses import dataclass

from api.authorization import codenames as c
from api.users.enums import UserRole


@dataclass(frozen=True)
class PermissionDef:
    codename: str
    label: str
    description: str
    group: str
    #: Shown as a chip in the design (as opposed to an extra capability group).
    is_design_chip: bool = False
    #: Codenames this one grants implicitly (e.g. Full Access grants View Only).
    implies: tuple = ()
    #: May be granted to the public sign-up roles. False for anything that
    #: acts on other people's accounts, money or platform configuration, since
    #: granting it there hands it to every public sign-up.
    grantable_to_public_roles: bool = False


@dataclass(frozen=True)
class PermissionGroup:
    key: str
    label: str
    is_design_group: bool


PERMISSION_GROUPS = (
    PermissionGroup("dashboard", "Dashboard", True),
    PermissionGroup("courses", "Courses", True),
    PermissionGroup("staff", "Staff", True),
    PermissionGroup("creators", "Creators", True),
    PermissionGroup("teams", "Teams", True),
    PermissionGroup("mie", "APE Pipeline & MIE", True),
    PermissionGroup("review_pipeline", "Review pipeline", False),
    PermissionGroup("catalog", "Catalog", False),
    PermissionGroup("platform", "Platform", False),
    PermissionGroup("earnings", "Earnings", False),
)

_PERMISSIONS = (
    # Dashboard
    PermissionDef(
        c.DASHBOARD_VIEW,
        "View Only",
        "View the admin overview, analytics and system health dashboards.",
        "dashboard",
        is_design_chip=True,
    ),
    PermissionDef(
        c.DASHBOARD_VIEW_LIMITED,
        "Limited Access",
        "View the admin dashboards with every money figure hidden.",
        "dashboard",
        is_design_chip=True,
    ),
    # Courses
    PermissionDef(
        c.COURSES_CREATE,
        "Create Course",
        "Create and author your own courses, including AI generation and imports.",
        "courses",
        is_design_chip=True,
        grantable_to_public_roles=True,
    ),
    PermissionDef(
        c.COURSES_VIEW,
        "View Course",
        "View any course on the platform, not only your own.",
        "courses",
        is_design_chip=True,
    ),
    PermissionDef(
        c.COURSES_EDIT,
        "Edit Course",
        "Edit or delete any course, its media and its quizzes.",
        "courses",
        is_design_chip=True,
    ),
    PermissionDef(
        c.COURSES_APPROVE,
        "Approve Course",
        "Claim and approve review seats your role is eligible for.",
        "courses",
        is_design_chip=True,
        grantable_to_public_roles=True,
    ),
    PermissionDef(
        c.COURSES_REJECT,
        "Reject Course",
        "Claim and reject review seats your role is eligible for.",
        "courses",
        is_design_chip=True,
        grantable_to_public_roles=True,
    ),
    PermissionDef(
        c.COURSES_ASSIGN,
        "Assign Course",
        "Take or decide any review seat, including one someone else claimed.",
        "courses",
        is_design_chip=True,
    ),
    PermissionDef(
        c.COURSES_SET_PRICING,
        "Set Course Pricing at Approval",
        "Set distribution channel pricing on a course under review.",
        "courses",
        is_design_chip=True,
        grantable_to_public_roles=True,
    ),
    PermissionDef(
        c.COURSES_FORCE_VERSION_MIGRATION,
        "Force Course Version Migration",
        "Move every unpublished course on one version to another version.",
        "courses",
        is_design_chip=True,
    ),
    # Review pipeline
    PermissionDef(
        c.COURSES_PUBLISH,
        "Publish Course",
        "Publish an approved course.",
        "review_pipeline",
        grantable_to_public_roles=True,
    ),
    PermissionDef(
        c.COURSES_DECIDE_APPEALS,
        "Decide Appeals",
        "Approve or reject creators' appeals against course rejections.",
        "review_pipeline",
    ),
    PermissionDef(
        c.COURSES_MANAGE_QUALITY,
        "Manage Quality Checks",
        "Edit the pre-submission quality checklist.",
        "review_pipeline",
    ),
    PermissionDef(
        c.REVIEWERS_ASSIGN_TRACK,
        "Assign Reviewer Track",
        "Set which production track a reviewer's queue shows.",
        "review_pipeline",
    ),
    # Staff
    PermissionDef(
        c.STAFF_VIEW,
        "View Only",
        "View the staff roster.",
        "staff",
        is_design_chip=True,
    ),
    PermissionDef(
        c.STAFF_VIEW_DETAIL,
        "View Staff Detail",
        "View a staff member's full profile.",
        "staff",
        is_design_chip=True,
    ),
    PermissionDef(
        c.STAFF_ADD,
        "Add Staff",
        "Invite new staff members.",
        "staff",
        is_design_chip=True,
    ),
    PermissionDef(
        c.STAFF_FULL_ACCESS,
        "Full Access",
        "Every staff permission, plus revoking, reactivating, changing roles and resetting MFA.",
        "staff",
        is_design_chip=True,
        implies=(
            c.STAFF_VIEW,
            c.STAFF_VIEW_DETAIL,
            c.STAFF_ADD,
            c.STAFF_DELETE,
            c.STAFF_RESET_PASSWORD,
        ),
    ),
    PermissionDef(
        c.STAFF_DELETE,
        "Delete Staff",
        "Permanently delete a staff account and erase its personal data.",
        "staff",
        is_design_chip=True,
    ),
    PermissionDef(
        c.STAFF_RESET_PASSWORD,
        "Reset password",
        "Email a staff member a password reset link.",
        "staff",
        is_design_chip=True,
    ),
    # Creators
    PermissionDef(
        c.CREATORS_VIEW_WALLET,
        "View Wallet",
        "View every creator's wallet, transactions, withdrawals and payout accounts.",
        "creators",
        is_design_chip=True,
    ),
    PermissionDef(
        c.CREATORS_SUSPEND,
        "Suspend Account",
        "Suspend, deactivate or reinstate a creator, and suspend payout accounts.",
        "creators",
        is_design_chip=True,
    ),
    PermissionDef(
        c.CREATORS_ISSUE_REFUND,
        "Issue Refund",
        "Credit or debit a creator's wallet, with a reason.",
        "creators",
        is_design_chip=True,
    ),
    PermissionDef(
        c.CREATORS_APPROVE_ACCOUNT,
        "Approve Account",
        "Review KYC submissions: approve, reject or flag.",
        "creators",
        is_design_chip=True,
    ),
    PermissionDef(
        c.CREATORS_VIEW_PROFILE,
        "View Profile",
        "View any user's profile and activity.",
        "creators",
        is_design_chip=True,
    ),
    # Teams
    PermissionDef(
        c.TEAMS_INVITE,
        "Invite Teams",
        "Invite someone to join as a Creator Reviewer.",
        "teams",
        is_design_chip=True,
    ),
    PermissionDef(
        c.TEAMS_SUSPEND,
        "Suspend Account",
        "Suspend, deactivate or reinstate a team member or creator (not an Admin).",
        "teams",
        is_design_chip=True,
    ),
    PermissionDef(
        c.TEAMS_DELETE_ACCOUNT,
        "Delete Account",
        "Permanently delete a team member or creator account (not an Admin) and erase its personal data.",
        "teams",
        is_design_chip=True,
    ),
    PermissionDef(
        c.TEAMS_RESET_PASSWORD,
        "Reset Password",
        "Email a team member or creator (not an Admin) a password reset link.",
        "teams",
        is_design_chip=True,
    ),
    # APE pipeline & MIE
    PermissionDef(
        c.MIE_VIEW_PIPELINE,
        "View APE Pipeline",
        "View the production pipeline dashboard.",
        "mie",
        is_design_chip=True,
    ),
    PermissionDef(
        c.MIE_APPROVE_TOPIC_PROPOSALS,
        "Approve MIE Topics Proposals",
        "Approve or reject course ideas from the Market Intelligence Engine.",
        "mie",
        is_design_chip=True,
    ),
    PermissionDef(
        c.MIE_MANAGE_CONSOLE,
        "Manage MIE Console",
        "Manage MIE developer accounts, submissions and rejection reasons.",
        "mie",
    ),
    # Catalog
    PermissionDef(
        c.CATALOG_MANAGE_CATEGORIES,
        "Manage Categories",
        "Create, edit and archive categories, and decide category requests.",
        "catalog",
    ),
    PermissionDef(
        c.CATALOG_MANAGE_TOPICS,
        "Manage Topics",
        "Edit topics and decide topic reservation requests.",
        "catalog",
        grantable_to_public_roles=True,
    ),
    PermissionDef(
        c.CATALOG_VIEW_TOPIC_QUEUE,
        "View Topic Reservation Queue",
        "View the admin topic reservation screens.",
        "catalog",
    ),
    # Platform
    PermissionDef(
        c.PLATFORM_EDIT_SETTINGS,
        "Edit Platform Settings",
        "Change platform-wide thresholds, providers and payment settings.",
        "platform",
    ),
    PermissionDef(
        c.AUDIT_VIEW,
        "View Audit Logs",
        "Read the platform audit and activity logs.",
        "platform",
    ),
    PermissionDef(
        c.ACHIEVEMENTS_MANAGE,
        "Manage Achievements",
        "Create, configure, award and revoke achievement badges.",
        "platform",
    ),
    PermissionDef(
        c.ROLES_VIEW,
        "View Roles & Permissions",
        "View roles and what each may do.",
        "platform",
    ),
    PermissionDef(
        c.ROLES_MANAGE,
        "Manage Roles & Permissions",
        "Create, edit and delete roles, within the permissions you hold yourself.",
        "platform",
        implies=(c.ROLES_VIEW,),
    ),
    # Earnings
    PermissionDef(
        c.EARNINGS_MANAGE_OWN,
        "Manage Own Earnings",
        "View your own wallet and withdraw your earnings.",
        "earnings",
        grantable_to_public_roles=True,
    ),
)

PERMISSIONS = {permission.codename: permission for permission in _PERMISSIONS}
ALL_CODENAMES = frozenset(PERMISSIONS)

CC = UserRole.COURSE_CREATOR
CR = UserRole.CREATOR_REVIEWER
W = UserRole.STAFF_WRITER
V = UserRole.STAFF_VERIFIER
AP = UserRole.STAFF_APPROVER
AI = UserRole.AI_REVIEWER
QA = UserRole.QA_REVIEWER
AD = UserRole.ADMIN
SA = UserRole.SUPER_ADMIN

#: Who held each capability under the role-class gates. Read as "codename:
#: roles"; inverted below into per-role grants.
_DEFAULT_HOLDERS = {
    c.DASHBOARD_VIEW: (AD, SA),
    # New capabilities with no predecessor gate: conservative defaults chosen
    # with the product owner, editable on the Roles & Permissions screen.
    c.DASHBOARD_VIEW_LIMITED: (SA,),
    c.COURSES_FORCE_VERSION_MIGRATION: (AD, AP, SA),
    c.STAFF_DELETE: (SA,),
    c.STAFF_RESET_PASSWORD: (SA,),
    c.CREATORS_ISSUE_REFUND: (SA,),
    c.TEAMS_INVITE: (AD, SA),
    c.TEAMS_DELETE_ACCOUNT: (SA,),
    c.TEAMS_RESET_PASSWORD: (AD, SA),
    c.COURSES_CREATE: (CC, W),
    c.COURSES_VIEW: (AD, AP, SA),
    c.COURSES_EDIT: (AD, AP, SA),
    c.COURSES_APPROVE: (CR, V, QA, AD, AP, SA),
    c.COURSES_REJECT: (CR, V, QA, AD, AP, SA),
    c.COURSES_ASSIGN: (AD, AP, SA),
    c.COURSES_SET_PRICING: (CR, V, AD, AP, SA),
    c.COURSES_PUBLISH: (CR, V, AD, AP, SA),
    c.COURSES_DECIDE_APPEALS: (AD, SA),
    c.COURSES_MANAGE_QUALITY: (AD, SA),
    c.REVIEWERS_ASSIGN_TRACK: (AD, SA),
    c.STAFF_VIEW: (SA,),
    c.STAFF_VIEW_DETAIL: (SA,),
    c.STAFF_ADD: (SA,),
    c.STAFF_FULL_ACCESS: (SA,),
    c.CREATORS_VIEW_WALLET: (AD, SA),
    c.CREATORS_SUSPEND: (AD, SA),
    c.CREATORS_APPROVE_ACCOUNT: (AD, SA),
    c.CREATORS_VIEW_PROFILE: (AD, SA),
    c.TEAMS_SUSPEND: (AD, SA),
    c.MIE_VIEW_PIPELINE: (AD, SA),
    c.MIE_APPROVE_TOPIC_PROPOSALS: (W, SA),
    c.MIE_MANAGE_CONSOLE: (SA,),
    c.CATALOG_MANAGE_CATEGORIES: (W, AD, SA),
    c.CATALOG_MANAGE_TOPICS: (CR, V, AD, AP, SA),
    c.CATALOG_VIEW_TOPIC_QUEUE: (AD, AP, SA),
    c.PLATFORM_EDIT_SETTINGS: (AD, SA),
    c.AUDIT_VIEW: (AD, SA),
    c.ACHIEVEMENTS_MANAGE: (W, AD, SA),
    c.ROLES_VIEW: (AD, SA),
    c.ROLES_MANAGE: (SA,),
    c.EARNINGS_MANAGE_OWN: (CC, W),
}

SYSTEM_ROLE_DEFAULT_GRANTS: dict[str, frozenset] = {
    role: frozenset(
        codename for codename, holders in _DEFAULT_HOLDERS.items() if role in holders
    )
    for role in UserRole.values
}
# The Super Admin is locked to every permission (the resolver grants it all
# regardless); storing the full set keeps the screen honest about that.
SYSTEM_ROLE_DEFAULT_GRANTS[UserRole.SUPER_ADMIN] = ALL_CODENAMES

#: The public sign-up roles; see PermissionDef.grantable_to_public_roles.
PUBLIC_ROLES = (UserRole.COURSE_CREATOR, UserRole.CREATOR_REVIEWER)


def expand(codenames) -> frozenset:
    """`codenames` plus everything they imply, restricted to registered ones."""

    expanded = set()
    pending = list(codenames)
    while pending:
        codename = pending.pop()
        permission = PERMISSIONS.get(codename)
        if permission is None or codename in expanded:
            continue
        expanded.add(codename)
        pending.extend(permission.implies)
    return frozenset(expanded)
