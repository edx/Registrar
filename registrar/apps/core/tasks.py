"""
This module contains common celery task definitions
as well as shared utility functions to be used in tasks.
"""
from celery import shared_task
from celery.utils.log import get_task_logger
from user_tasks.models import UserTaskArtifact
from user_tasks.tasks import UserTask

from .jobs import post_job_failure
from .models import Program


log = get_task_logger(__name__)


# pylint: disable=unused-argument
@shared_task(bind=True)
def debug_task(self, *args, **kwargs):
    """
    A task for debugging.  Will dump the context of the task request
    to the log as a DEBUG message.
    """
    log.debug(f'Request: {self.request!r}')


# pylint: disable=unused-argument
@shared_task(base=UserTask, bind=True)
def debug_user_task(self, user_id, text):
    """
    A user task for debugging.  Creates user artifact containing given text.
    """
    UserTaskArtifact.objects.create(status=self.status, text=text)


def get_program(job_id, program_key):
    """
    Load a Program by key. Fails job and returns None if key invalid.
    """
    try:
        return Program.objects.get(key=program_key)
    except Program.DoesNotExist:
        post_job_failure(job_id, f"Bad program key: {program_key}")
        return None
