""" Tests for API views. """
from contextlib import contextmanager
import csv
import json
import logging
import uuid
from io import StringIO
from posixpath import join as urljoin

from waffle import get_waffle_flag_model

import boto3
import ddt
import mock
import moto
import requests
import responses
from celery import shared_task
from django.conf import settings
from django.core.cache import cache
from django.urls import reverse
from faker import Faker
from guardian.shortcuts import assign_perm
from rest_framework.test import APITestCase
from user_tasks.models import UserTaskStatus
from user_tasks.tasks import UserTask

from registrar.apps.api.constants import ENROLLMENT_WRITE_MAX_SIZE
from registrar.apps.api.tests.mixins import AuthRequestMixin, TrackTestMixin
from registrar.apps.core import permissions as perms
from registrar.apps.core.constants import PROGRAM_CACHE_KEY_TPL
from registrar.apps.core.filestore import (
    get_enrollment_uploads_filestore,
    get_program_reports_filestore,
)
from registrar.apps.core.jobs import (
    post_job_failure,
    post_job_success,
    start_job,
)
from registrar.apps.core.models import (
    Organization,
    OrganizationGroup,
    ProgramOrganizationGroup,
    User,
)
from registrar.apps.core.permissions import JOB_GLOBAL_READ
from registrar.apps.core.tests.factories import (
    GroupFactory,
    OrganizationFactory,
    OrganizationGroupFactory,
    ProgramFactory,
    ProgramOrganizationGroupFactory,
    UserFactory,
)
from registrar.apps.core.tests.freezegun_wrapper import freeze_time
from registrar.apps.core.tests.mixins import S3MockEnvVarsMixin
from registrar.apps.core.tests.utils import (
    mock_oauth_login,
    patch_discovery_program_details,
)
from registrar.apps.core.utils import serialize_to_csv
from registrar.apps.enrollments.lms_interop import (
    LMS_PROGRAM_COURSE_ENROLLMENTS_API_TPL,
)
from registrar.apps.enrollments.tasks import lms
from registrar.apps.grades.constants import GradeReadStatus

from ..views import CourseRunEnrollmentUploadView, ProgramEnrollmentUploadView


ACTIVE_CURRICULUM_UUID = '77777777-4444-2222-1111-000000000000'
INACTIVE_CURRICULUM_UUID = '66666666-4444-2222-1111-000000000000'


@contextmanager
def get_waffle_flag(flag_name, is_active, groups):
    waffle_model = get_waffle_flag_model()
    waffle_flag = waffle_model.objects.create(name=flag_name)
    for group in groups:
        waffle_flag.groups.add(group)
    waffle_flag.everyone = is_active
    waffle_flag.save()
    waffle_flag.flush()
    yield waffle_flag
    waffle_flag.delete()


class RegistrarAPITestCase(TrackTestMixin, APITestCase):
    """ Base for tests of the Registrar API """

    api_root = '/api/v1/'
    TEST_PROGRAM_URL_TPL = 'http://registrar-test-data.edx.org/{key}/'

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

        cls.edx_admin = UserFactory(username='edx-admin')
        assign_perm(perms.ORGANIZATION_READ_METADATA, cls.edx_admin)

        # Some testing-specific terminology for the oranization groups here:
        #  - "admins" have enrollment read/write access & metadata read access
        #  - "ops" have enrollment read access & metadata read access
        #  - "users" have metadata read access

        cls.stem_org = OrganizationFactory(name='STEM Institute')
        cls.cs_program = ProgramFactory(
            key="masters-in-cs",
            managing_organization=cls.stem_org,
        )
        cls.mech_program = ProgramFactory(
            key="masters-in-me",
            managing_organization=cls.stem_org,
        )

        cls.stem_admin = UserFactory(username='stem-institute-admin')
        cls.stem_user = UserFactory(username='stem-institute-user')
        cls.global_read_and_write_group = GroupFactory(
            name='GlobalReadAndWrite',
            permissions=perms.OrganizationReadWriteEnrollmentsRole.permissions
        )
        cls.stem_admin_group = OrganizationGroupFactory(
            name='stem-admins',
            organization=cls.stem_org,
            role=perms.OrganizationReadWriteEnrollmentsRole.name
        )
        cls.stem_op_group = OrganizationGroupFactory(
            name='stem-ops',
            organization=cls.stem_org,
            role=perms.OrganizationReadEnrollmentsRole.name
        )
        cls.stem_user_group = OrganizationGroupFactory(
            name='stem-users',
            organization=cls.stem_org,
            role=perms.OrganizationReadMetadataRole.name
        )
        cls.stem_admin.groups.add(cls.stem_admin_group)  # pylint: disable=no-member
        cls.stem_user.groups.add(cls.stem_user_group)  # pylint: disable=no-member

        cls.hum_org = OrganizationFactory(name='Humanities College')
        cls.phil_program = ProgramFactory(
            key="masters-in-philosophy",
            managing_organization=cls.hum_org,
        )
        cls.english_program = ProgramFactory(
            key="masters-in-english",
            managing_organization=cls.hum_org,
        )

        cls.hum_admin = UserFactory(username='humanities-college-admin')
        cls.hum_admin_group = OrganizationGroupFactory(
            name='hum-admins',
            organization=cls.hum_org,
            role=perms.OrganizationReadWriteEnrollmentsRole.name
        )
        cls.hum_op_group = OrganizationGroupFactory(
            name='hum-ops',
            organization=cls.hum_org,
            role=perms.OrganizationReadEnrollmentsRole.name
        )
        cls.hum_user_group = OrganizationGroupFactory(
            name='hum-users',
            organization=cls.hum_org,
            role=perms.OrganizationReadMetadataRole.name
        )
        cls.hum_data_op_group = OrganizationGroupFactory(
            name='hum-data-ops',
            organization=cls.hum_org,
            role=perms.OrganizationReadReportRole.name
        )
        cls.hum_admin.groups.add(cls.hum_admin_group)  # pylint: disable=no-member
        cls.hum_admin.groups.add(cls.hum_data_op_group)  # pylint: disable=no-member

        cls.program_user = UserFactory(username='english-program-user')
        cls.program_group = ProgramOrganizationGroupFactory(
            name='english-program-users',
            program=cls.english_program,
            role=perms.ProgramReadMetadataRole.name
        )
        cls.program_user.groups.add(cls.program_group)  # pylint: disable=no-member

    def setUp(self):
        super().setUp()
        self._add_programs_to_cache()

    def mock_api_response(self, url, response_data, method='GET', response_code=200):
        responses.add(
            getattr(responses, method.upper()),
            url,
            body=json.dumps(response_data),
            content_type='application/json',
            status=response_code
        )

    def _add_programs_to_cache(self):
        """
        Adds the cs_, mech_, english_, and phil_ programs to the cache
        """
        programs = [self.cs_program, self.mech_program, self.english_program, self.phil_program]
        for program in programs:
            self._add_program_to_cache(program)

    @classmethod
    def _add_program_to_cache(cls, program, title=None, url=None, program_type="Masters", curricula=None):
        """
        Adds the given program to the program cache
        """
        title = title or str(program.key).replace('-', ' ')
        url = url or cls.TEST_PROGRAM_URL_TPL.format(key=program.key)
        curricula = curricula or []
        cache_key = PROGRAM_CACHE_KEY_TPL.format(uuid=program.discovery_uuid)
        mock_program_details = {
            "title": title,
            "marketing_url": url,
            "type": program_type,
            "curricula": curricula,
        }
        cache.set(cache_key, mock_program_details)


class S3MockMixin(S3MockEnvVarsMixin):
    """
    Mixin for classes that need to access S3 resources.

    Enables S3 mock and creates default bucket before tests.
    Disables S3 mock afterwards.

    `s3_bucket` class variable specifies which bucket to use.
    Defaults to settings.REGISTRAR_BUCKET.
    """
    s3_bucket = settings.REGISTRAR_BUCKET

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Suppress egregious boto/moto DEBUG logging.
        for logger_name in ['boto3', 'botocore', 's3transfer']:
            logging.getLogger(logger_name).setLevel(logging.INFO)
        cls._s3_mock = moto.mock_s3()
        cls._s3_mock.start()
        conn = boto3.resource('s3', region_name='us-west-1')
        conn.create_bucket(Bucket=cls.s3_bucket)

    @classmethod
    def tearDownClass(cls):
        cls._s3_mock.stop()
        super().tearDownClass()


@ddt.ddt
class ViewMethodNotSupportedTests(RegistrarAPITestCase, AuthRequestMixin):
    """ Tests for the case if user requested a not supported HTTP method """

    method = 'DELETE'
    path = 'programs'

    @ddt.data(
        ('programs', 'ProgramListView'),
        ('programs/masters-in-english', 'ProgramRetrieveView'),
        ('programs/masters-in-english/enrollments', 'ProgramEnrollmentView'),
        ('programs/masters-in-english/courses', 'ProgramCourseListView'),
        (
            'programs/masters-in-english/courses/HUMx+English-550+Spring/enrollments',
            'CourseEnrollmentView'
        ),
        ('jobs/', 'JobStatusListView'),
    )
    @ddt.unpack
    def test_not_supported_http_method(self, path, view_name):
        self.mock_logging.reset_mock()
        self.path = path
        response = self.request(self.method, path, self.edx_admin)
        self.mock_logging.error.assert_called_once_with(
            'Segment tracking event name not found for request method %s on view %s',
            self.method,
            view_name,
        )
        self.assertEqual(response.status_code, 405)


