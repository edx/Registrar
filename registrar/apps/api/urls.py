"""
Root API URLs.

All API URLs should be versioned, so urlpatterns should only
contain namespaces for the active versions of the API.
"""
from django.conf.urls import include, url

from registrar.apps.api.internal import urls as internal_urls
from registrar.apps.api.v1 import urls as v1_urls
from registrar.apps.api.v2 import urls as v2_urls


app_name = 'api'
urlpatterns = [
    url(r'^internal/', include(internal_urls)),
    url(r'^v1/', include(v1_urls)),
    url(r'^v2/', include(v2_urls)),
]
