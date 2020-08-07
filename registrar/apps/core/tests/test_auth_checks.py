"""
Tests for authorization-realted checks.
"""
import ddt
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.test import TestCase
from guardian.shortcuts import assign_perm

from ..auth_checks import get_api_permissions_by_program, get_programs_by_api_permission, get_user_organizations
from ..constants import PROGRAM_CACHE_KEY_TPL
from ..models import Organization
from ..permissions import (
    API_READ_ENROLLMENTS,
    API_READ_METADATA,
    API_READ_REPORTS,
    API_WRITE_ENROLLMENTS,
    ORGANIZATION_READ_ENROLLMENTS,
    ORGANIZATION_READ_METADATA,
    ORGANIZATION_WRITE_ENROLLMENTS,
    PROGRAM_READ_ENROLLMENTS,
    PROGRAM_READ_METADATA,
    PROGRAM_WRITE_ENROLLMENTS,
    OrganizationReadEnrollmentsRole,
    OrganizationReadMetadataRole,
    OrganizationReadReportRole,
    OrganizationReadWriteEnrollmentsRole,
    ProgramReadEnrollmentsRole,
    ProgramReadMetadataRole,
    ProgramReadReportRole,
)
from .factories import (
    GroupFactory,
    OrganizationFactory,
    OrganizationGroupFactory,
    ProgramFactory,
    ProgramOrganizationGroupFactory,
    UserFactory,
)


@ddt.ddt
class GetUserOrganizationsTests(TestCase):
    """ Tests for get_user_organizations """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        org1 = OrganizationFactory(key='org1')
        OrganizationGroupFactory(
            organization=org1, name='org1a'
        )
        OrganizationGroupFactory(
            organization=org1, name='org1b'
        )
        org2 = OrganizationFactory(key='org2')
        OrganizationGroupFactory(
            organization=org2, name='org2a'
        )
        OrganizationGroupFactory(
            organization=org2, name='org2b'
        )
        OrganizationFactory(key='org3')
        GroupFactory(name='normal_group')

    @ddt.data(
        ({'org1a', 'org1b', 'org2a'}, {'org1', 'org2'}),
        ({'org2a', 'org2b', 'normal_group'}, {'org2'}),
        ({'normal_group'}, set()),
        (set(), set()),
    )
    @ddt.unpack
    def test_get_user_organizations(self, group_names, expected_org_keys):
        groups = [Group.objects.get(name=name) for name in group_names]
        user = UserFactory(groups=groups)
        with self.assertNumQueries(1):
            orgs = get_user_organizations(user)
        org_keys = {org.key for org in orgs}
        self.assertEqual(org_keys, expected_org_keys)


