"""
Python API for Registrar 'jobs', which are a thin abstraction around UserTasks.

A 'job' is an asynchronous operation with a creation date-time, state, and
result URL. Every job is identified by a UUID-4 (called a job_id).

In its current implementation, every job is run by a single UserTask,
which is type of user-specific Celery task that saves its status
(UserTaskStatus) and produces persisted output (UserTaskArtifacts).
The job_id is equal to the UserTask ID. If this module were to be extended
in the future to include retrying tasks, the job_id could be decoupled
from the UserTask ID. For this reason, we attempt not to expose the relationship
between job_ids and UserTask IDs outside of this module.
"""
import logging
import uuid
from collections import namedtuple

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from user_tasks.models import UserTaskArtifact, UserTaskStatus

from .filestore import get_job_results_filestore
from .permissions import JOB_GLOBAL_READ


JobStatus = namedtuple(
    'JobStatus',
    ['job_id', 'name', 'created', 'state', 'result', 'text'],
)

logger = logging.getLogger(__name__)
result_filestore = get_job_results_filestore()

_RESULT_ARTIFACT_NAME = 'Job Result'

USER_TASK_STATUS_PROCESSING_STATES = [
    UserTaskStatus.PENDING,
    UserTaskStatus.IN_PROGRESS,
    UserTaskStatus.RETRYING,
]


def start_job(user, task_fn, *args, **kwargs):
    """
    Start a new job

    Arguments.
        user (User): User who owns the job.
        task_fn: UserTask function that will be invoked to perform job.
            The first three parameters of the function should be
            `self` (the Celery task instance), `job_id`, and `user_id`.
            They will be passed in automatically. The function may take
            additional parameters.
        *args/**kwargs: Additional arguments to pass to task_fn.
    """
    job_id = kwargs.pop('job_id') if 'job_id' in kwargs else str(uuid.uuid4())
    task_fn.apply_async(
        [job_id, user.id] + list(args),
        kwargs,
        task_id=job_id,
        queue=settings.CELERY_DEFAULT_QUEUE,
    )
    return job_id


def get_job_status(user, job_id):
    """
    Get the status of a job.

    Arguments:
        user (User): User attempting to read job status.
        job_id (str): UUID-4 job ID string.

    Raises:
        PermissionDenied: User may not read job status.
            Only (1) the creator of the job, and (2) users
            granted who have been `JOB_GLOBAL_READ` may read a job's status.

    Returns: JobStatus
    """
    try:
        task_status = UserTaskStatus.objects.get(task_id=job_id)
    except UserTaskStatus.DoesNotExist:
        raise ObjectDoesNotExist(f"No such job: {job_id}")
    if user.has_perm(JOB_GLOBAL_READ) or user == task_status.user:
        return _make_job_status(task_status)
    else:
        raise PermissionDenied()


def get_processing_jobs_for_user(user):
    """
    Get the statuses of any processing jobs for the given user.

    Arguments:
        user (User)

    Returns: seq[JobStatus]
    """
    task_statuses = UserTaskStatus.objects.filter(
        user=user,
        state__in=USER_TASK_STATUS_PROCESSING_STATES
    )
    return (_make_job_status(task_status) for task_status in task_statuses)


def processing_job_with_prefix_exists(prefix):
    """
    Returns whether there exists a job whose name begins with `prefix`
    that is currently processing (in progress, pending, or retrying).
    """
    return UserTaskStatus.objects.filter(
        name__startswith=prefix,
        state__in=USER_TASK_STATUS_PROCESSING_STATES,
    ).exists()


def _make_job_status(task_status):
    """
    Creates a JobStatus instance from a UserTaskStatus instance.
    """
    return JobStatus(
        task_status.task_id,
        task_status.name,
        task_status.created,
        task_status.state,
        *_get_result(task_status),
    )


def _get_result(task_status):
    """
    Get the result URL and text of a job from its UserTaskStatus.

    Will be stored in the `result` field of the first
    UserTaskArtifact with the name `_RESULT_ARTIFACT_NAME`
    of `task_status`.

    Arguments:
        task_status (UserTaskStatus)

    Returns: (str|NoneType, str|NoneType)
        Returns URL and text as a tuple.
        For successful job, either URL or text may be None.
        If no results posted, both will be None.
    """
    artifacts = UserTaskArtifact.objects.filter(
        status=task_status, name=_RESULT_ARTIFACT_NAME
    )
    if artifacts:
        if artifacts.count() > 1:  # pragma: no cover
            logger.error(
                'Multiple UserTaskArtifacts for job (job_id = %s, UserTaskStatus.uuid = %s).'
                ' First artifact will be returned',
                task_status.task_id,
                task_status.uuid
            )
        artifact = artifacts.first()
        return artifact.url or None, artifact.text or None
    else:
        return None, None


def post_job_success(job_id, results, file_extension, text=None):
    """
    Mark a job as Succeeded, providing a result.

    Arguments:
        job_id (str): UUID-4 string identifying Job
        results (str): String containing results of job; to be saved to file.
        file_extension (str): Desired file extension for result file(e.g. 'json').
        text (str): [optional] string to write to `text` field of result.
    """
    result_path = f"{job_id}.{file_extension}"
    result_url = result_filestore.store(result_path, results)
    task_status = UserTaskStatus.objects.get(task_id=job_id)
    _affirm_job_in_progress(job_id, task_status)
    log_message = f"Job {job_id} succeeded with result URL {result_url}"
    logger.info(log_message)
    UserTaskArtifact.objects.create(
        status=task_status,
        name=_RESULT_ARTIFACT_NAME,
        url=result_url,
        text=(text or ""),
    )
    task_status.succeed()


def post_job_failure(job_id, message):
    """
    Mark a job as Failed, providing `message` as a reason.

    Logs the message and saves an artifact with the message.
    Intended to be called from the job itself.

    Arguments:
        job_id (str): UUID-4 string identifying Job
        message: the reason the job failed
    """
    task_status = UserTaskStatus.objects.get(task_id=job_id)
    _affirm_job_in_progress(job_id, task_status)
    log_message = f"Job {job_id} failed. {message}"
    logger.error(log_message)
    task_status.fail(log_message)  # Creates UserTaskArtifact with name "Error"


def _affirm_job_in_progress(job_id, task_status):
    """
    Raise a ValueError if job is not currently In Progress.

    Arguments:
        job_id (str): UUID-4 string identifying Job
        task_status (UserTaskStatus): user task status associated with job
    """
    if task_status.state != UserTaskStatus.IN_PROGRESS:  # pragma: no cover
        raise ValueError(
            'Job can only be marked as Succeeded from state In Progress' +
            f'(job_id = {job_id})'
        )
