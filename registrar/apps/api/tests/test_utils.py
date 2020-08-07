"""
Tests for api/utils.py
"""
import uuid

import ddt
from django.test import TestCase

from registrar.apps.api.utils import build_absolute_api_url, to_absolute_api_url


@ddt.ddt
class AbsoluteUrlTestCase(TestCase):
    """ Tests for build_absolute_api_url and to_absolute_api_url """

    @ddt.data(
        (
            ('/api/v1', 'programs', 'abcd/enrollments/'),
            'http://localhost/api/v1/programs/abcd/enrollments/',
        ),
        (
            ('/api/', '/v1/programs/', '/123'),
            'http://localhost/api/v1/programs/123',
        ),
        (
            ('/api/v1/programs/', '/', '', '456', '', '/'),
            'http://localhost/api/v1/programs/456/',
        ),
    )
    @ddt.unpack
    def test_to_absolute_api_url(self, inputs, expected_output):
        self.assertEqual(to_absolute_api_url(*inputs), expected_output)

    def test_to_absolute_api_url_bad_input(self):
        with self.assertRaises(ValueError):
            to_absolute_api_url('abc/def', 'g')

    def test_build_absolute_api_url(self):
        job_id = uuid.uuid4()
        self.assertEqual(
            build_absolute_api_url('api:v1:job-status', job_id=job_id),
            f'http://localhost/api/v1/jobs/{job_id}',
        )