@ddt.ddt
class GetProgramsByAPIPermissionsTests(TestCase):
    """
    Tests for get_programs_by_api_permission
    """
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.org1 = OrganizationFactory(
            key='org1',
        )
        cls.org2 = OrganizationFactory(
            key='org2',
        )
        cls.masters1a = ProgramFactory(
            key='masters1a',
            managing_organization=cls.org1,
        )
        cls.masters1b = ProgramFactory(
            key='masters1b',
            managing_organization=cls.org1,
        )
        cls.micromasters1 = ProgramFactory(
            key='micromasters1',
            managing_organization=cls.org1,
        )
        cls.masters2 = ProgramFactory(
            key='masters2',
            managing_organization=cls.org2,
        )
        cls.micromasters2 = ProgramFactory(
            key='micromasters2',
            managing_organization=cls.org2,
        )
        cls.global_read_program_enrollments = GroupFactory(
            name='global_read_program_enrollments',
            permissions=ProgramReadEnrollmentsRole.permissions,
        )
        cls.global_read_organization_reports = GroupFactory(
            name='global_read_organization_reports',
            permissions=OrganizationReadReportRole.permissions,
        )
        OrganizationGroupFactory(
            name='org1_read_metadata',
            organization=cls.org1,
            role=OrganizationReadMetadataRole.name,
        )
        OrganizationGroupFactory(
            name='org1_read_enrollments',
            organization=cls.org1,
            role=OrganizationReadEnrollmentsRole.name,
        )
        OrganizationGroupFactory(
            name='org1_read_reports',
            organization=cls.org1,
            role=OrganizationReadReportRole.name,
        )
        OrganizationGroupFactory(
            name='org2_read_metadata',
            organization=cls.org2,
            role=OrganizationReadMetadataRole.name,
        )
        OrganizationGroupFactory(
            name='org2_read_enrollments',
            organization=cls.org2,
            role=OrganizationReadEnrollmentsRole.name,
        )
        OrganizationGroupFactory(
            name='org2_read_reports',
            organization=cls.org2,
            role=OrganizationReadReportRole.name,
        )
        ProgramOrganizationGroupFactory(
            name='masters1a_read_metadata',
            program=cls.masters1a,
            granting_organization=cls.org1,
            role=ProgramReadMetadataRole.name,
        )
        ProgramOrganizationGroupFactory(
            name='masters1a_read_enrollments',
            program=cls.masters1a,
            granting_organization=cls.org1,
            role=ProgramReadEnrollmentsRole.name,
        )
        ProgramOrganizationGroupFactory(
            name='masters1a_read_reports',
            program=cls.masters1a,
            granting_organization=cls.org1,
            role=ProgramReadReportRole.name,
        )
        ProgramOrganizationGroupFactory(
            name='micromasters2_read_metadata',
            program=cls.micromasters2,
            granting_organization=cls.org2,
            role=ProgramReadMetadataRole.name,
        )
        ProgramOrganizationGroupFactory(
            name='micromasters2_read_enrollments',
            program=cls.micromasters2,
            granting_organization=cls.org2,
            role=ProgramReadEnrollmentsRole.name,
        )
        ProgramOrganizationGroupFactory(
            name='micromasters2_read_reports',
            program=cls.micromasters2,
            granting_organization=cls.org2,
            role=ProgramReadReportRole.name,
        )
        cls.programs = [
            cls.masters1a,
            cls.masters1b,
            cls.micromasters1,
            cls.masters2,
            cls.micromasters2,
        ]

    def setUp(self):
        super().setUp()
        for program in self.programs:
            program_type = (
                'Masters'
                if program.key.startswith('masters')  # pylint: disable=no-member
                else 'MicroMasters'
            )
            cache.set(
                PROGRAM_CACHE_KEY_TPL.format(uuid=program.discovery_uuid),
                {'type': program_type},
            )

    @ddt.data(
        {
            # 1:
            # No groups ->
            # No program access.
            'group_names': set(),
            'required_api_permission': API_READ_METADATA,
            'expected_program_keys': set(),
            'expected_query_count': 4,
        },
        {
            # 2:
            # No groups + program filter ->
            # Still no program access.
            'group_names': set(),
            'required_api_permission': API_READ_METADATA,
            'expected_program_keys': set(),
            'filter_organization_key': 'org1',
            'expected_query_count': 5,
        },
        {
            # 3:
            # In a group for one org, but applying a filter for the other org ->
            # No programs.
            'group_names': {'org1_read_metadata'},
            'required_api_permission': API_READ_REPORTS,
            'expected_program_keys': set(),
            'filter_organization_key': 'org2',
            'expected_query_count': 5,
        },
        {
            # 4:
            # In a metadata org group, but requiring reports permission ->
            # No programs.
            'group_names': {'org1_read_metadata'},
            'required_api_permission': API_READ_REPORTS,
            'expected_program_keys': set(),
            'expected_query_count': 4,
        },
        {
            # 5:
            # In a reports org group and requiring reports permission ->
            # Access that org's programs.
            'group_names': {'org1_read_reports'},
            'required_api_permission': API_READ_REPORTS,
            'expected_program_keys': {'masters1a', 'masters1b', 'micromasters1'},
            'expected_query_count': 4,
        },
        {
            # 6:
            # In redundant org groups ->
            # Same access granted.
            'group_names': {'org1_read_reports', 'org1_read_metadata'},
            'required_api_permission': API_READ_REPORTS,
            'expected_program_keys': {'masters1a', 'masters1b', 'micromasters1'},
            'expected_query_count': 4,
        },
        {
            # 7:
            # In a reports group for one org and a metadata group for another org,
            # and requiring reports access ->
            # Access to former org's programs.
            'group_names': {'org1_read_reports', 'org2_read_metadata'},
            'required_api_permission': API_READ_REPORTS,
            'expected_program_keys': {'masters1a', 'masters1b', 'micromasters1'},
            'expected_query_count': 4,
        },
        {
            # 8:
            # In a metadata group for one org and a reports group for another org,
            # requiring metadata access, and filtering by the latter org ->
            # Access to latter org's programs.
            'group_names': {'org1_read_reports', 'org2_read_metadata'},
            'required_api_permission': API_READ_METADATA,
            'filter_organization_key': 'org2',
            'expected_program_keys': {'masters2', 'micromasters2'},
            'expected_query_count': 5,
        },
        {
            # 9:
            # In a metadata group for one org and a reports group for another org,
            # requiring metadata access ->
            # Access to both org's programs.
            'group_names': {'org1_read_reports', 'org2_read_metadata'},
            'required_api_permission': API_READ_METADATA,
            'expected_program_keys': {
                'masters1a', 'masters1b', 'micromasters1', 'masters2', 'micromasters2',
            },
            'expected_query_count': 4,
        },
        {
            # 10:
            # In enrollment groups for both orgs, requiring enrollment access ->
            # Access to both org's Masters programs but NOT MicroMasters programs.
            'group_names': {'org1_read_enrollments', 'org2_read_enrollments'},
            'required_api_permission': API_READ_ENROLLMENTS,
            'expected_program_keys': {'masters1a', 'masters1b', 'masters2'},
            'expected_query_count': 5,
        },
        {
            # 11:
            # In enrollment groups for both orgs, requiring enrollment access,
            # filtering for first org ->
            # Access to first org's Masters programs but NOT MicroMasters program.
            'group_names': {'org1_read_enrollments', 'org2_read_enrollments'},
            'required_api_permission': API_READ_ENROLLMENTS,
            'expected_program_keys': {'masters1a', 'masters1b'},
            'filter_organization_key': 'org1',
            'expected_query_count': 6,
        },
        {
            # 12:
            # A complex scenario involving program groups and ovelapping permission sets.
            'group_names': {
                'org1_read_reports',
                'masters1a_read_enrollments',
                'micromasters2_read_reports',
            },
            'required_api_permission': API_READ_REPORTS,
            'expected_program_keys': {
                'masters1a', 'masters1b', 'micromasters1', 'micromasters2'
            },
            'expected_query_count': 4,
        },
        {
            # 13:
            # A more complex scenario involving program groups and enrollment permissions.
            'group_names': {
                'masters1a_read_enrollments',
                'org2_read_enrollments',
                'micromasters2_read_enrollments',
            },
            'required_api_permission': API_READ_ENROLLMENTS,
            'expected_program_keys': {'masters1a', 'masters2'},
            'expected_query_count': 5,
        },
        {
            # 14:
            # A more complex scenario involving program groups and organization
            # filtering on a organization that the user has access to.
            'group_names': {
                'org1_read_enrollments',
                'masters1a_read_reports',
                'micromasters2_read_reports',
            },
            'required_api_permission': API_READ_METADATA,
            'expected_program_keys': {'masters1a', 'masters1b', 'micromasters1'},
            'filter_organization_key': 'org1',
            'expected_query_count': 5,
        },
        {
            # 15:
            # A more complex scenario involving program groups and organization
            # filtering on a organization that the user does NOT have access to.
            'group_names': {
                'org1_read_enrollments',
                'masters1a_read_reports',
                'micromasters2_read_reports',
            },
            'required_api_permission': API_READ_METADATA,
            'expected_program_keys': {'micromasters2'},
            'filter_organization_key': 'org2',
            'expected_query_count': 5,
        },
        {
            # 16:
            # Test global permissions that overlap with non-global permissions,
            # ensuring that enrollment access restrictions stil apply.
            'group_names': {
                'global_read_program_enrollments',
                'org1_read_enrollments',
                'micromasters2_read_enrollments',
            },
            'required_api_permission': API_READ_ENROLLMENTS,
            'expected_program_keys': {'masters1a', 'masters1b', 'masters2'},
            'expected_query_count': 5,
        },
        {
            # 17:
            # Test global permissions that overlap with non-global permissions
            # as well as organization filtering.
            'group_names': {
                'global_read_organization_reports',
                'org1_read_enrollments',
                'micromasters2_read_metadata',
            },
            'required_api_permission': API_READ_METADATA,
            'expected_program_keys': {'masters2', 'micromasters2'},
            'filter_organization_key': 'org2',
            'expected_query_count': 5,
        },
    )
    @ddt.unpack
    def test_get_programs_by_api_permission(
        self,
        group_names,
        required_api_permission,
        expected_program_keys,
        filter_organization_key=None,
        expected_query_count=None,
    ):
        groups = [Group.objects.get(name=name) for name in group_names]
        user = UserFactory(groups=groups)
        organization_filter = (
            Organization.objects.get(key=filter_organization_key)
            if filter_organization_key
            else None
        )
        with self.assertNumQueries(expected_query_count):
            programs = get_programs_by_api_permission(
                user, required_api_permission, organization_filter,
            )
        actual_program_keys = set(programs.values_list('key', flat=True))
        assert actual_program_keys == expected_program_keys


