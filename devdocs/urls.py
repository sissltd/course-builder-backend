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