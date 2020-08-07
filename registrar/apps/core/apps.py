"""
Registrar Core Django application initialization.
"""

from logging import getLogger

from django.apps import AppConfig


logger = getLogger(__name__)


class CoreConfig(AppConfig):
    """
    Custom configuration for the core app to hook up django signals for user creation and edit
    """
    USER_POST_SAVE_DISPATCH_UID = 'user_post_save_assign_org_group'
    name = "registrar.apps.core"

    # pylint: disable=import-outside-toplevel
    def ready(self):
        """
        Perform other one-time initialization steps.
        """
        from django.db.models.signals import post_save, pre_migrate  # pylint: disable=import-outside-toplevel

        from registrar.apps.core.models import User  # pylint: disable=import-outside-toplevel
        from registrar.apps.core.signals import handle_user_post_save  # pylint: disable=import-outside-toplevel

        post_save.connect(
            handle_user_post_save,
            sender=User,
            dispatch_uid=self.USER_POST_SAVE_DISPATCH_UID
        )
        pre_migrate.connect(self._disconnect_user_post_save_for_migrations)

    # pylint: disable=import-outside-toplevel
    def _disconnect_user_post_save_for_migrations(self, sender, **kwargs):  # pylint: disable=unused-argument
        """
        Handle pre_migrate signal - disconnect User post_save handler.
        """
        from django.db.models.signals import post_save  # pylint: disable=import-outside-toplevel

        from registrar.apps.core.models import User  # pylint: disable=import-outside-toplevel
        post_save.disconnect(sender=User, dispatch_uid=self.USER_POST_SAVE_DISPATCH_UID)
