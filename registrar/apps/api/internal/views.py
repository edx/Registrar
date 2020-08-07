""" Internal utility API views """
from django.http import Http404
from django.shortcuts import get_object_or_404
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.status import HTTP_204_NO_CONTENT
from rest_framework.views import APIView

from registrar.apps.core.discovery_cache import ProgramDetails
from registrar.apps.core.models import Program

from ..mixins import TrackViewMixin


class FlushProgramCacheView(TrackViewMixin, APIView):
    """
    A view for clearing the programs cache.
    Is only accessable to staff users.

    Paths:
        All programs:      /api/internal/cache/
        Specific program:  /api/internal/cache/{program_key}/

    Accepts: [DELETE]

    Returns:
     * 204: Program cache successfully cleared
     * 401: User not authenticated
     * 404: User not staff or program not found.
    """
    event_method_map = {'DELETE': 'registrar.internal.flush_program_cache'}
    event_parameter_map = {'program_key': 'program_key'}
    authentication_classes = (JwtAuthentication, SessionAuthentication)
    permission_classes = (IsAuthenticated,)

    def check_permissions(self, request):
        """
        Check that the authenticated user can access this view.

        Overrides APIView.check_permissions.
        """
        super().check_permissions(request)
        if not request.user.is_staff:
            self.add_tracking_data(failure='user_is_not_staff')
            raise Http404()

    def delete(self, request, program_key=None):
        """
        Clears a specific program from the cache, if specified.
        Otherwise, clears the entire cache.
        """
        if program_key:
            program = get_object_or_404(Program, key=program_key)
            program_uuids = [program.discovery_uuid]
        else:
            program_uuids = list(
                Program.objects.values_list('discovery_uuid', flat=True)
            )
        ProgramDetails.clear_cache_for_programs(program_uuids)
        return Response(status=HTTP_204_NO_CONTENT)
