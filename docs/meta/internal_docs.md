# Internal Developer Documentation Portal — Implementation Guide

## What this is

A rendered, navigable, GitHub-Docs-styled site — sidebar, breadcrumbs, sticky
"on this page" outline, light/dark theme, styled code blocks with copy
buttons — serving your repo's own `docs/*.md` files, running **inside your
existing Django app**. No new server, no database, no external docs service,
no build step. `docs/` stays exactly what it already is: plain Markdown
files, version-controlled, reviewed in the same PR as the code they
describe.

This is the exact, complete implementation built and shipped for one real
Django/DRF codebase, written up so it can be reproduced on **any** Django
codebase from this document alone. Every design decision below is followed
by the reasoning behind it — not just so you can copy it, but so you can
tell which parts are load-bearing (copy verbatim) and which are one
project's taste (adapt to your own).

**The core principle**: Git is the documentation CMS, Markdown is the
source of truth, and your app server is the documentation server.

---

## Before you start — find your codebase's own answers

This guide assumes decisions that were *discovered*, not invented, by
reading the actual target codebase first. Do the same in yours before
writing a line of code. Five questions, in order of how much they'll change
the implementation:

1. **How does your codebase distinguish dev / staging / production at
   runtime?** Look for whatever env var already gates things like debug
   toolbars or profiling middleware — don't invent a second one. (Ours was
   `DJANGO_ENV` via `shared.constants.environ`, values `"development"` /
   `"staging"` / `"production"`. **Not** `DEBUG`, which was `False` in
   staging too.)
2. **Is there an existing precedent for conditionally registering a URL by
   environment?** If your root URLconf already does
   `if <env-check>: urlpatterns += [...]` anywhere (a profiler, a debug
   route, anything dev-only), copy that exact shape. Don't invent a new
   settings flag when the pattern already exists.
3. **Do you already have *any* authenticated, trusted-user surface?**
   Django admin (`is_staff`/`is_superuser`) is the obvious one and probably
   already exists. If it does, **reuse it** — see Part 1, this is the
   single highest-leverage decision in this whole guide.
4. **Does your project already use Django templates anywhere**, or is it
   100% API/JSON? If the latter, this feature will be introducing
   `render()`/file-based templates and `request.session` usage for the
   first time — not a problem, just worth knowing going in so you're not
   surprised when nothing else in the codebase looks like a precedent for
   the template/view layer specifically.
5. **What does `docs/` already contain?** Read it before organizing it — a
   sensible category taxonomy should fall out of what's actually there
   (Part 12), not be decided in the abstract.

---

## Architecture at a glance

```text
your-project/
├── config/ (or wherever your settings/urls live)
│   ├── settings/
│   │   └── app_registry.py      # add "devdocs" here
│   └── urls.py                  # mount /docs/ here, env-gated
├── devdocs/                      # the new app — sibling to your project root
│   ├── __init__.py
│   ├── apps.py
│   ├── services.py               # doc-tree walking + Markdown rendering
│   ├── views.py                  # 3 plain Django views
│   ├── urls.py
│   └── templates/devdocs/
│       ├── base.html             # shell: topbar, breadcrumb, sidebar, rail
│       ├── _tree.html            # recursive sidebar partial
│       └── page.html             # doc content + landing page
└── docs/                          # your existing docs — untouched structurally
    ├── ARCHITECTURE.md            # stays flat — see Part 12
    └── some-subsystem/
        └── whatever.md            # nested folders become sidebar sections, free
```

Request flow: `GET /docs/subsystems/auth/` → Django resolves to
`devdocs.views.page` → `@staff_member_required` checks the session →
`services.resolve_doc("subsystems/auth")` finds the file on disk →
`services.get_rendered_doc()` converts it to HTML (cached by mtime) →
`page.html` renders it inside `base.html`'s shell.

---

## Part 1 — Access control: reuse what already exists, don't invent a secret

**Do this before anything else** — it's the decision every other part
depends on, and getting it right the first time avoids a rebuild.

The naive approach (and most tutorials' default) is a shared access code in
an env var, checked against a session flag. **Don't build that if you have
any alternative.** It means: a new secret to generate, distribute, and
rotate; a login page and form to build; a session key of your own to
invent; and when someone leaves the team, a "docs code" the whole team now
needs to rotate — none of which is actually about documentation.

**Better, and almost certainly already sitting in your codebase:** if you
have Django's admin app installed (`django.contrib.admin` — check
`INSTALLED_APPS` and whether `/admin/` is mounted), your `User` model
already has `is_staff`/`is_superuser`, and whoever can log into `/admin/`
today already has exactly the right kind of "internal, trusted" identity
for this. Gate the whole portal behind Django's own
`staff_member_required` decorator instead:

```python
from django.contrib.admin.views.decorators import staff_member_required

@staff_member_required(login_url="/admin/login/")
def index(request):
    ...
```

What this buys you, concretely:

- **Zero new secrets** — nothing to create, store, distribute, or rotate.
- **Real per-person identity**, not a shared code. You know *who* looked at
  the docs, and revoking one person's access is exactly as easy as it
  already is for admin access.
- **Less code**, not more — no access-code form, no custom session key, no
  `hmac.compare_digest`, no logout view (`/admin/logout/` already exists).
  The entire gate is one decorator on two view functions.

If your project genuinely has no admin/staff concept at all, the fallback
is a real (not `guess_lang`-style accidental) shared secret compared with
`hmac.compare_digest` or `secrets.compare_digest` — but treat that as the
fallback, not the default, and be honest in your own docs that it's a
weaker, shared-not-personal gate.

**What we explicitly did not build, and why:** relying on environment
gating alone (Part 2) with no access gate at all. Internal docs plausibly
describe auth internals, third-party integration credentials'
*shapes* (not values, but enough to be useful to an attacker), and infra
layout — real content worth not leaving open to "anyone who finds the
staging URL," even on an internally-restricted environment you don't fully
control the network boundary of.

---

## Part 2 — Environment gating: dev + staging only, never production

Find your project's real environment discriminator (see the checklist
above) and mirror whatever conditional-URL-registration pattern already
exists verbatim. Ours (`config/urls.py`) already had this shape for an
unrelated dev-only route, so the docs portal became a third copy of an
established pattern rather than a new one:

