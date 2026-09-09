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
    if DJANGO_ENV.lower() not in _ALLOWED_ENVS:
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
            "index_url": "/",
            "site_title": "Developer Docs",
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
            "index_url": "/",
            "site_title": "Developer Docs",
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
