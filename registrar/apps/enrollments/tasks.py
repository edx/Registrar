"""
This module contains the celery task definitions for the enrollment project.
"""
import json
from collections import OrderedDict, namedtuple

from celery import shared_task
from celery.utils.log import get_task_logger
from requests.exceptions import HTTPError
from rest_framework.exceptions import ValidationError
from user_tasks.tasks import UserTask

from registrar.apps.core.csv_utils import serialize_to_csv
from registrar.apps.core.filestore import get_enrollment_uploads_filestore
from registrar.apps.core.jobs import post_job_failure, post_job_success
from registrar.apps.core.tasks import get_program

from . import lms_interop as lms
from .constants import ENROLLMENT_ERROR_COURSE_NOT_FOUND, EnrollmentWriteStatus
from .serializers import (
    serialize_course_run_enrollments_to_csv,
    serialize_course_run_enrollments_with_course_staff_to_csv,
    serialize_enrollment_results_to_csv,
    serialize_program_enrollments_to_csv,
)
from .utils import build_enrollment_job_status_name


log = get_task_logger(__name__)
uploads_filestore = get_enrollment_uploads_filestore()


class EnrollmentReadTask(UserTask):
    """
    Base class for enrollment-reading tasks.
    Expects instances to have `program_key` field.
    """

    # pylint: disable=abstract-method

    @classmethod
    def generate_name(cls, arguments_dict):
        """
        Predictably sets name in such a way that other parts of the codebase
        can query for the state of enrollment writing tasks.
        """
        return build_enrollment_job_status_name(
            arguments_dict['program_key'],
            'read',
            cls.__name__,  # Name of specific task.
        )


# pylint: disable=unused-argument
@shared_task(base=EnrollmentReadTask, bind=True)
def list_program_enrollments(self, job_id, user_id, file_format, program_key, include_username_email=False):
    """
    A user task for retrieving program enrollments from LMS.
    """
    program = get_program(job_id, program_key)
    if not program:
        return

    try:
        enrollments = lms.get_program_enrollments(program.discovery_uuid, None, include_username_email)
    except HTTPError as err:
        post_job_failure(
            job_id,
            "HTTP error {} when getting enrollments at {}".format(
                err.response.status_code, err.request.url
            ),
        )
        return
    except ValidationError as err:
        post_job_failure(
            job_id,
            f"Invalid enrollment data from LMS: {err}",
        )
        return

    if file_format == 'json':
        serialized = json.dumps(enrollments, indent=4, sort_keys=True)
    elif file_format == 'csv':
        serialized = serialize_program_enrollments_to_csv(enrollments, include_username_email)
    else:
        raise ValueError(f'Invalid file_format: {file_format}')
    post_job_success(job_id, serialized, file_format)


@shared_task(base=EnrollmentReadTask, bind=True)
def list_course_run_enrollments(
        self,
        job_id,
        user_id,
        file_format,
        program_key,
        internal_course_key,
        external_course_key,
        course_role_management_enabled=False,
):
    """
    A user task for retrieving program course run enrollments from LMS.
    """
    program = get_program(job_id, program_key)
    if not program:
        return

    try:
        enrollments = lms.get_course_run_enrollments(
            program.discovery_uuid,
            internal_course_key,
            external_course_key,
            course_role_management_enabled,
        )
    except HTTPError as err:
        post_job_failure(
            job_id,
            "HTTP error {} when getting enrollments at {}".format(
                err.response.status_code, err.request.url
            ),
        )
        return
    except ValidationError as err:
        post_job_failure(
            job_id,
            f"Invalid enrollment data from LMS: {err}",
        )
        return

    if file_format == 'json':
        serialized = json.dumps(enrollments, indent=4)
    elif file_format == 'csv':
        serialized = (serialize_course_run_enrollments_with_course_staff_to_csv(enrollments)
                      if course_role_management_enabled
                      else serialize_course_run_enrollments_to_csv(enrollments))
    else:
        raise ValueError(f'Invalid file_format: {file_format}')
    post_job_success(job_id, serialized, file_format)


@shared_task(base=EnrollmentReadTask, bind=True)
def list_all_course_run_enrollments(self, job_id, user_id, file_format, program_key):
    """
    A user task for retrieving all course enrollments within a given program from LMS.
    """
    program = get_program(job_id, program_key)
    if not program:
        return

    results = []
    for course_run in program.details.course_runs:
        try:
            enrollments = lms.get_course_run_enrollments(
                program.discovery_uuid,
                course_run.key,
                course_run.external_key,
            )
        except HTTPError as err:
            post_job_failure(
                job_id,
                "HTTP error {} when getting enrollments at {}".format(
                    err.response.status_code, err.request.url
                ),
            )
            return
        except ValidationError as err:
            post_job_failure(
                job_id,
                f"Invalid enrollment data from LMS: {err}",
            )
            return

        results.extend(enrollments)

    if file_format == 'json':
        serialized = json.dumps(results, indent=4)
    elif file_format == 'csv':
        serialized = serialize_course_run_enrollments_to_csv(results)
    else:
        raise ValueError(f'Invalid file_format: {file_format}')
    post_job_success(job_id, serialized, file_format)


