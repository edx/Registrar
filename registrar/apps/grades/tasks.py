""" Tasks for the grades app """
import json

from celery import shared_task
from requests.exceptions import HTTPError
from rest_framework.exceptions import ValidationError
from user_tasks.tasks import UserTask

from registrar.apps.core.jobs import post_job_failure, post_job_success
from registrar.apps.core.tasks import get_program

from . import lms_interop as lms
from .constants import GradeReadStatus
from .serializers import serialize_course_run_grades_to_csv


@shared_task(base=UserTask, bind=True)
# pylint: disable=unused-argument
def get_course_run_grades(self, job_id, user_id, file_format, program_key, internal_course_key):
    """
    A user task that reads course run grade data from the LMS, and writes it to
    a JSON- or CSV-formatted result file.
    """
    program = get_program(job_id, program_key)
    if not program:
        return
    try:
        any_successes, any_failures, grades = lms.get_course_run_grades(
            program.discovery_uuid,
            internal_course_key,
        )
    except HTTPError as err:
        post_job_failure(
            job_id,
            "HTTP error {} when getting grades at {}".format(
                err.response.status_code, err.request.url
            ),
        )
        return
    except ValidationError as err:
        post_job_failure(
            job_id,
            f"Invalid grade data from LMS: {err}",
        )
        return

    if any_successes and any_failures:
        code_str = str(GradeReadStatus.MULTI_STATUS.value)
    elif not any_successes and not any_failures:
        code_str = str(GradeReadStatus.NO_CONTENT.value)
    elif any_successes:
        code_str = str(GradeReadStatus.OK.value)
    else:
        code_str = str(GradeReadStatus.UNPROCESSABLE_ENTITY.value)

    if file_format == 'json':
        serialized = json.dumps(grades, indent=4)
    elif file_format == 'csv':
        serialized = serialize_course_run_grades_to_csv(grades)
    else:
        raise ValueError(f'Invalid file_format: {file_format}')
    post_job_success(job_id, serialized, file_format, text=code_str)
