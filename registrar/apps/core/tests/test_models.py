""" Tests for core models. """

import ddt
from django.core.exceptions import ValidationError
from django.test import TestCase
from django_dynamic_fixture import G
from guardian.shortcuts import get_perms
from social_django.models import UserSocialAuth

from .. import permissions as perm
from ..models import (
    Organization,
    OrganizationGroup,
    Program,
    ProgramOrganizationGroup,
    User,
)
from .factories import (
    OrganizationFactory,
    OrganizationGroupFactory,
    PendingUserGenericGroupFactory,
    PendingUserOrganizationGroupFactory,
    PendingUserProgramGroupFactory,
    ProgramFactory,
    ProgramOrganizationGroupFactory,
    UserFactory,
)


class UserTests(TestCase):
    """ User model tests. """
    TEST_CONTEXT = {'foo': 'bar', 'baz': None}

    def test_access_token(self):
        user = G(User)
        self.assertIsNone(user.access_token)

        social_auth = G(UserSocialAuth, user=user)
        self.assertIsNone(user.access_token)

        access_token = 'My voice is my passport. Verify me.'
        social_auth.extra_data['access_token'] = access_token
        social_auth.save()
        self.assertEqual(user.access_token, access_token)

    def test_get_full_name(self):
        """ Test that the user model concatenates first and last name if the full name is not set. """
        full_name = 'George Costanza'
        user = G(User, full_name=full_name)
        self.assertEqual(user.get_full_name(), full_name)

        first_name = 'Jerry'
        last_name = 'Seinfeld'
        user = G(User, full_name=None, first_name=first_name, last_name=last_name)
        expected = '{first_name} {last_name}'.format(first_name=first_name, last_name=last_name)
        self.assertEqual(user.get_full_name(), expected)

        user = G(User, full_name=full_name, first_name=first_name, last_name=last_name)
        self.assertEqual(user.get_full_name(), full_name)


class OrganizationTests(TestCase):
    """ Tests for Program model """

    def test_bad_key_raises_validation_error(self):
        """
        Test that cleaning an Organization with a non-slug key raise a ValidationError.

        A "slug" can include ASCII-valid alphanumeric characters, underscores, and hyphens.
        """
        org = OrganizationFactory.build(key="AmericanDodgeballAssociationOfAmericá")
        with self.assertRaisesRegex(ValidationError, "Enter a valid 'slug'"):
            org.full_clean()


class ProgramTests(TestCase):
    """ Tests for Program model """

    def test_bad_key_raises_validation_error(self):
        """
        Test that cleaning a Program with a non-slug key raise a ValidationError.

        A "slug" can include ASCII-valid alphanumeric characters, underscores, and hyphens.
        """
        program = ProgramFactory.build(key="Master's_Degree")
        with self.assertRaisesRegex(ValidationError, "Enter a valid 'slug'"):
            program.full_clean()


