#!/usr/bin/env python3
"""
gc.py — course-builder git workflow helper.

Ported from the Feexeet script of the same name, with the CI gates swapped for
this project's (see devscripts/ci.py) and the test suite kept out of the
pre-commit gate — see run_ci below for why.

Usage:
  python3 devscripts/gc.py -b [--auto] [--no-wait] [--no-pr] [--skip-ci]
                             [--with-tests]
                           New feature branch flow.
                           Prompts for branch + message, creates (or switches
                           to, if it already exists locally) the branch,
                           previews `git status --short` and asks before
                           staging, commits with "<prefix>: <message>"
                           (prefix = part before "/" in the branch name),
                           pushes with -u, then opens a pull request against
                           staging with `pullrequest.md` as the description.

                           Shorthand (same as feexeet): flags and positionals
                           may be mixed in any order —
                             gc.py -b feat/x "message" --auto
                             gc.py -b --no-wait feat/x "message"
                           With two positionals the prompts are skipped.

                           By default it then WAITS HERE for GitHub's checks,
                           notifies you when they settle, and asks whether to
                           merge. On yes it merges, switches to staging and
                           pulls. The remote branch is left in place.

  python3 devscripts/gc.py -staging [--skip-ci] [--with-tests]
                           Quick commit + push on the staging branch.
                           Prompts for a commit message only. Uses the
                           message verbatim (no prefix). Refuses to run if
                           you aren't currently on `staging`.

Optional flags:
  --auto        Hand the merge to GitHub instead of waiting here: the PR merges
                itself the moment checks go green, whether or not this terminal
                (or the laptop) is still on. Returns immediately, leaves you on
                the feature branch. Needs auto-merge enabled on the repo.

  --no-wait     Open the PR and stop. No watching, no merging.

  --no-pr       Don't open a PR at all — the old manual flow.

  --skip-ci     Skip LOCAL CI checks (ruff, Django system check, migration
                consistency, OpenAPI validation) before committing. GitHub's
                checks still run on the PR.

  --with-tests  Also run the full Django test suite locally before committing.
                Off by default: this suite takes 10-18 minutes and needs
                Postgres AND Redis reachable, which would make the common path
                unusable. GitHub runs it on every PR regardless.

Runs from anywhere — it chdirs to the repository root first, so `manage.py` and
`pullrequest.md` resolve no matter which directory you invoke it from.

Requires the GitHub CLI (`gh`), authenticated. Without it the script falls
back to the manual "have you merged?" prompt rather than failing — the branch
is already pushed by that point, so nothing is ever lost.
"""

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


# ─── colors (ANSI, zero deps) ──────────────────────────────
class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    CYAN = "\033[36m"
    YELLOW = "\033[33m"
    MAGENTA = "\033[35m"
    GREEN = "\033[32m"
    RED = "\033[31m"
    GREY = "\033[90m"


# ─── ui helpers ────────────────────────────────────────────
def header(title: str) -> None:
    bar = "─" * 45
    print(f"\n{C.CYAN}╭{bar}╮{C.RESET}")
    print(f"{C.CYAN}│{C.RESET}  {C.BOLD}{title.ljust(41)}{C.RESET}{C.CYAN}│{C.RESET}")
    print(f"{C.CYAN}╰{bar}╯{C.RESET}\n")


def prompt(text: str) -> str:
    return input(f"{C.YELLOW}? {text}{C.RESET} ").strip()


def info(label: str, value: str) -> None:
    print(f"  {C.MAGENTA}↳{C.RESET} {C.DIM}{label:<18}{C.RESET}{value}")


def success(msg: str) -> None:
    print(f"\n  {C.GREEN}✔  {msg}{C.RESET}\n")


def failure(msg: str) -> None:
    print(f"\n  {C.RED}✖  {msg}{C.RESET}\n")


def fatal(msg: str, code: int = 1) -> None:
    failure(msg)
    sys.exit(code)


