# Admin Roles & Permissions

Who controls what on Course Builder, and how that is decided. Every rule below
was checked against the permission classes and service checks in the code.

---

## How access is decided

Three layers, in order:

1. **The account's role.** Every platform user holds exactly one role.
2. **The endpoint's gate.** Each endpoint declares which roles may call it.
3. **The service check.** Sensitive actions re-check the role inside the
   business logic too, so no other code path can reach them without it.

On top of that:

- **Superusers pass every role check.**
- **Admin and Super Admin must enrol MFA.** Changing platform settings, and
  creating, editing or deleting categories, additionally require an
  MFA-verified session.
- **MIE developers are not platform users.** They authenticate with an API key
  or a developer session and are governed by MIE's own rules, not by these
  roles.

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

## The permission groups

Endpoints gate on these groups rather than on single roles:

| Group | Admits |
|---|---|
| `IsCourseCreatorRole` | Course Creator, Writer |
| `IsCreatorReviewerRole` | Creator Reviewer, Verifier |
| `IsQaReviewerRole` | QA Reviewer |
| `IsAdminRole` | Admin, Approver, Super Admin |
| `IsAdminOrSuperAdminRole` | Admin, Super Admin |
| `CanManageCategories` | Writer, Admin, Super Admin |
| `IsSuperAdminRole` | Super Admin |
| `IsMFAVerifiedForSession` | An MFA-verified session (stacked on another gate) |

The difference that matters most is between the two admin groups.
`IsAdminRole` includes the **Approver**. `IsAdminOrSuperAdminRole` does not, and
it guards everything touching money, people, settings and the audit trail.

---

## Who can do what

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

| Action | Who |
|---|---|
| Invite, revoke or reactivate staff — including Admins | Super Admin |
| Reset another user's MFA | Super Admin |

### Operations and MIE

| Action | Who |
|---|---|
| Operations dashboards, including MIE recommendations | Admin, Super Admin |
| MIE developers, submissions and rejection reasons | Super Admin |

---

## Admin, Approver and Super Admin

**The Super Admin alone** manages staff (invites, including Admins; revokes;
reactivation; the Teams roster), resets other users' MFA, and administers MIE.

**An Admin** can do everything else on this page.

**An Approver** is admin tier for the course pipeline only: any review seat, QA,
pricing and publishing, topics. An Approver cannot touch money, users,
settings, the audit log, operations dashboards or MIE.

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
- **The Admin role** could not be granted through the API at all. The Super
  Admin can now invite Admins.

---

## Worth knowing

- **Creator Reviewers can publish.** They sign up publicly, and publishing
  shares its gate with price review. That looks deliberate, but it is worth
  confirming.
- **The AI Reviewer role has no endpoint yet.**
- **A suspended bank account cannot be unsuspended through the API.**
- **IP-based rate limits** — sign-up, login, MIE registration — are only
  trustworthy once `DRF_NUM_PROXIES` is set for the environment. MIE's
  authenticated routes are limited per developer account and don't depend on
  it.