class GetAPIPermissionsByProgramTests(TestCase):
    """ Tests for get_api_permissions_by_program """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.org1 = OrganizationFactory()
        org1_readwrite = OrganizationGroupFactory(
            organization=cls.org1,
            role=OrganizationReadWriteEnrollmentsRole.name
        )

        cls.masters_program = ProgramFactory(
            managing_organization=cls.org1
        )
        cls.non_masters_program = ProgramFactory(
            managing_organization=cls.org1
        )

        cache.set(
            PROGRAM_CACHE_KEY_TPL.format(uuid=cls.masters_program.discovery_uuid),
            {
                'title': 'test-masters-program',
                'type': 'Masters',
                'curricula': [],
            }
        )
        cache.set(
            PROGRAM_CACHE_KEY_TPL.format(uuid=cls.non_masters_program.discovery_uuid),
            {
                'title': 'test-non-masters-program',
                'type': 'MicroMasters',
                'curricula': [],
            }
        )

        cls.global_readwrite_enrollments = GroupFactory(
            permissions=[ORGANIZATION_READ_ENROLLMENTS, ORGANIZATION_WRITE_ENROLLMENTS]
        )

        # assign permissions on a per organization and global basis
        cls.user1 = UserFactory(groups=[org1_readwrite])
        assign_perm(ORGANIZATION_READ_METADATA, cls.user1)

        cls.user2 = UserFactory()
        # assign permissions on a per program basis
        assign_perm(PROGRAM_READ_METADATA, cls.user2, cls.masters_program)
        assign_perm(PROGRAM_READ_ENROLLMENTS, cls.user2, cls.masters_program)
        assign_perm(PROGRAM_WRITE_ENROLLMENTS, cls.user2, cls.masters_program)

        assign_perm(PROGRAM_READ_METADATA, cls.user2, cls.non_masters_program)
        assign_perm(PROGRAM_READ_ENROLLMENTS, cls.user2, cls.non_masters_program)
        assign_perm(PROGRAM_READ_ENROLLMENTS, cls.user2, cls.non_masters_program)

    def test_masters_perms_via_org_and_global_perms(self):
        """
        Check the effective permissions of a user on a Master's program via the
        user's organization-scoped and global permissions.
        """
        perms = get_api_permissions_by_program(self.user1, self.masters_program)
        assert perms == {
            API_READ_METADATA,
            API_READ_ENROLLMENTS,
            API_WRITE_ENROLLMENTS,
        }

    def test_non_masters_perms_via_org_and_global_perms(self):
        """
        Check the effective permissions of a user on a non-Master's program via the
        user's organization-scoped and global permissions. The read and write enrollments
        permissions for the program should be filtered out, as non-Master's programs do not have
        enrollments enabled.
        """
        perms = get_api_permissions_by_program(self.user1, self.non_masters_program)
        assert perms == {API_READ_METADATA}

    def test_masters_perms_via_program_perms(self):
        """
        Check the effective permissions of a user on a Master's program via the
        user's program-scoped permissions.
        """
        perms = get_api_permissions_by_program(self.user2, self.masters_program)
        assert perms == {
            API_READ_METADATA,
            API_READ_ENROLLMENTS,
            API_WRITE_ENROLLMENTS,
        }

    def test_non_masters_perms_via_program_perms(self):
        """
        Check the effective permissions of a user on a non-Master's program via the
        user's program-scoped permissions. The read and write enrollments permissions for
        the program should be filtered out, as non-Master's programs do not have
        enrollments enabled.
        """
        perms = get_api_permissions_by_program(self.user2, self.non_masters_program)
        assert perms == {API_READ_METADATA}