```python
# config/urls.py
from shared.constants.environ import DJANGO_ENV   # <- your project's own env source

urlpatterns = [
    # ... your existing patterns ...
]

# dev utilities — available in dev and staging (pre-existing pattern, shown
# for reference — you may already have something like this)
if DJANGO_ENV in ("development", "staging"):
    from api.dev_views import dev_logs
    urlpatterns += [path("dev/logs/", dev_logs, name="dev-logs")]

# internal developer docs — available in dev and staging
if DJANGO_ENV in ("development", "staging"):
    urlpatterns += [path("docs/", include("devdocs.urls"))]
```

**Do not use `DEBUG` as the discriminator.** Staging very plausibly runs
`DEBUG=False` (production-like settings) while still needing the docs
portal — check what your staging environment actually sets before assuming.

**Defense in depth, not just at the URL layer:** also check the same
condition inside the views themselves (Part 7). If the URL registration
condition and the settings module ever drift apart (a settings refactor, a
new environment name), the view-level check is what actually keeps
production safe — belt and suspenders, costs three lines.

**Register the app unconditionally, gate only the URL.** Add `"devdocs"` to
your custom apps list in every environment — it has no models and does
nothing just by being installed. The *only* environment gate that matters
is the URL registration above and the view-level check in Part 7. One gate
to keep in sync is simpler than two.

---

## Part 3 — The Django app skeleton

```text
devdocs/
├── __init__.py
├── apps.py
├── services.py
├── views.py
├── urls.py
└── templates/devdocs/
    ├── base.html
    ├── _tree.html
    └── page.html
```

`apps.py` — nothing unusual, matches whatever your other minimal apps look
like:

```python
from django.apps import AppConfig


class DevdocsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "devdocs"
    verbose_name = "Developer Documentation"
```

**Where to put the app**: top-level, sibling to your project's `config`/
settings directory — **not** nested under an existing `api/` package if you
have one. It is not part of your API surface (no DRF, no JSON, nothing an
API client would ever call), so nesting it there would misrepresent what it
is. If your root URLconf already treats non-API surfaces (admin, a
dev-tools route) as first-class root-level mounts rather than smuggling
them under `/api/`, this is the same category of thing.

---

## Part 4 — The doc-tree service

This is the one file with no UI concerns at all — it only walks `docs/` on
disk and turns Markdown into HTML. Everything else in the app calls into
this module; nothing else touches the filesystem directly.

