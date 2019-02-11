"""
Root API URLs.

All API URLs should be versioned, so urlpatterns should only
contain namespaces for the active versions of the API.
"""
from django.conf.urls import url, include


app_name = 'api'
urlpatterns = [
    url(r'^v1/', include('registrar.apps.api.v1.urls')),
]
