"""
Mixins for the public V1 REST API.
"""
import uuid
from collections.abc import Iterable

import waffle
from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django.http import Http404
from django.utils.functional import cached_property
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from rest_framework.authentication import SessionAuthentication
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.status import HTTP_200_OK, HTTP_202_ACCEPTED, HTTP_207_MULTI_STATUS, HTTP_422_UNPROCESSABLE_ENTITY

from registrar.apps.core import permissions as perms
from registrar.apps.core.auth_checks import get_api_permissions_by_program
from registrar.apps.core.filestore import get_enrollment_uploads_filestore
from registrar.apps.core.jobs import start_job
from registrar.apps.core.models import Program
from registrar.apps.enrollments.lms_interop import write_course_run_enrollments, write_program_enrollments

from .. import exceptions
from ..constants import ENROLLMENT_WRITE_MAX_SIZE
from ..mixins import TrackViewMixin
from ..serializers import JobAcceptanceSerializer
from ..utils import build_absolute_api_url


upload_filestore = get_enrollment_uploads_filestore()


class ProgramSpecificViewMixin(TrackViewMixin):
    """
    A mixin for views that operate on or within a specific program.

    Provides program-specific AuthN/AuthZ functionality.
    """
    authentication_classes = (JwtAuthentication, SessionAuthentication)
    permission_classes = (IsAuthenticated,)
    permission_required = []

    def get_required_permissions(self, _request):
        """
        Get the APIPermissions required on the program to use this view.

        Simply looks at `permission_required` and fails if it isn't an APIPermission
        or an Iterable.

        This method may be overriden in order to cofiy dynamic permission requirements.

        Returns:
            set[APIPermission]
        """
        if isinstance(self.permission_required, perms.APIPermission):  # pragma: no branch
            return [self.permission_required]  # pragma: no cover
        elif isinstance(self.permission_required, Iterable):
            return self.permission_required
        else:  # pragma: no cover
            raise ImproperlyConfigured(
                'permission_required must be an APIPermission or iterable; ' +
                f'was {self.permission_required}'
            )

    def check_permissions(self, request):
        """
        Check that the authenticated user can access this view.

        Ensure that the user has all of the permissions specified in
        `self.get_required_permissions()` granted on the `self.program`.

        If not, an HTTP 403 is raised.

        Overrides APIView.check_permissions.
        """
        super().check_permissions(request)
        required_permissions = set(self.get_required_permissions(request))
        possessed_permissions = set(
            get_api_permissions_by_program(request.user, self.program)
        )
        missing_permissions = required_permissions - possessed_permissions
        if missing_permissions:
            self.add_tracking_data(missing_permissions=list(missing_permissions))
            raise PermissionDenied()

    @cached_property
    def program(self):
        """
        The program specified by the `program_key` URL parameter.
        """
        program_key = self.kwargs['program_key']
        try:
            return Program.objects.get(key=program_key)
        except Program.DoesNotExist:
            self.add_tracking_data(failure='program_not_found')
            raise Http404()


class CourseSpecificViewMixin(ProgramSpecificViewMixin):
    """
    A mixin for views that operate on or within a specific program course run.

    In addition to the functionality provided in `ProgramSpecificViewMixin`,
    this mixin provides the `internal_course_key` and `external_course_key` properties,
    which return the edX course key of the course referred to in the URL (either by the
    edX course key itself or by an external course key). It also validates
    that the course key exists within the program, raising 404 if not.
    """

    @cached_property
    def course_run(self):
        """
        Raises a 404 if the course run identified by the `course_id` path
        parameter is not part of self.program.
        """
        provided_course_id = self.kwargs['course_id']
        real_course_run = self.program.details.find_course_run(
            provided_course_id
        )
        if not real_course_run:
            self.add_tracking_data(failure='course_not_found')
            raise Http404()
        return real_course_run

    @property
    def internal_course_key(self):
        return self.course_run.key

    @property
    def external_course_key(self):
        return self.course_run.external_key


