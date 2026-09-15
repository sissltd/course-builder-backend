# Admin Writer — Categories

This guide explains how an Admin Writer consumes the category API. Categories
control where creators file courses and the creator payout for each difficulty
level.

## Access

Use a bearer access token for a user with the `STAFF_WRITER`, `ADMIN`, or
`SUPER_ADMIN` role. Create, update, archive, unarchive, and delete operations
also require an MFA-verified session.

Base path: `/api/v1/categories/`

## Read categories

Load the admin table with:

```http
GET /api/v1/categories/
```

The response is paginated. It includes active, inactive, and archived
categories, ordered by name by default. Use the category `id` for subsequent
operations. The response includes the three creator prices, `icon`,
`track_preference`, `status`, timestamps, and `total_courses`.

Fetch one category with:

```http
GET /api/v1/categories/{category_id}/
```

The `description` field is not part of the category API.

For dashboard tiles:

```http
GET /api/v1/categories/stats/
```

The returned `total`, `active`, `inactive`, and `archived` counts exclude
soft-deleted categories.

## Create and edit

Create a category with:

```http
POST /api/v1/categories/
Content-Type: application/json

{
  "name": "Software Engineering",
  "creator_price_beginner": "150.00",
  "creator_price_intermediate": "200.00",
  "creator_price_advanced": "300.00",
  "icon": "code",
  "track_preference": "OPEN",
  "status": "ACTIVE"
}
```

`name` must be unique. Prices must be zero or greater. Price changes affect
future submissions; a submitted course keeps its existing price snapshot.

Use `PATCH` for a partial edit:

```http
PATCH /api/v1/categories/{category_id}/
Content-Type: application/json

{ "status": "INACTIVE" }
```

`INACTIVE` prevents new course submissions while leaving existing courses
untouched. `ARCHIVED` is managed with the archive endpoint below.

## Archive and restore

Archive a category without deleting its record or courses:

```http
POST /api/v1/categories/{category_id}/archive/
```

Restore it to `ACTIVE`:

```http
POST /api/v1/categories/{category_id}/unarchive/
```

Archived categories are hidden from the creator picker but remain visible to
Admin Writers.

## Category requests

Admin Writers review every creator request from:

```http
GET /api/v1/admin/category-requests/
```

Useful filters are `status`, `search`, `requested_by`, `date_from`, and
`date_to`. Retrieve one request with:

```http
GET /api/v1/admin/category-requests/{request_id}/
```

Approve a pending request by supplying the starting payout rate:

```http
POST /api/v1/admin/category-requests/{request_id}/approve/
Content-Type: application/json

{ "creator_price": "150.00", "track_preference": "OPEN" }
```

Reject a pending request with:

```http
POST /api/v1/admin/category-requests/{request_id}/reject/
```

The admin response includes requester and reviewer identity. Category-request
descriptions belong to the request record; they are not copied onto the
created Category.

## Topic requests

Admin Writers review every creator topic request from:

```http
GET /api/v1/admin/topic-requests/
GET /api/v1/admin/topic-requests/{request_id}/
```

The queue supports `status`, `category`, `search`, `requested_by`, `date_from`,
`date_to`, and ordering filters. Approve or reject a pending request with:

```http
POST /api/v1/admin/topic-requests/{request_id}/approve/
POST /api/v1/admin/topic-requests/{request_id}/reject/
Content-Type: application/json

{ "reason": "A matching topic already exists." }
```

Approval creates and reserves the topic using the category's beginner payout;
rejection keeps the request in history with its reason.

## Delete safely

Preview the impact before deleting:

```http
GET /api/v1/categories/{category_id}/deletion-impact/
```

If the category has no courses, delete it with:

```http
DELETE /api/v1/categories/{category_id}/
```

Deletion is a soft delete: the database row and audit history remain, but the
category disappears from normal category lists, statistics, and the creator
picker. The response is `204 No Content`.

If courses are attached, the API returns `409 Conflict` unless a strategy is
provided. Reassign them:

```http
DELETE /api/v1/categories/{category_id}/?strategy=REASSIGN&replacement_category={replacement_id}
```

Or permanently remove the courses and their dependent content before
soft-deleting the category:

```http
DELETE /api/v1/categories/{category_id}/?strategy=DELETE_COURSES
```

Use `DELETE_COURSES` carefully: it can remove published course content,
modules, lessons, assessments, and review history.

## Creator picker

Creators do not use the admin list. They use:

```http
GET /api/v1/categories/picker/
```

The response contains only active, non-deleted categories with `id`, `name`,
and `is_active`. Send the selected `id` when creating a course.