@ddt.ddt
class ProgramListViewTests(RegistrarAPITestCase, AuthRequestMixin):
    """ Tests for the /api/v1/programs?org={org_key} endpoint """

    method = 'GET'
    path = 'programs'
    event = 'registrar.v1.list_programs'

    def test_all_programs_200(self):
        with self.assert_tracking(user=self.edx_admin):
            response = self.get('programs', self.edx_admin)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 4)
        response_programs = sorted(response.data, key=lambda p: p['program_key'])
        self.assertListEqual(
            response_programs,
            [
                {
                    'program_key': 'masters-in-cs',
                    'program_title': 'masters in cs',
                    'program_url': 'http://registrar-test-data.edx.org/masters-in-cs/',
                    'program_type': 'Masters',
                    'permissions': ['read_metadata'],
                },
                {
                    'program_key': 'masters-in-english',
                    'program_title': 'masters in english',
                    'program_url': 'http://registrar-test-data.edx.org/masters-in-english/',
                    'program_type': 'Masters',
                    'permissions': ['read_metadata'],
                },
                {
                    'program_key': 'masters-in-me',
                    'program_title': 'masters in me',
                    'program_url': 'http://registrar-test-data.edx.org/masters-in-me/',
                    'program_type': 'Masters',
                    'permissions': ['read_metadata'],
                },
                {
                    'program_key': 'masters-in-philosophy',
                    'program_title': 'masters in philosophy',
                    'program_url': 'http://registrar-test-data.edx.org/masters-in-philosophy/',
                    'program_type': 'Masters',
                    'permissions': ['read_metadata'],
                },
            ]
        )

    def test_partial_programs_200(self):
        with self.assert_tracking(user=self.program_user):
            response = self.get('programs', self.program_user)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        response_programs = sorted(response.data, key=lambda p: p['program_key'])
        self.assertListEqual(
            response_programs,
            [
                {
                    'program_key': 'masters-in-english',
                    'program_title': 'masters in english',
                    'program_url': 'http://registrar-test-data.edx.org/masters-in-english/',
                    'program_type': 'Masters',
                    'permissions': ['read_metadata'],
                }
            ]
        )

    @ddt.data(

        # If you use only an org filter, and you DO have access to that org,
        # you get only that org's programs.
        {
            'groups': {'stem-users'},
            'org_filter': 'stem-institute',
            'expect_stem_programs': True,
        },
        {
            'groups': {'stem-admins', 'stem-users'},
            'org_filter': 'stem-institute',
            'expect_stem_programs': True,
        },
        {
            'groups': {'stem-ops', 'hum-admins'},
            'org_filter': 'stem-institute',
            'expect_stem_programs': True,
        },

        # If you use only an org filter, and you only have access to one program in that org,
        # then you get only that program.
        {
            'groups': {'english-program-users'},
            'org_filter': 'humanities-college',
            'expect_hum_program': True,
            'test_program_group': True,
        },

        # If you use a permissions filter, you always get a 200, with all
        # the programs you have access to (which may be an empty list).
        {
            'groups': set(),
            'perm_filter': 'read_metadata',
        },
        {
            'groups': set(),
            'perm_filter': 'read_enrollments',
        },
        {
            'groups': set(),
            'perm_filter': 'write_enrollments',
        },
        {
            'groups': set(),
            'perm_filter': 'write_enrollments',
            'global_perm': True,
            'expect_stem_programs': True,
            'expect_hum_programs': True,
        },
        {
            'groups': {'stem-users', 'hum-ops'},
            'perm_filter': 'write_enrollments',
        },
        {
            'groups': {'stem-admins', 'hum-ops'},
            'perm_filter': 'write_enrollments',
            'expect_stem_programs': True,
        },
        {
            'groups': {'stem-admins', 'hum-ops'},
            'perm_filter': 'read_enrollments',
            'expect_stem_programs': True,
            'expect_hum_programs': True,
        },
        {
            'groups': {'hum-admins', 'hum-users'},
            'perm_filter': 'write_enrollments',
            'expect_hum_programs': True,
        },
        {
            'groups': {'hum-admins', 'hum-users'},
            'perm_filter': 'read_metadata',
            'expect_hum_programs': True,
        },
        # Validate old permissions filter (to be deprecated)
        {
            'groups': {'hum-admins', 'hum-users'},
            'perm_filter': 'metadata',
            'expect_hum_programs': True,
        },

        # Finally, the filters may be combined
        {
            'groups': {'stem-admins', 'hum-ops'},
            'perm_filter': 'read_enrollments',
            'org_filter': 'humanities-college',
            'expect_hum_programs': True,
        },
        {
            'groups': {'stem-admins', 'hum-ops'},
            'perm_filter': 'write_enrollments',
            'org_filter': 'stem-institute',
            'expect_stem_programs': True,
        },
        {
            'groups': {'stem-admins', 'hum-ops'},
            'perm_filter': 'write_enrollments',
            'org_filter': 'humanities-college',
        },
    )
    @ddt.unpack
    def test_program_filters(
            self,
            groups=frozenset(),
            perm_filter=None,
            org_filter=None,
            global_perm=False,
            expected_status=200,
            expect_stem_programs=False,
            expect_hum_programs=False,
            expect_hum_program=False,
            test_program_group=False,
    ):
        org_or_program_groups = [ProgramOrganizationGroup.objects.get(name=name) for name in groups] \
            if test_program_group else [OrganizationGroup.objects.get(name=name) for name in groups]
        user = UserFactory(groups=org_or_program_groups)
        if global_perm:
            user.groups.add(self.global_read_and_write_group)  # pylint: disable=no-member

        query = []
        tracking_kwargs = {}
        if org_filter:
            query.append('org=' + org_filter)
            tracking_kwargs['organization_filter'] = org_filter
        if perm_filter:
            query.append('user_has_perm=' + perm_filter)
            tracking_kwargs['permission_filter'] = perm_filter
        querystring = '&'.join(query)

        expected_programs_keys = set()
        if expect_stem_programs:
            expected_programs_keys.update({
                'masters-in-cs', 'masters-in-me'
            })
        if expect_hum_programs:
            expected_programs_keys.update({
                'masters-in-english', 'masters-in-philosophy'
            })
        if expect_hum_program:
            expected_programs_keys.update({
                'masters-in-english'
            })

        with self.assert_tracking(
                user=user,
                status_code=expected_status,
                **tracking_kwargs
        ):
            response = self.get('programs?' + querystring, user)
        self.assertEqual(response.status_code, expected_status)

        if expected_status == 200:  # pragma: no branch
            actual_program_keys = {
                program['program_key'] for program in response.data
            }
            self.assertEqual(expected_programs_keys, actual_program_keys)

    @ddt.data(
        {
            'groups': set(),
            'perm_filter': 'write_enrollments',
            'expected_programs': {
                'masters-in-cs',
                'masters-in-me',
                'masters-in-philosophy',
            },
            'global_perm': True,
        },
        {
            'groups': set(),
            'perm_filter': 'read_enrollments',
            'expected_programs': {
                'masters-in-cs',
                'masters-in-me',
                'masters-in-philosophy',
            },
            'global_perm': True,
        },
        {
            'groups': set(),
            'perm_filter': 'read_metadata',
            'expected_programs': {
                'masters-in-cs',
                'masters-in-me',
                'masters-in-english',
                'masters-in-philosophy',
            },
            'global_perm': True,
        },
        {
            'groups': {'hum-admins'},
            'perm_filter': 'write_enrollments',
            'expected_programs': {'masters-in-philosophy'},
        },
        {
            'groups': {'hum-ops'},
            'perm_filter': 'read_enrollments',
            'expected_programs': {'masters-in-philosophy'},
        },
        {
            'groups': {'hum-users'},
            'perm_filter': 'read_metadata',
            'expected_programs': {'masters-in-english', 'masters-in-philosophy'},
        },
    )
    @ddt.unpack
    def test_program_filter_with_mm_programs(
        self,
        groups,
        perm_filter,
        expected_programs,
        global_perm=False,
    ):
        self._add_program_to_cache(self.english_program, program_type='MicroMasters')
        org_groups = [OrganizationGroup.objects.get(name=name) for name in groups]
        user = UserFactory(groups=org_groups)
        if global_perm:
            user.groups.add(self.global_read_and_write_group)  # pylint: disable=no-member

        with self.assert_tracking(
                user=user,
                status_code=200,
                permission_filter=perm_filter,
        ):
            response = self.get('programs?user_has_perm=' + perm_filter, user)
        self.assertEqual(response.status_code, 200)
        returned_program_keys = [program['program_key'] for program in response.data]
        for expected_key in expected_programs:
            self.assertIn(expected_key, returned_program_keys)

    @ddt.data(
        # Bad org filter, no perm filter
        ('intergalactic-univ', None, 'org_not_found'),
        # Bad org filter, good perm filter
        ('intergalactic-univ', 'write_enrollments', 'org_not_found'),
        # No org filter, bad perm filter
        (None, 'right', 'no_such_perm'),
        # Good org filter, bad perm filter
        ('stem-institute', 'right', 'no_such_perm'),
        # Bad org filter, bad perm filter
        # Note: whether this raises `no_such_perm` or `org_not_found`
        #       is essentially an implementation detail; either would
        #       be acceptable. Either way, the user sees a 404.
        ('intergalactic-univ', 'right', 'org_not_found'),
    )
    @ddt.unpack
    def test_404(self, org_filter, perm_filter, expected_failure):
        query = []
        tracking_kwargs = {}
        if org_filter:
            query.append('org=' + org_filter)
            tracking_kwargs['organization_filter'] = org_filter
        if perm_filter:
            query.append('user_has_perm=' + perm_filter)
            tracking_kwargs['permission_filter'] = perm_filter
        querystring = '&'.join(query)

        with self.assert_tracking(
                user=self.stem_admin,
                failure=expected_failure,
                status_code=404,
                **tracking_kwargs
        ):
            response = self.get('programs?' + querystring, self.stem_admin)
        self.assertEqual(response.status_code, 404)

    @mock.patch.object(Organization.objects, 'get', wraps=Organization.objects.get)
    def test_org_property_caching(self, get_org_wrapper):
        # If the 'managing_organization' property is not cached, a single
        # call to this endpoint would cause multiple Organization queries
        response = self.get("programs?org=stem-institute", self.stem_admin)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)
        get_org_wrapper.assert_called_once()


@ddt.ddt
class ProgramRetrieveViewTests(RegistrarAPITestCase, AuthRequestMixin):
    """ Tests for the /api/v1/programs/{program_key} endpoint """

    method = 'GET'
    path = 'programs/masters-in-english'
    event = 'registrar.v1.get_program_detail'

    @ddt.data(
        ('edx-admin', ['read_metadata']),
        ('english-program-user', ['read_metadata']),
        ('humanities-college-admin', [
            'read_metadata',
            'read_reports',
            'read_enrollments',
            'write_enrollments',
        ]),
    )
    @ddt.unpack
    def test_get_program(self, username, api_permissions):
        user = User.objects.get(username=username)
        with self.assert_tracking(user=user, program_key='masters-in-english'):
            response = self.get('programs/masters-in-english', user)
        self.assertEqual(response.status_code, 200)
        response.data['permissions'] = sorted(response.data['permissions'])
        api_permissions = sorted(api_permissions)
        self.assertDictEqual(
            response.data,
            {
                'program_key': 'masters-in-english',
                'program_title': 'masters in english',
                'program_url': 'http://registrar-test-data.edx.org/masters-in-english/',
                'program_type': 'Masters',
                'permissions': api_permissions,
            },
        )

    def test_get_program_unauthorized(self):
        with self.assert_tracking(
                user=self.stem_admin,
                program_key='masters-in-english',
                missing_permissions=[perms.APIReadMetadataPermission],
        ):
            response = self.get('programs/masters-in-english', self.stem_admin)
        self.assertEqual(response.status_code, 403)

    def test_program_not_found(self):
        with self.assert_tracking(
                user=self.stem_admin,
                program_key='masters-in-polysci',
                failure='program_not_found',
        ):
            response = self.get('programs/masters-in-polysci', self.stem_admin)
        self.assertEqual(response.status_code, 404)


