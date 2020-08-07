"""
Unit tests for the enrollment.tasks module.
"""
import json
from collections import OrderedDict, namedtuple
from unittest.mock import patch
from uuid import UUID

import boto3
import ddt
import moto
from django.conf import settings
from django.test import TestCase
from requests.exceptions import HTTPError
from rest_framework.exceptions import ValidationError

from registrar.apps.core.discovery_cache import ProgramDetails
from registrar.apps.core.filestore import get_enrollment_uploads_filestore
from registrar.apps.core.tests.mixins import BaseTaskTestMixin, S3MockEnvVarsMixin
from registrar.apps.core.tests.utils import patch_discovery_program_details

from .. import tasks
from ..constants import (
    COURSE_ENROLLMENT_ACTIVE,
    COURSE_ENROLLMENT_INACTIVE,
    PROGRAM_ENROLLMENT_ENROLLED,
    PROGRAM_ENROLLMENT_PENDING,
)


FakeRequest = namedtuple('FakeRequest', ['url'])
FakeResponse = namedtuple('FakeResponse', ['status_code'])


uploads_filestore = get_enrollment_uploads_filestore()


@ddt.ddt
class ListEnrollmentTaskTestMixin(BaseTaskTestMixin):
    """ Tests for enrollment listing task error behavior. """

    # Override in subclass
    enrollment_statuses = (None, None)
    mocked_get_enrollments_method = None

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.enrollment_data = [
            cls._enrollment('0001', cls.enrollment_statuses[0], True),
            cls._enrollment('0002', cls.enrollment_statuses[1], True),
            cls._enrollment('0003', cls.enrollment_statuses[0], False),
            cls._enrollment('0004', cls.enrollment_statuses[1], False),
        ]

    @staticmethod
    def _enrollment(student_key, status, exists):
        """ Helper to create enrollment record """
        return {
            'student_key': student_key,
            'status': status,
            'account_exists': exists
        }

    @ddt.data(500, 404)
    def test_http_error(self, status_code):
        with patch.object(tasks.lms, self.mocked_get_enrollments_method) as mock_get_enrollments:
            error = HTTPError(request=FakeRequest('registrar.edx.org'), response=FakeResponse(status_code))
            mock_get_enrollments.side_effect = error
            task = self.spawn_task()  # pylint: disable=assignment-from-no-return
            task.wait()
        expected_msg = f"HTTP error {status_code} when getting enrollments at registrar.edx.org"
        self.assert_failed(expected_msg)

    def test_invalid_data(self):
        with patch.object(tasks.lms, self.mocked_get_enrollments_method) as mock_get_enrollments:
            mock_get_enrollments.side_effect = ValidationError()
            task = self.spawn_task()  # pylint: disable=assignment-from-no-return
            task.wait()
        self.assert_failed("Invalid enrollment data from LMS")

    def test_invalid_format(self):
        with patch.object(tasks.lms, self.mocked_get_enrollments_method) as mock_get_enrollments:
            mock_get_enrollments.return_value = self.enrollment_data
            exception_raised = False
            try:
                task = self.spawn_task(file_format='invalid-format')  # pylint: disable=assignment-from-no-return
                task.wait()
            except ValueError as e:
                self.assertIn('Invalid file_format', str(e))
                exception_raised = True
        self.assertTrue(exception_raised)


@patch_discovery_program_details({})
class ListProgramEnrollmentTaskTests(ListEnrollmentTaskTestMixin, TestCase):
    """ Tests for task error behavior. """
    enrollment_statuses = (
        PROGRAM_ENROLLMENT_ENROLLED,
        PROGRAM_ENROLLMENT_PENDING,
    )
    mocked_get_enrollments_method = 'get_program_enrollments'

    def spawn_task(self, program_key=None, **kwargs):
        return tasks.list_program_enrollments.apply_async(
            (
                self.job_id,
                self.user.id,
                kwargs.get('file_format', 'json'),
                program_key or self.program.key,
            ),
            task_id=self.job_id
        )


