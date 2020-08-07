""" Tests for loading grade data from LMS """
import uuid
from posixpath import join as urljoin

import ddt
import responses
from django.conf import settings
from django.test import TestCase
from requests.exceptions import HTTPError
from rest_framework.exceptions import ValidationError

from registrar.apps.core.tests.utils import mock_oauth_login

from ..lms_interop import LMS_PROGRAM_COURSE_GRADES_API_TPL, get_course_run_grades


@ddt.ddt
class GetCourseGradesTest(TestCase):
    """ Tests for get_course_run_grades """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.good_input_1 = [
            cls._grade('alice', 'A', '0.950', True),
            cls._grade('bob', 'D', '0.500', False)
        ]
        cls.good_input_2 = [
            cls._grade('caryl', 'C', '0.700', True),
            cls._grade('daryl', None, '0.200', False)
        ]
        cls.bad_input = [
            {
                'letter_grade': True,
                'passed': 'probably not',
            },
        ]
        cls.good_output = [
            grade.copy()
            for grade in cls.good_input_1 + cls.good_input_2
        ]
        cls.program_uuid = str(uuid.uuid4())
        cls.course_id = 'test/course-1a/test'
        cls.lms_url = urljoin(
            settings.LMS_BASE_URL,
            LMS_PROGRAM_COURSE_GRADES_API_TPL.format(cls.program_uuid, cls.course_id)
        )

    @staticmethod
    def _grade(student_key, letter_grade, percent, passed):
        return {
            'student_key': student_key,
            'letter_grade': letter_grade,
            'percent': percent,
            'passed': passed,
        }

    @ddt.unpack
    @ddt.data(
        (200, 200, True, False),
        (200, 207, True, True),
        (207, 207, True, True),
        (200, 422, True, True),
        (422, 422, False, True),
    )
    @mock_oauth_login
    @responses.activate
    def test_get_grades(self, status_1, status_2, expected_successes, expected_failures):
        responses.add(
            responses.GET,
            self.lms_url,
            status=status_1,
            json={'next': self.lms_url + "?cursor=xxx", 'results': self.good_input_1},
        )
        responses.add(
            responses.GET,
            self.lms_url,
            status=status_2,
            json={'next': None, 'results': self.good_input_2},
        )
        any_successes, any_failures, grades = get_course_run_grades(self.program_uuid, self.course_id)
        self.assertCountEqual(grades, self.good_output)
        self.assertEqual(any_successes, expected_successes)
        self.assertEqual(any_failures, expected_failures)

    @mock_oauth_login
    @responses.activate
    def test_get_grades_bad_input(self):
        responses.add(
            responses.GET,
            self.lms_url,
            status=200,
            json={'next': None, 'results': self.bad_input},
        )
        with self.assertRaises(ValidationError):
            get_course_run_grades(self.program_uuid, self.course_id)

    @mock_oauth_login
    @responses.activate
    def test_no_data(self):
        responses.add(
            responses.GET,
            self.lms_url,
            status=204,
            json={'next': None, 'results': []},
        )
        any_successes, any_failures, grades = get_course_run_grades(self.program_uuid, self.course_id)
        self.assertFalse(any_successes)
        self.assertFalse(any_failures)
        self.assertEqual(grades, {})

    @mock_oauth_login
    @responses.activate
    def test_get_enrollments_500(self):
        responses.add(responses.GET, self.lms_url, status=500)
        with self.assertRaises(HTTPError):
            get_course_run_grades(self.program_uuid, self.course_id)

    @mock_oauth_login
    @responses.activate
    def test_get_enrollments_404(self):
        responses.add(responses.GET, self.lms_url, status=404)
        with self.assertRaisesRegex(HTTPError, '404 Client Error: Not Found'):
            get_course_run_grades(self.program_uuid, self.course_id)

    @mock_oauth_login
    @responses.activate
    def test_json_decode(self):
        responses.add(
            responses.GET,
            self.lms_url,
            status=422,
            body='this is 422',
        )
        with self.assertRaises(ValidationError):
            get_course_run_grades(self.program_uuid, self.course_id)