@ddt.ddt
class ProgramCourseListViewTests(RegistrarAPITestCase, AuthRequestMixin):
    """ Tests for the /api/v1/programs/{program_key}/courses endpoint """

    method = 'GET'
    path = 'programs/masters-in-english/courses'
    event = 'registrar.v1.get_program_courses'

    program_uuid = str(uuid.uuid4())
    program_title = Faker().sentence(nb_words=6)
    program_url = Faker().uri()
    program_type = 'Masters'

    @ddt.data(True, False)
    @mock_oauth_login
    @responses.activate
    def test_get_program_courses(self, is_staff):
        user = self.edx_admin if is_staff else self.hum_admin

        mock_program_details = {
            "title": self.program_title,
            "marketing_url": self.program_url,
            "type": self.program_type,
            "curricula": [
                {
                    'is_active': False,
                    'courses': []
                },
                {
                    'is_active': True,
                    'courses': [{
                        'course_runs': [
                            {
                                'key': '0001',
                                'external_key': 'extkey1',
                                'uuid': '123456',
                                'title': 'Test Course 1',
                                'marketing_url': 'https://humanities-college.edx.org/masters-in-english/test-course-1',
                            }
                        ],
                    }]
                },
            ]
        }

        with self.assert_tracking(user=user, program_key='masters-in-english'):
            with patch_discovery_program_details(mock_program_details):
                response = self.get('programs/masters-in-english/courses', user)

        self.assertEqual(response.status_code, 200)
        self.assertListEqual(
            response.data,
            [{
                'course_id': '0001',
                'external_course_key': 'extkey1',
                'course_title': 'Test Course 1',
                'course_url': 'https://humanities-college.edx.org/masters-in-english/test-course-1',
            }],
        )

    def test_get_program_courses_unauthorized(self):
        with self.assert_tracking(
                user=self.hum_admin,
                program_key='masters-in-cs',
                missing_permissions=[perms.APIReadMetadataPermission],
        ):
            response = self.get('programs/masters-in-cs/courses', self.hum_admin)
        self.assertEqual(response.status_code, 403)

    @mock_oauth_login
    @responses.activate
    def test_get_program_with_no_course_runs(self):
        user = self.hum_admin

        mock_program_details = {
            "title": self.program_title,
            "marketing_url": self.program_url,
            "type": self.program_type,
            "curricula": [{
                'is_active': True,
                'courses': [{
                    'course_runs': []
                }]
            }]
        }

        with self.assert_tracking(user=user, program_key='masters-in-english'):
            with patch_discovery_program_details(mock_program_details):
                response = self.get('programs/masters-in-english/courses', user)

        self.assertEqual(response.status_code, 200)
        self.assertListEqual(response.data, [])

    @mock_oauth_login
    @responses.activate
    def test_get_program_with_no_active_curriculum(self):
        user = self.hum_admin

        mock_program_details = {
            "title": self.program_title,
            "marketing_url": self.program_url,
            "type": self.program_type,
            "curricula": [{
                'is_active': False,
                'courses': [{
                    'course_runs': []
                }]
            }]
        }

        with self.assert_tracking(user=user, program_key='masters-in-english'):
            with patch_discovery_program_details(mock_program_details):
                response = self.get('programs/masters-in-english/courses', user)

        self.assertEqual(response.status_code, 200)
        self.assertListEqual(response.data, [])

    @mock_oauth_login
    @responses.activate
    def test_get_program_with_multiple_courses(self):
        user = self.stem_admin

        mock_program_details = {
            "title": self.program_title,
            "marketing_url": self.program_url,
            "type": self.program_type,
            "curricula": [{
                'is_active': True,
                'courses': [
                    {
                        'course_runs': [
                            {
                                'key': '0001',
                                'uuid': '0000-0001',
                                'title': 'Test Course 1',
                                'external_key': 'extkey1',
                                'marketing_url': 'https://stem-institute.edx.org/masters-in-cs/test-course-1',
                            },
                        ],
                    },
                    {
                        'course_runs': [
                            {
                                'key': '0002a',
                                'uuid': '0000-0002a',
                                'title': 'Test Course 2',
                                'external_key': 'extkey2a',
                                'marketing_url': 'https://stem-institute.edx.org/masters-in-cs/test-course-2a',
                            },
                            {
                                'key': '0002b',
                                'uuid': '0000-0002b',
                                'title': 'Test Course 2',
                                'external_key': 'extkey2b',
                                'marketing_url': 'https://stem-institute.edx.org/masters-in-cs/test-course-2b',
                            },
                        ],
                    }
                ],
            }],
        }

        with self.assert_tracking(user=user, program_key='masters-in-cs'):
            with patch_discovery_program_details(mock_program_details):
                response = self.get('programs/masters-in-cs/courses', user)

        self.assertEqual(response.status_code, 200)
        self.assertListEqual(
            response.data,
            [
                {
                    'course_id': '0001',
                    'external_course_key': 'extkey1',
                    'course_title': 'Test Course 1',
                    'course_url': 'https://stem-institute.edx.org/masters-in-cs/test-course-1',
                },
                {
                    'course_id': '0002a',
                    'external_course_key': 'extkey2a',
                    'course_title': 'Test Course 2',
                    'course_url': 'https://stem-institute.edx.org/masters-in-cs/test-course-2a',
                },
                {
                    'course_id': '0002b',
                    'external_course_key': 'extkey2b',
                    'course_title': 'Test Course 2',
                    'course_url': 'https://stem-institute.edx.org/masters-in-cs/test-course-2b',
                }
            ],
        )

    def test_program_not_found(self):
        with self.assert_tracking(
                user=self.stem_admin,
                program_key='masters-in-polysci',
                failure='program_not_found'
        ):
            response = self.get('programs/masters-in-polysci/courses', self.stem_admin)
        self.assertEqual(response.status_code, 404)


@ddt.ddt
class ProgramEnrollmentWriteMixin:
    """ Test write requests to the /api/v1/programs/{program_key}/enrollments endpoint """
    path = 'programs/masters-in-english/enrollments'

    @classmethod
    def setUpTestData(cls):   # pylint: disable=missing-function-docstring
        super().setUpTestData()
        program_uuid = cls.cs_program.discovery_uuid
        cls.mock_program_details = {
            'curricula': [
                {'uuid': INACTIVE_CURRICULUM_UUID, 'is_active': False},
                {'uuid': ACTIVE_CURRICULUM_UUID, 'is_active': True}
            ],
            'type': 'Masters',
        }
        cls.program_no_curricula = {
            'curricula': [],
            'type': 'Masters',
        }
        cls.lms_request_url = urljoin(
            settings.LMS_BASE_URL, 'api/program_enrollments/v1/programs/{}/enrollments/'
        ).format(program_uuid)

    def mock_enrollments_response(self, method, expected_response, response_code=200):
        self.mock_api_response(self.lms_request_url, expected_response, method=method, response_code=response_code)

    def student_enrollment(self, status, student_key=None):
        return {
            'status': status,
            'student_key': student_key or uuid.uuid4().hex[0:10]
        }

    def test_program_unauthorized_at_organization(self):
        req_data = [
            self.student_enrollment('enrolled'),
        ]

        with self.assert_tracking(
                user=self.hum_admin,
                program_key='masters-in-cs',
                missing_permissions=[perms.APIWriteEnrollmentsPermission],
        ):
            response = self.request(
                self.method,
                'programs/masters-in-cs/enrollments/',
                self.hum_admin,
                req_data,
            )
        self.assertEqual(response.status_code, 403)

    def test_program_insufficient_permissions(self):
        req_data = [
            self.student_enrollment('enrolled'),
        ]
        with self.assert_tracking(
                user=self.stem_user,
                program_key='masters-in-cs',
                missing_permissions=[perms.APIWriteEnrollmentsPermission],
        ):
            response = self.request(
                self.method,
                'programs/masters-in-cs/enrollments/',
                self.stem_user,
                req_data,
            )
        self.assertEqual(response.status_code, 403)

    def test_program_not_found(self):
        req_data = [
            self.student_enrollment('enrolled'),
        ]
        with self.assert_tracking(
                user=self.stem_admin,
                program_key='uan-salsa-dancing-with-sharks',
                failure='program_not_found',
        ):
            response = self.request(
                self.method,
                'programs/uan-salsa-dancing-with-sharks/enrollments/',
                self.stem_admin,
                req_data,
            )
        self.assertEqual(response.status_code, 404)

    def test_mm_program_permission_denied(self):
        self._add_program_to_cache(self.mech_program, program_type='MicroMasters')
        req_data = [
            self.student_enrollment('enrolled')
        ]
        response = self.request(
            self.method,
            'programs/masters-in-me/enrollments/',
            self.stem_admin,
            req_data,
        )
        self.assertEqual(response.status_code, 403)

    @mock_oauth_login
    @responses.activate
    def test_successful_program_enrollment_write(self):
        expected_lms_response = {
            '001': 'enrolled',
            '002': 'enrolled',
            '003': 'pending'
        }
        self.mock_enrollments_response(self.method, expected_lms_response)

        req_data = [
            self.student_enrollment('enrolled', '001'),
            self.student_enrollment('enrolled', '002'),
            self.student_enrollment('pending', '003'),
        ]

        with self.assert_tracking(user=self.stem_admin, program_key='masters-in-cs'):
            with patch_discovery_program_details(self.mock_program_details):
                response = self.request(
                    self.method,
                    'programs/masters-in-cs/enrollments/',
                    self.stem_admin,
                    req_data,
                )

        lms_request_body = json.loads(responses.calls[-1].request.body.decode('utf-8'))
        self.assertCountEqual(lms_request_body, [
            {
                'status': 'enrolled',
                'student_key': '001',
                'curriculum_uuid': ACTIVE_CURRICULUM_UUID,
            },
            {
                'status': 'enrolled',
                'student_key': '002',
                'curriculum_uuid': ACTIVE_CURRICULUM_UUID,
            },
            {
                'status': 'pending',
                'student_key': '003',
                'curriculum_uuid': ACTIVE_CURRICULUM_UUID,
            }
        ])
        self.assertEqual(response.status_code, 200)
        self.assertDictEqual(response.data, expected_lms_response)

    @mock_oauth_login
    @responses.activate
    def test_backend_unprocessable_response(self):
        expected_lms_response = {
            '001': 'conflict',
            '002': 'conflict',
            '003': 'conflict'
        }
        self.mock_enrollments_response(self.method, expected_lms_response, response_code=422)

        req_data = [
            self.student_enrollment('enrolled', '001'),
            self.student_enrollment('enrolled', '002'),
            self.student_enrollment('pending', '003'),
        ]

        with self.assert_tracking(
                user=self.stem_admin,
                program_key='masters-in-cs',
                failure='unprocessable_entity',
        ):
            with patch_discovery_program_details(self.mock_program_details):
                response = self.request(
                    self.method,
                    'programs/masters-in-cs/enrollments/',
                    self.stem_admin,
                    req_data,
                )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.data, expected_lms_response)

    @mock_oauth_login
    @responses.activate
    def test_backend_multi_status_response(self):
        expected_lms_response = {
            '001': 'enrolled',
            '002': 'enrolled',
            '003': 'invalid-status'
        }
        self.mock_enrollments_response(self.method, expected_lms_response, response_code=207)

        req_data = [
            self.student_enrollment('enrolled', '001'),
            self.student_enrollment('enrolled', '002'),
            self.student_enrollment('not_a_valid_value', '003'),
        ]
        with self.assert_tracking(
                user=self.stem_admin,
                program_key='masters-in-cs',
                status_code=207,
        ):
            with patch_discovery_program_details(self.mock_program_details):
                response = self.request(
                    self.method,
                    'programs/masters-in-cs/enrollments/',
                    self.stem_admin,
                    req_data,
                )
        self.assertEqual(response.status_code, 207)
        self.assertDictEqual(response.data, expected_lms_response)

    @mock_oauth_login
    @responses.activate
    @ddt.data(
        (500, 'Internal Server Error'),
        (404, 'Not Found'),
        (413, 'Payload Too Large'),
    )
    @ddt.unpack
    def test_backend_request_failed(self, status_code, content):
        self.mock_enrollments_response(
            self.method, content, response_code=status_code
        )
        req_data = [
            self.student_enrollment('enrolled', '001'),
            self.student_enrollment('enrolled', '002'),
            self.student_enrollment('pending', '003'),
        ]
        expected_response_data = {
            '001': 'internal-error',
            '002': 'internal-error',
            '003': 'internal-error',
        }
        with self.assert_tracking(
                user=self.stem_admin,
                program_key='masters-in-cs',
                failure='unprocessable_entity',
        ):
            with patch_discovery_program_details(self.mock_program_details):
                response = self.request(
                    self.method,
                    'programs/masters-in-cs/enrollments/',
                    self.stem_admin,
                    req_data,
                )
        self.assertEqual(response.status_code, 422)
        self.assertDictEqual(response.data, expected_response_data)

    def test_write_enrollment_payload_limit(self):
        req_data = [self.student_enrollment('enrolled')] * (ENROLLMENT_WRITE_MAX_SIZE + 1)

        with self.assert_tracking(
                user=self.stem_admin,
                program_key='masters-in-cs',
                failure='request_entity_too_large',
        ):
            response = self.request(self.method, 'programs/masters-in-cs/enrollments/', self.stem_admin, req_data)
        self.assertEqual(response.status_code, 413)

    @mock_oauth_login
    @patch_discovery_program_details({})
    def test_discovery_data_fetch_failed(self):
        req_data = [
            self.student_enrollment('enrolled', '001'),
        ]
        response = self.request(
            self.method,
            'programs/masters-in-cs/enrollments/',
            self.stem_admin,
            req_data,
        )
        # We expect a 403 because enrollments are disabled by default if we can't
        # reach Discovery to check.
        self.assertEqual(403, response.status_code)