```python
# devdocs/services.py
"""
Doc-tree walking and Markdown rendering for the internal docs portal.

`docs/` is the source of truth (plain files, no DB). This module never
writes anything — it only reads `docs/*.md` and renders it. Works with a
flat layout as-is; a subdirectory added later becomes a nested nav section
for free, nothing here assumes flat.
"""

import re
import time
from pathlib import Path

import markdown
from django.conf import settings

_MD_EXTENSIONS = ["extra", "codehilite", "toc", "sane_lists"]
_MD_EXTENSION_CONFIGS = {
    "toc": {
        # Depth 1-2 only: a doc's chapter-level headings (H1) and their
        # immediate subsections (H2). Deeper headings still render normally
        # in the body — they just don't clutter the on-page outline. A doc
        # with dozens of sub-headings (a style guide, a numbered-rules
        # handbook) will otherwise produce an outline longer than most
        # docs' actual content. Tune the range to your own docs' typical
        # heading depth.
        "toc_depth": "1-2",
        "permalink": True,
        "permalink_class": "headerlink",
        "permalink_title": "Link to this section",
    },
    "codehilite": {
        # THE SINGLE MOST IMPORTANT LINE IN THIS FILE.
        #
        # codehilite's own default is guess_lang=True, which runs Pygments'
        # guess_lexer() on any fenced code block with no declared language.
        # This is not a graceful "skip highlighting" fallback — it always
        # picks SOME lexer, and any character that lexer doesn't recognise
        # gets tagged Pygments' "err" token class, which every color scheme
        # renders as a glaring error color (usually red).
        #
        # In practice: any bare ``` fence containing an ASCII tree diagram,
        # a box-drawing flowchart (┌─┐│└┘→▼), a checklist using □, or even
        # just an em dash (—) in plain prose gets partially rendered in
        # angry red, because the guesser tokenized prose as if it were some
        # programming language and choked on ordinary punctuation.
        #
        # An unlabeled fence isn't "code in an unknown language" — it's
        # usually not code at all. False: unlabeled fences render as plain,
        # unstyled text with zero false-positive tokenization. Fences that
        # DO declare a language (```python, ```bash, ...) are completely
        # unaffected — guess_lang only ever applied to the undeclared ones.
        "guess_lang": False,
    },
}

# In-process caches, keyed off file mtimes so a doc edit + redeploy busts
# them automatically. No multi-worker invalidation story needed — each
# worker just rebuilds its own copy the first time it notices a change.
#
# _CACHE_TTL_SECONDS bounds how often the mtime signature itself gets
# recomputed. A single request calls get_doc_tree()/get_flat_docs() more
# than once (the sidebar, resolve_doc, and prev/next navigation each do) —
# without a TTL, every one of those re-stats every file under docs/, which
# is cheap on a native filesystem but a real, measurable cost under a
# Docker bind mount, and it serializes inside the same synchronous request.
# A 2s TTL means a whole request (and any others in that window) shares one
# disk check, while still picking up a doc edit within 2 seconds.
_CACHE_TTL_SECONDS = 2.0
_tree_cache: dict = {"tree": None, "flat": None, "signature": None, "checked_at": 0.0}
_render_cache: dict[Path, tuple] = {}


def docs_root() -> Path:
    return Path(settings.BASE_DIR) / "docs"


def slugify(stem: str) -> str:
    return stem.lower().replace("_", "-")


def _humanize(name: str) -> str:
    return name.replace("_", " ").replace("-", " ").strip().title()


def _extract_title(path: Path) -> str:
    """The file's first `# H1` line, or a prettified filename if it doesn't
    open with one."""
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                if line.startswith("# "):
                    return line[2:].strip()
                break
    except OSError:
        pass
    return _humanize(path.stem)


def _walk_dir(directory: Path, root: Path) -> list[dict]:
    nodes = []
    try:
        entries = sorted(directory.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return nodes

    for entry in entries:
        if entry.is_dir():
            children = _walk_dir(entry, root)
            if children:
                nodes.append(
                    {
                        "title": _humanize(entry.name),
                        "slug": None,
                        "path": None,
                        # This section's slug-space prefix (e.g. "subsystems")
                        # — lets the sidebar auto-expand the section that
                        # contains whichever doc is currently open, without
                        # re-walking the tree per request. Built the same way
                        # as a leaf's slug (slugified parts joined by "/"),
                        # not `str(Path)`, so it's a valid slug prefix
                        # regardless of OS path-separator conventions.
                        "prefix": "/".join(slugify(part) for part in entry.relative_to(root).parts),
                        "children": children,
                    }
                )
        elif entry.suffix.lower() == ".md":
            rel = entry.relative_to(root)
            slug = "/".join(slugify(part) for part in rel.with_suffix("").parts)
            nodes.append(
                {
                    "title": _extract_title(entry),
                    "slug": slug,
                    "path": entry,
                    "children": [],
                }
            )

    nodes.sort(key=lambda n: n["title"].lower())
    return nodes


def _flatten(nodes: list[dict]) -> list[dict]:
    """Leaf (doc) nodes only, in the same order the sidebar renders them —
    used for slug lookup and prev/next navigation."""
    flat = []
    for node in nodes:
        if node["slug"] is not None:
            flat.append(node)
        if node["children"]:
            flat.extend(_flatten(node["children"]))
    return flat


def _signature(root: Path) -> tuple:
    """A cheap fingerprint of docs/'s current mtimes — changes the moment a
    file is added, removed, or edited."""
    sig = []
    try:
        for p in sorted(root.rglob("*.md")):
            try:
                sig.append((str(p.relative_to(root)), p.stat().st_mtime))
            except OSError:
                continue
    except OSError:
        pass
    return tuple(sig)


def _ensure_cache_fresh() -> None:
    now = time.monotonic()
    if _tree_cache["tree"] is not None and now - _tree_cache["checked_at"] < _CACHE_TTL_SECONDS:
        return
    root = docs_root()
    signature = _signature(root)
    if _tree_cache["signature"] != signature:
        tree = _walk_dir(root, root)
        _tree_cache["tree"] = tree
        _tree_cache["flat"] = _flatten(tree)
        _tree_cache["signature"] = signature
    _tree_cache["checked_at"] = now


def get_doc_tree() -> list[dict]:
    """The nested nav tree, rebuilt only when a file under docs/ changed
    (checked at most once per _CACHE_TTL_SECONDS)."""
    _ensure_cache_fresh()
    return _tree_cache["tree"]


def get_flat_docs() -> list[dict]:
    _ensure_cache_fresh()
    return _tree_cache["flat"]


def ancestor_prefixes(slug: str) -> set[str]:
    """Every section prefix that contains `slug` — e.g. for
    "subsystems/auth-architecture" that's {"subsystems"}, for a
    hypothetical "a/b/doc" it's {"a", "a/b"}. The sidebar auto-expands
    exactly these sections; everything else starts collapsed."""
    parts = slug.split("/")[:-1]
    return {"/".join(parts[: i + 1]) for i in range(len(parts))}


def get_breadcrumb(slug: str) -> list[dict]:
    """[{"title", "slug"}, ...] from the root down to the doc itself —
    section labels first (slug=None, not a link), then the doc's own
    title with its real slug. Derived from the slug's own path segments
    rather than re-walking the tree — a section's breadcrumb label is just
    its humanized directory name, same as the sidebar computes it."""
    parts = slug.split("/")
    crumbs = [{"title": _humanize(part), "slug": None} for part in parts[:-1]]
    node = resolve_doc(slug)
    if node:
        crumbs.append({"title": node["title"], "slug": slug})
    return crumbs


def resolve_doc(slug: str) -> dict | None:
    """slug -> its tree node, or None.

    Looked up against the set of files actually found by walking
    `docs_root()` — never reconstructed from the slug into a filesystem
    path — so a crafted slug (e.g. containing `..`) simply matches nothing
    rather than needing a separate path-containment check. This is the
    entire security-relevant property of this function: it can only ever
    return a file that a real directory walk actually found.
    """
    for node in get_flat_docs():
        if node["slug"] == slug:
            return node
    return None


def get_prev_next(slug: str) -> tuple[dict | None, dict | None]:
    flat = get_flat_docs()
    for i, node in enumerate(flat):
        if node["slug"] == slug:
            prev = flat[i - 1] if i > 0 else None
            nxt = flat[i + 1] if i < len(flat) - 1 else None
            return prev, nxt
    return None, None


_CODEHILITE_DIV_RE = re.compile(r'<div class="codehilite">')

_COPY_ICON_SVG = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round">'
    '<rect x="9" y="9" width="13" height="13" rx="2"></rect>'
    '<path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>'
)


def _fence_languages(text: str) -> list[str]:
    """The declared language (possibly "") of every fenced code block, in
    source order — e.g. "python", "bash", or "" for a bare ```.

    codehilite's own rendered output doesn't preserve which language a
    fence declared anywhere (verify this yourself before trusting it:
    render a ```python fence and a bare ``` fence and diff the output —
    both produce an identical `<div class="codehilite">`, nothing
    distinguishes them). So labeling the rendered blocks means recovering
    the language from the source ourselves and matching positionally
    against the `.codehilite` divs in the output, in the same order.

    This assumes every code block in your docs is a ``` fence, never a
    bare 4-space-indented block — an indented block ALSO produces a
    `.codehilite` div (with no language, since indent syntax has no way to
    declare one), but wouldn't appear in this scan, throwing off the
    positional match from that point on. Check your own `docs/` for any
    bare indented code block before trusting this; if one exists, either
    convert it to a fence (the normal fix — indented code blocks are a
    legacy Markdown feature almost nobody still uses on purpose) or extend
    this scan to detect them too.
    """
    langs = []
    in_fence = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            if not in_fence:
                langs.append(line.strip()[3:].strip())
            in_fence = not in_fence
    return langs


def _decorate_code_blocks(html: str, languages: list[str]) -> str:
    """Attach a language label + copy-to-clipboard button to each rendered
    code block. Matched positionally (the Nth `.codehilite` div in the
    output <-> the Nth fence in `languages`) since codehilite's HTML gives
    us nothing to match on directly."""
    remaining = iter(languages)

    def _inject(_match):
        lang = next(remaining, "") or "text"
        toolbar = (
            f'<div class="code-toolbar"><span class="code-lang">{lang}</span>'
            f'<button class="copy-btn" type="button" data-copy aria-label="Copy code">'
            f'{_COPY_ICON_SVG}<span class="copy-btn-label">Copy</span></button></div>'
        )
        return f'<div class="codehilite">{toolbar}'

    return _CODEHILITE_DIV_RE.sub(_inject, html)


def get_rendered_doc(path: Path) -> tuple[str, str]:
    """(html, toc_html) for a doc file, cached by mtime."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = None

    cached = _render_cache.get(path)
    if cached and cached[0] == mtime:
        return cached[1], cached[2]

    text = path.read_text(encoding="utf-8")
    languages = _fence_languages(text)
    md = markdown.Markdown(extensions=_MD_EXTENSIONS, extension_configs=_MD_EXTENSION_CONFIGS)
    html = md.convert(text)
    html = _decorate_code_blocks(html, languages)
    toc_html = getattr(md, "toc", "")
    _render_cache[path] = (mtime, html, toc_html)
    return html, toc_html
```

---

## Part 5 — Why `guess_lang=False` matters this much

This gets its own section because it's the single most likely thing to go
wrong if you build this from a tutorial rather than this guide, and the
failure mode is genuinely confusing to debug from the symptom alone (it
looks like "the CSS is wrong" or "wrong Pygments theme," not "the language
guesser is running on prose").

**Before shipping, verify it on your own docs, don't take it on faith:**

```python
import markdown

md = markdown.Markdown(extensions=["extra", "codehilite"])   # guess_lang defaults True
html = md.convert(open("docs/whatever-has-a-diagram.md").read())
print('class="err"' in html)   # if True, you've found the bug
```

Then confirm the fix and confirm labeled fences are unaffected:

```python
md = markdown.Markdown(
    extensions=["extra", "codehilite"],
    extension_configs={"codehilite": {"guess_lang": False}},
)
html = md.convert(same_text)
assert 'class="err"' not in html
```

If your docs have zero unlabeled fences (everything is already
` ```python`/` ```bash`/etc.), this bug may never surface — but it's a
one-line config change with no downside, worth setting regardless.

---

## Part 6 — Code block chrome: language label + copy button

Stock `codehilite` output is a flat `<div class="codehilite"><pre>...`
with no visual distinction from surrounding prose beyond whatever CSS you
give the container, and no language label anywhere — genuinely "not even
code-snippet-worthy" without deliberate styling. Two things fix that,
implemented in `_decorate_code_blocks` above:

1. **A real toolbar, injected server-side** — language label on the left,
   a clickable Copy button on the right — not a CSS `::before` pseudo-label,
   because pseudo-elements can't contain a real interactive `<button>`.
2. **Matched positionally**, per the reasoning in Part 5's docstring, since
   codehilite throws away the declared language once it's picked a lexer.

The copy button needs one small piece of JS (goes in `base.html`, Part 9)
using event delegation so it works for however many code blocks a page has
without attaching N listeners:

```javascript
document.addEventListener("click", function (e) {
  var btn = e.target.closest(".copy-btn");
  if (!btn) return;
  var block = btn.closest(".codehilite");
  var codeEl = block && block.querySelector("code");
  if (!codeEl) return;
  var text = codeEl.innerText;

  var onCopied = function () {
    var label = btn.querySelector(".copy-btn-label");
    btn.classList.add("copied");
    if (label) label.textContent = "Copied!";
    setTimeout(function () {
      btn.classList.remove("copied");
      if (label) label.textContent = "Copy";
    }, 1500);
  };

  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(onCopied, function () {});
  } else {
    var ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand("copy"); onCopied(); } catch (err) {}
    document.body.removeChild(ta);
  }
});
```

---

## Part 7 — Views: plain Django functions, not your API framework

```python
# devdocs/views.py
"""
Plain Django function views, not DRF (or whatever your API framework is) —
deliberately. This serves rendered HTML to a browser, not JSON to an API
client, and isn't part of your API surface at all. Forcing your API
framework's conventions onto it would fight the framework for nothing.
"""

from django.contrib.admin.views.decorators import staff_member_required
from django.http import FileResponse, Http404
from django.shortcuts import render

from devdocs import services
from shared.constants.environ import DJANGO_ENV   # <- your project's own env source

_ALLOWED_ENVS = {"development", "staging"}


def _guard_env():
    """Belt-and-suspenders: the URL layer already only registers this app's
    URLs in dev/staging, but the view checks too, in case that ever drifts."""
    if DJANGO_ENV not in _ALLOWED_ENVS:
        raise Http404


@staff_member_required(login_url="/admin/login/")
def index(request):
    _guard_env()
    flat = services.get_flat_docs()
    return render(
        request,
        "devdocs/page.html",
        {
            "tree": services.get_doc_tree(),
            "flat_docs": flat,
            "active_slug": None,
            "open_prefixes": set(),
            "breadcrumbs": [],
            "title": "Developer Docs",
            "content_html": None,
            "toc_html": None,
            "prev_doc": None,
            "next_doc": flat[0] if flat else None,
            "doc_count": len(flat),
            "django_env": DJANGO_ENV,
        },
    )


@staff_member_required(login_url="/admin/login/")
def page(request, slug):
    _guard_env()
    node = services.resolve_doc(slug)
    if node is None:
        raise Http404("No such doc.")

    content_html, toc_html = services.get_rendered_doc(node["path"])
    prev_doc, next_doc = services.get_prev_next(slug)

    return render(
        request,
        "devdocs/page.html",
        {
            "tree": services.get_doc_tree(),
            "active_slug": slug,
            "open_prefixes": services.ancestor_prefixes(slug),
            "breadcrumbs": services.get_breadcrumb(slug),
            "title": node["title"],
            "filename": node["path"].name,
            "content_html": content_html,
            "toc_html": toc_html,
            "prev_doc": prev_doc,
            "next_doc": next_doc,
            "django_env": DJANGO_ENV,
        },
    )


@staff_member_required(login_url="/admin/login/")
def download(request, slug):
    _guard_env()
    node = services.resolve_doc(slug)
    if node is None:
        raise Http404("No such doc.")

    return FileResponse(
        node["path"].open("rb"),
        as_attachment=True,
        filename=node["path"].name,
        content_type="text/markdown; charset=utf-8",
    )
```

Logging in happens at your existing `/admin/login/` — nothing new to build.
An unauthenticated visit redirects there automatically via `login_url`,
Django's admin login already supports `?next=` to bounce back to the
originally-requested page.

---

## Part 8 — URL routing, and the greedy-pattern gotcha

```python
# devdocs/urls.py
from django.urls import path

from devdocs import views

urlpatterns = [
    path("", views.index, name="devdocs-index"),
    # Must come before the generic page pattern below: `<path:slug>/` is
    # greedy (it matches any number of "/"-separated segments), so it would
    # otherwise swallow "download/<slug>/" as slug="download/<slug>" and
    # this route would never be reached. Django tries urlpatterns top to
    # bottom, first match wins — the more specific literal-prefixed pattern
    # has to come first.
    path("download/<path:slug>/", views.download, name="devdocs-download"),
    # `path`, not Django's `slug` converter: a nested doc's slug contains
    # "/" (e.g. "subsystems/auth"), which the `slug` converter's regex
    # deliberately excludes. `path` matches any characters including "/".
    path("<path:slug>/", views.page, name="devdocs-page"),
]
```

**If you add more routes under `/docs/` later**, remember this ordering
rule: anything with a literal prefix (`download/`, `search/`, whatever)
must be registered *before* the catch-all `<path:slug>/` pattern, or it
will never be reached.

---

## Part 9 — Templates: the 3-column layout

Three template files, one shell + one recursive nav partial + one content
template using two named blocks (`content` for the main column,
`rail` for the sticky right column):

**`base.html`** — the shell. Structure only here; full styling in Parts
10–11:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{{ title }} — Docs</title>
  <script>
    /* Set the theme attribute before first paint so there's no flash of
       the wrong theme. No stored preference -> the CSS media query
       decides, based on the OS setting. */
    (function () {
      try {
        var stored = localStorage.getItem("devdocs-theme");
        if (stored === "light" || stored === "dark") {
          document.documentElement.setAttribute("data-theme", stored);
        }
      } catch (e) {}
    })();
  </script>
  <style>
    /* ... Parts 10-11 ... */
  </style>
