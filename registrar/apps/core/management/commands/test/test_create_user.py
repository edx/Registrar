""" Tests for create_user management command """
from unittest.mock import patch

import ddt
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from registrar.apps.core.models import User
from registrar.apps.core.tests.factories import OrganizationGroupFactory, UserFactory


@ddt.ddt
class TestCreateUser(TestCase):
    """ Test create_user command """

    command = 'create_user'
    username = 'create_user_test_user_username'
    email = 'create_user_test_user_email@edx.edu'

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.org1group_1 = OrganizationGroupFactory().name
        cls.org1group_2 = OrganizationGroupFactory().name
        cls.org2group = OrganizationGroupFactory().name
        cls.org3group = OrganizationGroupFactory().name
        cls.all_groups = [cls.org1group_1, cls.org1group_2, cls.org2group, cls.org3group]

    def assert_user(
        self,
        user,
        expected_username=None,
        expected_email='',
        is_superuser=False,
        is_staff=False,
        expected_group_names=None
    ):
        """ Assert that the given user has certain values """
        self.assertEqual(user.username, expected_username or self.username)
        self.assertEqual(user.email, expected_email)
        self.assertEqual(user.is_superuser, is_superuser)
        self.assertEqual(user.is_staff, is_staff)
        self.assertEqual({group.name for group in user.groups.all()}, set(expected_group_names or []))

    def test_create_user(self):
        call_command(self.command, self.username, email=self.email)
        self.assert_user(User.objects.get(username=self.username), expected_email=self.email)

    @ddt.data(
        [],
        [0, 1],
        [3, ],
        [1, 2],
        [0, 1, 2, 3],
    )
    def test_create_user_groups(self, group_indices):
        group_names = [self.all_groups[i] for i in group_indices]
        call_command(self.command, self.username, groups=group_names)
        user = User.objects.get(username=self.username)
        self.assert_user(user, expected_group_names=group_names)

    @ddt.unpack
    @ddt.data(
        (True, True),
        (True, False),
        (False, True),
        (False, False),
    )
    def test_create_user_staff_superuser(self, superuser, staff):
        call_command(self.command, self.username, superuser=superuser, staff=staff)
        self.assert_user(User.objects.get(username=self.username), is_superuser=superuser, is_staff=staff)

    def test_user_already_exists_email(self):
        UserFactory.create(email=self.email)
        call_command(self.command, self.username, email=self.email)
        user = User.objects.get(username=self.username)
        self.assert_user(user, expected_email=self.email)

    def test_user_already_exists_username(self):
        UserFactory.create(username=self.username)
        with self.assertRaisesRegex(CommandError, f'User {self.username} already exists'):
            call_command(self.command, self.username, email=self.email)

    def test_duplicate_groups(self):
        groups = self.all_groups + [self.org1group_1]
        with self.assertRaisesRegex(CommandError, 'Duplicate groups not allowed'):
            call_command(self.command, self.username, email=self.email, groups=groups)

    def test_nonexistant_groups(self):
        groups = [self.org2group, self.org3group, 'idontexist']
        expected_msg = r"Group idontexist does not exist"
        with self.assertRaisesRegex(CommandError, expected_msg):
            call_command(self.command, self.username, email=self.email, groups=groups)

    @patch('registrar.apps.core.models.User.objects.get_or_create', autospec=True)
    def test_create_user_exception(self, mocked_create):
        mocked_create.side_effect = Exception('myexception')
        with self.assertRaisesRegex(CommandError, f'Unable to create User {self.username}. Cause: myexception'):
            call_command(self.command, self.username, email=self.email)