@ddt.ddt
class OrganizationGroupTests(TestCase):
    """ Tests for OrganizationGroup model """

    def setUp(self):
        super().setUp()
        self.organization = OrganizationFactory()
        self.user = UserFactory()

    @ddt.data(
        perm.OrganizationReadMetadataRole,
        perm.OrganizationReadEnrollmentsRole,
        perm.OrganizationReadWriteEnrollmentsRole,
    )
    def test_roles(self, role):
        org_group = OrganizationGroup.objects.create(
            role=role.name,
            organization=self.organization,
        )
        permissions = get_perms(self.user, self.organization)
        self.assertEqual([], permissions)
        self.user.groups.add(org_group)
        permissions = get_perms(self.user, self.organization)
        self.assertEqual(len(role.permissions), len(permissions))
        for permission in Organization._meta.permissions:
            self.assertEqual(
                permission in role.permissions,
                self.user.has_perm(permission, self.organization)
            )

    def test_global_permission_not_granted(self):
        org_group = OrganizationGroup.objects.create(
            role=perm.OrganizationReadMetadataRole.name,
            organization=self.organization,
        )
        self.user.groups.add(org_group)
        permission = perm.OrganizationReadMetadataRole.permissions[0]
        self.assertTrue(self.user.has_perm(permission, self.organization))
        self.assertFalse(self.user.has_perm(permission))

    def test_roles_are_org_specific(self):
        organization2 = OrganizationFactory()
        permission = perm.OrganizationReadMetadataRole.permissions[0]
        self.assertFalse(self.user.has_perm(permission, self.organization))
        self.assertFalse(self.user.has_perm(permission, organization2))
        org_group = OrganizationGroup.objects.create(
            role=perm.OrganizationReadMetadataRole.name,
            organization=self.organization,
        )
        self.user.groups.add(org_group)
        self.assertTrue(self.user.has_perm(permission, self.organization))
        self.assertFalse(self.user.has_perm(permission, organization2))

    def test_org_group_recalculates_permissions(self):
        org1 = self.organization
        org2 = OrganizationFactory()
        metdata_permission = perm.ORGANIZATION_READ_METADATA
        write_permission = perm.ORGANIZATION_WRITE_ENROLLMENTS

        # Scenario 1: read/write on org1
        org_group = OrganizationGroup.objects.create(
            role=perm.OrganizationReadWriteEnrollmentsRole.name,
            organization=org1,
        )
        self.user.groups.add(org_group)
        self.assertTrue(self.user.has_perm(metdata_permission, org1))
        self.assertTrue(self.user.has_perm(write_permission, org1))
        self.assertFalse(self.user.has_perm(metdata_permission, org2))
        self.assertFalse(self.user.has_perm(write_permission, org2))

        # Scenario 2: metadata only on org1
        org_group.role = perm.OrganizationReadEnrollmentsRole.name
        org_group.save()
        self.assertTrue(self.user.has_perm(metdata_permission, org1))
        self.assertFalse(self.user.has_perm(write_permission, org1))
        self.assertFalse(self.user.has_perm(metdata_permission, org2))
        self.assertFalse(self.user.has_perm(write_permission, org2))

        # Scenario 3: metadata only on org2
        org_group.organization = org2
        org_group.save()
        self.assertFalse(self.user.has_perm(metdata_permission, org1))
        self.assertFalse(self.user.has_perm(write_permission, org1))
        self.assertTrue(self.user.has_perm(metdata_permission, org2))
        self.assertFalse(self.user.has_perm(write_permission, org2))

        # Scenario 4: read/write on org2
        org_group.role = perm.OrganizationReadWriteEnrollmentsRole.name
        org_group.save()
        self.assertFalse(self.user.has_perm(metdata_permission, org1))
        self.assertFalse(self.user.has_perm(write_permission, org1))
        self.assertTrue(self.user.has_perm(metdata_permission, org2))
        self.assertTrue(self.user.has_perm(write_permission, org2))