</head>
<body>

  <div class="topbar">
    <div class="brand">
      <span class="brand-eyebrow">YOUR-PROJECT · INTERNAL</span>
      <h1>Developer Docs</h1>
    </div>
    <div class="topbar-actions">
      <span class="env-tag">{{ django_env }}</span>
      {% if request.user.is_authenticated %}<span>{{ request.user.email }}</span>{% endif %}
      <a href="/admin/logout/?next=/docs/">Log out</a>
      <button id="theme-toggle" class="theme-toggle" type="button" aria-label="Toggle color theme">
        <svg class="icon-moon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path></svg>
        <svg class="icon-sun" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"></circle><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"></path></svg>
      </button>
    </div>
  </div>

  <div class="breadcrumb">
    <a href="{% url 'devdocs-index' %}">Docs</a>
    {% for crumb in breadcrumbs %}
      <span class="sep">/</span>
      {% if forloop.last %}<span class="current">{{ crumb.title }}</span>{% else %}<span>{{ crumb.title }}</span>{% endif %}
    {% endfor %}
  </div>

  <div class="shell">
    <nav class="sidebar">
      {% include "devdocs/_tree.html" with nodes=tree active_slug=active_slug open_prefixes=open_prefixes %}
    </nav>
    <main class="content-area">
      <div class="content-row">
        <div class="content-main">
          {% block content %}{% endblock %}
        </div>
        <aside class="toc-rail">
          {% block rail %}{% endblock %}
        </aside>
      </div>
    </main>
  </div>

  <script>
    (function () {
      var btn = document.getElementById("theme-toggle");
      if (!btn) return;
      btn.addEventListener("click", function () {
        var isDark = document.documentElement.getAttribute("data-theme") === "dark"
          || (!document.documentElement.getAttribute("data-theme")
              && window.matchMedia("(prefers-color-scheme: dark)").matches);
        var next = isDark ? "light" : "dark";
        document.documentElement.setAttribute("data-theme", next);
        try { localStorage.setItem("devdocs-theme", next); } catch (e) {}
      });
    })();

    /* Copy-button handler from Part 6 goes here too. */
  </script>