@patch_discovery_program_details({})
class ListCourseRunEnrollmentTaskTests(ListEnrollmentTaskTestMixin, TestCase):
    """ Tests for task error behavior. """
    enrollment_statuses = (
        COURSE_ENROLLMENT_ACTIVE,
        COURSE_ENROLLMENT_INACTIVE,
    )
    mocked_get_enrollments_method = 'get_course_run_enrollments'
    internal_course_key = 'course-1'
    external_course_key = 'external_course_key'

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        for enrollment in cls.enrollment_data:
            enrollment['course_id'] = cls.external_course_key

    def spawn_task(self, program_key=None, **kwargs):
        return tasks.list_course_run_enrollments.apply_async(
            (
                self.job_id,
                self.user.id,
                kwargs.get('file_format', 'json'),
                program_key or self.program.key,
                self.internal_course_key,
                self.external_course_key,
            ),
            task_id=self.job_id
        )


@patch_discovery_program_details({
    'curricula': [{
        'is_active': True,
        'courses': [{
            'course_runs': [{
                'key': 'course-key',
                'external_key': 'external-key',
                'title': 'title',
                'marketing_url': 'www',
            }],
        }],
    }],
})
class ListAllCourseRunEnrollmentTaskTests(ListEnrollmentTaskTestMixin, TestCase):
    """ Tests for task error behavior. """
    enrollment_statuses = (
        COURSE_ENROLLMENT_ACTIVE,
        COURSE_ENROLLMENT_INACTIVE,
    )
    mocked_get_enrollments_method = 'get_course_run_enrollments'
    course_key = 'course-1'
    external_course_key = 'external_course_key'
    default_course_run = {'key': 'course-key', 'external_key': 'external-key'}

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        for enrollment in cls.enrollment_data:
            enrollment['course_id'] = cls.external_course_key

    def spawn_task(self, program_key=None, **kwargs):
        return tasks.list_all_course_run_enrollments.apply_async(
            (
                self.job_id,
                self.user.id,
                kwargs.get('file_format', 'json'),
                program_key or self.program.key,
            ),
            task_id=self.job_id
        )


class WriteEnrollmentTaskTestMixin(BaseTaskTestMixin, S3MockEnvVarsMixin):
    """
    Tests common for both program and course-run enrollment writing tasks.
    """
    json_filepath = "testfile.csv"
    mock_function = None  # Override in subclass
    expected_fieldnames = None  # Override in subclass

    @classmethod
    def setUpClass(cls):
        # This is unfortunately duplicated from:
        #   registrar.apps.api.v1.tests.test_views:S3MockMixin.
        # It would be ideal to move that mixin to a utilities file and re-use
        # it here, but moto seems to have a bug/"feature" where it only works
        # in modules that explicitly import it.
        super().setUpClass()
        cls._s3_mock = moto.mock_s3()
        cls._s3_mock.start()
        conn = boto3.resource('s3')
        conn.create_bucket(Bucket=settings.REGISTRAR_BUCKET)

    @classmethod
    def tearDownClass(cls):
        cls._s3_mock.stop()
        super().tearDownClass()

    def tearDown(self):
        super().tearDown()
        uploads_filestore.delete(self.json_filepath)

    def mock_write_enrollments(self, any_successes, any_failures):
        """
        Creates a mock function that returns results normally returned
        by an enrollment-writing function.
        """
        raise NotImplementedError()  # pragma: no cover

    def test_empty_list_file(self):
        uploads_filestore.store(self.json_filepath, "[]")
        with patch.object(
                tasks.lms,
                self.mock_function,
                new=self.mock_write_enrollments(False, False),
        ):
            self.spawn_task().wait()
        self.assert_succeeded(','.join(self.expected_fieldnames) + "\r\n", "204")

    def test_no_such_file(self):
        self.spawn_task().wait()
        self.assert_failed(
            "Enrollment file for program_key={} not found at {}".format(
                self.program.key, self.json_filepath
            )
        )

    def test_file_not_json(self):
        uploads_filestore.store(self.json_filepath, "this is not valid json")
        self.spawn_task().wait()
        self.assert_failed(
            "Enrollment file for program_key={} at {} is not valid JSON".format(
                self.program.key, self.json_filepath
            )
        )


