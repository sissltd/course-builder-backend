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