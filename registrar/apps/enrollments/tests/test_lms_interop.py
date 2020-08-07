"""
Tests for enrollments/lms_interop.py

Much of lms_interop.py is not tested in this file because it is already implicitly
tested by our view tests.
"""

import json
import uuid
from posixpath import join as urljoin
from unittest import mock

import ddt
import responses
from django.conf import settings
from django.test import TestCase
from requests.exceptions import HTTPError
from rest_framework.exceptions import ValidationError

from registrar.apps.core.tests.factories import ProgramFactory
from registrar.apps.core.tests.utils import mock_oauth_login, patch_discovery_program_details

from ..lms_interop import (
    LMS_PROGRAM_COURSE_ENROLLMENTS_API_TPL,
    LMS_PROGRAM_ENROLLMENTS_API_TPL,
    get_course_run_enrollments,
    get_program_enrollments,
)
from ..lms_interop import logger as data_logger
from ..lms_interop import write_course_run_enrollments, write_program_enrollments


class GetEnrollmentsTestMixin:
    """ Common tests for enrollment-getting functions """

    lms_url = None  # Override in subclass
    status_choices = None  # Override in subclass
    curriculum_uuid_in_input = False  # Override in subclass

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.good_input_1 = [
            {
                'student_key': 'abcd',
                'account_exists': True,
                'status': cls.status(0),
            },
            {
                'student_key': 'efgh',
                'account_exists': False,
                'status': cls.status(4),
            },
        ]
        cls.good_input_2 = [
            {
                'student_key': 'ijkl',
                'account_exists': False,
                'status': cls.status(2),
            },
            {
                'student_key': 'mnop',
                'account_exists': True,
                'status': cls.status(3),
            },
        ]
        cls.bad_input = [
            {
                'student_key': 'qrst',
                'account_exists': True,
                'status': 'this-is-not-a-status',
            },
        ]
        cls.good_output = [
            enrollment.copy()
            for enrollment in cls.good_input_1 + cls.good_input_2
        ]
        all_input = cls.good_input_1 + cls.good_input_2 + cls.bad_input
        if cls.curriculum_uuid_in_input:
            for enrollment in all_input:
                enrollment['curriculum_uuid'] = str(uuid.uuid4())

    @classmethod
    def status(cls, i):
        return cls.status_choices[i % len(cls.status_choices)]

    def get_enrollments(self):
        """ Override in subclass """
        raise NotImplementedError  # pragma: no cover

    @mock_oauth_login
    @responses.activate
    def test_get_enrollments(self):
        responses.add(
            responses.GET,
            self.lms_url,
            status=200,
            json={'next': self.lms_url + "?cursor=xxx", 'results': self.good_input_1},
        )
        responses.add(
            responses.GET,
            self.lms_url,
            status=200,
            json={'next': None, 'results': self.good_input_2},
        )
        enrolls = self.get_enrollments()
        self.assertCountEqual(enrolls, self.good_output)

    @mock_oauth_login
    @responses.activate
    def test_get_enrollments_bad_input(self):
        responses.add(
            responses.GET,
            self.lms_url,
            status=200,
            json={'next': None, 'results': self.bad_input},
        )
        with self.assertRaises(ValidationError):
            self.get_enrollments()

    @mock_oauth_login
    @responses.activate
    def test_get_enrollments_500(self):
        responses.add(responses.GET, self.lms_url, status=500)
        with self.assertRaises(HTTPError):
            self.get_enrollments()

    @mock_oauth_login
    @responses.activate
    def test_get_enrollments_404(self):
        responses.add(responses.GET, self.lms_url, status=404)
        with self.assertRaisesRegex(HTTPError, '404 Client Error: Not Found'):
            self.get_enrollments()


class GetProgramEnrollmentsTestCase(GetEnrollmentsTestMixin, TestCase):
    """ Tests for data.get_program_enrollments """

    program_uuid = '7fbefaa4-c0e8-431b-af69-8d3ddde543a2'
    lms_url = urljoin(settings.LMS_BASE_URL, LMS_PROGRAM_ENROLLMENTS_API_TPL.format(program_uuid))
    status_choices = ['enrolled', 'pending', 'suspended', 'canceled']
    curriculum_uuid_in_input = True

    def get_enrollments(self):
        return get_program_enrollments(self.program_uuid)