@ddt.ddt
class ProgramOrganizationGroupTests(TestCase):
    """ Tests for ProgramOrganizationGroup model """

    def setUp(self):
        super().setUp()
        self.program = ProgramFactory()
        self.user = UserFactory()

    @ddt.data(
        perm.ProgramReadMetadataRole,
        perm.ProgramReadEnrollmentsRole,
        perm.ProgramReadWriteEnrollmentsRole,
    )
    def test_roles(self, role):
        program_group = ProgramOrganizationGroup.objects.create(
            role=role.name,
            program=self.program,
            granting_organization=self.program.managing_organization,
        )
        permissions = get_perms(self.user, self.program)
        self.assertEqual([], permissions)
        self.user.groups.add(program_group)
        permissions = get_perms(self.user, self.program)
        self.assertEqual(len(role.permissions), len(permissions))
        for permission in Program._meta.permissions:
            self.assertEqual(
                permission in role.permissions,
                self.user.has_perm(permission, self.program)
            )

    def test_global_permission_not_granted(self):
        program_group = ProgramOrganizationGroup.objects.create(
            role=perm.ProgramReadMetadataRole.name,
            program=self.program,
            granting_organization=self.program.managing_organization,
        )
        self.user.groups.add(program_group)
        permission = perm.ProgramReadMetadataRole.permissions[0]
        self.assertTrue(self.user.has_perm(permission, self.program))
        self.assertFalse(self.user.has_perm(permission))

    def test_roles_are_program_specific(self):
        program2 = ProgramFactory()
        permission = perm.ProgramReadMetadataRole.permissions[0]
        self.assertFalse(self.user.has_perm(permission, self.program))
        self.assertFalse(self.user.has_perm(permission, program2))
        program_group = ProgramOrganizationGroup.objects.create(
            role=perm.ProgramReadMetadataRole.name,
            program=self.program,
            granting_organization=self.program.managing_organization,
        )
        self.user.groups.add(program_group)
        self.assertTrue(self.user.has_perm(permission, self.program))
        self.assertFalse(self.user.has_perm(permission, program2))

    def test_program_group_recalculates_permissions(self):
        program1 = self.program
        program2 = ProgramFactory()
        metdata_permission = perm.PROGRAM_READ_METADATA
        write_permission = perm.PROGRAM_WRITE_ENROLLMENTS

        # Scenario 1: read/write on program1
        program_group = ProgramOrganizationGroup.objects.create(
            role=perm.ProgramReadWriteEnrollmentsRole.name,
            program=program1,
            granting_organization=program1.managing_organization,
        )
        self.user.groups.add(program_group)
        self.assertTrue(self.user.has_perm(metdata_permission, program1))
        self.assertTrue(self.user.has_perm(write_permission, program1))
        self.assertFalse(self.user.has_perm(metdata_permission, program2))
        self.assertFalse(self.user.has_perm(write_permission, program2))

        # Scenario 2: metadata only on program1
        program_group.role = perm.ProgramReadEnrollmentsRole.name
        program_group.save()
        self.assertTrue(self.user.has_perm(metdata_permission, program1))
        self.assertFalse(self.user.has_perm(write_permission, program1))
        self.assertFalse(self.user.has_perm(metdata_permission, program2))
        self.assertFalse(self.user.has_perm(write_permission, program2))

        # Scenario 3: metadata only on program2
        program_group.program = program2
        program_group.save()
        self.assertFalse(self.user.has_perm(metdata_permission, program1))
        self.assertFalse(self.user.has_perm(write_permission, program1))
        self.assertTrue(self.user.has_perm(metdata_permission, program2))
        self.assertFalse(self.user.has_perm(write_permission, program2))

        # Scenario 4: read/write on program2
        program_group.role = perm.ProgramReadWriteEnrollmentsRole.name
        program_group.save()
        self.assertFalse(self.user.has_perm(metdata_permission, program1))
        self.assertFalse(self.user.has_perm(write_permission, program1))
        self.assertTrue(self.user.has_perm(metdata_permission, program2))
        self.assertTrue(self.user.has_perm(write_permission, program2))


@ddt.ddt
class StringificationTests(TestCase):
    """
    Tests for str() and repr() functions of all models.
    """

    @ddt.data(
        (lambda: OrganizationFactory(key="hi", name="HELLO"), "hi", "HELLO"),
        (lambda: OrganizationGroupFactory(name="ya"), "ya", "ya"),
        (lambda: PendingUserGenericGroupFactory(user_email="a@b.c"), "a@b.c", "a@b.c"),
        (lambda: PendingUserOrganizationGroupFactory(user_email="a@b.c"), "a@b.c", "a@b.c"),
        (lambda: PendingUserProgramGroupFactory(user_email="a@b.c"), "a@b.c", "a@b.c"),
        (lambda: ProgramFactory(key="dude"), "dude", "dude"),
        (lambda: ProgramOrganizationGroupFactory(name="woah"), "woah", "woah"),
        (lambda: UserFactory(username="socrates"), "socrates", "socrates"),
    )
    @ddt.unpack
    def test_repr_and_str_sanity(self, instance_fn, expected_repr_snippet, expected_str_snippet):
        """
        Tests that __repr__ and __str__ of the given object have at least minimally-
        sane implementations and that __repr__() contains that class name.

        We pass in `instance_fn` as a lambda because pytest cannot create the instance
        during test collection.
        """
        instance = instance_fn()
        actual_repr = repr(instance)
        actual_str = str(instance)
        assert actual_repr != actual_str, (
            "repr() and str() should have different implementations. "
            "The former is for developers, and the latter is for end-users."
        )
        assert instance.__class__.__name__ in actual_repr
        assert expected_repr_snippet in actual_repr
        assert expected_str_snippet in actual_str