</body>
</html>
```

**Why a sticky right rail for "On this page," not an inline block:** the
first version of this put the outline inline at the top of the content
column as a collapsed `<details>`. It worked, but it's not what GitHub
Docs (or most mature docs sites) actually do — theirs is a **persistent
sticky column** that never touches content flow at all, scrolls
independently, and stays visible the whole time you're reading. That
structural difference is bigger than a color/spacing tweak, so if you're
modeling this on GitHub Docs specifically, build the 3-column layout from
the start rather than the inline-collapsible version.

**`_tree.html`** — the recursive sidebar partial. This is genuinely
recursive (a template including itself), which is how it supports
arbitrary nesting depth with no code changes if you ever add sub-sub-folders:

```html
{% comment %}
Recursive sidebar nav. Included by base.html and by itself for nested
sections — works for a flat docs/ layout and for any subdirectories added
later, with no depth limit. Sections are collapsible <details>,
auto-expanded when they contain the active doc (via open_prefixes,
precomputed in the view) — matches GitHub Docs' sidebar behavior.
{% endcomment %}
<ul class="nav-tree">
  {% for node in nodes %}
    {% if node.slug %}
      <li>
        <a href="{% url 'devdocs-page' node.slug %}"
           class="{% if node.slug == active_slug %}active{% endif %}">{{ node.title }}</a>
      </li>
    {% else %}
      <li>
        <details class="nav-section" {% if node.prefix in open_prefixes %}open{% endif %}>
          <summary>{{ node.title }}</summary>
          {% include "devdocs/_tree.html" with nodes=node.children active_slug=active_slug open_prefixes=open_prefixes %}
        </details>
      </li>
    {% endif %}
  {% endfor %}