@ddt.ddt
@patch_discovery_program_details({})
class WriteProgramEnrollmentTaskTests(WriteEnrollmentTaskTestMixin, TestCase):
    """
    Tests for write_program_enrollments task.
    """
    mock_function = 'write_program_enrollments'
    expected_fieldnames = ('student_key', 'status')

    def spawn_task(self, program_key=None, **kwargs):
        return tasks.write_program_enrollments.apply_async(
            (
                self.job_id,
                self.user.id,
                self.json_filepath,
                program_key or self.program.key,
            ),
            task_id=self.job_id
        )

    def mock_write_enrollments(self, any_successes, any_failures):
        """
        Create mock for data.write_program_enrollments

        Mock will return `any_successes`, `any_failures`, and `enrollments`
        echoed back as a dictionary.
        """
        def inner(_method, program_uuid, enrollments):
            """ Mock for data.write_program_enrollments. """
            self.assertIsInstance(program_uuid, UUID)
            results = OrderedDict([
                (enrollment['student_key'], enrollment['status'])
                for enrollment in enrollments
            ])
            return any_successes, any_failures, results
        return inner

    @ddt.data(
        (True, False, "200"),
        (True, True, "207"),
        (False, True, "422"),
    )
    @ddt.unpack
    def test_success(self, any_successes, any_failures, expected_code_str):
        enrolls = [
            {'student_key': 'john', 'status': 'x'},
            {'student_key': 'bob', 'status': 'y'},
            {'student_key': 'serena', 'status': 'z'},
        ]
        uploads_filestore.store(self.json_filepath, json.dumps(enrolls))
        with patch.object(
                tasks.lms,
                self.mock_function,
                new=self.mock_write_enrollments(any_successes, any_failures),
        ):
            self.spawn_task().wait()
        self.assert_succeeded(
            "student_key,status\r\n"
            "john,x\r\n"
            "bob,y\r\n"
            "serena,z\r\n",
            expected_code_str,
        )


