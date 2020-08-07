""" Test API Serializers """
from django.core.cache import cache
from django.test import TestCase

from registrar.apps.api.serializers import DetailedProgramSerializer
from registrar.apps.core.constants import PROGRAM_CACHE_KEY_TPL
from registrar.apps.core.permissions import API_READ_METADATA, API_WRITE_ENROLLMENTS
from registrar.apps.core.tests.factories import OrganizationFactory, ProgramFactory


class DetailedProgramSerializerTests(TestCase):
    """ Tests the DetailedProgramSerializer """
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.org = OrganizationFactory()
        cls.program = ProgramFactory(managing_organization=cls.org)

    def setUp(self):
        super().setUp()

        cache.set(
            PROGRAM_CACHE_KEY_TPL.format(uuid=self.program.discovery_uuid),
            {
                'title': 'test-program',
                'type': 'Masters',
                'curricula': [],
            }
        )

    def test_serializer(self):
        """
        Test that the serializer should `user_api_permissions_by_program to
        compute for permissions.
        """
        program = DetailedProgramSerializer(
            self.program,
            context={
                'user_api_permissions_by_program': {
                    self.program: [API_WRITE_ENROLLMENTS, API_READ_METADATA],
                },
            }
        ).data
        self.assertListEqual(
            program.get('permissions'),
            [API_READ_METADATA.name, API_WRITE_ENROLLMENTS.name]
        )

    def test_permissions_no_permissions_context(self):
        """
        Serializer should return an empty list for permissions if invoked
        without `user_api_permissions_by_program` in its context.
        """
        program = DetailedProgramSerializer(self.program).data
        self.assertListEqual(program.get('permissions'), [])
