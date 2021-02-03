""" Management command to create an Organization and associated OrganizationGroups """
import logging
import re
import uuid

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from registrar.apps.core.constants import ORGANIZATION_KEY_PATTERN
from registrar.apps.core.models import Organization, OrganizationGroup
from registrar.apps.core.permissions import ORGANIZATION_ROLES


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """class for command that creates an organization"""
    help = 'Creates an Organization with the given key, and any specified OrganizationGroups'
    role_names = [role.name for role in ORGANIZATION_ROLES]

    def add_arguments(self, parser):
        parser.add_argument('org_key')
        parser.add_argument(
            '--group',
            nargs='+',
            dest='groups',
            action='append',
            default=[],
            help='Create an OrganizationGroup. Args: role [name] \n acceptable roles: {}'.format(self.role_names)
        )

    # pylint: disable=arguments-differ
    @transaction.atomic
    def handle(self, org_key, groups, *args, **options):
        groups = self.parse_groups(groups)
        org = self.create_organization(org_key)
        for group in groups:
            self.create_org_group(org, group[0], group[1])

    def parse_groups(self, groups):  # pylint: disable=missing-function-docstring
        result = []
        for group in groups:
            if len(group) > 2:
                raise CommandError('--group only accepts one or two arguments')
            role = group[0]
            if group[0] not in self.role_names:
                raise CommandError('first argument to --group must be one of {}'.format(self.role_names))
            group_name = None
            if len(group) == 2:
                group_name = group[1]
            result.append((role, group_name))
        return result

    def create_organization(self, org_key):  # pylint: disable=missing-function-docstring
        if not re.fullmatch(ORGANIZATION_KEY_PATTERN, org_key):
            raise CommandError('org_key can only contain alphanumeric characters, dashes, and underscores')
        try:
            org = Organization.objects.create(
                key=org_key,
                name=org_key,
                discovery_uuid=uuid.uuid4(),
            )
        except Exception as e:
            raise CommandError('Unable to create Organization. cause: {}'.format(e)) from e
        logger.info('Created Organization {}'.format(org.key))
        return org

    def create_org_group(self, org, group_role, group_name):  # pylint: disable=missing-function-docstring
        if not group_name:
            group_name = "{}_{}".format(org.name, group_role)
        try:
            OrganizationGroup.objects.create(
                name=group_name,
                organization=org,
                role=group_role,
            )
        except Exception as e:
            raise CommandError('Unable to create OrganizationGroup {}. cause: {}'.format(group_name, e)) from e
        logger.info('Created OrganizationGroup {} with role {}'.format(group_name, group_role))