@ddt.ddt
@patch_discovery_program_details({})
class WriteCourseRunEnrollmentTaskTests(WriteEnrollmentTaskTestMixin, TestCase):
    """
    Tests for write_course_run_enrollments task.
    """
    mock_function = 'write_course_run_enrollments'
    expected_fieldnames = ('course_id', 'student_key', 'status')

    def spawn_task(self, program_key=None, **kwargs):
        return tasks.write_course_run_enrollments.apply_async(
            (
                self.job_id,
                self.user.id,
                self.json_filepath,
                program_key or self.program.key,
            ),
            task_id=self.job_id
        )

    # pylint: disable=arguments-differ
    def mock_write_enrollments(self, any_successes, any_failures, expected_enrolls_by_course_key=None):
        """
        Create mock for data.write_course_run_enrollments.

        Mock will return `any_successes`, `any_failures`, and `enrollments`
        echoed back as a dictionary.
        """

        def inner(_method, program_uuid, course_key, enrollments):
            """ Mock for data.write_course_run_enrollments. """
            self.assertIsInstance(program_uuid, UUID)
            if expected_enrolls_by_course_key is not None:  # pragma: no branch
                self.assertListEqual(enrollments, expected_enrolls_by_course_key.get(course_key))

            results = OrderedDict([
                (enrollment['student_key'], enrollment['status'])
                for enrollment in enrollments
            ])
            return any_successes, any_failures, results
        return inner

    @ddt.data(
        (True, False, "200"),
        (True, True, "207"),
        (False, True, "422"),
    )
    @ddt.unpack
    @patch.object(
        ProgramDetails, 'get_course_key', lambda _self, x: x + '-internal'
    )
    def test_success_status(self, any_successes, any_failures, expected_code_str):
        enrolls = [
            {'student_key': 'john', 'status': 'x', 'course_id': 'course-1'},
            {'student_key': 'bob', 'status': 'y', 'course_id': 'course-1'},
            {'student_key': 'serena', 'status': 'z', 'course_id': 'course-2'},
        ]
        expected_enrolls_by_course_key = {
            "course-1-internal": [
                {'status': 'x', 'student_key': 'john'},
                {'status': 'y', 'student_key': 'bob'},
            ],
            "course-2-internal": [
                {'status': 'z', 'student_key': 'serena'},
            ]
        }
        uploads_filestore.store(self.json_filepath, json.dumps(enrolls))
        with patch.object(
                tasks.lms,
                self.mock_function,
                new=self.mock_write_enrollments(any_successes, any_failures, expected_enrolls_by_course_key),
        ):
            self.spawn_task().wait()

        self.assert_succeeded(
            "course_id,student_key,status\r\n"
            "course-1,john,x\r\n"
            "course-1,bob,y\r\n"
            "course-2,serena,z\r\n",
            expected_code_str,
        )

    @patch.object(
        ProgramDetails, 'get_course_key', lambda _self, x: x + '-internal'
    )
    def test_success_status_course_staff_included(self):
        enrolls = [
            {'student_key': 'john', 'status': 'x', 'course_id': 'course-1', 'course_staff': 'TRUE'},
            {'student_key': 'bob', 'status': 'y', 'course_id': 'course-1', 'course_staff': 'FALSE'},
            {'student_key': 'serena', 'status': 'z', 'course_id': 'course-2'},
        ]

        expected_enrolls_by_course_key = {
            "course-1-internal": [
                {'status': 'x', 'course_staff': 'TRUE', 'student_key': 'john'},
                {'status': 'y', 'course_staff': 'FALSE', 'student_key': 'bob'},
            ],
            "course-2-internal": [
                {'status': 'z', 'student_key': 'serena'},
            ]
        }

        uploads_filestore.store(self.json_filepath, json.dumps(enrolls))
        with patch.object(
                tasks.lms,
                self.mock_function,
                new=self.mock_write_enrollments(True, False, expected_enrolls_by_course_key),
        ):
            self.spawn_task().wait()

        expected_code_str = '200'
        self.assert_succeeded(
            "course_id,student_key,status,course_staff\r\n"
            "course-1,john,x,TRUE\r\n"
            "course-1,bob,y,FALSE\r\n"
            "course-2,serena,z,\r\n",
            expected_code_str,
        )

    @patch.object(
        ProgramDetails, 'get_course_key', {'course-1': 'course-1-internal'}.get
    )
    @patch_discovery_program_details({})
    def test_success_invalid_course_id(self):
        enrolls = [
            {'student_key': 'john', 'status': 'x', 'course_id': 'course-1'},
            {'student_key': 'bob', 'status': 'y', 'course_id': 'course-1'},
            {'student_key': 'serena', 'status': 'z', 'course_id': 'invalid-course'},
        ]
        expected_enrolls_by_course_key = {
            "course-1-internal": [
                {'status': 'x', 'student_key': 'john'},
                {'status': 'y', 'student_key': 'bob'},
            ],
            "invalid-course-internal": [
                {'status': 'z', 'student_key': 'serena'},
            ]
        }
        uploads_filestore.store(self.json_filepath, json.dumps(enrolls))
        with patch.object(
                tasks.lms,
                self.mock_function,
                new=self.mock_write_enrollments(True, False, expected_enrolls_by_course_key),
        ):
            self.spawn_task().wait()
        self.assert_succeeded(
            "course_id,student_key,status\r\n"
            "course-1,john,x\r\n"
            "course-1,bob,y\r\n"
            "invalid-course,serena,course-not-found\r\n",
            "207",
        )