@ddt.ddt
class GetCourseRunEnrollmentsTestCase(GetEnrollmentsTestMixin, TestCase):
    """ Tests for data.get_course_run_enrollments """

    program_uuid = 'ce5ea4c8-666e-429c-9d2c-a5698f86f6fb'
    course_id = 'course-v1:ABCx+Subject-101+Term'
    lms_url = urljoin(settings.LMS_BASE_URL, LMS_PROGRAM_COURSE_ENROLLMENTS_API_TPL.format(program_uuid, course_id))
    status_choices = ['active', 'inactive']
    curriculum_uuid_in_input = False

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for enrollment in cls.good_output:
            enrollment['course_id'] = cls.course_id

    def get_enrollments(self):
        return get_course_run_enrollments(self.program_uuid, self.course_id)

    @mock_oauth_login
    @responses.activate
    @ddt.data(None, 'myFavoriteCourse', 'someOtherName')
    def test_external_course_key(self, external_course_key):
        responses.add(
            responses.GET,
            self.lms_url,
            status=200,
            json={'next': None, 'results': self.good_input_2},
        )
        enrollments = get_course_run_enrollments(
            self.program_uuid,
            self.course_id,
            external_course_key
        )
        for enrollment in enrollments:
            self.assertEqual(enrollment.get('course_id'), external_course_key or self.course_id)


