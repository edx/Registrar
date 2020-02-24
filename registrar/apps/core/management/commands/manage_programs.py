""" Management command to create or modify programs"""
import logging

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from registrar.apps.core.models import Organization, Program
from registrar.apps.core.proxies import DiscoveryProgram


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """class for management command to create or modify programs"""
    help = 'Creates or modifies Programs'

    def add_arguments(self, parser):
        parser.add_argument(
            'uuidkeys',
            help=('specify the programs to create or modify, in a single comma '
                  'separated string, in the format <discovery_uuid>[:<program_key>]')
        )

    # pylint: disable=arguments-differ
    @transaction.atomic
    def handle(self, uuidkeys, *args, **options):
        uuidkeys = self.parse_uuidkeys(uuidkeys)
        for uuidkey in uuidkeys:
            discovery_details = DiscoveryProgram.get_program_details(uuidkey[0])
            if not discovery_details:
                raise CommandError('Could not read program from course-discovery; aborting')
            authoring_orgs = self.get_authoring_org_keys(discovery_details)
            org = self.get_org(authoring_orgs)
            self.create_or_modify_program(org, discovery_details, *uuidkey)

    def parse_uuidkeys(self, uuidkeys):  # pylint: disable=missing-function-docstring
        result = []
        for uuidkey in uuidkeys.split(','):
            split_args = uuidkey.split(':')
            if len(split_args) == 1:
                result.append((uuidkey, None))
            elif len(split_args) == 2:
                result.append((split_args[0], split_args[1]))
            else:
                message = ('incorrectly formatted argument {}, '
                           'must be in form <program uuid>:<program key> or <program_uuid>').format(uuidkey)
                raise CommandError(message)
        return result

    def get_authoring_org_keys(self, program_details):
        """
        Return a list of authoring_organization keys
        """
        org_keys = []
        authoring_orgs = program_details.get('authoring_organizations', [])
        for authoring_org in authoring_orgs:
            if 'key' in authoring_org:
                org_keys.append(authoring_org['key'])
        if not org_keys:
            raise CommandError('No authoring org keys found for program {}'.format(
                program_details.get('uuid'))
            )
        logger.info('Authoring Organizations are {}'.format(org_keys))
        return org_keys

    def get_org(self, org_keys):
        """
        From the list of authoring_organization keys from discovery,
        return the first matching Registrar Organization
        """
        for org_key in org_keys:
            try:
                org = Organization.objects.get(key=org_key)
                logger.info('Using {} as program organization'.format(org))
                return org
            except Organization.DoesNotExist:
                logger.info('Org {} not found in registrar'.format(org_key))
        raise CommandError('None of the authoring organizations {} were found in Registrar'.format(org_keys))

    def create_or_modify_program(self, org, program_details, program_uuid, program_key):  # pylint: disable=missing-function-docstring
        program, created = Program.objects.get_or_create(
            discovery_uuid=program_uuid,
            defaults={
                'managing_organization': org,
                'key': program_key or program_details.get('marketing_slug'),
            },
        )
        if (not created) and program_key and (program.key != program_key):
            program.key = program_key
            program.save()
        verb = 'Created' if created else 'Modified existing'
        logger.info('{} program (key={} uuid={} managing_org={})'.format(verb, program_key, program_uuid, org.key))
