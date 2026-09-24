# Admin Roles & Permissions

Who controls what on Course Builder, and how that is decided. Every rule below
was checked against the permission classes and service checks in the code.

---

## How access is decided

1. **The account's role.** Every user holds one role: a built-in role, or a
   custom role created on the Roles & Permissions screen.
2. **The role's permissions.** A role is a set of permissions, such as
   Approve Course or Issue Refund. Every endpoint checks for a specific
   permission, not for a role.
3. **The service check.** Sensitive actions check the permission again inside
   the business logic, so no other code path can skip it.

On top of that:

- **The Super Admin holds every permission.** That role is locked: it can't be
  edited, deleted or handed out. Superusers hold everything too.
- **A custom role has a base role**, one of the staff roles. The base role
  decides how its members work, not what they may do: which review seats they
  sit, whether MFA is mandatory, and which workspace they land in.
- **Admin and Super Admin must enrol MFA.** Changing platform settings, and
  creating, editing or deleting categories, need an MFA-verified session.
- **Money and identity actions need a session that passed MFA, whatever the
  role.** That covers editing roles, changing someone's role, deleting an
  account and adjusting a wallet.
- **MIE developers are not platform users.** They authenticate with an API key
  or a developer session and follow MIE's own rules.

---

## The roles

| Role | What it is for | How someone gets it |
|---|---|---|
| Course Creator | Authors courses | Public sign-up |
| Creator Reviewer | First and Second Review of courses | Public sign-up (the reviewer sign-up) |
| Writer | Authors courses; manages categories | Invited by the Super Admin |
| Verifier | The Verification seat of content review | Invited |
| Approver | Admin tier for the course pipeline | Invited |
| QA Reviewer | QA verification of course media | Invited |
| AI Reviewer | Reserved for the AI review track — nothing uses it yet | Invited |
| Admin | Platform administration | Invited by the Super Admin; must enrol MFA |
| Super Admin | The single platform owner | One-time bootstrap; only one can ever exist |

---

## Permissions and their default holders

The built-in roles start with exactly the access they had before roles became
editable. Admins can change any built-in role except Super Admin, and create
new roles.

| Permission | Built-in roles that hold it by default |
|---|---|
| Dashboard: View Only | Admin |
| Dashboard: Limited Access (dashboards without money figures) | — |
| Create Course | Course Creator, Writer |
| View Course / Edit Course (any course) | Approver, Admin |
| Approve Course / Reject Course (review seats their role can sit) | Creator Reviewer, Verifier, QA Reviewer, Approver, Admin |
| Assign Course (take or assign any seat) | Approver, Admin |
| Set Course Pricing at Approval; Publish Course | Creator Reviewer, Verifier, Approver, Admin |
| Force Course Version Migration | Approver, Admin |
| Decide appeals; manage quality checks; assign reviewer tracks | Admin |
| Staff: View Only, View Staff Detail, Add Staff, Full Access, Delete Staff, Reset password (any account, Admins included) | Super Admin only |
| Creators: View Wallet, Suspend Account, Approve Account (KYC), View Profile | Admin |
| Creators: Issue Refund | Super Admin only |
| Teams: Invite Teams, Suspend Account, Reset Password (any account except Admins) | Admin |
| Teams: Delete Account (any account except Admins) | Super Admin only |
| View APE Pipeline | Admin |
| Approve MIE Topics Proposals | Writer |
| Manage MIE console | Super Admin only |
| Manage categories | Writer, Admin |
| Manage topics | Creator Reviewer, Verifier, Approver, Admin |
| Topic reservation queue | Approver, Admin |
| Edit platform settings; view audit logs | Admin |
| Manage achievements | Writer, Admin |
| View roles | Admin |
| Manage roles | Super Admin only |
| Manage own earnings | Course Creator, Writer |

The Super Admin holds every permission in the table.

Guard rails on managing roles, for anyone other than the Super Admin:

- You can only grant or remove permissions you hold.
- You can't edit your own role, and nobody can edit the Super Admin role.
- You can only assign a role whose permissions you hold, to someone whose
  permissions you hold.

**Course Creator and Creator Reviewer are public sign-up roles.** They can
never be given permissions that act on other people's accounts, money or
platform settings, because every self-registered user would then hold them.

---

## Who can do what (by default)

The tables below describe the built-in roles as shipped. A role's permissions can be changed on the Roles & Permissions screen.

### The course pipeline

| Action | Who |
|---|---|
| Create and edit courses | Course Creator, Writer (their own); Admin, Approver, Super Admin (any) |
| Submit a course for review | The course's owner |
| First Review, Second Review | Creator Reviewer — two different people |
| Verification | Verifier |
| Take any review seat, or decide one someone else claimed | Admin, Approver, Super Admin — logged every time |
| QA verification | QA Reviewer, Admin, Approver, Super Admin |
| Review prices and publish | Creator Reviewer, Verifier, Admin, Approver, Super Admin |
| Decide an appeal | Admin, Super Admin |
| Write topics | Creator Reviewer, Verifier, Admin, Approver, Super Admin |

The review seats are described in full in
[content_review_chain_handover.md](../frontend/content_review_chain_handover.md).

### Catalog and platform

| Action | Who |
|---|---|
| Create, edit or delete categories | Writer, Admin, Super Admin — MFA session required |
| File a category request | Course Creator, Writer, Admin, Super Admin |
| Approve or reject a category request | Writer, Admin, Super Admin |
| Change platform settings | Admin, Super Admin — MFA session required |
| Create, configure, delete, award or revoke achievement badges | Writer, Admin, Super Admin |
| View your own badges and progress | Course Creator, Writer |
| View the Roles & Permissions catalogue | Admin, Super Admin |