@ddt.ddt
class WriteEnrollmentsTestMixin:
    """
    Common tests for program and course enrollment writing functionality
    """

    # Override in subclass
    url = None

    # Optionally override in subclass
    program_uuid = '99999999-aaaa-bbbb-cccc-123412341234'
    curriculum_uuid = None
    course_key = None

    max_write_const = 'registrar.apps.enrollments.lms_interop.LMS_ENROLLMENT_WRITE_MAX_SIZE'

    # The stautses here are purposely nonsensical
    # because the write_enrollment functions don't do any
    # status validation.
    enrollments = [
        {'student_key': 'erin', 'status': 'x'},
        {'student_key': 'peter', 'status': 'y'},
        {'student_key': 'tom', 'status': 'z'},
        {'student_key': 'lucy', 'status': 'x'},
        {'student_key': 'craig', 'status': 'y'},
        {'student_key': 'sheryl', 'status': 'z'},
        {'student_key': 'erin', 'status': 'y'},
        {'student_key': 'peter', 'status': 'y'},
        {'student_key': 'mark', 'status': 'x'},
        {'student_key': 'mark', 'status': 'x'},
        {'student_key': 'peter', 'status': 'z'},
        {'student_key': 'steven', 'status': 'z'},
        {'student_key': 'kathy', 'status': 'x'},
        {'student_key': 'gloria', 'status': 'y'},
        {'student_key': 'robert', 'status': 'z'},
    ]
    expected_output = {
        'erin': 'duplicated',
        'peter': 'duplicated',
        'tom': 'z',
        'lucy': 'x',
        'craig': 'y',
        'sheryl': 'z',
        'mark': 'duplicated',
        'steven': 'z',
        'kathy': 'x',
        'gloria': 'y',
        'robert': 'z',
    }

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        ProgramFactory(discovery_uuid=cls.program_uuid)

    def _add_echo_callback(self, status_code):
        """
        Returns mock handler for LMS enrollment write request
        that echos back statuses and returns the given status code.
        """
        def callback(request):
            """ Echo student statuses back and return with `status_code` """
            req_body = json.loads(request.body.decode('utf-8'))
            resp_body = {
                enrollment['student_key']: enrollment['status']
                for enrollment in req_body
            }
            return status_code, request.headers, json.dumps(resp_body)
        responses.add_callback(responses.POST, self.url, callback=callback)

    @ddt.data(
        (25, [200], True),
        (25, [422], False),
        (4, [200, 200], True),
        (4, [201, 201], True),
        (4, [207, 207], True),
        (3, [200, 207, 422], True),
        (3, [422, 200, 422], True),
        (3, [422, 422, 422], False),
    )
    @ddt.unpack
    @mock_oauth_login
    @responses.activate
    def test_write_enrollments(
            self, lms_write_size, lms_statuses, expected_good,
    ):
        for status_code in lms_statuses:
            self._add_echo_callback(status_code)
        with mock.patch(self.max_write_const, new=lms_write_size):
            good, bad, output = self.write_enrollments(self.enrollments)

        self.assertDictEqual(output, self.expected_output)
        self.assertEqual(good, expected_good)
        self.assertTrue(bad)  # Will always be 'bad' because of duplicates

        expected_num_calls = len(lms_statuses)
        # There is an initial request for the OAuth token
        self.assertEqual(len(responses.calls) - 1, expected_num_calls)
        for call in responses.calls[1:]:
            body = json.loads(call.request.body.decode('utf-8'))
            self.assertLessEqual(len(body), lms_write_size)
            if self.curriculum_uuid:
                for enrollment in body:
                    self.assertEqual(
                        enrollment.get('curriculum_uuid'),
                        self.curriculum_uuid
                    )

    expected_err_output = {
        'erin': 'duplicated',
        'peter': 'duplicated',
        'tom': 'z',
        'lucy': 'x',
        'craig': 'y',
        'sheryl': 'z',
        'mark': 'duplicated',
        'steven': 'internal-error',
        'kathy': 'internal-error',
        'gloria': 'internal-error',
        'robert': 'internal-error',
    }

    @mock_oauth_login
    @responses.activate
    @mock.patch.object(data_logger, 'info', autospec=True)
    def test_backend_errors(self, mock_data_logger):
        self._add_echo_callback(200)
        self._add_echo_callback(422)
        string_422 = "this a string error from lms"
        dict_400 = {
            "developer_message": "this is a dict error from lms"
        }
        responses.add(responses.POST, self.url, status=422, body=string_422)
        responses.add(responses.POST, self.url, status=400, json=dict_400)
        with mock.patch(self.max_write_const, new=2):
            good, bad, output = self.write_enrollments(self.enrollments)

        self.assertDictEqual(output, self.expected_err_output)
        self.assertEqual(good, True)
        self.assertEqual(bad, True)

        for i, status in enumerate([200, 422, 422, 400]):
            log_str = mock_data_logger.call_args_list[i].args[0]
            self.assertIn('POST', log_str)
            self.assertIn(self.url, log_str)
            self.assertIn(str(status), log_str)

    def write_enrollments(self, enrollments):
        """ Overridden in child classes """
        raise NotImplementedError  # pragma: no cover


class WriteProgramEnrollmentsTests(WriteEnrollmentsTestMixin, TestCase):
    """
    Tests for the method to write program enrollment data
    to the LMS API.
    """
    curriculum_uuid = 'c94adf9a-29e8-471c-b8ce-d53320507490'

    @property
    def url(self):
        return urljoin(
            settings.LMS_BASE_URL,
            LMS_PROGRAM_ENROLLMENTS_API_TPL.format(
                self.program_uuid
            ),
        )

    def write_enrollments(self, enrollments):
        mock_program_details = {
            'curricula': [
                {
                    'is_active': True,
                    'uuid': self.curriculum_uuid,
                },
            ],
        }
        with patch_discovery_program_details(mock_program_details):
            return write_program_enrollments('POST', self.program_uuid, enrollments)


class WriteCourseRunEnrollmentsTests(WriteEnrollmentsTestMixin, TestCase):
    """
    Tests for the method to write program/course enrollment data
    to the LMS API.
    """
    course_key = 'course-v1:edX+DemoX+Demo_Course'

    @property
    def url(self):
        return urljoin(
            settings.LMS_BASE_URL,
            LMS_PROGRAM_COURSE_ENROLLMENTS_API_TPL.format(
                self.program_uuid,
                self.course_key
            )
        )

    def write_enrollments(self, enrollments):
        return write_course_run_enrollments(
            'POST', self.program_uuid, self.course_key, enrollments
        )
