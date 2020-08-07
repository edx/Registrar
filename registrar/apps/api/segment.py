"""
Convenience functions for working with the segment.io analytics library
"""
import logging

import analytics
from django.conf import settings

from registrar.apps.core.auth_checks import get_user_organizations

from .constants import TRACKING_CATEGORY


logger = logging.getLogger(__name__)


def track(
    user_id,
    event,
    properties=None,
    context=None,
    timestamp=None,
    anonymous_id=None,
    integrations=None,
):
    """
    Function to track events
    """
    if settings.SEGMENT_KEY:
        analytics.track(user_id, event, properties, context, timestamp, anonymous_id, integrations)
    else:
        logger.debug(f"{{{user_id}, {event}}} not tracked because SEGMENT_KEY not set")


def get_tracking_properties(user, **kwargs):
    """
    Helper function to construct the properties for tracking events
    """
    user_orgs = get_user_organizations(user)
    property_dict = kwargs.copy()
    property_dict['user_organizations'] = [org.name for org in user_orgs]
    property_dict['category'] = TRACKING_CATEGORY

    return property_dict
