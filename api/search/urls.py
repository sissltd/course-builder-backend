from django.urls import path

from api.search.views import GlobalSearchView

urlpatterns = [
    path("global-search/", GlobalSearchView.as_view(), name="global-search"),
]
