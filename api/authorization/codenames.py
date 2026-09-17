"""Permission codenames: the stable strings stored on roles and checked by gates.

Constants only. Labels, groups and default holders live in `registry.py`;
never add a codename here without registering it there in the same change.
"""

# Dashboard
DASHBOARD_VIEW = "dashboard.view"
DASHBOARD_VIEW_LIMITED = "dashboard.view_limited"

# Courses
COURSES_CREATE = "courses.create"
COURSES_VIEW = "courses.view"
COURSES_EDIT = "courses.edit"
COURSES_APPROVE = "courses.approve"
COURSES_REJECT = "courses.reject"
COURSES_ASSIGN = "courses.assign"
COURSES_SET_PRICING = "courses.set_pricing"
COURSES_FORCE_VERSION_MIGRATION = "courses.force_version_migration"

# Review pipeline
COURSES_PUBLISH = "courses.publish"
COURSES_DECIDE_APPEALS = "courses.decide_appeals"
COURSES_MANAGE_QUALITY = "courses.manage_quality"
REVIEWERS_ASSIGN_TRACK = "reviewers.assign_track"

# Staff (invited staff accounts)
STAFF_VIEW = "staff.view"
STAFF_VIEW_DETAIL = "staff.view_detail"
STAFF_ADD = "staff.add"
STAFF_FULL_ACCESS = "staff.full_access"
STAFF_DELETE = "staff.delete"
STAFF_RESET_PASSWORD = "staff.reset_password"

# Creators
CREATORS_VIEW_WALLET = "creators.view_wallet"
CREATORS_SUSPEND = "creators.suspend"
CREATORS_APPROVE_ACCOUNT = "creators.approve_account"
CREATORS_VIEW_PROFILE = "creators.view_profile"
CREATORS_ISSUE_REFUND = "creators.issue_refund"

# Teams (non-staff accounts: Creator Reviewers and creators)
TEAMS_INVITE = "teams.invite"
TEAMS_SUSPEND = "teams.suspend"
TEAMS_DELETE_ACCOUNT = "teams.delete_account"
TEAMS_RESET_PASSWORD = "teams.reset_password"

# APE pipeline & MIE
MIE_VIEW_PIPELINE = "mie.view_pipeline"
MIE_APPROVE_TOPIC_PROPOSALS = "mie.approve_topic_proposals"
MIE_MANAGE_CONSOLE = "mie.manage_console"

# Catalog
CATALOG_MANAGE_CATEGORIES = "catalog.manage_categories"
CATALOG_MANAGE_TOPICS = "catalog.manage_topics"
CATALOG_VIEW_TOPIC_QUEUE = "catalog.view_topic_queue"

# Platform
PLATFORM_EDIT_SETTINGS = "platform.edit_settings"
AUDIT_VIEW = "audit.view"
ACHIEVEMENTS_MANAGE = "achievements.manage"
ROLES_VIEW = "roles.view"
ROLES_MANAGE = "roles.manage"

# Earnings (a creator's own money)
EARNINGS_MANAGE_OWN = "earnings.manage_own"
