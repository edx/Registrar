"""
Module for reading and writing data from LMS.
"""
import json
from posixpath import join as urljoin

from django.conf import settings
from rest_framework.exceptions import ValidationError
from rest_framework.status import HTTP_200_OK, HTTP_204_NO_CONTENT, HTTP_207_MULTI_STATUS, HTTP_422_UNPROCESSABLE_ENTITY

from registrar.apps.core.rest_utils import get_all_paginated_responses

from .serializers import CourseGradeSerializer


LMS_PROGRAM_COURSE_GRADES_API_TPL = 'api/program_enrollments/v1/programs/{}/courses/{}/grades/'


def get_course_run_grades(program_uuid, internal_course_key, client=None):
    """
    Fetches course run grades from the LMS.

    Arguments:
        program_uuid (str): UUID-4 string
        internal_course_key (str): edX course key identifying course run

    Returns: bool, bool, list[dict]
        Whether or not there were any successful grade responses
        Whether or not there were any unsuccessful grade responses
        A list of student grades, validated by CourseGradeSerializer.

    Raises:
        - HTTPError if there is an issue communicating with LMS
        - ValidationError if grades data from LMS is invalid
    """
    url = _lms_course_run_grades_url(program_uuid, internal_course_key)
    try:
        responses = get_all_paginated_responses(url, client, expected_error_codes={HTTP_422_UNPROCESSABLE_ENTITY})
    except json.JSONDecodeError as e:
        raise ValidationError(e)

    any_successes = False
    any_failures = False
    results = []
    for response in responses:
        if response.status_code == HTTP_204_NO_CONTENT:
            return False, False, {}
        elif response.status_code == HTTP_200_OK:
            any_successes = True
        elif response.status_code == HTTP_207_MULTI_STATUS:
            any_successes = True
            any_failures = True
        else:
            any_failures = True
        response_data = response.json().get('results')
        results.extend(response_data)
    serializer = CourseGradeSerializer(data=results, many=True)
    serializer.is_valid(raise_exception=True)
    return any_successes, any_failures, serializer.data


def _lms_course_run_grades_url(program_uuid, course_id):
    """
    Given a program UUID and an edX course ID,
    get the LMS URL for course run grades.
    """
    endpoint_path = LMS_PROGRAM_COURSE_GRADES_API_TPL.format(
        program_uuid, course_id
    )
    return urljoin(settings.LMS_BASE_URL, endpoint_path)
