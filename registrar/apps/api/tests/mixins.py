"""
Mixins for Registrar API tests.
"""
import json
from contextlib import contextmanager
from time import time
from unittest import mock

import jwt
from django.conf import settings

from registrar.apps.api.constants import TRACKING_CATEGORY
from registrar.apps.core.auth_checks import get_user_organizations


JWT_AUTH = 'JWT_AUTH'


class TrackTestMixin:
    """
    Mixin enabling testing of tracking.

    Mocks out tracking functions and provides `assert_tracking` context manager.

    Expects:
    * to be subclass of Django test case
    * to be provided fields self.user and self.user_org
    """
    event = None  # Override in subclass

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.segment_patcher = mock.patch('registrar.apps.api.segment.track', autospec=True)
        cls.logging_patcher = mock.patch('registrar.apps.api.mixins.logger', autospec=True)
        cls.mock_segment_track = cls.segment_patcher.start()
        cls.mock_logging = cls.logging_patcher.start()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls.segment_patcher.stop()
        cls.logging_patcher.stop()

    @contextmanager
    def assert_tracking(self, user=None, event=None, status_code=None, **kwargs):
        """
        Context manager to make sure event was tracked within given kwargs
        exactly once.
        """
        properties = kwargs.copy()
        properties['category'] = TRACKING_CATEGORY
        user = user or self.user
        if 'user_organizations' not in properties:  # pragma: no branch
            properties['user_organizations'] = [
                org.name for org in get_user_organizations(user)
            ]
        event = event or self.event
        if not status_code:
            if kwargs.get('missing_permissions'):
                status_code = 403
            elif kwargs.get('failure') == 'bad_request':
                status_code = 400
            elif kwargs.get('failure', '').endswith('_not_found'):
                status_code = 404
            elif kwargs.get('failure') == 'request_entity_too_large':
                status_code = 413
            elif kwargs.get('failure') == 'unprocessable_entity':
                status_code = 422
            else:
                status_code = 200
        properties['status_code'] = status_code
        self.mock_segment_track.reset_mock()
        self.mock_logging.reset_mock()

        yield  # Call the code that we expect to fire the event

        self.mock_segment_track.assert_called_once_with(
            user.username,
            event,
            properties
        )
        self.mock_logging.info.assert_called_once_with(
            '%s invoked on Registrar by user with ID=%s with properties %s',
            event,
            user.id,
            json.dumps(properties, skipkeys=True, sort_keys=True),
        )


class JwtMixin:
    """ Mixin with JWT-related helper functions. """

    JWT_ISSUER_OBJ = getattr(settings, JWT_AUTH)['JWT_ISSUERS'][0]

    def generate_token(self, payload, secret=None):
        """Generate a JWT token with the provided payload."""
        secret = secret or self.JWT_ISSUER_OBJ['SECRET_KEY']
        token = jwt.encode(payload, secret)
        return token

    def generate_id_token(self, user, admin=False, ttl=5, **overrides):
        """Generate a JWT id_token that looks like the ones currently
        returned by the edx oauth provider."""

        payload = self.default_payload(user=user, admin=admin, ttl=ttl)
        payload.update(overrides)
        return self.generate_token(payload)

    def default_payload(self, user, admin=False, ttl=5):
        """Generate a bare payload, in case tests need to manipulate
        it directly before encoding."""
        now = int(time())

        return {
            "iss": self.JWT_ISSUER_OBJ['ISSUER'],
            "sub": user.pk,
            "aud": self.JWT_ISSUER_OBJ['AUDIENCE'],
            "nonce": "dummy-nonce",
            "exp": now + ttl,
            "iat": now,
            "preferred_username": user.username,
            "administrator": admin,
            "email": user.email,
            "locale": "en",
            "name": user.full_name,
        }

    def generate_jwt_header(self, user, admin=False, ttl=5, **overrides):
        """Generate a jwt header value for AUTHORIZATION"""
        jwt_token = self.generate_id_token(user, admin, ttl, **overrides).decode('utf-8')
        return f'JWT {jwt_token}'


class AuthRequestMixin(JwtMixin):
    """
    Mixin with authenticated get/post/put/patch/delete helper functions.

    Expects implementing classes to provide ``self.client`` attribute.

    Also tests that endpoint returns a 401 if unauthenticated.
    """
    # Define in subclasss
    api_root = None  # Prepended to all non-absolute request URLs
    method = None  # Used in test_unauthenticated
    path = None  # Used in test_unauthenticated

    def test_unauthenticated(self):
        if isinstance(self.method, str):
            methods = [self.method]
        else:
            methods = self.method

        for method in methods:
            response = self.request(method, self.api_root + self.path, None)
            self.assertEqual(response.status_code, 401)

    def get(self, path, user):
        """
        Perform a GET on the given path with the given user.
        """
        return self.request('get', path, user)

    def post(self, path, data, user):
        """
        Perform a POST on the given path with the given user.
        """
        return self.request('post', path, user, data)  # pragma: no cover

    def put(self, path, data, user):
        """
        Perform a PUT on the given path with the given user.
        """
        return self.request('put', path, user, data)  # pragma: no cover

    def patch(self, path, data, user):
        """
        Perform a PATCH on the given path with the given user.
        """
        return self.request('patch', path, user, data)  # pragma: no cover

    def delete(self, path, user):
        """
        Perform a DELETE on the given with the given user.
        """
        return self.request('delete', path, user)

    def request(self, method, path, user, data=None, file=None):
        """
        Perform an HTTP request of the given method.

        If user is not None, include a JWT auth header.
        """
        kwargs = {'follow': True}
        if user:
            kwargs['HTTP_AUTHORIZATION'] = self.generate_jwt_header(
                user, admin=user.is_staff,
            )
        if data:
            kwargs['data'] = json.dumps(data)
            kwargs['content_type'] = 'application/json'
        if file:
            kwargs['data'] = {
                'file': file
            }
            kwargs['format'] = 'multipart'
        path_is_absolute = (
            path.startswith('http://') or
            path.startswith('https://') or
            path.startswith('/')
        )
        if not path_is_absolute:
            path = self.api_root + path
        return getattr(self.client, method.lower())(path, **kwargs)
