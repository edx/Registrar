"""
Tests for core proxy models.
"""

from posixpath import join as urljoin
from unittest.mock import patch
from uuid import UUID

import ddt
import responses
from django.conf import settings
from django.core.cache import cache
from django.test import TestCase

from ..discovery_cache import DISCOVERY_PROGRAM_API_TPL, ProgramDetails
from .utils import mock_oauth_login


def make_course_run(n, with_external_key=False):
    """
    Return a course run dictionary for testing.

    If `with_external_key` is set to True, set ext key to testorg-course-${n}.
    """
    return {
        'key': f'course-v1:TestRun+{n}',
        'external_key': (
            f'testorg-course-{n}'
            if with_external_key
            else None
        ),
        'title': f'Test Course {n}',
        'marketing_url': f'https://stem.edx.org/masters-in-cs/test-course-{n}',
        'extraneous_data': ['blah blah blah'],
    }


def patch_fetch_program_from_discovery(mock_response_data):
    """
    Patch the function that we use to call the Discovery service
    to instead statically return `mock_response_data`.

    Note that the responses will still be stored in the Django cache.
    """
    return patch.object(
        ProgramDetails,
        'fetch_program_from_discovery',
        lambda *_args, **_kwargs: mock_response_data,
    )


@ddt.ddt
class ProgramDetailsTestCase(TestCase):
    """
    Test ProgramDetails interface to the Discovery data cache.
    """
    program_uuid = UUID("88888888-4444-2222-1111-000000000000")
    discovery_url = urljoin(
        settings.DISCOVERY_BASE_URL,
        DISCOVERY_PROGRAM_API_TPL.format(program_uuid)
    )

    inactive_curriculum_uuid = UUID("77777777-4444-2222-1111-000000000000")
    active_curriculum_uuid = UUID("77777777-4444-2222-1111-000000000001")
    ignored_active_curriculum_uuid = UUID("77777777-4444-2222-1111-000000000002")

    program_from_discovery = {
        'title': "Master's in CS",
        'marketing_url': "https://stem.edx.org/masters-in-cs",
        'type': "Masters",
        'curricula': [
            {
                # Inactive curriculum. Should be ignored.
                'uuid': str(inactive_curriculum_uuid),
                'is_active': False,
                'courses': [{'course_runs': [make_course_run(0, True)]}],
            },
            {
                # Active curriculum. All three course runs should be aggregated.
                'uuid': str(active_curriculum_uuid),
                'is_active': True,
                'courses': [
                    {'course_runs': [make_course_run(1)]},
                    {'course_runs': []},
                    {'course_runs': [make_course_run(2, True), make_course_run(3)]},
                    {'course_runs': [{'this course run': 'has the wrong format'}]},
                ]
            },
            {
                # Second active curriculum.
                # In current implementation, should be ignored.
                'uuid': str(ignored_active_curriculum_uuid),
                'is_active': True,
                'courses': [{'course_runs': [make_course_run(4, True)]}],
            },
        ],
    }

    def setUp(self):
        super().setUp()
        cache.clear()

    @mock_oauth_login
    @responses.activate
    @ddt.data(
        (200, program_from_discovery, program_from_discovery),
        (200, {}, {}),
        (200, 'this is a string, but it should be a dict', {}),
        (404, {'message': 'program not found'}, {}),
        (500, {'message': 'everything is broken'}, {}),
    )
    @ddt.unpack
    def test_discovery_program_get(self, disco_status, disco_json, expected_raw_data):
        responses.add(
            responses.GET,
            self.discovery_url,
            status=disco_status,
            json=disco_json,
        )
        loaded_program = ProgramDetails(self.program_uuid)
        assert isinstance(loaded_program, ProgramDetails)
        assert loaded_program.uuid == self.program_uuid
        assert loaded_program.raw_data == expected_raw_data
        self.assertEqual(len(responses.calls), 2)

        # This should used the cached Discovery response.
        reloaded_program = ProgramDetails(self.program_uuid)
        assert isinstance(reloaded_program, ProgramDetails)
        assert reloaded_program.uuid == self.program_uuid
        assert reloaded_program.raw_data == expected_raw_data
        self.assertEqual(len(responses.calls), 2)

    @patch_fetch_program_from_discovery(program_from_discovery)
    def test_active_curriculum(self):
        program = ProgramDetails(self.program_uuid)
        assert program.active_curriculum_uuid == self.active_curriculum_uuid
        assert len(program.course_runs) == 4
        assert program.course_runs[0].title == "Test Course 1"
        assert program.course_runs[-1].title is None

    @patch_fetch_program_from_discovery({})
    def test_no_active_curriculum(self):
        program = ProgramDetails(self.program_uuid)
        assert program.active_curriculum_uuid is None
        assert not program.course_runs

    @patch_fetch_program_from_discovery(program_from_discovery)
    @ddt.data(
        # Non-existent course run.
        ('non-existent', None, None),
        # Real course run, but not in active curriculum.
        ('course-v1:TestRun+0', None, None),
        ('testorg-course-0', None, None),
        # Real course run, in active curriculum, only has internal course key.
        ('course-v1:TestRun+1', 'course-v1:TestRun+1', None),
        ('testorg-course-1', None, None),
        # Real course run, in active curriculum, has internal and external keys.
        ('course-v1:TestRun+2', 'course-v1:TestRun+2', 'testorg-course-2'),
        ('testorg-course-2', 'course-v1:TestRun+2', 'testorg-course-2'),
    )
    @ddt.unpack
    def test_get_course_keys(self, argument, expected_course_key, expected_external_key):
        program = ProgramDetails(self.program_uuid)
        actual_course_key = program.get_course_key(argument)
        assert actual_course_key == expected_course_key
        actual_external_key = program.get_external_course_key(argument)
        assert actual_external_key == expected_external_key