class EnrollmentWriteTask(UserTask):
    """
    Base class for enrollment-writing tasks.
    Expects instances to have `program_key` field.
    """

    # pylint: disable=abstract-method

    @classmethod
    def generate_name(cls, arguments_dict):
        """
        Predictably sets name in such a way that other parts of the codebase
        can query for the state of enrollment writing tasks.
        """
        return build_enrollment_job_status_name(
            arguments_dict['program_key'],
            'write',
            cls.__name__,  # Name of specific task.
        )


@shared_task(base=EnrollmentWriteTask, bind=True)
def write_program_enrollments(
        self, job_id, user_id, json_filepath, program_key
):
    """
    A user task that reads program enrollment requests from json_filepath,
    writes them to the LMS, and stores a CSV-formatted results file.
    """
    program = get_program(job_id, program_key)
    if not program:
        return

    requests = _load_enrollment_requests(job_id, program_key, json_filepath)
    if requests is None:
        return

    any_successes, any_failures, response_json = lms.write_program_enrollments(
        'PUT', program.discovery_uuid, requests
    )

    if any_successes and any_failures:
        code_str = str(EnrollmentWriteStatus.MULTI_STATUS.value)
    elif any_successes:
        code_str = str(EnrollmentWriteStatus.OK.value)
    elif any_failures:
        code_str = str(EnrollmentWriteStatus.UNPROCESSABLE_ENTITY.value)
    else:
        # This only happens if no enrollments are given.
        code_str = str(EnrollmentWriteStatus.NO_CONTENT.value)

    results_str = serialize_enrollment_results_to_csv(response_json)
    post_job_success(job_id, results_str, "csv", text=code_str)


CourseEnrollmentResponseItem = namedtuple(
    'CourseEnrollmentResponseItem',
    ['course_id', 'student_key', 'status', 'course_staff'],
)


@shared_task(base=EnrollmentWriteTask, bind=True)
def write_course_run_enrollments(
        self, job_id, user_id, json_filepath, program_key
):
    """
    A user task that reads course run enrollment requests from json_filepath,
    writes them to the LMS, and stores a CSV-formatted results file.
    """
    program = get_program(job_id, program_key)
    if not program:
        return

    requests = _load_enrollment_requests(job_id, program_key, json_filepath)
    if requests is None:
        return

    requests_by_course_key = OrderedDict()
    student_to_course_staff = OrderedDict()
    include_course_staff = False
    for request in requests:
        if 'course_staff' in request:
            include_course_staff = True
        requested_course_key = request.pop('course_id')
        if requested_course_key not in requests_by_course_key:
            requests_by_course_key[requested_course_key] = []
        requests_by_course_key[requested_course_key].append(request)

        requested_student_key = request.get('student_key')
        requested_course_staff = request.get('course_staff')
        student_to_course_staff[requested_student_key] = requested_course_staff

    successes = []
    failures = []
    course_responses = []

    for requested_course_key, course_requests in requests_by_course_key.items():
        # we don't know if the requested key was an external key or internal key,
        # so convert it before making requests to the LMS.
        internal_course_key = program.details.get_course_key(requested_course_key)

        if internal_course_key:
            successes_in_course, failures_in_course, status_by_student_key = lms.write_course_run_enrollments(
                'PUT', program.discovery_uuid, internal_course_key, course_requests
            )
            course_responses.extend([
                CourseEnrollmentResponseItem(
                    requested_course_key, student_key, status, student_to_course_staff[student_key])._asdict()
                for student_key, status in status_by_student_key.items()
            ])
            successes.append(successes_in_course)
            failures.append(failures_in_course)
        else:
            course_responses.extend([
                CourseEnrollmentResponseItem(
                    requested_course_key,
                    request.get('student_key'),
                    ENROLLMENT_ERROR_COURSE_NOT_FOUND,
                    student_to_course_staff[request.get('student_key')]
                )._asdict()
                for request in course_requests
            ])
            failures.append(True)

    if any(successes) and any(failures):
        code_str = str(EnrollmentWriteStatus.MULTI_STATUS.value)
    elif any(successes):
        code_str = str(EnrollmentWriteStatus.OK.value)
    elif any(failures):
        code_str = str(EnrollmentWriteStatus.UNPROCESSABLE_ENTITY.value)
    else:
        # This only happens if no enrollments are given.
        code_str = str(EnrollmentWriteStatus.NO_CONTENT.value)

    course_enrollment_response_format = list(CourseEnrollmentResponseItem._fields)
    if not include_course_staff:
        course_enrollment_response_format.remove('course_staff')

    results_str = serialize_to_csv(course_responses, course_enrollment_response_format, include_headers=True)
    post_job_success(job_id, results_str, "csv", text=code_str)


def _load_enrollment_requests(job_id, program_key, json_filepath):
    """
    Load enrollment reqeusts from JSON file.

    If file doesn't exist or isn't valid JSON, post job failure and retrurn None.
    Else, return data loaded from JSON.
    """
    json_text = uploads_filestore.retrieve(json_filepath)
    if not json_text:
        post_job_failure(
            job_id,
            "Enrollment file for program_key={} not found at {}".format(
                program_key, json_filepath
            )
        )
        return None
    try:
        return json.loads(json_text)
    except json.decoder.JSONDecodeError:
        post_job_failure(
            job_id,
            "Enrollment file for program_key={} at {} is not valid JSON".format(
                program_key, json_filepath
            )
        )
        return None
