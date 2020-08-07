""" API internal URLs. """

from django.conf.urls import url

from registrar.apps.core.constants import PROGRAM_KEY_PATTERN

from . import views


app_name = 'internal'

urlpatterns = [
    url(
        r'^cache/?$',
        views.FlushProgramCacheView.as_view(),
        name="flush-program-cache-all",
    ),
    url(
        fr'^cache/{PROGRAM_KEY_PATTERN}/?$',
        views.FlushProgramCacheView.as_view(),
        name="flush-program-cache-one",
    ),
]