class ProgramEnrollmentPostTests(ProgramEnrollmentWriteMixin, RegistrarAPITestCase, AuthRequestMixin):
    method = 'POST'
    event = 'registrar.v1.post_program_enrollment'


class ProgramEnrollmentPatchTests(ProgramEnrollmentWriteMixin, RegistrarAPITestCase, AuthRequestMixin):
    method = 'PATCH'
    event = 'registrar.v1.patch_program_enrollment'


@ddt.ddt
class ProgramEnrollmentGetTests(S3MockMixin, RegistrarAPITestCase, AuthRequestMixin):
    """ Tests for GET /api/v1/programs/{program_key}/enrollments endpoint """
    method = 'GET'
    path = 'programs/masters-in-english/enrollments'
    event = 'registrar.v1.get_program_enrollment'

    enrollments = [
        {
            'student_key': 'abcd',
            'status': 'enrolled',
            'account_exists': True,
        },
        {
            'student_key': 'efgh',
            'status': 'pending',
            'account_exists': False,
        },
    ]
    enrollments_json = json.dumps(enrollments, indent=4)
    enrollments_csv = (
        "student_key,status,account_exists\r\n"
        "abcd,enrolled,True\r\n"
        "efgh,pending,False\r\n"
    )

    @mock.patch.object(lms, 'get_program_enrollments', return_value=enrollments)
    @ddt.data(
        (None, 'json', enrollments_json),
        ('json', 'json', enrollments_json),
        ('csv', 'csv', enrollments_csv),
    )
    @ddt.unpack
    def test_ok(self, format_param, expected_format, expected_contents, _enrollments_mock):
        format_suffix = "?fmt=" + format_param if format_param else ""
        kwargs = {'result_format': format_param} if format_param else {}
        with self.assert_tracking(
                user=self.hum_admin,
                program_key='masters-in-english',
                status_code=202,
                **kwargs
        ):
            response = self.get(self.path + format_suffix, self.hum_admin)
        self.assertEqual(response.status_code, 202)
        with self.assert_tracking(
                event='registrar.v1.get_job_status',
                user=self.hum_admin,
                job_id=response.data['job_id'],
                job_state='Succeeded',
        ):
            job_response = self.get(response.data['job_url'], self.hum_admin)
        self.assertEqual(job_response.status_code, 200)
        self.assertEqual(job_response.data['state'], 'Succeeded')

        result_url = job_response.data['result']
        self.assertIn(".{}?".format(expected_format), result_url)
        file_response = requests.get(result_url)
        self.assertEqual(file_response.status_code, 200)
        self.assertEqual(file_response.text, expected_contents)

    def test_permission_denied(self):
        with self.assert_tracking(
                user=self.stem_admin,
                program_key='masters-in-english',
                missing_permissions=[perms.APIReadEnrollmentsPermission],
        ):
            response = self.get(self.path, self.stem_admin)
        self.assertEqual(response.status_code, 403)

    def test_mm_program_permission_denied(self):
        self._add_program_to_cache(self.english_program, program_type='MicroMasters')
        response = self.get(self.path, self.stem_admin)
        self.assertEqual(response.status_code, 403)

    def test_program_not_found(self):
        with self.assert_tracking(
                user=self.hum_admin,
                program_key='masters-in-polysci',
                failure='program_not_found',
        ):
            response = self.get('programs/masters-in-polysci/enrollments', self.hum_admin)
        self.assertEqual(response.status_code, 404)

    def test_invalid_format_404(self):
        with self.assert_tracking(
                user=self.hum_admin,
                program_key='masters-in-english',
                failure='result_format_not_supported',
                result_format='invalidformat',
                status_code=404,
        ):
            response = self.get(self.path + '?fmt=invalidformat', self.hum_admin)
        self.assertEqual(response.status_code, 404)


@ddt.ddt
class ProgramCourseEnrollmentGetTests(S3MockMixin, RegistrarAPITestCase, AuthRequestMixin):
    """ Tests for GET /api/v1/programs/{program_key}/enrollments endpoint """
    method = 'GET'
    path = 'programs/masters-in-english/courses/HUMx+English-550+Spring/enrollments'
    event = 'registrar.v1.get_course_enrollment'

    program_uuid = str(uuid.uuid4())
    mock_program_details = {
        'curricula': [{
            'is_active': True,
            'courses': [{
                'course_runs': [{
                    'key': 'HUMx+English-550+Spring',
                    'external_key': 'ENG55-S19',
                    'title': "English 550",
                    'marketing_url': 'https://example.com/english-550',
                }]
            }]
        }],
        'type': "Masters",
    }

    enrollments = [
        {
            'course_id': 'ENG55-S19',
            'student_key': 'abcd',
            'status': 'enrolled',
            'account_exists': True,
        },
        {
            'course_id': 'ENG55-S19',
            'student_key': 'efgh',
            'status': 'pending',
            'account_exists': False,
        },
    ]
    enrollments_json = json.dumps(enrollments, indent=4)
    enrollments_csv = (
        "course_id,student_key,status,account_exists\r\n"
        "ENG55-S19,abcd,enrolled,True\r\n"
        "ENG55-S19,efgh,pending,False\r\n"
    )

    @patch_discovery_program_details(mock_program_details)
    @mock.patch.object(lms, 'get_course_run_enrollments', return_value=enrollments)
    @ddt.data(
        (None, 'json', enrollments_json),
        ('json', 'json', enrollments_json),
        ('csv', 'csv', enrollments_csv),
    )
    @ddt.unpack
    def test_ok(self, format_param, expected_format, expected_contents, _enrollments_mock):
        format_suffix = "?fmt=" + format_param if format_param else ""
        kwargs = {'result_format': format_param} if format_param else {}
        with self.assert_tracking(
                user=self.hum_admin,
                program_key='masters-in-english',
                course_id='HUMx+English-550+Spring',
                status_code=202,
                **kwargs
        ):
            response = self.get(self.path + format_suffix, self.hum_admin)
        self.assertEqual(response.status_code, 202)
        with self.assert_tracking(
                event='registrar.v1.get_job_status',
                user=self.hum_admin,
                job_id=response.data['job_id'],
                job_state='Succeeded',
        ):
            job_response = self.get(response.data['job_url'], self.hum_admin)
        self.assertEqual(job_response.status_code, 200)
        self.assertEqual(job_response.data['state'], 'Succeeded')

        result_url = job_response.data['result']
        self.assertIn(".{}?".format(expected_format), result_url)
        file_response = requests.get(result_url)
        self.assertEqual(file_response.status_code, 200)
        self.assertEqual(file_response.text, expected_contents)

    @patch_discovery_program_details(mock_program_details)
    def test_permission_denied(self):
        with self.assert_tracking(
                user=self.stem_admin,
                program_key='masters-in-english',
                course_id='HUMx+English-550+Spring',
                missing_permissions=[perms.APIReadEnrollmentsPermission],
        ):
            response = self.get(self.path, self.stem_admin)
        self.assertEqual(response.status_code, 403)

    def test_mm_program_permission_denied(self):
        self._add_program_to_cache(self.english_program, program_type='MicroMasters')
        response = self.get(self.path, self.stem_admin)
        self.assertEqual(response.status_code, 403)

    @patch_discovery_program_details(mock_program_details)
    @ddt.data(
        # Bad program
        ('masters-in-polysci', 'course-v1:HUMx+English-550+Spring', 'program_not_found'),
        # Good program, course key formatted correctly, but course does not exist
        ('masters-in-english', 'course-v1:STEMx+Biology-440+Fall', 'course_not_found'),
        # Good program, course key matches URL but is formatted incorrectly
        ('masters-in-english', 'not-a-course-key:a+b+c', 'course_not_found'),
    )
    @ddt.unpack
    def test_not_found(self, program_key, course_id, expected_failure):
        path_fmt = 'programs/{}/courses/{}/enrollments'
        with self.assert_tracking(
                user=self.hum_admin,
                program_key=program_key,
                course_id=course_id,
                failure=expected_failure,
        ):
            response = self.get(
                path_fmt.format(program_key, course_id), self.hum_admin
            )
        self.assertEqual(response.status_code, 404)