</ul>
```

**`page.html`** — extends `base.html`, fills both blocks. Handles two
cases: the index/landing page (`content_html` is `None`) and an actual
doc page:

```html
{% extends "devdocs/base.html" %}

{% block content %}
{% if content_html %}
  <div class="markdown-body">
    {{ content_html|safe }}
  </div>

  <div class="doc-nav">
    {% if prev_doc %}
      <a href="{% url 'devdocs-page' prev_doc.slug %}">
        <span class="label">← Previous</span>{{ prev_doc.title }}
      </a>
    {% endif %}
    {% if next_doc %}
      <a class="next" href="{% url 'devdocs-page' next_doc.slug %}">
        <span class="label">Next →</span>{{ next_doc.title }}
      </a>
    {% endif %}
  </div>
{% else %}
  <div class="markdown-body">
    <h1>Developer Docs</h1>
    <p>{{ doc_count }} document{{ doc_count|pluralize }}, rendered straight from
      <code>docs/</code> — pick one from the sidebar, or jump in below.</p>
  </div>

  <div class="landing-grid">
    {% for doc in flat_docs %}
      <div class="landing-card">
        <a href="{% url 'devdocs-page' doc.slug %}">{{ doc.title }}</a>
      </div>
    {% endfor %}
  </div>
{% endif %}
{% endblock %}

{% block rail %}
{% if content_html %}
  <a class="download-btn download-btn-rail" href="{% url 'devdocs-download' active_slug %}">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg>
    Download .md
  </a>
  {% if toc_html %}
    <div class="rail-toc">
      <span class="rail-toc-title">On this page</span>
      {{ toc_html|safe }}
    </div>
  {% endif %}
{% endif %}
{% endblock %}
```

`{{ toc_html|safe }}` and `{{ content_html|safe }}` are safe to mark `|safe`
here specifically because both strings are generated server-side by your
own `markdown.Markdown().convert()` call from files that live in your own
repo — not user input. Don't apply this pattern to anything that
originated from a request.

---

## Part 10 — Theming: light default, dark via system or explicit toggle

Three-state pattern: no stored preference → follow the OS
(`prefers-color-scheme`); explicit user choice → override via
`data-theme="light"|"dark"` on `<html>`, persisted in `localStorage`.

```css
:root {
  --bg: #ffffff;
  --bg-inset: #f6f8fa;
  --panel: #ffffff;
  --panel-alt: #f6f8fa;
  --border: #d0d7de;
  --border-soft: #eaeef2;
  --text: #1f2328;
  --text-dim: #59636e;
  --text-faint: #818b98;
  --link: #0969da;
  --accent: #0969da;
  --accent-soft: rgba(9,105,218,0.08);
  --danger: #cf222e;
  --mono: 'JetBrains Mono', ui-monospace, 'SF Mono', Menlo, Consolas, monospace;
  --sans: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
  --radius: 6px;
  color-scheme: light;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #0d1117;
    --bg-inset: #161b22;
    --panel: #0d1117;
    --panel-alt: #161b22;
    --border: #30363d;
    --border-soft: #21262d;
    --text: #e6edf3;
    --text-dim: #8b949e;
    --text-faint: #6e7681;
    --link: #4493f8;
    --accent: #4493f8;
    --accent-soft: rgba(68,147,248,0.12);
    --danger: #f85149;
    color-scheme: dark;
  }
}
:root[data-theme="dark"] {
  /* identical values to the media-query block above */
  --bg: #0d1117; --bg-inset: #161b22; --panel: #0d1117; --panel-alt: #161b22;
  --border: #30363d; --border-soft: #21262d; --text: #e6edf3; --text-dim: #8b949e;
  --text-faint: #6e7681; --link: #4493f8; --accent: #4493f8;
  --accent-soft: rgba(68,147,248,0.12); --danger: #f85149; color-scheme: dark;
}
```

Every rule everywhere else in the stylesheet uses `var(--...)`, never a
hardcoded hex. That's what makes the toggle in Part 9's script (`
document.documentElement.setAttribute("data-theme", next)`) sufficient on
its own — no re-render, the CSS variables just resolve differently.

**The one place this pattern gets subtle: Pygments.** See Part 11 — Pygments'
own generated CSS hardcodes literal colors per theme, which doesn't fit
the variable system without adaptation.

---

## Part 11 — Styling: typography, code blocks, and the Pygments trap

### Markdown typography

Modeled directly on GitHub's own `.markdown-body` rules (the real values
GitHub's rendered Markdown uses) rather than invented from scratch — if
your project has its own design system, this is the part to swap out for
your own type scale/spacing, the structure (block-level rules, one
selector per element type) will still make sense:

```css
.markdown-body { font-size: 15px; line-height: 1.55; color: var(--text); }
.markdown-body h1 { font-size: 1.85em; padding-bottom: .3em; border-bottom: 1px solid var(--border-soft); margin: 0 0 16px; }
.markdown-body h2 { font-size: 1.4em; padding-bottom: .3em; border-bottom: 1px solid var(--border-soft); margin: 24px 0 14px; scroll-margin-top: 60px; }
.markdown-body h3 { font-size: 1.15em; margin: 22px 0 10px; scroll-margin-top: 60px; }
.markdown-body code { font-family: var(--mono); font-size: 85%; background: var(--bg-inset); padding: .15em .4em; border-radius: 6px; }
.markdown-body blockquote { padding: 0 1em; color: var(--text-dim); border-left: .25em solid var(--border); }
.markdown-body table { display: block; width: max-content; max-width: 100%; overflow: auto; border-collapse: collapse; font-size: 13px; }
.markdown-body tr:nth-child(2n) { background: var(--bg-inset); }
/* heading anchors: always faintly visible, not hover-only — matches
   GitHub Docs, which shows its anchor icon at low opacity by default
   rather than hiding it entirely until hover */