# ─── git plumbing ──────────────────────────────────────────
def run(cmd: list[str]) -> tuple[int, str]:
    """
    Run a command, streaming combined stdout/stderr live to the terminal,
    and return (exit_code, captured_output) so the caller can inspect it
    (used for CONFLICT detection on `git pull`).
    """
    print(f"{C.DIM}▶ {' '.join(cmd)}{C.RESET}")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    captured: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        print(f"  {line.rstrip()}")
        captured.append(line)
    proc.wait()
    print()
    return proc.returncode, "".join(captured)


def run_quiet(cmd: list[str]) -> tuple[int, str]:
    """Run silently; return (exit_code, stdout)."""
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode, proc.stdout


def run_capture(cmd: list[str]) -> tuple[int, str, str]:
    """Run silently; return (exit_code, stdout, stderr).

    `run_quiet` drops stderr, which is exactly where `gh` writes the reason a
    command failed ("pull request already exists", "Protected branch update
    failed", ...). Those messages are the useful part, so keep them.
    """
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def repo_root() -> Path:
    """The repository root, so this works from any working directory.

    The script lives in devscripts/, so the natural way to run it is
    `python3 devscripts/gc.py` from the root — but nothing stops you invoking
    it from inside devscripts/, and every `manage.py` call below would then
    fail confusingly.
    """
    code, out = run_quiet(["git", "rev-parse", "--show-toplevel"])
    if code != 0:
        fatal("not inside a git repo (or git not installed).")
    return Path(out.strip())


