"""
Mixins that provide common tests and utilities
"""
import requests
from user_tasks.models import UserTaskStatus

from registrar.apps.core.jobs import get_job_status
from registrar.apps.core.tests.factories import (
    OrganizationFactory,
    ProgramFactory,
    UserFactory,
)


class BaseTaskTestMixin(object):
    """ Mixin for common task testing utility functions, and program_not_found """
    job_id = "6fee9384-f7f7-496f-a607-ee9f59201ee0"
    mock_base = None
    mock_function = None

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = UserFactory()
        org = OrganizationFactory(name='STEM Institute')
        cls.program = ProgramFactory(managing_organization=org)

    def spawn_task(self, program_key=None, **kwargs):
        """
        Overridden in children.
        """
        pass  # pragma: no cover

    def full_mock_path(self):
        return self.mock_base + self.mock_function

    def assert_succeeded(self, expected_contents, expected_text, job_id=None):
        """
        Assert that the task identified by `job_id` succeeded
        and that its contents are equal to `expected_contents`.

        If `job_id` is None, use `self.job_id`.
        """
        status = get_job_status(self.user, job_id or self.job_id)
        self.assertEqual(status.state, UserTaskStatus.SUCCEEDED)
        self.assertEqual(status.text, expected_text)
        result_response = requests.get(status.result)
        self.assertIn(result_response.text, expected_contents)

    def assert_failed(self, sub_message, job_id=None):
        """
        Assert that the task identified by `job_id` failed
        and that it contains `sub_message` in its failure reason.

        If `job_id` is None, use `self.job_id`.
        """
        task_status = UserTaskStatus.objects.get(task_id=(job_id or self.job_id))
        self.assertEqual(task_status.state, UserTaskStatus.FAILED)
        error_artifact = task_status.artifacts.filter(name='Error').first()
        self.assertIn(sub_message, error_artifact.text)

    def test_program_not_found(self):
        task = self.spawn_task(program_key="program-nonexistant")
        task.wait()
        self.assert_failed("Bad program key")
