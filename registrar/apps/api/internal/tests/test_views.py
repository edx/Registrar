""" Tests for internal API views """
import ddt
from django.core.cache import cache

from registrar.apps.api.tests.mixins import AuthRequestMixin
from registrar.apps.api.v1.tests.test_views import RegistrarAPITestCase
from registrar.apps.core.constants import PROGRAM_CACHE_KEY_TPL
from registrar.apps.core.tests.factories import UserFactory


@ddt.ddt
class FlushCacheTests(RegistrarAPITestCase, AuthRequestMixin):
    """
    Tests for the program cache flushing endpoint
    """
    method = ['DELETE']
    path = 'cache'
    api_root = '/api/internal/'

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.edx_staff_user = UserFactory(is_staff=True)

    def setUp(self):
        super().setUp()
        cache.clear()
        self.programs = [
            self.cs_program,
            self.mech_program,
            self.phil_program,
            self.english_program,
        ]
        for program in self.programs:
            cache.set(PROGRAM_CACHE_KEY_TPL.format(uuid=program.discovery_uuid), program)
        self.assert_programs_in_cache()

    def tearDown(self):
        super().tearDown()
        cache.clear()

    def assert_programs_in_cache(self, cs=True, mech=True, phil=True, english=True):
        self.assert_program_in_cache(self.cs_program, cs)
        self.assert_program_in_cache(self.mech_program, mech)
        self.assert_program_in_cache(self.phil_program, phil)
        self.assert_program_in_cache(self.english_program, english)

    def assert_program_in_cache(self, program, expected):
        """
        Method to assert if a program exists inside cache or not
        """
        cache_result = cache.get(PROGRAM_CACHE_KEY_TPL.format(uuid=program.discovery_uuid))
        if expected:
            self.assertIsNotNone(cache_result)
        else:
            self.assertIsNone(cache_result)

    def test_flush_all_programs(self):
        response = self.delete('cache/', self.edx_staff_user)
        self.assertEqual(response.status_code, 204)
        self.assert_programs_in_cache(cs=False, mech=False, phil=False, english=False)

    def test_flush_all_programs_unauthorized(self):
        response = self.delete('cache/', self.stem_admin)
        self.assertEqual(response.status_code, 404)
        self.assert_programs_in_cache()

    def flush_specific_program(self, program, user):
        return self.delete(f'cache/{program.key}/', user)

    def test_flush_specific_program(self):
        user = self.edx_staff_user

        response = self.flush_specific_program(self.cs_program, user)
        self.assertEqual(response.status_code, 204)
        self.assert_programs_in_cache(cs=False)

        response = self.flush_specific_program(self.english_program, user)
        self.assertEqual(response.status_code, 204)
        self.assert_programs_in_cache(cs=False, english=False)

        response = self.flush_specific_program(self.phil_program, user)
        self.assertEqual(response.status_code, 204)
        self.assert_programs_in_cache(cs=False, phil=False, english=False)

    def test_flush_specific_program_unauthorized(self):
        for program in self.programs:
            response = self.flush_specific_program(program, self.stem_admin)
            self.assertEqual(response.status_code, 404)
            self.assert_programs_in_cache()

    @ddt.data(True, False)
    def test_program_not_found(self, is_staff):
        user = self.edx_staff_user if is_staff else self.stem_admin
        response = self.delete('cache/program-10000-fake/', user)
        self.assertEqual(404, response.status_code)
        self.assert_programs_in_cache()

    def test_delete_program_twice(self):
        for _ in range(2):
            response = self.flush_specific_program(
                self.english_program,
                self.edx_staff_user
            )
            self.assertEqual(204, response.status_code)
            self.assert_programs_in_cache(english=False)

    def test_only_programs_deleted(self):
        key = "key2134143"
        data = "data data data data"
        cache.set(key, data)
        response = self.delete('cache/', self.edx_staff_user)
        self.assertEqual(response.status_code, 204)
        self.assert_programs_in_cache(cs=False, mech=False, phil=False, english=False)
        self.assertEqual(data, cache.get(key))
