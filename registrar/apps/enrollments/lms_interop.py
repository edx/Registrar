"""
Module for reading and writing data from LMS.
"""
import json
import logging
from itertools import groupby
from posixpath import join as urljoin

from django.conf import settings
from rest_framework.status import HTTP_200_OK, HTTP_201_CREATED, HTTP_207_MULTI_STATUS, HTTP_422_UNPROCESSABLE_ENTITY

from registrar.apps.core.models import Program
from registrar.apps.core.rest_utils import do_batched_lms_write, get_all_paginated_results

from .constants import ENROLLMENT_ERROR_DUPLICATED, ENROLLMENT_ERROR_INTERNAL, LMS_ENROLLMENT_WRITE_MAX_SIZE
from .serializers import (
    CourseEnrollmentSerializer,
    CourseEnrollmentWithCourseStaffSerializer,
    ProgramEnrollmentSerializer,
    ProgramEnrollmentWithUsernameEmailSerializer,
)


logger = logging.getLogger(__name__)

LMS_PROGRAM_ENROLLMENTS_API_TPL = 'api/program_enrollments/v1/programs/{}/enrollments/'
LMS_PROGRAM_COURSE_ENROLLMENTS_API_TPL = 'api/program_enrollments/v1/programs/{}/courses/{}/enrollments/'


def get_program_enrollments(program_uuid, client=None, include_username_email=False):
    """
    Fetches program enrollments from the LMS.

    Arguments:
        program_uuid (str): UUID-4 string

    Returns: list[dict]
        A list of enrollment dictionaries, validated by
        ProgramEnrollmentSerializer.

    Raises:
        - HTTPError if there is an issue communicating with LMS
        - ValidationError if enrollment data from LMS is invalid
    """
    url = _lms_program_enrollment_url(program_uuid)
    enrollments = get_all_paginated_results(url, client)
    if include_username_email:
        serializer = ProgramEnrollmentWithUsernameEmailSerializer(data=enrollments, many=True)
    else:
        serializer = ProgramEnrollmentSerializer(data=enrollments, many=True)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


def get_course_run_enrollments(
        program_uuid, internal_course_key, external_course_key=None, course_role_management_enabled=False, client=None):
    """
    Fetches program course run enrollments from the LMS.

    Arguments:
        program_uuid (str): UUID-4 string
        internal_course_key (str): edX course key identifying course run
        external_course_key (str): optional external course key

    Returns: list[dict]
        A list of enrollment dictionaries, validated by
        CourseEnrollmentSerializer.

    Raises:
        - HTTPError if there is an issue communicating with LMS
        - ValidationError if enrollment data from LMS is invalid
    """
    url = _lms_course_run_enrollment_url(program_uuid, internal_course_key)
    enrollments = get_all_paginated_results(url, client)
    context = {'course_id': external_course_key or internal_course_key}
    serializer = (CourseEnrollmentWithCourseStaffSerializer(data=enrollments, many=True, context=context)
                  if course_role_management_enabled
                  else CourseEnrollmentSerializer(data=enrollments, many=True, context=context))
    serializer.is_valid(raise_exception=True)
    return serializer.data


def write_program_enrollments(method, program_uuid, enrollments, client=None):
    """
    Create or update program enrollments in the LMS.

    Returns: See _write_enrollments.
    """
    url = _lms_program_enrollment_url(program_uuid)
    program = Program.objects.get(discovery_uuid=program_uuid)
    curriculum_uuid_string = str(program.details.active_curriculum_uuid)
    enrollments = enrollments.copy()
    for enrollment in enrollments:
        enrollment['curriculum_uuid'] = curriculum_uuid_string
    return _write_enrollments(method, url, enrollments, client)


def write_course_run_enrollments(method, program_uuid, course_id, enrollments, client=None):
    """
    Create or update program course enrollments in the LMS.

    Returns: See _write_enrollments.
    """
    url = _lms_course_run_enrollment_url(program_uuid, course_id)
    return _write_enrollments(method, url, enrollments, client)


def _write_enrollments(method, url, enrollments, client=None):
    """
    Arguments:
        method: "POST", "PATCH", or "PUT"
        url: str
        enrollments: list[dict], with at least "student_key" in each dict.
        client (optional)

    Returns:
        tuple(
            good: bool,
            bad: bool,
            results: dict[str: str]
        )
        where:
          * `good` indicates whether at least one enrollment was successful
          * `bad` indicates whether at least one enrollment failed
          * `results` is a dict that maps each student key to a status
            indicating the result of the enrollment operation.
    """
    def key_fn(e):
        return e['student_key']
    # groupby requires sorted input.
    sorted_enrollments = sorted(enrollments, key=key_fn)
    enrollments_by_student = {
        student_key: list(student_enrollments)
        for student_key, student_enrollments
        in groupby(sorted_enrollments, key=key_fn)
    }
    duplicated_student_keys = {
        student_key
        for student_key, student_enrollments in enrollments_by_student.items()
        if len(student_enrollments) > 1
    }
    unique_enrollments = [
        enrollment
        for enrollment in enrollments
        if enrollment['student_key'] not in duplicated_student_keys
    ]
    responses = do_batched_lms_write(
        method, url, unique_enrollments, LMS_ENROLLMENT_WRITE_MAX_SIZE, client
    )

    good = False
    bad = bool(duplicated_student_keys)
    # By default, mark all enrollments as "internal-error".
    # Under normal circumstances, every processed enrollment will be marked
    # with a status, so if there are any left over, there was indeed an
    # internal error.
    results = {
        student_key: ENROLLMENT_ERROR_INTERNAL
        for student_key in enrollments_by_student
    }
    results.update({
        student_key: ENROLLMENT_ERROR_DUPLICATED
        for student_key in duplicated_student_keys
    })
    expected_codes = {
        HTTP_200_OK,
        HTTP_201_CREATED,
        HTTP_207_MULTI_STATUS,
        HTTP_422_UNPROCESSABLE_ENTITY,
    }
    for response in responses:
        if response.status_code in (HTTP_200_OK, HTTP_201_CREATED):
            good = True
        elif response.status_code == HTTP_207_MULTI_STATUS:
            good = True
            bad = True
        else:
            bad = True
        if response.status_code in expected_codes:
            try:
                response_data = response.json()
            except json.JSONDecodeError:
                response_data = None
            if isinstance(response_data, dict):
                results.update(response_data)
        logger.info(
            "LMS responded to {} {} with status {} and body {}".format(
                method, url, response.status_code, response.text
            )
        )
    return good, bad, results


def _lms_program_enrollment_url(program_uuid):
    """
    Given a program UUID, get the LMS URL for program enrollments.
    """
    endpoint_path = LMS_PROGRAM_ENROLLMENTS_API_TPL.format(program_uuid)
    return urljoin(settings.LMS_BASE_URL, endpoint_path)


def _lms_course_run_enrollment_url(program_uuid, course_id):
    """
    Given a program UUID and an edX course ID,
    get the LMS URL for course run enrollments.
    """
    endpoint_path = LMS_PROGRAM_COURSE_ENROLLMENTS_API_TPL.format(
        program_uuid, course_id
    )
    return urljoin(settings.LMS_BASE_URL, endpoint_path)