class JobInvokerMixin:
    """
    A mixin for views that invoke jobs and return ID and status URL.
    """
    def invoke_upload_job(self, task_fn, upload_content, *args, **kwargs):
        """
        Invoke a data upload job with task_fn and the path to an input file.
        Returns a 202 with job_id and job_url.

        *args and **kwargs are passed to task_fn *in addition* to
        job_id, user_id, and file_format.
        """
        job_id = str(uuid.uuid4())
        file_path = '{}.{}'.format(job_id, 'json')
        upload_filestore.store(file_path, upload_content)
        return self._invoke_job(task_fn, file_path, job_id=job_id, *args, **kwargs)

    def invoke_download_job(self, task_fn, *args, **kwargs):
        """
        Invoke a data download job with task_fn,
        and a return a 202 with job_id and job_url.

        *args and **kwargs are passed to task_fn *in addition* to
        job_id, user_id, and file_format.
        """
        file_format = self.request.query_params.get('fmt', 'json')
        if file_format not in {'json', 'csv'}:
            self.add_tracking_data(failure='result_format_not_supported')
            raise Http404()

        return self._invoke_job(task_fn, file_format, *args, **kwargs)

    def _invoke_job(self, task_fn, *args, **kwargs):
        """
        Invoke a job with task_fn
        """
        job_id = start_job(self.request.user, task_fn, *args, **kwargs)
        api_version = self.request.get_full_path().split('/')[2]
        job_url = build_absolute_api_url(f'api:{api_version}:job-status', job_id=job_id)
        data = {'job_id': job_id, 'job_url': job_url}
        return Response(JobAcceptanceSerializer(data).data, HTTP_202_ACCEPTED)


class EnrollmentMixin(ProgramSpecificViewMixin):
    """
    This mixin defines the required permissions
    for any views that read or write program/course enrollment data.
    Overrides AuthMixin.check_permissions.
    """
    def get_required_permissions(self, request):
        if request.method == 'GET':
            return [perms.API_READ_ENROLLMENTS]
        if request.method == 'POST' or self.request.method == 'PATCH':
            return [perms.API_WRITE_ENROLLMENTS]
        return []  # pragma: no cover

    def handle_enrollments(self, course_id=None):
        """
        Handle Create/Update requests for program/course-run enrollments.

        Expects `course_id` to be internal course key.

        Does program enrollments if `course_id` is None.
        Does course run enrollments otherwise.
        """
        self.validate_enrollment_data(self.request, course_id)
        if course_id:
            good, bad, results = write_course_run_enrollments(
                self.request.method,
                self.program.discovery_uuid,
                course_id,
                self.request.data,
            )
        else:
            good, bad, results = write_program_enrollments(
                self.request.method,
                self.program.discovery_uuid,
                self.request.data,
            )
        if good and bad:
            status = HTTP_207_MULTI_STATUS
        elif good:
            status = HTTP_200_OK
        else:
            status = HTTP_422_UNPROCESSABLE_ENTITY
            self.add_tracking_data(failure='unprocessable_entity')
        return Response(results, status=status)

    def validate_enrollment_data(self, request, course_id=None):
        """
        Validate enrollments request body
        """
        enrollments = request.data
        if not isinstance(enrollments, list):
            self.add_tracking_data(failure='bad_request')
            raise ValidationError('expected request body type: List')

        if len(enrollments) > ENROLLMENT_WRITE_MAX_SIZE:
            self.add_tracking_data(failure='request_entity_too_large')
            raise exceptions.EnrollmentPayloadTooLarge()

        for enrollment in enrollments:
            if not isinstance(enrollment, dict):
                self.add_tracking_data(failure='bad_request')
                raise ValidationError(
                    'expected items in request to be of type Dict'
                )
            if not isinstance(enrollment.get('student_key'), str):
                self.add_tracking_data(failure='bad_request')
                raise ValidationError(
                    'expected request dicts to have string value for "student_key"'
                )
            if not isinstance(enrollment.get('status'), str):
                self.add_tracking_data(failure='bad_request')
                raise ValidationError(
                    'expected request dicts to have string value for "status"'
                )
            course_staff_flag = enrollment.get('course_staff')
            if course_id and course_staff_flag is not None:
                if not isinstance(course_staff_flag, bool):
                    self.add_tracking_data(failure='bad_request')
                    raise ValidationError(
                        'expected request dicts to have boolean value for "course_staff"'
                    )
                if not waffle.flag_is_active(request, 'enable_course_role_management'):
                    raise PermissionDenied('"course_staff" not accepted since role assignment is not enabled')