class JobStatusRetrieveViewTests(S3MockMixin, RegistrarAPITestCase, AuthRequestMixin):
    """ Tests for GET /api/v1/jobs/{job_id} endpoint """
    method = 'GET'
    path = 'jobs/a6393974-cf86-4e3b-a21a-d27e17932447'
    event = 'registrar.v1.get_job_status'

    def test_successful_job(self):
        job_id = start_job(self.stem_admin, _succeeding_job)
        with self.assert_tracking(
                user=self.stem_admin,
                job_id=job_id,
                job_state='Succeeded',
        ):
            job_respose = self.get('jobs/' + job_id, self.stem_admin)
        self.assertEqual(job_respose.status_code, 200)

        job_status = job_respose.data
        self.assertIn('created', job_status)
        self.assertEqual(job_status['state'], 'Succeeded')
        self.assertIsNone(job_status['text'])
        result_url = job_status['result']
        self.assertIn("/job-results/{}.json?".format(job_id), result_url)

        file_response = requests.get(result_url)
        self.assertEqual(file_response.status_code, 200)
        json.loads(file_response.text)  # Make sure this doesn't raise an error

    @mock.patch('registrar.apps.core.jobs.logger', autospec=True)
    def test_failed_job(self, mock_jobs_logger):
        FAIL_MESSAGE = "everything is broken"
        job_id = start_job(self.stem_admin, _failing_job, FAIL_MESSAGE)
        with self.assert_tracking(
                user=self.stem_admin,
                job_id=job_id,
                job_state='Failed',
        ):
            job_respose = self.get('jobs/' + job_id, self.stem_admin)
        self.assertEqual(job_respose.status_code, 200)

        job_status = job_respose.data
        self.assertIn('created', job_status)
        self.assertEqual(job_status['state'], 'Failed')
        self.assertIsNone(job_status['text'])
        self.assertIsNone(job_status['result'])
        self.assertEqual(mock_jobs_logger.error.call_count, 1)

        error_logged = mock_jobs_logger.error.call_args_list[0][0][0]
        self.assertIn(job_id, error_logged)
        self.assertIn(FAIL_MESSAGE, error_logged)

    def test_job_permission_denied(self):
        job_id = start_job(self.stem_admin, _succeeding_job)
        with self.assert_tracking(
                user=self.hum_admin,
                job_id=job_id,
                missing_permissions=[perms.JOB_GLOBAL_READ],
        ):
            job_respose = self.get('jobs/' + job_id, self.hum_admin)
        self.assertEqual(job_respose.status_code, 403)

    def test_job_global_read_permission(self):
        job_id = start_job(self.stem_admin, _succeeding_job)
        assign_perm(JOB_GLOBAL_READ, self.hum_admin)
        with self.assert_tracking(
                user=self.hum_admin,
                job_id=job_id,
                job_state='Succeeded',
        ):
            job_respose = self.get('jobs/' + job_id, self.hum_admin)
        self.assertEqual(job_respose.status_code, 200)

    def test_job_does_not_exist(self):
        nonexistant_job_id = str(uuid.uuid4())
        with self.assert_tracking(
                user=self.stem_admin,
                job_id=nonexistant_job_id,
                failure='job_not_found',
        ):
            job_respose = self.get('jobs/' + nonexistant_job_id, self.stem_admin)
        self.assertEqual(job_respose.status_code, 404)


@shared_task(base=UserTask, bind=True)
def _succeeding_job(self, job_id, user_id, *args, **kwargs):  # pylint: disable=unused-argument
    """ A job that just succeeds, posting an empty JSON list as its result. """
    fake_data = Faker().pystruct(20, str, int, bool)
    post_job_success(job_id, json.dumps(fake_data), 'json')


@shared_task(base=UserTask, bind=True)
def _failing_job(self, job_id, user_id, fail_message, *args, **kwargs):  # pylint: disable=unused-argument
    """ A job that just fails, providing `fail_message` as its reason """
    post_job_failure(job_id, fail_message)