### People and money

| Action | Who |
|---|---|
| Review KYC submissions | Admin, Super Admin |
| Suspend or deactivate users | Admin, Super Admin |
| Wallet and withdrawal administration | Admin, Super Admin |
| Suspend a user's bank account | Admin, Super Admin |
| Read the platform-wide audit log | Admin, Super Admin |
| Download your own audit trail | Any signed-in user |

### Staff and security

**Staff and Teams are one group.** Everyone on the Teams page is staff,
Creator Reviewers included, and anyone on it can be moved to any other
staff role. The Staff and Teams permission groups both act on that same
group; Admin and Super Admin accounts are managed through the Staff
permissions only.

| Action | Who |
|---|---|
| Invite, revoke or reactivate staff — including Admins and Creator Reviewers | Super Admin |
| Invite a Creator Reviewer (Invite Teams) | Admin, Super Admin |
| Reset another user's MFA | Super Admin |

### Operations and MIE

| Action | Who |
|---|---|
| Operations dashboards | Admin, Super Admin |
| MIE recommendations (approve or reject ideas) | Writer, Super Admin — not a plain Admin |
| MIE developers, submissions and rejection reasons | Super Admin |

---

## Admin, Approver and Super Admin (by default)

**The Super Admin** holds everything, including managing staff and roles,
deleting accounts, issuing refunds and the MIE console.

**An Admin** holds the rest of the admin surface.

**An Approver** is the admin tier for the course pipeline only: any review
seat, QA, pricing and publishing, topics, assigning courses and version
migration.

Any of these can be widened or narrowed on the Roles & Permissions screen,
except the Super Admin.

---

## Recently corrected

- **Bank account suspension** was open to Approvers. It is now Admin or Super
  Admin, re-checked inside the service. It is also recorded on the account
  owner's activity log with the admin as the actor; previously it was written
  to the admin's own log, so the owner had no record of it.
- **The audit log** was gated by Django's `is_staff` flag, which only the
  bootstrapped Super Admin had, so no invited Admin could read it. It now uses
  the platform's roles.
- **Category requests** disagreed with themselves. The endpoint let Approvers
  through only for the service to refuse them, and hid requests from Writers
  who were allowed to decide them. The endpoint, the list and the service now
  share one rule: Writer, Admin, Super Admin.
- **The in-app notifications toggle** was saved but ignored. Switching it off
  now stops in-app notifications, except account suspension/reinstatement and
  the admin security alerts (repeated login lockouts, MIE circuit breaker).
- **MIE recommendations** were listed here as Admin and Super Admin. The screen
  is actually Writer and Super Admin; a plain Admin is refused.
- **Notifications about requests now reach whoever can act on them.**
  Category requests used to notify Admins and Approvers. They now notify
  holders of Manage categories (Writers, Admins and Super Admin by default).
- **Voluntary MFA is now enforced.** Anyone who has enrolled an MFA device is
  challenged at login in production, not only Admins.
- **The Admin role** could not be granted through the API at all. The Super
  Admin can now invite Admins.

---

## Achievement badges

Staff define badges; Course Creators and Writers earn them. Each badge counts
one thing per creator — courses **created**, courses that **passed content
review** (all three seats), courses **approved** (passed QA), or courses
**published** — and is earned when that count reaches its requirement.

- **Auto award on:** creators get the badge automatically, including everyone
  who already qualifies when the badge is created or made easier.
- **Auto award off:** staff award it by hand.
- Raising a requirement never takes a badge away. Deleting a badge removes it
  from everyone, optionally moving holders to the next badge down on the same
  criterion.

---

## The Roles & Permissions screen

Admins with Manage roles can:

- add roles
- tick or untick permissions on any role except Super Admin
- see each role's members
- move staff between roles

A change applies to every member on their next request. Moving someone to
another role signs them out.

First and Second Review stay one role, Creator Reviewer, with the rule that
the two seats need different people.

---

## Account and money actions

- **Delete Staff / Delete Account.**
  - The person can never sign in again, and their personal details are erased.
  - Their courses, payouts, reviews and history stay, under an anonymous account.
  - Refused while money is still on the account.
  - Can't be undone.
- **Issue Refund.**
  - Credits or debits a creator's wallet, with a reason.
  - The wallet can never go below zero, and a retry never moves money twice.
- **Reset password.** Emails the person a reset link. Nothing changes until
  they use it.
- **Assign Course.** Puts a specific reviewer in a course's seat. The reviewer
  must still be allowed to sit it.
- **Force Course Version Migration.** Moves unpublished courses from one
  version to another. Published courses are untouched.

---

## Worth knowing

- **Creator Reviewers can publish.** They sign up publicly, and publishing
  shares its gate with price review. That looks deliberate, but it is worth
  confirming.
- **The AI Reviewer role has no endpoint yet.**
- **"Courses created" badges count drafts.** Drafts can be deleted, but a badge
  once earned is kept, so a creator could earn one by creating and deleting
  drafts. Counting only submitted courses would close that; it needs a
  product decision.
- **A suspended bank account cannot be unsuspended through the API.**
- **IP-based rate limits** — sign-up, login, MIE registration — are only
  trustworthy once `DRF_NUM_PROXIES` is set for the environment. MIE's
  authenticated routes are limited per developer account and don't depend on
  it.