.markdown-body .headerlink { margin-left: 6px; opacity: .3; font-size: .8em; color: var(--text-faint); }
.markdown-body h2:hover .headerlink, .markdown-body h3:hover .headerlink { opacity: 1; }
```

`scroll-margin-top` on headings matters once you have a sticky topbar —
without it, clicking a TOC/permalink anchor scrolls the target heading
*under* the fixed header.

### Code blocks — the card, the toolbar, and where the color actually comes from

```css
.codehilite {
  position: relative;
  background: var(--bg-inset);
  border: 1px solid var(--border);
  border-radius: 8px;
  margin-bottom: 16px;
  box-shadow: 0 1px 2px rgba(0,0,0,.05);
  overflow: hidden;
}
.markdown-body .codehilite pre { margin: 0; border: none; background: transparent; padding: 14px 16px; }
.code-toolbar {
  display: flex; align-items: center; justify-content: space-between;
  padding: 6px 8px 6px 14px; background: var(--panel-alt);
  border-bottom: 1px solid var(--border-soft);
  font-family: var(--mono); font-size: 11px; color: var(--text-faint);
}
.code-lang { text-transform: lowercase; letter-spacing: .02em; }
.copy-btn {
  display: flex; align-items: center; gap: 5px; padding: 4px 8px;
  background: transparent; border: 1px solid transparent; border-radius: 5px;
  color: var(--text-faint); font-family: var(--sans); font-size: 11px;
}
.copy-btn:hover { background: var(--bg); border-color: var(--border); color: var(--text); }
.copy-btn.copied { color: var(--accent); border-color: var(--accent-soft); background: var(--accent-soft); }
```

**The trap to know about before you hit it:** generate Pygments' token CSS
via `HtmlFormatter(style=...).get_style_defs('.codehilite')`, and it
includes a **container-level rule** — `.codehilite { background: #f8f8f8 }`
for a light style, `.codehilite { background: #0d1117; color: #e6edf3 }`
for a dark one. If you paste that verbatim alongside your own
`.codehilite { background: var(--bg-inset); ... }` card rule above, the two
fight over the same property. Worse, the pasted dark-mode version usually
gets wrapped in a compound selector like
`:root:not([data-theme="light"]) .codehilite { background: #0d1117 }` for
theme-scoping, which has *higher specificity* than a bare `.codehilite`
rule — so it silently wins regardless of source order, and in our case
that hardcoded `#0d1117` happened to exactly equal the *page* background,
so the code block had zero visual distinction from its surroundings in
dark mode. Looked like "the styling is broken"; the actual bug was two
rules disagreeing about who owns `background`.

**Fix: delete the container-level `background`/`color` lines from every
copy of the pasted Pygments CSS** (there will be one per theme variant —
light, the `@media` dark block, and the explicit `[data-theme="dark"]`
block) and let your own card rule own that property everywhere via
`var(--bg-inset)`/`var(--text)`. Keep every *token-level* rule
(`.codehilite .k`, `.codehilite .s`, etc.) exactly as generated — those are
what actually needs Pygments' color choices.

Generate the token CSS once, per theme, and paste it as static text — it's
deterministic for a pinned Pygments version, no reason to compute it
per-request:

```python
from pygments.formatters import HtmlFormatter

print(HtmlFormatter(style="default").get_style_defs(".codehilite"))       # light
print(HtmlFormatter(style="github-dark").get_style_defs(".codehilite"))   # dark — Pygments ships this exact palette
```

For the dark output, prefix every generated selector with your dark-mode
scope so it only applies there — do this programmatically (regex substitution
over the generated CSS text), not by hand, there are 60+ rules:

```python
import re

def scope(css_text, prefix):
    out = []
    for line in css_text.strip().splitlines():
        m = re.match(r"^(.*?)\{(.*)\}(.*)$", line.strip())
        if not m:
            continue
        selectors, body, trailer = m.groups()
        scoped = ", ".join(f"{prefix} {s.strip()}" for s in selectors.split(","))
        out.append(f"{scoped} {{{body}}}{trailer}")
    return "\n".join(out)
```

Regenerate only if you change the pinned Pygments version.

### Overall layout — sidebar / content / sticky rail

```css
.shell { display: flex; flex: 1; min-height: 0; }
.sidebar { width: 252px; flex-shrink: 0; border-right: 1px solid var(--border-soft); overflow-y: auto; }
.content-area { flex: 1; min-width: 0; display: flex; justify-content: center; overflow-y: auto; }
.content-row { display: flex; align-items: flex-start; gap: 36px; width: 100%; max-width: 1180px; padding: 24px 28px 64px; }
.content-main { flex: 1; min-width: 0; max-width: 800px; }
.toc-rail { width: 230px; flex-shrink: 0; position: sticky; top: 66px; max-height: calc(100vh - 86px); overflow-y: auto; }
@media (max-width: 1080px) { .toc-rail { display: none; } }
@media (max-width: 820px) {
  .shell { flex-direction: column; }
  .sidebar { width: 100%; max-height: 220px; border-right: none; border-bottom: 1px solid var(--border-soft); }
}
```

The sticky rail (`position: sticky; top: <topbar height>`) is what makes
"On this page" persist while scrolling without any JS — it's the whole
reason this layout doesn't have the "giant outline pushes real content off
screen" problem an inline collapsible block does for a long, heavily-headed
doc.

---

## Part 12 — Organizing `docs/` into categories

If your `docs/` folder is already flat with more than a handful of files,
this is worth doing alongside shipping the portal — the sidebar tree
(Part 4) supports nested folders natively, they just don't exist yet if
every file has always lived flat.

**Read what you actually have before choosing categories.** Don't invent a
taxonomy in the abstract; group by what the files actually are (we ended
up with categories like `subsystems/`, `integrations/`, `features/`,
`reference/`, `testing/`, `ops/`, `meta/` — yours will differ).

**Check for cross-references before moving anything — this is the step
that actually matters.** A doc that's `grep`-referenced from real source
code (a docstring pointing someone at `docs/some_standard.md`, say) has a
real blast radius if you move it — every reference needs updating too.
Audit before moving:

```bash
for f in docs/*.md; do
  name=$(basename "$f")
  count=$(grep -rl "docs/$name" --include="*.py" . | wc -l)
  [ "$count" != "0" ] && echo "$name -> referenced from $count files"
done
```

A heavily-cross-referenced doc is a good candidate to **leave flat at the
root** rather than move — the file that matters most for this decision in
our case was referenced from 53 unrelated source files; moving it for the
sake of taxonomic tidiness would have meant touching all 53 for zero real
benefit. Also check whether any doc references *another* doc by path
internally (`grep -rn "docs/.*\.md" docs/*.md`) — those need the same
audit.

**No migration needed in the app itself either way** — `_walk_dir` (Part 4)
already handles arbitrary nesting; moving files is purely a `docs/`
reorganization, zero code changes.

---

## Part 13 — Dependencies

```text
Markdown==3.7
Pygments==2.18.0
```

Add to your **main** requirements file, not a dev-only one — staging needs
this feature to actually render, and if your dev-only requirements don't
ship to staging (check your Dockerfile/deploy process), this dependency
has to live wherever staging actually installs from. This mirrors whatever
other env-gated-but-always-installed dependency you probably already have
(a profiler, e.g.) — the *package* installs everywhere, only its *usage*
is gated by environment in Python.

No new settings variables, no `.env`/`.env.example` changes if you went
with Part 1's admin-auth approach — nothing new to deploy or ask anyone
for.

---

## Part 14 — Wiring it together: the three diffs to existing files

Everything above is new files. These are the only edits to files that
already existed:

**1. `config/settings/app_registry.py`** (or wherever your `INSTALLED_APPS`
is built) — add one line, unconditionally:

```python
CUSTOM_APPS = [
    ...,
    "devdocs",
]
```

**2. `config/urls.py`** — the env-gated mount from Part 2:

```python
if DJANGO_ENV in ("development", "staging"):
    urlpatterns += [path("docs/", include("devdocs.urls"))]
```

**3. Your main requirements file** — the two lines from Part 13.

That's the complete integration surface. Nothing else in the existing
codebase changes.

---

## Verification checklist

```bash
python manage.py check
python manage.py collectstatic --noinput   # confirm nothing breaks with the new app installed
python manage.py makemigrations --check --dry-run   # devdocs has no models — expect no new migration
ruff check devdocs/          # or your linter of choice
```

Manually, or scripted via Django's test `Client` with `force_login(staff_user)`
(useful precisely because it doesn't require knowing anyone's real
password):

1. `GET /docs/` logged out → redirected to your admin login.
2. Log in as a non-staff user (if one exists) → still refused.
3. Log in as staff/superuser → lands on `/docs/`, sidebar lists every doc.
4. Click into a nested doc → breadcrumb shows the right path, sidebar
   auto-expands the right section, the active item is highlighted.
5. A doc with a code fence renders with the toolbar + working copy button;
   a doc with an ASCII diagram or box-drawing chart renders in plain text,
   not red.
6. Toggle the theme button → persists across reload (`localStorage`).
7. Download button on a doc page serves the raw `.md` with the right
   filename.
8. Temporarily flip your env var to production locally → `/docs/` 404s
   (URL never registered).

Rendering the actual HTML and screenshotting it (headless Chrome, or
anything similar) before calling styling work done is worth the extra
step — CSS that reads correctly in the stylesheet can still fight itself
in ways (like the Pygments specificity trap in Part 11) that are only
obvious once rendered.

---

## Adapting this to your own codebase — concrete checklist

Everything in this list is something we made a specific choice about for
one codebase; each has a reason stated in the relevant Part above so you
can judge whether the same reasoning applies to yours.

- [ ] **Env var name and values** (Part 2) — replace `DJANGO_ENV`/
      `shared.constants.environ` and the `{"development", "staging"}` set
      with your project's own.
- [ ] **Access control** (Part 1) — confirm you actually have
      `django.contrib.admin` with real staff accounts before assuming
      `staff_member_required` is available; build the fallback shared-code
      gate only if you genuinely have no alternative.
- [ ] **App location** — top-level `devdocs/`, sibling to your settings
      package; adjust if your project's layout convention differs, but
      keep it out of your API-surface package.
- [ ] **Branding strings** — "YOUR-PROJECT · INTERNAL", page titles,
      the landing page copy in `page.html` — cosmetic, change freely.
- [ ] **Color palette** (Part 10) — this guide uses GitHub's own Primer
      light/dark values verbatim; swap for your own design system's
      tokens if you have one, keeping the three-state CSS-variable
      structure (`:root` / media-query-guarded / explicit `[data-theme]`).
- [ ] **`toc_depth`** (Part 4) — tuned to `"1-2"` for docs with deep
      sub-heading structure; adjust to your own docs' typical depth.
- [ ] **`docs/` category taxonomy** (Part 12) — entirely dependent on what
      you actually have; don't copy ours, derive your own from your files.
- [ ] **Bare indented code blocks** (Part 4/5's `_fence_languages`
      docstring) — verify none exist in your `docs/` before trusting the
      language-label positional matching; fix or extend the scanner if any
      do.

---

## Known limitations — deliberately out of scope

- **No search.** A real implementation needs actual search infrastructure;
  a non-functional search box would be worse than none. Add it as its own
  pass if/when it's worth the investment.
- **No brute-force protection on the access gate**, if you built the
  fallback shared-code version from Part 1 instead of reusing admin auth.
  It's explicitly a lightweight second layer over your real security
  boundary (network-restricted staging, ideally), not a security mechanism
  standing alone.
- **Single-process cache, no cross-worker invalidation.** Each worker
  process rebuilds its own copy of the doc tree the first time it notices
  a file changed (Part 4's TTL). Fine for a low-traffic internal tool;
  revisit if you ever run this at a scale where that matters.
- **Positional code-block language matching** (Part 4/5) — a real but
  narrow assumption (no bare indented code blocks). Documented, not
  silently relied on.