@ddt.ddt
class ProgramCourseEnrollmentWriteMixin:
    """ Test write requests to the /api/v1/programs/{program_key}/courses/{course_id}/enrollments/ endpoint """

    @classmethod
    def setUpTestData(cls):   # pylint: disable=missing-function-docstring
        super().setUpTestData()
        cls.course_run_keys = [
            ('course-v1:STEMx+CS111+F19', 'CompSci1_Fall'),
            ('course-v1:STEMx+CS222+JF19', 'CompSci2_Fall'),
            ('course-v1:STEMx+CS333+F19', 'CompSci3_Fall'),
            ('course-v1:STEMx+CS444+F19', 'CompSci4_Fall'),
        ]
        cls.curriculum = {'courses': [], 'is_active': True}
        for key, external_key in cls.course_run_keys:
            course = {'course_runs': [{'key': key, 'external_key': external_key}]}
            cls.curriculum['courses'].append(course)
        cls.curricula = [cls.curriculum]

        cls.program = cls.cs_program
        cls.program_uuid = cls.program.discovery_uuid
        cls.course_id = cls.course_run_keys[2][0]
        cls.external_course_key = cls.course_run_keys[2][1]
        cls.path = 'programs/masters-in-english/courses/{}/enrollments'.format(cls.course_id)
        cls.lms_request_url = urljoin(
            settings.LMS_BASE_URL, LMS_PROGRAM_COURSE_ENROLLMENTS_API_TPL
        ).format(cls.program_uuid, cls.course_id)

    def setUp(self):
        super().setUp()
        self._add_program_to_cache(self.cs_program, curricula=self.curricula)

    def get_url(self, program_key=None, course_id=None):
        """ Helper to determine the request URL for this test class. """
        kwargs = {
            'program_key': program_key or self.cs_program.key,
            'course_id': course_id or self.course_id,
        }
        return reverse('api:v1:program-course-enrollment', kwargs=kwargs)

    def mock_course_enrollments_response(self, method, expected_response, response_code=200):
        self.mock_api_response(self.lms_request_url, expected_response, method=method, response_code=response_code)

    def student_course_enrollment(self, status, student_key=None, course_staff=None):
        return {
            'status': status,
            'student_key': student_key or uuid.uuid4().hex[0:10],
            'course_staff': course_staff
        }

    def test_program_unauthorized_at_organization(self):
        req_data = [
            self.student_course_enrollment('active'),
        ]

        # The humanities admin can't access data from the CS program
        with self.assert_tracking(
                user=self.hum_admin,
                program_key=self.cs_program.key,
                course_id=self.course_id,
                missing_permissions=[perms.APIWriteEnrollmentsPermission],
        ):
            response = self.request(
                self.method, self.get_url(), self.hum_admin, req_data
            )
        self.assertEqual(response.status_code, 403)

    def test_program_insufficient_permissions(self):
        req_data = [
            self.student_course_enrollment('active'),
        ]
        # The STEM learner doesn't have sufficient permissions to enroll learners
        with self.assert_tracking(
                user=self.stem_user,
                program_key=self.cs_program.key,
                course_id=self.course_id,
                missing_permissions=[perms.APIWriteEnrollmentsPermission],
        ):
            response = self.request(
                self.method, self.get_url(), self.stem_user, req_data
            )
        self.assertEqual(response.status_code, 403)

    def test_mm_program_permission_denied(self):
        self._add_program_to_cache(self.cs_program, program_type='MicroMasters')
        req_data = [
            self.student_course_enrollment('active')
        ]
        response = self.request(
            self.method, self.get_url(), self.stem_user, req_data
        )
        self.assertEqual(response.status_code, 403)

    def test_program_not_found(self):
        req_data = [
            self.student_course_enrollment('active'),
        ]
        # this program just doesn't exist
        with self.assert_tracking(
                user=self.stem_admin,
                program_key='uan-salsa-dancing-with-sharks',
                course_id=self.course_id,
                failure='program_not_found',
        ):
            response = self.request(
                self.method,
                self.get_url(program_key='uan-salsa-dancing-with-sharks'),
                self.stem_admin,
                req_data,
            )
        self.assertEqual(response.status_code, 404)

    @mock_oauth_login
    @responses.activate
    def test_course_not_found(self):
        req_data = [
            self.student_course_enrollment('active'),
        ]
        not_in_program_course_key = 'course-v1:edX+DemoX+Demo_Course'
        with self.assert_tracking(
                user=self.stem_admin,
                program_key=self.program.key,
                course_id=not_in_program_course_key,
                failure='course_not_found',
        ):
            response = self.request(
                self.method,
                self.get_url(course_id=not_in_program_course_key),
                self.stem_admin,
                req_data,
            )
        self.assertEqual(response.status_code, 404)

    @mock_oauth_login
    @responses.activate
    @ddt.data(False, True)
    def test_successful_program_course_enrollment_write(self, use_external_course_key):
        course_id = self.external_course_key if use_external_course_key else self.course_id
        expected_lms_response = {
            '001': 'active',
            '002': 'active',
            '003': 'inactive'
        }
        self.mock_course_enrollments_response(self.method, expected_lms_response)

        req_data = [
            self.student_course_enrollment('active', '001'),
            self.student_course_enrollment('active', '002'),
            self.student_course_enrollment('inactive', '003'),
        ]

        with self.assert_tracking(
                user=self.stem_admin,
                program_key=self.cs_program.key,
                course_id=course_id,
        ):
            response = self.request(
                self.method, self.get_url(course_id=course_id), self.stem_admin, req_data
            )

        lms_request_body = json.loads(responses.calls[-1].request.body.decode('utf-8'))
        self.assertCountEqual(lms_request_body, [
            {
                'status': 'active',
                'student_key': '001',
                'course_staff': None,
            },
            {
                'status': 'active',
                'student_key': '002',
                'course_staff': None,
            },
            {
                'status': 'inactive',
                'student_key': '003',
                'course_staff': None,
            }
        ])
        self.assertEqual(response.status_code, 200)
        self.assertDictEqual(response.data, expected_lms_response)

    @mock_oauth_login
    @responses.activate
    @ddt.data(False, True)
    def test_successful_program_course_enrollment_write_with_course_staff(self, use_external_course_key):
        course_id = self.external_course_key if use_external_course_key else self.course_id
        expected_lms_response = {
            '001': 'active',
            '002': 'active',
            '003': 'inactive'
        }
        self.mock_course_enrollments_response(self.method, expected_lms_response)

        req_data = [
            self.student_course_enrollment('active', '001', True),
            self.student_course_enrollment('active', '002', False),
            self.student_course_enrollment('inactive', '003', True),
        ]

        with self.assert_tracking(
                user=self.stem_admin,
                program_key=self.cs_program.key,
                course_id=course_id,
        ):
            with get_waffle_flag('enable_course_role_management', True, [self.stem_admin_group]) as overrider:
                response = self.request(
                    self.method, self.get_url(course_id=course_id), self.stem_admin, req_data
                )

        lms_request_body = json.loads(responses.calls[-1].request.body.decode('utf-8'))
        self.assertCountEqual(lms_request_body, [
            {
                'status': 'active',
                'student_key': '001',
                'course_staff': True,
            },
            {
                'status': 'active',
                'student_key': '002',
                'course_staff': False,
            },
            {
                'status': 'inactive',
                'student_key': '003',
                'course_staff': True,
            }
        ])
        self.assertEqual(response.status_code, 200)
        self.assertDictEqual(response.data, expected_lms_response)

    @mock_oauth_login
    @responses.activate
    @ddt.data(False, True)
    def test_failed_program_course_enrollment_write_with_course_staff(self, use_external_course_key):
        course_id = self.external_course_key if use_external_course_key else self.course_id
        expected_lms_response = {
            '001': 'active',
            '002': 'active',
            '003': 'inactive'
        }
        self.mock_course_enrollments_response(self.method, expected_lms_response)

        req_data = [
            self.student_course_enrollment('active', '001', True),
            self.student_course_enrollment('active', '002', False),
            self.student_course_enrollment('inactive', '003', True),
        ]

        with self.assert_tracking(
                user=self.stem_admin,
                program_key=self.cs_program.key,
                course_id=course_id,
                status_code=403,
        ):
            with override_flag('enable_course_role_management', active=False):
                response = self.request(
                    self.method, self.get_url(course_id=course_id), self.stem_admin, req_data
                )

        # lms_request_body = json.loads(responses.calls[-1].request.body.decode('utf-8'))
        self.assertEqual(response.status_code, 403)

    @mock_oauth_login
    @responses.activate
    @ddt.data(False, True)
    def test_backend_unprocessable_response(self, use_external_course_key):
        course_id = self.external_course_key if use_external_course_key else self.course_id
        expected_lms_response = {
            '001': 'conflict',
            '002': 'conflict',
            '003': 'conflict'
        }
        self.mock_course_enrollments_response(self.method, expected_lms_response, response_code=422)
        req_data = [
            self.student_course_enrollment('active', '001'),
            self.student_course_enrollment('active', '002'),
            self.student_course_enrollment('inactive', '003'),
        ]
        with self.assert_tracking(
                user=self.stem_admin,
                program_key=self.cs_program.key,
                course_id=course_id,
                failure='unprocessable_entity',
        ):
            response = self.request(
                self.method, self.get_url(course_id=course_id), self.stem_admin, req_data
            )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.data, expected_lms_response)

    @mock_oauth_login
    @responses.activate
    @ddt.data(False, True)
    def test_backend_multi_status_response(self, use_external_course_key):
        course_id = self.external_course_key if use_external_course_key else self.course_id
        expected_lms_response = {
            '001': 'active',
            '002': 'active',
            '003': 'invalid-status'
        }
        self.mock_course_enrollments_response(self.method, expected_lms_response, response_code=207)

        req_data = [
            self.student_course_enrollment('active', '001'),
            self.student_course_enrollment('active', '002'),
            self.student_course_enrollment('not_a_valid_value', '003'),
        ]

        with self.assert_tracking(
                user=self.stem_admin,
                program_key=self.cs_program.key,
                course_id=course_id,
                status_code=207,
        ):
            response = self.request(
                self.method, self.get_url(course_id=course_id), self.stem_admin, req_data
            )

        self.assertEqual(response.status_code, 207)
        self.assertDictEqual(response.data, expected_lms_response)

    @mock_oauth_login
    @responses.activate
    @ddt.data(
        (500, 'Internal Server Error', True),
        (404, 'Not Found', False),
        (413, 'Payload Too Large', True),
    )
    @ddt.unpack
    def test_backend_request_failed(self, status_code, content, use_external_course_key):
        course_id = (
            self.external_course_key if use_external_course_key
            else self.course_id
        )
        self.mock_course_enrollments_response(
            self.method, content, response_code=status_code
        )
        req_data = [
            self.student_course_enrollment('active', '001'),
            self.student_course_enrollment('active', '002'),
            self.student_course_enrollment('inactive', '003'),
        ]
        expected_response_data = {
            '001': 'internal-error',
            '002': 'internal-error',
            '003': 'internal-error',
        }
        with self.assert_tracking(
                user=self.stem_admin,
                program_key=self.cs_program.key,
                course_id=course_id,
                failure='unprocessable_entity',
        ):
            response = self.request(
                self.method,
                self.get_url(course_id=course_id),
                self.stem_admin,
                req_data,
            )
        self.assertEqual(response.status_code, 422)
        self.assertDictEqual(response.data, expected_response_data)

    def test_write_enrollment_payload_limit(self):
        req_data = [self.student_course_enrollment('active')] * (ENROLLMENT_WRITE_MAX_SIZE + 1)

        with self.assert_tracking(
                user=self.stem_admin,
                program_key=self.cs_program.key,
                course_id=self.course_id,
                failure='request_entity_too_large',
        ):
            response = self.request(
                self.method, self.get_url(), self.stem_admin, req_data
            )

        self.assertEqual(response.status_code, 413)

    @ddt.data(
        (
            "this is a string",
            "expected request body type: List",
        ),
        (
            {"this is a": "dict"},
            "expected request body type: List",
        ),
        (
            ["this enrollment is a string"],
            "expected items in request to be of type Dict",
        ),
        (
            [None],
            "expected items in request to be of type Dict",
        ),
        (
            [{"this enrollment": "has no student key"}],
            'expected request dicts to have string value for "student_key"',
        ),
        (
            [{"student_key": None}],
            'expected request dicts to have string value for "student_key"',
        ),
        (
            [{"student_key": "bob-has-no-status"}],
            'expected request dicts to have string value for "status"',
        ),
        (
            [{"student_key": "bobs-status-is-not-a-string", "status": 1}],
            'expected request dicts to have string value for "status"',
        ),
    )
    @ddt.unpack
    def test_bad_request(self, payload, message):
        with self.assert_tracking(
                user=self.stem_admin,
                program_key=self.cs_program.key,
                course_id=self.course_id,
                failure='bad_request',
        ):
            response = self.request(
                self.method, self.get_url(), self.stem_admin, payload
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn(message, response.data)


class ProgramCourseEnrollmentPostTests(ProgramCourseEnrollmentWriteMixin, RegistrarAPITestCase, AuthRequestMixin):
    method = 'POST'
    event = 'registrar.v1.post_course_enrollment'


class ProgramCourseEnrollmentPatchTests(ProgramCourseEnrollmentWriteMixin, RegistrarAPITestCase, AuthRequestMixin):
    method = 'PATCH'
    event = 'registrar.v1.patch_course_enrollment'


class JobStatusListView(S3MockMixin, RegistrarAPITestCase, AuthRequestMixin):
    """ Tests for GET /api/v1/jobs/ endpoint """
    method = 'GET'
    path = 'jobs/'
    event = 'registrar.v1.list_job_statuses'

    job_ids = [
        '8dd98f5e-f7af-4b7d-a2b6-a44a596e8267',
        '98ac44f8-1e1e-4715-8e7d-e1aa0b8ebe9f',
        '38040bf8-7384-4ffc-8e5e-a0ec862ba280',
        '59b883ac-cabb-4c13-864d-8e822f607690',
        '8295253b-7483-46ec-8d5a-f53ba21d28c2',
        '04d749a8-52ca-4008-8e06-95396db43810',
        'c95c7bda-b40e-4503-95b8-4b3082f10762',
        'ab3ef797-3413-41d2-971d-c5859d0b6904',
        'ba7eee39-3693-4875-966e-829210cac60f',
        '1b8b376c-fcf9-4532-a1c6-90a04c1b4663',
        'abcac34d-ad16-47e8-968d-ef84613577fb',
        '4a4405f7-7e1c-4087-b36f-f8ab9a8ff920',
        'f91bd181-6a1f-4055-9793-5db2aed07ba9',
    ]

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

        # cls.edx_admin has some processing tasks and some not processing
        cls.edx_admin_jobs = [
            cls.create_dummy_job_status(0, cls.edx_admin, UserTaskStatus.PENDING),
            cls.create_dummy_job_status(1, cls.edx_admin, UserTaskStatus.CANCELED),
            cls.create_dummy_job_status(2, cls.edx_admin, UserTaskStatus.IN_PROGRESS),
            cls.create_dummy_job_status(3, cls.edx_admin, UserTaskStatus.FAILED),
            cls.create_dummy_job_status(4, cls.edx_admin, UserTaskStatus.SUCCEEDED),
        ]
        # cls.stem_admin has tasks, all processing
        cls.stem_admin_jobs = [
            cls.create_dummy_job_status(5, cls.stem_admin, UserTaskStatus.IN_PROGRESS),
            cls.create_dummy_job_status(6, cls.stem_admin, UserTaskStatus.PENDING),
            cls.create_dummy_job_status(7, cls.stem_admin, UserTaskStatus.IN_PROGRESS),
            cls.create_dummy_job_status(8, cls.stem_admin, UserTaskStatus.RETRYING),
        ]
        # cls.stem_user has tasks, none processing
        cls.stem_user_jobs = [
            cls.create_dummy_job_status(9, cls.stem_user, UserTaskStatus.SUCCEEDED),
            cls.create_dummy_job_status(10, cls.stem_user, UserTaskStatus.SUCCEEDED),
            cls.create_dummy_job_status(11, cls.stem_user, UserTaskStatus.FAILED),
            cls.create_dummy_job_status(12, cls.stem_user, UserTaskStatus.CANCELED),
        ]
        # cls.hum_admin has no tasks

    @classmethod
    def create_dummy_job_status(cls, n, user, state):
        """
        Create dummy job status in the database, and return
        serialized version of it.
        """
        task_status = UserTaskStatus.objects.create(
            state=state,
            user=user,
            task_id=cls.job_ids[n],
            total_steps=1,
        )
        return {
            'state': state,
            'name': task_status.name,
            'job_id': cls.job_ids[n],
            'created': task_status.created.isoformat().replace('+00:00', 'Z'),
            'result': None,
            'text': None,
        }

    def test_some_jobs_processing(self):
        self._test_list_job_statuses(
            self.edx_admin,
            [self.edx_admin_jobs[0], self.edx_admin_jobs[2]],
        )

    def test_all_jobs_processing(self):
        self._test_list_job_statuses(self.stem_admin, self.stem_admin_jobs)

    def test_no_jobs_processing(self):
        self._test_list_job_statuses(self.stem_user, [])

    def test_no_jobs(self):
        self._test_list_job_statuses(self.hum_admin, [])

    def _test_list_job_statuses(self, user, expected_response):
        response = self.get(self.path, user)
        self.assertEqual(response.status_code, 200)
        self.assertCountEqual(response.data, expected_response)


class EnrollmentUploadMixin:
    """ Test CSV upload endpoints """
    method = 'POST'

    @classmethod
    def setUpTestData(cls):   # pylint: disable=missing-function-docstring
        super().setUpTestData()

        program_uuid = cls.cs_program.discovery_uuid
        cls.mock_program_details = {
            'curricula': [
                {'uuid': INACTIVE_CURRICULUM_UUID, 'is_active': False},
                {'uuid': ACTIVE_CURRICULUM_UUID, 'is_active': True}
            ],
            'type': 'Masters',
        }
        cls.lms_request_url = urljoin(
            settings.LMS_BASE_URL, 'api/program_enrollments/v1/programs/{}/enrollments/'
        ).format(program_uuid)

    def _upload_enrollments(self, enrollments, user=None):   # pylint: disable=missing-function-docstring
        upload_file = StringIO(
            serialize_to_csv(enrollments, self.csv_headers, include_headers=True)
        )
        if not user:
            user = self.stem_admin

        with patch_discovery_program_details(self.mock_program_details):
            return self.request(
                self.method,
                self.path,
                user,
                file=upload_file
            )

    def test_unauthorized_at_organization(self):
        enrollments = [
            self.build_enrollment('enrolled', '001'),
        ]

        with self.assert_tracking(
                user=self.hum_admin,
                program_key='masters-in-cs',
                missing_permissions=[perms.APIWriteEnrollmentsPermission],
        ):
            response = self._upload_enrollments(enrollments, user=self.hum_admin)
        self.assertEqual(response.status_code, 403)

    def test_insufficient_permissions(self):
        enrollments = [
            self.build_enrollment('enrolled', '001'),
        ]
        with self.assert_tracking(
                user=self.stem_user,
                program_key='masters-in-cs',
                missing_permissions=[perms.APIWriteEnrollmentsPermission],
        ):
            response = self._upload_enrollments(enrollments, user=self.stem_user)
        self.assertEqual(response.status_code, 403)

    def test_enrollment_upload_mm_program(self):
        self.mock_program_details = {
            'curricula': [{'uuid': ACTIVE_CURRICULUM_UUID, 'is_active': True}],
            'type': 'MicroMasters',
        }
        enrollments = [
            self.build_enrollment('enrolled', '001')
        ]

        upload_response = self._upload_enrollments(enrollments)
        self.assertEqual(upload_response.status_code, 403)

    @mock.patch.object(ProgramEnrollmentUploadView, 'task_fn', _succeeding_job)
    @mock.patch.object(CourseRunEnrollmentUploadView, 'task_fn', _succeeding_job)
    def test_enrollment_upload_success(self):
        enrollments = [
            self.build_enrollment('enrolled', '001'),
            self.build_enrollment('pending', '002'),
            self.build_enrollment('enrolled', '003'),
        ]

        with self.assert_tracking(
                user=self.stem_admin,
                program_key=self.cs_program.key,
                status_code=202
        ):
            upload_response = self._upload_enrollments(enrollments)

        self.assertEqual(upload_response.status_code, 202)

        job_response = self.get(upload_response.data['job_url'], self.stem_admin)
        self.assertEqual(job_response.status_code, 200)
        self.assertEqual(job_response.data['state'], 'Succeeded')

        filestore = get_enrollment_uploads_filestore()
        retrieved = filestore.retrieve('/{}/{}.json'.format(
            'uploads',
            upload_response.data['job_id'],
        ))
        self.assertEqual(json.loads(retrieved), enrollments)

    def test_enrollment_upload_conflict(self):
        enrollments = [self.build_enrollment('enrolled', '001')]

        with mock.patch(
                'registrar.apps.api.v1.views.is_enrollment_write_blocked',
                return_value=True
        ):
            upload_response = self._upload_enrollments(enrollments)

        self.assertEqual(upload_response.status_code, 409)

    def test_enrollment_upload_invalid_header(self):
        enrollment = self.build_enrollment('enrolled', '001')
        enrollment['something'] = enrollment.pop('student_key')
        enrollments = [enrollment]
        self.csv_headers = ('something' if field == 'student_key' else field for field in self.csv_headers)
        upload_response = self._upload_enrollments(enrollments)

        self.assertEqual(upload_response.status_code, 400)

    def test_enrollment_upload_missing_data(self):
        enrollments = [
            self.build_enrollment('enrolled', '001'),
            {'student_key': '002'}
        ]
        upload_response = self._upload_enrollments(enrollments)

        self.assertEqual(upload_response.status_code, 400)

    def test_unmatched_row_data(self):
        upload_file = StringIO()
        writer = csv.writer(upload_file, delimiter=',', quotechar='"', quoting=csv.QUOTE_MINIMAL)
        writer.writerow(list(self.csv_headers))
        writer.writerow(['001', 'enrolled', 'foo', 'bar'])
        upload_file.seek(0)

        with patch_discovery_program_details(self.mock_program_details):
            upload_response = self.request(
                self.method,
                self.path,
                self.stem_admin,
                file=upload_file
            )
        self.assertEqual(upload_response.status_code, 400)

    def test_enrollment_upload_too_large(self):
        enrollments = [
            self.build_enrollment('pending', str(n)) for n in range(1000)
        ]

        with mock.patch('registrar.apps.api.v1.views.UPLOAD_FILE_MAX_SIZE', 1024):
            upload_response = self._upload_enrollments(enrollments)

        self.assertEqual(upload_response.status_code, 413)

    def test_no_file(self):
        with patch_discovery_program_details(self.mock_program_details):
            upload_response = self.request(
                self.method,
                self.path,
                self.stem_admin,
                file=None
            )
        self.assertEqual(upload_response.status_code, 400)

    def test_extra_columns(self):
        enrollment = self.build_enrollment('enrolled', '001')
        enrollment['blood_type'] = 'AB-'
        enrollments = [enrollment]
        self.csv_headers = self.csv_headers + ('blood_type',)
        with patch_discovery_program_details(self.mock_program_details):
            upload_response = self._upload_enrollments(enrollments)

        self.assertEqual(202, upload_response.status_code)
        self.assertIn('job_id', upload_response.data)
        self.assertIn('job_url', upload_response.data)


class ProgramEnrollmentUploadTest(EnrollmentUploadMixin, S3MockMixin, RegistrarAPITestCase, AuthRequestMixin):
    """ Test program enrollment csv upload """
    path = 'programs/masters-in-cs/enrollments/upload/'
    event = 'registrar.v1.upload_program_enrollments'
    csv_headers = ('student_key', 'status')

    def build_enrollment(self, status, student_key=None):
        return {
            'status': status,
            'student_key': student_key or uuid.uuid4().hex[0:10]
        }


class CourseEnrollmentUploadTest(EnrollmentUploadMixin, S3MockMixin, RegistrarAPITestCase, AuthRequestMixin):
    """ Test course enrollment csv upload """
    path = 'programs/masters-in-cs/course_enrollments/upload/'
    event = 'registrar.v1.upload_course_enrollments'
    csv_headers = ('student_key', 'course_id', 'status')

    def build_enrollment(self, status, student_key=None, course_id=None):
        return {
            'status': status,
            'student_key': student_key or uuid.uuid4().hex[0:10],
            'course_id': course_id or uuid.uuid4().hex[0:10],
        }


@ddt.ddt
class CourseEnrollmentDownloadTest(S3MockMixin, RegistrarAPITestCase, AuthRequestMixin):
    """ Tests for GET /api/v1/programs/{program_key}/course_enrollments endpoint """
    method = 'GET'
    path = 'programs/masters-in-english/course_enrollments'
    event = 'registrar.v1.download_course_enrollments'

    program_uuid = str(uuid.uuid4())
    english_key = 'HUMx+English-550+Spring'
    spanish_key = 'HUMx+Spanish-1000+Spring'
    russian_key = 'HUMx+Russian-440+Spring'
    french_key = 'HUMx+French-9910+Spring'
    course_runs = [
        {
            "key": english_key,
            "external_key": "ENG55-S19",
            "title": "English 550",
            "marketing_url": "https://example.com/english-550",
        },
        {
            "key": spanish_key,
            "external_key": "SPAN101-S19",
            "title": "Spanish 101",
            "marketing_url": "https://example.com/spanish-101",
        },
        {
            "key": russian_key,
            "external_key": "RUS44-S19",
            "title": "Russian 440",
            "marketing_url": "https://example.com/russian-440",
        },
        {
            "key": french_key,
            "external_key": None,
            "title": "French 9910",
            "marketing_url": "https://example.com/french-9910",
        },
    ]
    mock_program_details = {
        'curricula': [{
            'is_active': True,
            'courses': [{'course_runs': course_runs}],
        }],
        'type': "Masters",
    }
    english_enrollments = [
        {
            'course_id': 'ENG55-S19',
            'student_key': 'learner-01',
            'status': 'enrolled',
            'account_exists': True,
        },
        {
            'course_id': 'ENG55-S19',
            'student_key': 'learner-02',
            'status': 'pending',
            'account_exists': False,
        },
    ]
    spanish_enrollments = [
        {
            'course_id': 'SPAN101-S19',
            'student_key': 'learner-01',
            'status': 'enrolled',
            'account_exists': True,
        },
        {
            'course_id': 'SPAN101-S19',
            'student_key': 'learner-03',
            'status': 'pending',
            'account_exists': False,
        },
    ]
    russian_enrollments = []
    french_enrollments = [
        {
            'course_id': 'HUMx+French-9910+Spring',
            'student_key': 'learner-01',
            'status': 'enrolled',
            'account_exists': True,
        },
        {
            'course_id': 'HUMx+French-9910+Spring',
            'student_key': 'learner-04',
            'status': 'pending',
            'account_exists': False,
        },
    ]

    enrollments_by_key = {
        english_key: english_enrollments,
        spanish_key: spanish_enrollments,
        russian_key: russian_enrollments,
        french_key: french_enrollments,
    }

    all_enrollments = english_enrollments + spanish_enrollments + russian_enrollments + french_enrollments
    enrollments_json = json.dumps(all_enrollments, indent=4)
    enrollments_csv = (
        "course_id,student_key,status,account_exists\r\n"
        "ENG55-S19,learner-01,enrolled,True\r\n"
        "ENG55-S19,learner-02,pending,False\r\n"
        "SPAN101-S19,learner-01,enrolled,True\r\n"
        "SPAN101-S19,learner-03,pending,False\r\n"
        "HUMx+French-9910+Spring,learner-01,enrolled,True\r\n"
        "HUMx+French-9910+Spring,learner-04,pending,False\r\n"
    )

    # pylint: disable=unused-argument
    def spoof_get_course_run_enrollments(
        self, program_uuid, internal_course_key, external_course_key=None, client=None
    ):
        return self.enrollments_by_key.get(internal_course_key)

    @patch_discovery_program_details(mock_program_details)
    @mock.patch.object(lms, 'get_course_run_enrollments')
    @ddt.data(
        (None, 'json', enrollments_json),
        ('json', 'json', enrollments_json),
        ('csv', 'csv', enrollments_csv),
    )
    @ddt.unpack
    def test_ok(self, format_param, expected_format, expected_contents, mock_get_enrollments):
        mock_get_enrollments.side_effect = self.spoof_get_course_run_enrollments
        format_suffix = "?fmt=" + format_param if format_param else ""
        kwargs = {'result_format': format_param} if format_param else {}
        with self.assert_tracking(
                user=self.hum_admin,
                program_key='masters-in-english',
                status_code=202,
                **kwargs
        ):
            response = self.get(self.path + format_suffix, self.hum_admin)
        self.assertEqual(response.status_code, 202)
        with self.assert_tracking(
                event='registrar.v1.get_job_status',
                user=self.hum_admin,
                job_id=response.data['job_id'],
                job_state='Succeeded',
        ):
            job_response = self.get(response.data['job_url'], self.hum_admin)
        self.assertEqual(job_response.status_code, 200)
        self.assertEqual(job_response.data['state'], 'Succeeded')

        result_url = job_response.data['result']
        self.assertIn(".{}?".format(expected_format), result_url)
        file_response = requests.get(result_url)
        self.assertEqual(file_response.status_code, 200)
        self.assertEqual(file_response.text, expected_contents)

    def test_permission_denied(self):
        with self.assert_tracking(
                user=self.stem_admin,
                program_key='masters-in-english',
                missing_permissions=[perms.APIReadEnrollmentsPermission],
        ):
            response = self.get(self.path, self.stem_admin)
        self.assertEqual(response.status_code, 403)

    def test_program_not_found(self):
        with self.assert_tracking(
                user=self.hum_admin,
                program_key='masters-in-polysci',
                failure='program_not_found',
        ):
            response = self.get('programs/masters-in-polysci/course_enrollments', self.hum_admin)
        self.assertEqual(response.status_code, 404)

    @patch_discovery_program_details(mock_program_details)
    def test_invalid_format_404(self):
        with self.assert_tracking(
                user=self.hum_admin,
                program_key='masters-in-english',
                failure='result_format_not_supported',
                result_format='invalidformat',
                status_code=404,
        ):
            response = self.get(self.path + '?fmt=invalidformat', self.hum_admin)
        self.assertEqual(response.status_code, 404)


@ddt.ddt
class CourseGradeViewTest(S3MockMixin, RegistrarAPITestCase, AuthRequestMixin):
    """ Tests for GET /api/v1/programs/{program_key}/courses/{course_id}/grades endpoint """
    method = 'GET'
    path = 'programs/masters-in-english/courses/HUMx+English-550+Spring/grades'
    event = 'registrar.v1.get_course_grades'

    mock_program_details = {
        'curricula': [{
            'is_active': True,
            'courses': [{
                'course_runs': [{
                    'key': 'HUMx+English-550+Spring',
                    'external_key': 'ENG55-S19',
                    'title': "English 550",
                    'marketing_url': 'https://example.com/english-550',
                }]
            }]
        }],
    }

    grades = [
        {
            'student_key': 'learner-01',
            'letter_grade': 'A',
            'percent': 0.95,
            'passed': True,
        },
        {
            'student_key': 'learner-02',
            'letter_grade': 'F',
            'percent': 0.4,
            'passed': False,
        },
        {
            'student_key': 'learner-03',
            'error': 'error loading grades',
        }
    ]
    grades_json = json.dumps(grades, indent=4)
    grades_csv = (
        "student_key,letter_grade,percent,passed,error\r\n"
        "learner-01,A,0.95,True,\r\n"
        "learner-02,F,0.4,False,\r\n"
        "learner-03,,,,error loading grades\r\n"
    )

    @patch_discovery_program_details(mock_program_details)
    @mock.patch(
        'registrar.apps.grades.lms_interop.get_course_run_grades',
        return_value=(True, False, grades),
    )
    @ddt.data(
        (None, 'json', grades_json),
        ('json', 'json', grades_json),
        ('csv', 'csv', grades_csv),
    )
    @ddt.unpack
    def test_ok(self, format_param, expected_format, expected_contents, _grades_mock):
        format_suffix = "?fmt=" + format_param if format_param else ""
        kwargs = {'result_format': format_param} if format_param else {}
        with self.assert_tracking(
                user=self.hum_admin,
                program_key='masters-in-english',
                course_id='HUMx+English-550+Spring',
                status_code=202,
                **kwargs
        ):
            response = self.get(self.path + format_suffix, self.hum_admin)
        self.assertEqual(response.status_code, 202)
        with self.assert_tracking(
                event='registrar.v1.get_job_status',
                user=self.hum_admin,
                job_id=response.data['job_id'],
                job_state='Succeeded',
        ):
            job_response = self.get(response.data['job_url'], self.hum_admin)
        self.assertEqual(job_response.status_code, 200)
        self.assertEqual(job_response.data['state'], 'Succeeded')

        result_url = job_response.data['result']
        self.assertIn(".{}?".format(expected_format), result_url)
        file_response = requests.get(result_url)
        self.assertEqual(file_response.status_code, 200)
        self.assertEqual(file_response.text, expected_contents)

    @patch_discovery_program_details(mock_program_details)
    @mock.patch('registrar.apps.grades.lms_interop.get_course_run_grades')
    @ddt.data(
        (True, True, GradeReadStatus.MULTI_STATUS.value),
        (True, False, GradeReadStatus.OK.value),
        (False, True, GradeReadStatus.UNPROCESSABLE_ENTITY.value),
        (False, False, GradeReadStatus.NO_CONTENT.value),
    )
    @ddt.unpack
    def test_text(self, good, bad, expected_text, grades_mock):
        grades_mock.return_value = (good, bad, self.grades)
        response = self.get(self.path, self.hum_admin)
        self.assertEqual(response.status_code, 202)
        job_response = self.get(response.data['job_url'], self.hum_admin)
        self.assertEqual(job_response.status_code, 200)
        self.assertEqual(job_response.data['state'], 'Succeeded')
        self.assertEqual(job_response.data['text'], str(expected_text))

    @patch_discovery_program_details(mock_program_details)
    def test_permission_denied(self):
        with self.assert_tracking(
                user=self.stem_admin,
                program_key='masters-in-english',
                course_id='HUMx+English-550+Spring',
                missing_permissions=[perms.APIReadEnrollmentsPermission],
        ):
            response = self.get(self.path, self.stem_admin)
        self.assertEqual(response.status_code, 403)

    @patch_discovery_program_details(mock_program_details)
    @ddt.data(
        # Bad program
        ('masters-in-polysci', 'course-v1:HUMx+English-550+Spring', 'program_not_found'),
        # Good program, course key formatted correctly, but course does not exist
        ('masters-in-english', 'course-v1:STEMx+Biology-440+Fall', 'course_not_found'),
        # Good program, course key matches URL but is formatted incorrectly
        ('masters-in-english', 'not-a-course-key:a+b+c', 'course_not_found'),
    )
    @ddt.unpack
    def test_not_found(self, program_key, course_id, expected_failure):
        path_fmt = 'programs/{}/courses/{}/grades'
        with self.assert_tracking(
                user=self.hum_admin,
                program_key=program_key,
                course_id=course_id,
                failure=expected_failure,
        ):
            response = self.get(
                path_fmt.format(program_key, course_id), self.hum_admin
            )
        self.assertEqual(response.status_code, 404)


# the value returned by the get_url method depends on the time, so freeze time to ensure equality between
# calls to get_url
@freeze_time('2020-01-08')
class ReportsListViewTest(S3MockMixin, RegistrarAPITestCase, AuthRequestMixin):
    """
    Tests for GET /api/v1/programs/{program_key}/reports endpoint
    """
    method = 'GET'
    path = 'programs/masters-in-english/reports'
    s3_bucket = settings.PROGRAM_REPORTS_BUCKET

    @classmethod
    def uuid_hexify(cls, uuid_string):
        return uuid_string.replace('-', '')

    @classmethod
    def tearDown(cls):
        super().tearDown(cls)
        filestore = get_program_reports_filestore()
        files = filestore.list('{}/{}'.format(cls.hum_org.key, cls.uuid_hexify(cls.english_program.discovery_uuid)))

        for file in files[1]:
            file_path = '{}/{}/{}'.format(cls.hum_org.key, cls.uuid_hexify(cls.english_program.discovery_uuid), file)
            filestore.delete(file_path)

    def test_ok(self):
        files = [
            {
                'name': 'aggregate_report__2019-12-18',
                'created_date': '2019-12-18',
            },
            {
                'name': 'individual_report__2019-12-17',
                'created_date': '2019-12-17',
            },
            {
                'name': 'individual_report__2019-12-18.csv',
                'created_date': '2019-12-18',
            },
        ]

        file_prefix = '{}/{}'.format(self.hum_org.key, self.uuid_hexify(self.english_program.discovery_uuid))
        filestore = get_program_reports_filestore()
        for file in files:
            filestore.store(
                '{}/{}'.format(file_prefix, file['name']),
                'data'
            )

        expected_data = [
            {
                'name': file['name'],
                'created_date': file['created_date'],
                'download_url': filestore.get_url('{}/{}'.format(file_prefix, file['name'])),
            } for file in files
        ]
        response = self.get(self.path, self.hum_admin)
        self.assertEqual(response.data, expected_data)

    def test_min_created_date_query_parameter(self):
        files = [
            {
                'name': 'aggregate_report__2019-12-17',
                'created_date': '2019-12-17',
            },
            {
                'name': 'aggregate_report__2019-12-18',
                'created_date': '2019-12-18',
            },
            {
                'name': 'individual_report__2019-12-17',
                'created_date': '2019-12-17',
            },
            {
                'name': 'individual_report__2019-12-18',
                'created_date': '2019-12-18',
            },
        ]

        file_prefix = '{}/{}'.format(self.hum_org.key, self.uuid_hexify(self.english_program.discovery_uuid))
        filestore = get_program_reports_filestore()
        for file in files:
            filestore.store(
                '{}/{}'.format(file_prefix, file['name']),
                'data',
            )

        expected_data = [
            {
                'name': 'aggregate_report__2019-12-18',
                'created_date': '2019-12-18',
                'download_url': filestore.get_url('{}/{}'.format(file_prefix, 'aggregate_report__2019-12-18')),
            },
            {
                'name': 'individual_report__2019-12-18',
                'created_date': '2019-12-18',
                'download_url': filestore.get_url('{}/{}'.format(file_prefix, 'individual_report__2019-12-18')),
            },
        ]

        response = self.get(self.path + '?min_created_date=2019-12-18', self.hum_admin)
        self.assertEqual(response.data, expected_data)

    def test_min_created_date_parameter_invalid_date(self):
        filestore = get_program_reports_filestore()
        filestore.store(
            '{}/{}/individual_report_1'.format(self.hum_org.key, self.uuid_hexify(self.english_program.discovery_uuid)),
            'data',
        )

        response = self.get(self.path + '?min_created_date=2019-12-18', self.hum_admin)
        self.assertEqual(response.data, [])

    def test_filename_misformatted(self):
        """
        Test that reports with malformed names and/or invalid dates are ignored,
        but reports with well-formed names with valid dates are still returned.
        """
        files = [
            'aggregate_report_1',  # Malformed
            'aggregate_report__2019-25-25',  # Invalid date
            'individual_report_1',  # Malformed
            'individual_report__2019-12-18',  # Well-formed
        ]
        file_prefix = '{}/{}'.format(self.hum_org.key, self.uuid_hexify(self.english_program.discovery_uuid))
        filestore = get_program_reports_filestore()
        for file in files:
            filestore.store(
                '{}/{}'.format(file_prefix, file),
                'data',
            )
        expected_data = [
            {
                'name': 'individual_report__2019-12-18',
                'created_date': '2019-12-18',
                'download_url': filestore.get_url(
                    '{file_prefix}/individual_report__2019-12-18'.format(
                        file_prefix=file_prefix
                    ),
                ),
            }
        ]
        response = self.get(self.path, self.hum_admin)
        self.assertEqual(response.data, expected_data)

    def test_get_program_list_unauthorized(self):
        response = self.get(self.path, self.stem_admin)
        self.assertEqual(response.status_code, 403)
