""" Tests for create_organization management command """
from unittest.mock import patch

import ddt
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from registrar.apps.core.models import Organization
from registrar.apps.core.permissions import (
    ORGANIZATION_ROLES,
    OrganizationReadEnrollmentsRole,
    OrganizationReadMetadataRole,
    OrganizationReadWriteEnrollmentsRole,
)


@ddt.ddt
class TestCreateOrganization(TestCase):
    """ Test create_organization command """

    command = 'create_organization'
    org_key = 'UniversityOfEducation'

    def test_create_org_no_groups(self):
        call_command(self.command, self.org_key)
        org = Organization.objects.get(key=self.org_key)
        self.assertEqual(len(org.organizationgroup_set.all()), 0)

    @ddt.data(
        [
            [OrganizationReadMetadataRole.name, 'meta_1'],
            [OrganizationReadMetadataRole.name, 'meta_2']
        ],
        [
            [OrganizationReadMetadataRole.name],
            [OrganizationReadEnrollmentsRole.name],
            [OrganizationReadWriteEnrollmentsRole.name]
        ],
        [
            [OrganizationReadMetadataRole.name],
            [OrganizationReadEnrollmentsRole.name, 'g1'],
            [OrganizationReadWriteEnrollmentsRole.name]
        ],
        [
            [OrganizationReadEnrollmentsRole.name, 'r1'],
            [OrganizationReadWriteEnrollmentsRole.name]
        ],
    )
    def test_create_org_groups(self, groups):
        call_command(self.command, self.org_key, groups=groups)
        org = Organization.objects.get(key=self.org_key)
        groups_by_role = {role.name: [] for role in ORGANIZATION_ROLES}
        for group in groups:
            groups_by_role[group[0]].append(group)

        self.assertEqual(len(org.organizationgroup_set.all()), len(groups))
        for role, expected_groups in groups_by_role.items():
            qs = org.organizationgroup_set.filter(role=role)
            self.assertEqual(len(qs), len(expected_groups))
            for group in expected_groups:
                if len(group) == 1:
                    group_name = f'{org.name}_{role}'
                else:
                    group_name = group[1]
                qs.get(name=group_name)  # will raise exception if not found

    def test_group_parsing_too_many_args(self):
        groups = [[OrganizationReadMetadataRole.name, 'g1', 'test'], [OrganizationReadEnrollmentsRole.name, 'g2']]
        with self.assertRaisesRegex(CommandError, '--group only accepts one or two arguments'):
            call_command(self.command, self.org_key, groups=groups)

    def test_group_parsing_invalid_role(self):
        groups = [['notarole', 'g1']]
        with self.assertRaisesRegex(CommandError, r'first argument to --group must be one of .*'):
            call_command(self.command, self.org_key, groups=groups)

    @patch('registrar.apps.core.models.Organization.objects.create', autospec=True)
    def test_create_org_exception(self, mocked_create):
        mocked_create.side_effect = Exception('myexception')
        with self.assertRaisesRegex(CommandError, 'Unable to create Organization. cause: myexception'):
            call_command(self.command, self.org_key)

    @patch('registrar.apps.core.models.OrganizationGroup.objects.create', autospec=True)
    def test_create_org_group_exception(self, mocked_create):
        mocked_create.side_effect = Exception('myexception')
        with self.assertRaisesRegex(CommandError, 'Unable to create OrganizationGroup g1. cause: myexception'):
            call_command(self.command, self.org_key, groups=[[OrganizationReadMetadataRole.name, 'g1']])

    def test_invalid_group_name(self):
        msg = 'org_key can only contain alphanumeric characters, dashes, and underscores'
        with self.assertRaisesRegex(CommandError, msg):
            call_command(self.command, "RobertU'); DROP TABLE organizations;--")