def current_branch() -> str:
    code, out = run_quiet(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    if code != 0:
        fatal("not inside a git repo (or git not installed).")
    return out.strip()


def branch_exists_locally(branch: str) -> bool:
    """True when a local ref `refs/heads/<branch>` already exists."""
    code, _ = run_quiet(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"]
    )
    return code == 0


def checkout_or_create(branch: str) -> None:
    """
    Switch to `branch`. If it already exists locally (e.g. on a re-run after
    a pre-commit hook failed), check it out instead of trying to re-create
    and failing with "already exists".
    """
    if branch_exists_locally(branch):
        print(
            f"  {C.DIM}branch '{branch}' already exists locally — "
            f"switching to it instead of creating.{C.RESET}\n"
        )
        if run(["git", "checkout", branch])[0] != 0:
            fatal(f"failed to switch to existing branch '{branch}'.")
    else:
        if run(["git", "checkout", "-b", branch])[0] != 0:
            fatal(f"failed to create branch '{branch}'.")


def preview_and_stage() -> None:
    """
    Show `git status --short`, ask before staging, then run `git add .`.

    Closes the blunt-instrument risk of `git add .` blindly staging
    stray scratch files, fresh untracked configs, etc. Aborts cleanly
    on empty tree or no-confirm.
    """
    code, status_out = run_quiet(["git", "status", "--short"])
    if code != 0:
        fatal("failed to read git status.")
    if not status_out.strip():
        fatal("working tree clean — nothing to stage.")

    print(f"  {C.DIM}files git will stage:{C.RESET}")
    for line in status_out.splitlines():
        # `git status --short` lines look like " M path", "?? path", etc.
        # First two chars are the status code, then a space, then the path.
        code_part, path_part = line[:2], line[3:]
        print(f"    {C.YELLOW}{code_part}{C.RESET}  {path_part}")
    print()

    answer = prompt("Stage all of the above? [y/n]:").lower()
    if answer not in ("y", "yes"):
        fatal("aborted — nothing staged.")

    if run(["git", "add", "."])[0] != 0:
        fatal("git add failed.")


def has_staged_changes() -> bool:
    code, out = run_quiet(["git", "diff", "--cached", "--name-only"])
    return code == 0 and bool(out.strip())


# ─── CI checks ─────────────────────────────────────────────
def run_ci(with_tests: bool = False) -> None:
    """Run the same gates CI runs, minus the test suite by default.

    Mirrors devscripts/ci.py, with two deliberate differences:

    * The test suite is opt-in (--with-tests). It takes 10-18 minutes here and
      needs Postgres AND Redis reachable, and a pre-commit gate that slow stops
      being run at all. GitHub runs it on every PR either way.
    * `ruff format` is informational, not blocking — same as ci.py, since the
      repo is not fully formatted and a hard gate would block every commit.

    Ruff is invoked through `python -m ruff` rather than a bare `ruff`, so it
    resolves from the active virtualenv instead of needing to be on PATH.
    """
    header("🔍  CI checks")

    schema_out = Path(tempfile.gettempdir()) / "openapi-schema.yaml"

    checks: list[tuple[str, list[str], bool]] = [
        ("Linter (ruff)", [sys.executable, "-m", "ruff", "check", "."], True),
        (
            "Formatter (ruff)",
            [sys.executable, "-m", "ruff", "format", "--check", "."],
            False,
        ),
        ("Django system check", [sys.executable, "manage.py", "check"], True),
        (
            "Migration consistency",
            [sys.executable, "manage.py", "makemigrations", "--check", "--dry-run"],
            True,
        ),
        (
            "OpenAPI schema",
            [
                sys.executable,
                "manage.py",
                "spectacular",
                "--file",
                str(schema_out),
                "--validate",
            ],
            True,
        ),
    ]

    if with_tests:
        checks.append(("Test suite", [sys.executable, "manage.py", "test"], True))

    failures: list[tuple[str, str]] = []
    for name, cmd, blocking in checks:
        print(f"  {C.DIM}running {name}…{C.RESET}")
        if name == "Test suite":
            # Streamed, not captured: this one runs for many minutes and a
            # silent terminal that long is indistinguishable from a hang.
            code, out = run(cmd)
        else:
            code, out = run_quiet(cmd)
        if code != 0:
            if blocking:
                failures.append((name, out))
                print(f"  {C.RED}✖  {name} FAILED{C.RESET}")
            else:
                print(f"  {C.YELLOW}!  {name} failed (informational){C.RESET}")
        else:
            print(f"  {C.GREEN}✔  {name} passed{C.RESET}")
        print()

    if failures:
        for name, out in failures:
            print(f"\n  {C.BOLD}{name} output:{C.RESET}")
            for line in out.strip().splitlines():
                # Truncate long lines for readability.
                print(f"    {line[:300]}")
        fatal("fix the issues above, re-stage, and re-run.")

    success("all CI checks passed")


# ─── github (gh) ───────────────────────────────────────────
BASE_BRANCH = "staging"
PR_BODY_FILE = "pullrequest.md"

# Merge commits, matching this repo's history ("Merge pull request #9 from …").
MERGE_METHOD = "--merge"


def gh_ready() -> tuple[bool, str]:
    """Whether `gh` is installed AND authenticated.

    Returns (ok, reason). Never fatal: every caller falls back to the manual
    flow, because a missing CLI should not strand a branch that is already
    committed and pushed.
    """
    code, _, _ = run_capture(["gh", "--version"])
    if code != 0:
        return (
            False,
            "the GitHub CLI (`gh`) is not installed — see https://cli.github.com",
        )

    # `--active` matters: plain `gh auth status` exits non-zero if ANY stored
    # account has a stale token, even when the account you're actually using is
    # fine. Several accounts in one keyring is normal, and without this the
    # script would fall back to the manual flow for no reason.
    code, _, err = run_capture(["gh", "auth", "status", "--active"])
    if code != 0:
        return False, f"`gh` is not authenticated — run `gh auth login`.\n{err.strip()}"

    # Proves the active token actually works, rather than merely existing.
    code, _, err = run_capture(["gh", "api", "user", "--jq", ".login"])
    if code != 0:
        return (
            False,
            "`gh` is signed in but the token was rejected — run `gh auth login`."
            f"\n{err.strip()}",
        )

    return True, ""


def notify(title: str, message: str) -> None:
    """Desktop banner + terminal bell. Best-effort and never raises.

    The whole point of watching CI is that you go and do something else, so the
    result has to reach you without the terminal in front of you.
    """
    print("\a", end="", flush=True)
    if sys.platform != "darwin":
        return
    body = message.replace('"', "'")
    head = title.replace('"', "'")
    try:
        subprocess.run(
            ["osascript", "-e", f'display notification "{body}" with title "{head}"'],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        pass  # a missing banner must never break the workflow


def pr_body_args() -> list[str]:
    """`--body-file pullrequest.md` when it has content, else nothing.

    Falling back to gh's default (the commit message) is better than passing an
    empty body, which produces a blank PR description.
    """
    path = Path(PR_BODY_FILE)
    if path.is_file() and path.read_text(encoding="utf-8").strip():
        return ["--body-file", str(path)]
    print(
        f"  {C.DIM}no non-empty {PR_BODY_FILE} found — "
        f"letting gh fill the body from the commit.{C.RESET}"
    )
    return ["--fill"]


def existing_pr_url(branch: str) -> str | None:
    """The URL of an OPEN PR already targeting this branch, if any."""
    code, out, _ = run_capture(
        [
            "gh",
            "pr",
            "view",
            branch,
            "--json",
            "url,state",
            "--jq",
            '.url + " " + .state',
        ]
    )
    if code != 0 or not out.strip():
        return None
    parts = out.strip().split()
    if len(parts) == 2 and parts[1].upper() == "OPEN":
        return parts[0]
    return None


def create_or_reuse_pr(branch: str, title: str) -> str | None:
    """Open a PR for `branch`, or return the one already open. None on failure."""
    existing = existing_pr_url(branch)
    if existing:
        print(f"  {C.DIM}a PR is already open for '{branch}' — reusing it.{C.RESET}")
        info("pull request:", existing)
        return existing

    cmd = [
        "gh",
        "pr",
        "create",
        "--base",
        BASE_BRANCH,
        "--head",
        branch,
        "--title",
        title,
    ]
    cmd += pr_body_args()

    print(f"{C.DIM}▶ {' '.join(cmd)}{C.RESET}")
    code, out, err = run_capture(cmd)
    if code != 0:
        # gh races itself if a PR appeared between our check and this call.
        recovered = existing_pr_url(branch)
        if recovered:
            info("pull request:", recovered)
            return recovered
        failure("could not create the pull request.")
        for line in (err or out).strip().splitlines():
            print(f"    {line}")
        return None

    url = out.strip().splitlines()[-1] if out.strip() else ""
    info("pull request:", url or "(created)")
    return url or existing_pr_url(branch)


# How long to give GitHub to register the first check run before concluding
# there are none. Registration lags PR creation by a few seconds; without this
# the watch would race it and report "no checks" on a perfectly healthy repo.
CHECKS_APPEAR_TIMEOUT_S = 90
CHECKS_POLL_INTERVAL_S = 5

# gh says this, on exit 1, when the PR has no check runs at all. Distinct from a
# real failure, and only distinguishable by the message — see watch_checks.
NO_CHECKS_MARKER = "no checks reported"

# gh's own API client can die mid-watch on transient network errors (seen in
# the wild: "Post https://api.github.com/graphql: net/http: TLS handshake
# timeout") while the checks themselves are healthy. When that happens the
# watcher is re-queried, snapshot-style, until the checks actually settle.
TRANSPORT_ERROR_MARKERS = (
    "tls handshake",
    "connection refused",
    "connection reset",
    "connection timed out",
    "client.timeout",
    "context deadline exceeded",
    "unexpected eof",
    "i/o timeout",
)
SETTLE_RETRIES = 6
SETTLE_DELAY_S = 20


def _looks_like_transport_error(output: str) -> bool:
    """True when gh died talking to GitHub rather than reporting check state."""

    lowered = output.lower()
    return any(marker in lowered for marker in TRANSPORT_ERROR_MARKERS)


def _settled_status(pr: str) -> str | None:
    """One snapshot of the check table: "passed"/"failed"/"none", or None
    when GitHub could not be reached or checks are still pending — the
    caller keeps polling in those cases."""

    code, out, err = run_capture(["gh", "pr", "checks", pr])
    blob = f"{out}\n{err}".lower()
    if _looks_like_transport_error(blob):
        return None
    if NO_CHECKS_MARKER in blob:
        return "none"
    if code == 0:
        return "passed"
    if code == 8:
        return None
    return "failed"


def wait_for_checks_to_appear(pr: str) -> bool:
    """Poll until at least one check registers, or the timeout expires."""
    waited = 0
    while waited < CHECKS_APPEAR_TIMEOUT_S:
        code, out, err = run_capture(["gh", "pr", "checks", pr])
        blob = f"{out}\n{err}".lower()
        if NO_CHECKS_MARKER not in blob:
            # Anything else — pending, passing, failing — means checks exist.
            return True
        if waited == 0:
            print(f"  {C.DIM}waiting for GitHub to register the checks…{C.RESET}")
        time.sleep(CHECKS_POLL_INTERVAL_S)
        waited += CHECKS_POLL_INTERVAL_S
    return False


def watch_checks(pr: str) -> str:
    """Block until GitHub's checks settle.

    Returns "passed", "failed", or "none".

    Exit codes from `gh pr checks` are not enough on their own:
      0 — every check passed
      8 — checks pending
      1 — EITHER a check failed OR the PR has no checks at all. gh only
          distinguishes the two in its message ("no checks reported"), so that
          string is what separates "CI is red" from "CI never ran".

    "none" is deliberately not folded into either. Reporting it as a failure is
    alarming and wrong (nothing broke); reporting it as a pass would merge a PR
    whose CI never ran.

    On top of that, the watcher process itself can die mid-stream on a
    transient network error (TLS handshake timeouts against
    api.github.com happen) while the actual checks are fine and still
    running. That is gh's problem, not CI's — so before believing any
    failure, re-query the settled state directly a few times.
    """
    header("👀  watching GitHub CI")

    if not wait_for_checks_to_appear(pr):
        return "none"

    print(f"  {C.DIM}streaming check status — safe to leave this running.{C.RESET}\n")
    code, out = run(["gh", "pr", "checks", pr, "--watch", "--fail-fast"])
    if code == 0:
        return "passed"
    if NO_CHECKS_MARKER in out.lower():
        return "none"

    if _looks_like_transport_error(out) or code == 8:
        for attempt in range(1, SETTLE_RETRIES + 1):
            print(
                f"  {C.DIM}watcher dropped mid-stream (network blip?); "
                f"re-querying settled state ({attempt}/{SETTLE_RETRIES})…{C.RESET}"
            )
            time.sleep(SETTLE_DELAY_S)
            settled = _settled_status(pr)
            if settled is not None:
                if settled == "passed":
                    success("all checks passed.")
                return settled
        failure("GitHub kept dropping the connection; could not confirm final state.")
    else:
        failure("GitHub CI did not pass.")

    _, detail, _ = run_capture(["gh", "pr", "checks", pr])
    if detail.strip():
        for line in detail.strip().splitlines():
            print(f"    {line}")
    print(f"\n  {C.DIM}details: {pr}{C.RESET}\n")
    return "failed"


def merge_pr(pr: str, *, auto: bool) -> bool:
    """Merge the PR, or queue it for auto-merge. True on success."""
    # The remote branch is kept on purpose — it stays available for reference,
    # for a follow-up fix, or to re-open the PR. Delete it yourself when done.
    cmd = ["gh", "pr", "merge", pr, MERGE_METHOD]
    if auto:
        cmd.append("--auto")

    code, out, err = run_capture(cmd)
    if code == 0:
        return True

    failure("merge failed.")
    for line in (err or out).strip().splitlines():
        print(f"    {line}")
    if auto:
        print(
            f"\n  {C.DIM}if auto-merge is disabled for this repo, enable it in "
            f"Settings → General → Pull Requests, or re-run without --auto.{C.RESET}"
        )
    else:
        print(
            f"\n  {C.DIM}branch protection or a pending review can block a CLI "
            f"merge — merge from the PR page instead: {pr}{C.RESET}"
        )
    return False


def sync_staging() -> None:
    """Return to staging and fast-forward. Aborts loudly on conflict."""
    if run(["git", "checkout", BASE_BRANCH])[0] != 0:
        fatal(f"failed to checkout {BASE_BRANCH}.")

    code, output = run(["git", "pull"])
    if code != 0 or "CONFLICT" in output.upper():
        fatal("merge conflict detected — resolve manually, then re-run.")

    success(f"{BASE_BRANCH} is up to date.   all done 🎉")


def manual_merge_fallback(reason: str) -> None:
    """The old behaviour: park on staging and wait for a human to confirm."""
    print(f"  {C.YELLOW}{reason}{C.RESET}\n")

    if run(["git", "checkout", BASE_BRANCH])[0] != 0:
        fatal(f"failed to checkout {BASE_BRANCH}.")

    header("⏸   waiting for you to merge on GitHub")

    while True:
        answer = prompt("Have you merged? [y/n]:").lower()
        if answer in ("y", "yes"):
            break
        if answer in ("n", "no", ""):
            continue
        print(f"  {C.DIM}(type y or n){C.RESET}")

    code, output = run(["git", "pull"])
    if code != 0 or "CONFLICT" in output.upper():
        fatal("merge conflict detected — resolve manually, then re-run.")

    success(f"{BASE_BRANCH} is up to date.   all done 🎉")


# ─── flow: -b new branch ───────────────────────────────────
def flow_new_branch(
    wait_for_merge: bool = True,
    skip_ci: bool = False,
    auto: bool = False,
    open_pr: bool = True,
    with_tests: bool = False,
    branch: str | None = None,
    message: str | None = None,
) -> None:
    header("🌿  course-builder · new branch workflow")

    if branch is None:
        branch = prompt("Branch name (e.g. feat/admin-operations):")
        if not branch:
            fatal("branch name is required.")

    if message is None:
        message = prompt("Commit message:")
        if not message:
            fatal("commit message is required.")

    prefix = branch.split("/", 1)[0] if "/" in branch else ""
    final_msg = f"{prefix}: {message}" if prefix else message

    if auto:
        merge_mode = "GitHub auto-merge (--auto)"
    elif not open_pr:
        merge_mode = f"{C.DIM}manual (--no-pr){C.RESET}"
    elif not wait_for_merge:
        merge_mode = f"{C.DIM}none — exits after opening the PR (--no-wait){C.RESET}"
    else:
        merge_mode = "wait here for CI, then ask"

    if skip_ci:
        ci_mode = "skipped"
    elif with_tests:
        ci_mode = "enabled + full test suite"
    else:
        ci_mode = "enabled (no tests — see --with-tests)"

    print()
    info("detected prefix:", prefix or f"{C.DIM}(none — no '/' in branch){C.RESET}")
    info("final commit:", final_msg)
    info("pull request:", "yes" if open_pr else f"{C.DIM}no (--no-pr){C.RESET}")
    info("merge:", merge_mode)
    info("CI checks:", ci_mode)
    print()

    checkout_or_create(branch)

    preview_and_stage()

    if not has_staged_changes():
        fatal("nothing staged after `git add .` — aborting empty commit.")

    if not skip_ci:
        run_ci(with_tests=with_tests)

    if run(["git", "commit", "-m", final_msg])[0] != 0:
        fatal("git commit failed (hook? re-stage and re-run gc.py -b).")

    if run(["git", "push", "-u", "origin", branch])[0] != 0:
        fatal("git push failed — branch was created locally but is NOT on origin.")

    success(f"pushed '{branch}' to origin")

    # ── from here on the branch is safely on origin. Nothing below may lose
    # work, so every failure degrades to "do it on the PR page" rather than
    # aborting in a way that leaves you wondering what landed.
    if not open_pr:
        manual_merge_fallback("--no-pr set; open the PR on GitHub yourself.")
        return

    ok, reason = gh_ready()
    if not ok:
        manual_merge_fallback(reason)
        return

    header("🔀  opening the pull request")
    pr = create_or_reuse_pr(branch, final_msg)
    if not pr:
        manual_merge_fallback("falling back to the manual flow.")
        return

    # --auto hands the merge to GitHub: it lands whenever the checks go green,
    # laptop open or not. Nothing to wait for locally, and nothing to pull yet.
    if auto:
        if merge_pr(pr, auto=True):
            success("auto-merge armed — GitHub will merge this once CI is green")
            print(
                f"  {C.DIM}staying on '{branch}'. once it lands: "
                f"`git checkout {BASE_BRANCH} && git pull`.{C.RESET}\n"
            )
        else:
            print(f"  {C.DIM}the PR is open either way: {pr}{C.RESET}\n")
        return

    if not wait_for_merge:
        print(
            f"  {C.DIM}--no-wait set; staying on '{branch}'. "
            f"merge at {pr}, then `git checkout {BASE_BRANCH} && git pull`.{C.RESET}\n"
        )
        return

    outcome = watch_checks(pr)

    if outcome == "failed":
        notify("CI failed", f"{branch} — checks did not pass")
        print(
            f"  {C.DIM}staying on '{branch}' so you can fix and re-push. "
            f"the PR stays open.{C.RESET}\n"
        )
        sys.exit(1)

    if outcome == "none":
        notify("CI never ran", f"{branch} — no checks reported")
        print(f"\n  {C.YELLOW}⚠  no checks ran on this PR.{C.RESET}\n")
        print(
            f"  {C.DIM}nothing failed — nothing ran at all, so there is no green to "
            f"trust. usual causes:{C.RESET}\n"
            f"  {C.DIM}  · Actions minutes exhausted for the billing cycle "
            f"(private repos stop silently){C.RESET}\n"
            f"  {C.DIM}  · Actions disabled, or the workflow's trigger doesn't match "
            f"this base branch{C.RESET}\n"
            f"  {C.DIM}  · a required approval gate on workflow runs{C.RESET}\n"
            f"  {C.DIM}check: {pr}/checks{C.RESET}\n"
        )
        answer = prompt("Merge anyway, without CI? [y/n]:").lower()
        if answer not in ("y", "yes"):
            print(f"  {C.DIM}left unmerged and staying on '{branch}'.{C.RESET}\n")
            return
    else:
        notify("CI passed", f"{branch} — ready to merge")
        success("all GitHub checks passed")

    while True:
        answer = prompt(f"Merge this PR into {BASE_BRANCH}? [y/n]:").lower()
        if answer in ("y", "yes"):
            break
        if answer in ("n", "no"):
            print(
                f"  {C.DIM}left unmerged and staying on '{branch}'. "
                f"merge later at {pr}.{C.RESET}\n"
            )
            return
        print(f"  {C.DIM}(type y or n){C.RESET}")

    if not merge_pr(pr, auto=False):
        print(f"  {C.DIM}staying on '{branch}'.{C.RESET}\n")
        sys.exit(1)

    success("merged")
    sync_staging()


# ─── flow: -staging quick push ─────────────────────────────
def flow_staging(skip_ci: bool = False, with_tests: bool = False) -> None:
    header("🚀  course-builder · staging quick push")

    branch = current_branch()
    info("current branch:", branch)
    if branch != BASE_BRANCH:
        fatal(f"not on {BASE_BRANCH} (current: '{branch}'). switch first, then re-run.")

    message = prompt("Commit message:")
    if not message:
        fatal("commit message is required.")
    print()

    preview_and_stage()

    if not has_staged_changes():
        fatal("nothing staged after `git add .` — aborting empty commit.")

    if not skip_ci:
        run_ci(with_tests=with_tests)

    if run(["git", "commit", "-m", message])[0] != 0:
        fatal("git commit failed (hook? re-stage and re-run gc.py -staging).")

    if run(["git", "push"])[0] != 0:
        fatal("git push failed.")

    success(f"pushed to {BASE_BRANCH}   done 🎉")


# ─── entry ─────────────────────────────────────────────────
USAGE = (
    "usage: python3 devscripts/gc.py "
    "[-b [--auto] [--no-wait] [--no-pr] [--skip-ci] [--with-tests] "
    "| -staging [--skip-ci] [--with-tests]]"
)


def main() -> None:
    args = sys.argv[1:]
    if not args:
        fatal(USAGE)

    # Every gate below shells out to `manage.py` and reads `pullrequest.md` by
    # relative path, so anchor the process at the repo root before anything runs.
    os.chdir(repo_root())

    flag, *rest = args
    try:
        if flag == "-b":
            # Support both interactive and shorthand:
            #   gc.py -b --auto feat/branch "message"
            #   gc.py -b feat/branch "message" --auto
            # Separate flags from positional args.
            known_flags = {"--no-wait", "--skip-ci", "--auto", "--no-pr", "--with-tests"}
            flags = [a for a in rest if a in known_flags]
            positionals = [a for a in rest if a not in known_flags]

            if positionals:
                # Shorthand mode: first two positionals are branch and message
                if len(positionals) < 2:
                    fatal(f"shorthand requires branch and message: {USAGE}")
                branch = positionals[0]
                message = positionals[1]
            else:
                # Interactive mode (original behavior)
                branch = None
                message = None

            auto = "--auto" in flags
            open_pr = "--no-pr" not in flags
            wait_for_merge = "--no-wait" not in flags
            skip_ci = "--skip-ci" in flags
            with_tests = "--with-tests" in flags

            # Contradictory combinations, rejected up front rather than silently
            # letting one flag win — you would only find out at the end.
            if auto and not open_pr:
                fatal("--auto needs a pull request; drop --no-pr. " + USAGE)
            if auto and not wait_for_merge:
                fatal(
                    "--auto already returns immediately; --no-wait is redundant. "
                    + USAGE
                )
            if skip_ci and with_tests:
                fatal("--skip-ci and --with-tests contradict each other. " + USAGE)

            flow_new_branch(
                wait_for_merge=wait_for_merge,
                skip_ci=skip_ci,
                auto=auto,
                open_pr=open_pr,
                with_tests=with_tests,
                branch=branch,
                message=message,
            )
        elif flag == "-staging":
            known = {"--skip-ci", "--with-tests"}
            unknown = [a for a in rest if a not in known]
            if unknown:
                fatal(f"unknown args after -staging: {unknown}. {USAGE}")
            skip_ci = "--skip-ci" in rest
            with_tests = "--with-tests" in rest
            if skip_ci and with_tests:
                fatal("--skip-ci and --with-tests contradict each other. " + USAGE)
            flow_staging(skip_ci=skip_ci, with_tests=with_tests)
        else:
            fatal(f"unknown flag: {flag!r}. {USAGE}")
    except KeyboardInterrupt:
        print()
        fatal("aborted by user.", code=130)


if __name__ == "__main__":
    main()
