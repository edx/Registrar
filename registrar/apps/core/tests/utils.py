""" General utilities for unit tests. """

import json
from functools import wraps
from unittest.mock import patch

import responses
from django.conf import settings

from ..discovery_cache import ProgramDetails


def mock_oauth_login(fn):
    """
    Mock request to authenticate registrar as a backend client
    """
    @wraps(fn)
    def inner(self, *args, **kwargs):
        responses.add(
            responses.POST,
            settings.LMS_BASE_URL + '/oauth2/access_token',
            body=json.dumps({'access_token': 'abcd', 'expires_in': 60}),
            status=200
        )
        return fn(self, *args, **kwargs)
    return inner


def patch_discovery_program_details(mock_raw_data):
    """
    Patch the function that is used to load data from the
    Discovery cache to instead statically return `mock_raw_data`.

    Note that this circumvents any usage of the Django cache.
    """
    return patch.object(
        ProgramDetails,
        'get_raw_data_for_program',
        lambda *_args, **_kwargs: mock_raw_data,
    )
