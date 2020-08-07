""" Tests for celery task error behavior in the grades app """

from unittest import mock

import ddt
import requests
from django.test import TestCase
from rest_framework.exceptions import ValidationError

from registrar.apps.core.tests.mixins import BaseTaskTestMixin
from registrar.apps.core.tests.utils import patch_discovery_program_details

from ..tasks import get_course_run_grades


@ddt.ddt
@patch_discovery_program_details({})
class GetCourseRunGradesTest(BaseTaskTestMixin, TestCase):
    """ Error behavior tests for get_course_run_grades"""
    mock_base = 'registrar.apps.grades.lms_interop.'
    mock_function = 'get_course_run_grades'
    internal_course_key = 'course-v1:edX+Test101+F19'

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.lms_data = [
            cls._grade('0001', 'A', 0.95, True),
            cls._grade('0002', 'B', 0.8, True),
            cls._grade('0003', None, 0.1, False),
            cls._grade('0004', 'F', 0.31, False),
        ]

    @staticmethod
    def _grade(student_key, letter_grade, percent, passed):
        return {
            'student_key': student_key,
            'letter_grade': letter_grade,
            'percent': percent,
            'passed': passed,
        }

    def spawn_task(self, program_key=None, **kwargs):
        return get_course_run_grades.apply_async(
            (
                self.job_id,
                self.user.id,
                kwargs.get('file_format', 'json'),
                program_key or self.program.key,
                self.internal_course_key,
            ),
            task_id=self.job_id
        )

    @ddt.data(500, 404)
    def test_http_error(self, status_code):
        with mock.patch(self.full_mock_path()) as mock_load_data:
            mock_request = mock.Mock()
            mock_request.url = 'registrar.edx.org'
            mock_response = mock.Mock()
            mock_response.status_code = status_code
            error = requests.exceptions.HTTPError(
                request=mock_request,
                response=mock_response,
            )
            mock_load_data.side_effect = error
            self.spawn_task().wait()
        expected_msg = "HTTP error {} when getting grades at registrar.edx.org".format(
            status_code,
        )
        self.assert_failed(expected_msg)

    def test_invalid_data(self):
        with mock.patch(self.full_mock_path()) as mock_load_data:
            mock_load_data.side_effect = ValidationError()
            self.spawn_task().wait()
        self.assert_failed("Invalid grade data from LMS")

    def test_invalid_format(self):
        with mock.patch(self.full_mock_path()) as mock_load_data:
            mock_load_data.return_value = (True, False, self.lms_data)
            with self.assertRaisesRegex(ValueError, 'Invalid file_format'):
                self.spawn_task(file_format='invalid-format').wait()
