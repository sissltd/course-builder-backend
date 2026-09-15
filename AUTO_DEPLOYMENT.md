# Auto-Deployment Configuration

This project uses GitHub Actions for validation and deployment automation.

## CI workflow

The workflow at `.github/workflows/ci.yml` runs on pull requests and pushes to
`staging` and `main`.

It runs:

- Ruff linting and formatting checks
- Security checks with gitleaks, Bandit, and pip-audit
- Django checks and migration validation
- Database migrations from zero
- OpenAPI schema validation
- The Django test suite
- A Docker image build using `.docker/Dockerfile`

The security checks and Ruff formatting check are currently informational, while
Ruff linting and the test/build jobs act as deployment gates.

## Staging deployment

Successful pushes to `staging` trigger the `trigger-staging-redeploy` job in
`ci.yml` after all CI jobs pass.

That job sends a POST request to the Dokploy deployment webhook stored in the
following GitHub secret:

```text
DOKPLOY_STAGING_DEPLOY_WEBHOOK
```

Dokploy then rebuilds and redeploys the application from the `staging` branch.
The webhook request is retried up to three times if Dokploy does not return a
successful HTTP status.

The staging deployment uses `compose.prod.yaml`, which defines:

- `api` — Django application served on port 8000
- `worker` — shared Celery worker
- `worker-ai` — dedicated AI Celery worker
- `beat` — Celery Beat scheduler
- `postgres` — PostgreSQL database
- `redis` — Redis broker and result backend

## Container image publishing

The workflow at `.github/workflows/cd.yml` runs after a successful CI workflow
on a push to `staging` or `main`.

It only runs when the GitHub repository variable below is set to `true`:

```text
CD_ENABLED=true
```

When enabled, it:

1. Checks out the exact commit that passed CI.
2. Logs in to DigitalOcean Container Registry.
3. Builds the image using `.docker/Dockerfile`.
4. Pushes the image to DigitalOcean Container Registry.
5. Runs an informational Trivy vulnerability scan.

The image tags are:

| Branch | Tags |
|---|---|
| `staging` | `staging`, `staging-<commit-sha>` |
| `main` | `latest`, `latest-<commit-sha>` |

Required GitHub configuration:

### Secrets

```text
DIGITALOCEAN_ACCESS_TOKEN
DOKPLOY_STAGING_DEPLOY_WEBHOOK
```

### Repository variables

```text
CD_ENABLED
DOCR_REGISTRY
DOCR_REPOSITORY
```

## Container startup behavior

The Docker image uses `.docker/entrypoint.sh` as its entrypoint. On startup it:

1. Creates the `staticfiles` and `media` directories.
2. Runs `python manage.py check`.
3. Runs database migrations when `DJANGO_RUN_MIGRATIONS=1`.
4. Runs `collectstatic` when `DJANGO_COLLECTSTATIC=1`.
5. Starts the configured application or Celery process.

The production API service enables migrations and static collection. Celery
workers and Beat disable both operations.

## DigitalOcean App Platform specification

`do-app-spec.yaml` defines an alternative DigitalOcean App Platform deployment.
It includes:

- A Gunicorn API service
- Managed PostgreSQL and Redis databases
- Runtime environment variables and secrets
- A `/health/` health check
- Automatic migrations and static collection

No GitHub workflow currently invokes this App Platform specification, so it is
not part of the active automatic deployment path unless deployed manually or
configured externally in DigitalOcean.

## Deployment flow summary

```text
Pull request / push
        |
        v
GitHub Actions CI
        |
        +--> Push to staging + CI passes
        |        |
        |        v
        |   Dokploy webhook
        |        |
        |        v
        |   Staging rebuild and redeploy
        |
        +--> Push to staging/main + CI passes + CD_ENABLED=true
                 |
                 v
        Build and push image to DigitalOcean Container Registry
```
