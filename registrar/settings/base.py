import os
from os.path import join, abspath, dirname

from corsheaders.defaults import default_headers as corsheaders_default_headers

from registrar.settings.utils import get_logger_config

# PATH vars
here = lambda *x: join(abspath(dirname(__file__)), *x)
PROJECT_ROOT = here("..")
root = lambda *x: join(abspath(PROJECT_ROOT), *x)


# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.environ.get('REGISTRAR_SECRET_KEY', 'insecure-secret-key')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = False

ALLOWED_HOSTS = []

# Application definition

INSTALLED_APPS = (
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
)

THIRD_PARTY_APPS = (
    'analytics',
    'corsheaders',
    'csrf.apps.CsrfAppConfig',  # Enables frontend apps to retrieve CSRF tokens
    'edx_api_doc_tools',
    'drf_yasg',
    'guardian',
    'release_util',
    'rest_framework',
    'simple_history',
    'social_django',
    'user_tasks',
    'waffle',
)

PROJECT_APPS = (
    'registrar.apps.core.apps.CoreConfig',
    'registrar.apps.api.apps.ApiConfig',
    'registrar.apps.enrollments',
    'registrar.apps.grades',
)

INSTALLED_APPS += THIRD_PARTY_APPS
INSTALLED_APPS += PROJECT_APPS

MIDDLEWARE = (
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.locale.LocaleMiddleware',
    'django.middleware.common.CommonMiddleware',
    'edx_rest_framework_extensions.auth.jwt.middleware.EnsureJWTAuthSettingsMiddleware',
    'edx_rest_framework_extensions.auth.jwt.middleware.JwtAuthCookieMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'simple_history.middleware.HistoryRequestMiddleware',
    'social_django.middleware.SocialAuthExceptionMiddleware',
    'waffle.middleware.WaffleMiddleware',
)

# Enable CORS
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_HEADERS = corsheaders_default_headers + (
    'use-jwt-cookie',
)
CORS_ORIGIN_WHITELIST = []


ROOT_URLCONF = 'registrar.urls'
APPEND_SLASH = False

# Python dotted path to the WSGI application used by Django's runserver.
WSGI_APPLICATION = 'registrar.wsgi.application'

# Database
# https://docs.djangoproject.com/en/1.11/ref/settings/#databases
# Set this value in the environment-specific files (e.g. local.py, production.py, test.py)
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.',
        'NAME': 'registrar',
        'USER': 'registrar001',
        'PASSWORD': 'password',
        'HOST': 'localhost',  # Empty for localhost through domain sockets or '127.0.0.1' for localhost through TCP.
        'PORT': '',  # Set to empty string for default.
        'ATOMIC_REQUESTS': False,
        'CONN_MAX_AGE': 60,
    }
}

############################# BEGIN CELERY #################################3

# Message configuration
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_ACCEPT_CONTENT = ['json']
CELERY_MESSAGE_COMPRESSION = 'gzip'

# Results configuration
CELERY_IGNORE_RESULT = False
CELERY_STORE_ERRORS_EVEN_IF_IGNORED = True

# Events configuration
CELERY_TRACK_STARTED = True
CELERY_SEND_EVENTS = True
CELERY_SEND_TASK_SENT_EVENT = True

# let logging work as configured:
CELERYD_HIJACK_ROOT_LOGGER = False

# Celery task routing configuration.
# Only registrar workers should receive registrar tasks.
# Explicitly define these to avoid name collisions with other services
# using the same broker and the standard default queue name of "celery".
CELERY_DEFAULT_EXCHANGE = os.environ.get('CELERY_DEFAULT_EXCHANGE', 'registrar')
CELERY_DEFAULT_ROUTING_KEY = os.environ.get('CELERY_DEFAULT_ROUTING_KEY', 'registrar')
CELERY_DEFAULT_QUEUE = os.environ.get('CELERY_DEFAULT_QUEUE', 'registrar.default')

# Celery Broker
# These settings need not be set if CELERY_ALWAYS_EAGER == True, like in Standalone.
# Devstack overrides these in its docker-compose.yml.
# Production environments can override these to be whatever they want.
CELERY_BROKER_TRANSPORT = os.environ.get("CELERY_BROKER_TRANSPORT", "")
CELERY_BROKER_HOSTNAME = os.environ.get("CELERY_BROKER_HOSTNAME", "")
CELERY_BROKER_VHOST = os.environ.get("CELERY_BROKER_VHOST", "")
CELERY_BROKER_USER = os.environ.get("CELERY_BROKER_USER", "")
CELERY_BROKER_PASSWORD = os.environ.get("CELERY_BROKER_PASSWORD", "")
BROKER_URL = "{}://{}:{}@{}/{}".format(
    CELERY_BROKER_TRANSPORT,
    CELERY_BROKER_USER,
    CELERY_BROKER_PASSWORD,
    CELERY_BROKER_HOSTNAME,
    CELERY_BROKER_VHOST
)

# Celery task time limits.
# Tasks will be asked to quit after four minutes, and un-gracefully killed
# after five.
# This should prevent UserTasks from getting stuck indefinitely in an
# In-Progress/Pending state, which in the case of enrollment-writing tasks,
# would block any other enrollment-writing tasks for the associated program
# from ever starting.
CELERY_TASK_SOFT_TIME_LIMIT = 240
CELERY_TASK_TIME_LIMIT = 300

############################# END CELERY #################################3

# Internationalization
# https://docs.djangoproject.com/en/dev/topics/i18n/

LANGUAGE_CODE = 'en'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_L10N = True

USE_TZ = True

LOCALE_PATHS = (
    root('conf', 'locale'),
)

# MEDIA CONFIGURATION
MEDIA_ROOT = root('media')
MEDIA_URL = '/api/media/'
DEFAULT_FILE_STORAGE = 'django.core.files.storage.FileSystemStorage'
REGISTRAR_BUCKET = 'change-me-to-registrar-bucket'
PROGRAM_REPORTS_BUCKET = 'change-me-to-program-reports-bucket'
PROGRAM_REPORTS_FOLDER = 'reports_v2'

# STATIC FILE CONFIGURATION
# See: https://docs.djangoproject.com/en/dev/ref/settings/#static-root
STATIC_ROOT = root('assets')

# See: https://docs.djangoproject.com/en/dev/ref/settings/#static-url
STATIC_URL = '/static/'

# See: https://docs.djangoproject.com/en/dev/ref/contrib/staticfiles/#std:setting-STATICFILES_DIRS
STATICFILES_DIRS = (
    root('static'),
)

# TEMPLATE CONFIGURATION
# See: https://docs.djangoproject.com/en/1.11/ref/settings/#templates
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'APP_DIRS': True,
        'DIRS': (
            root('templates'),
        ),
        'OPTIONS': {
            'context_processors': (
                'django.contrib.auth.context_processors.auth',
                'django.template.context_processors.debug',
                'django.template.context_processors.i18n',
                'django.template.context_processors.media',
                'django.template.context_processors.static',
                'django.template.context_processors.tz',
                'django.contrib.messages.context_processors.messages',
                'registrar.apps.core.context_processors.core',
            ),
            'debug': True,  # Django will only display debug pages if the global DEBUG setting is set to True.
        }
    },
]
# END TEMPLATE CONFIGURATION


# COOKIE CONFIGURATION
# The purpose of customizing the cookie names is to avoid conflicts when
# multiple Django services are running behind the same hostname.
# Detailed information at: https://docs.djangoproject.com/en/dev/ref/settings/
SESSION_COOKIE_NAME = 'registrar_sessionid'
CSRF_COOKIE_NAME = 'registrar_csrftoken'
LANGUAGE_COOKIE_NAME = 'openedx-language-preference'
# END COOKIE CONFIGURATION

# AUTHENTICATION CONFIGURATION
LOGIN_URL = '/login/'
LOGOUT_URL = '/logout/'

AUTH_USER_MODEL = 'core.User'

AUTHENTICATION_BACKENDS = (
    'auth_backends.backends.EdXOAuth2',
    'django.contrib.auth.backends.ModelBackend',
    'guardian.backends.ObjectPermissionBackend',
)

ENABLE_AUTO_AUTH = False
AUTO_AUTH_USERNAME_PREFIX = 'auto_auth_'

SOCIAL_AUTH_STRATEGY = 'auth_backends.strategies.EdxDjangoStrategy'

# Set these to the correct values for your OAuth2/OpenID Connect provider (e.g., devstack)
SOCIAL_AUTH_EDX_OAUTH2_KEY = 'registrar-sso-key'
SOCIAL_AUTH_EDX_OAUTH2_SECRET = 'registrar-sso-secret'
SOCIAL_AUTH_EDX_OAUTH2_ENDPOINT = 'replace-me'
SOCIAL_AUTH_EDX_OAUTH2_ISSUER = 'http://127.0.0.1:8000'
SOCIAL_AUTH_EDX_OAUTH2_URL_ROOT = 'http://127.0.0.1:8000'
SOCIAL_AUTH_EDX_OAUTH2_LOGOUT_URL = 'http://127.0.0.1:8000/logout'

# These values are used to make server to server rest api call. Should be fed into edx_rest_api_client
BACKEND_SERVICE_EDX_OAUTH2_KEY = 'registrar-backend-service-key'
BACKEND_SERVICE_EDX_OAUTH2_SECRET = 'registrar-backend-service-secret'
BACKEND_SERVICE_EDX_OAUTH2_PROVIDER_URL = 'http://127.0.0.1:8000/oauth2'

JWT_AUTH = {
    'JWT_AUTH_HEADER_PREFIX': 'JWT',
    'JWT_ISSUERS': [],
    'JWT_ALGORITHM': 'RS512',
    'JWT_VERIFY_EXPIRATION': True,
    'JWT_VERIFY_AUDIENCE': False,
    'JWT_DECODE_HANDLER': 'edx_rest_framework_extensions.auth.jwt.decoder.jwt_decode_handler',
    'JWT_AUTH_COOKIE': 'edx-jwt-cookie',
    'JWT_PUBLIC_SIGNING_JWK_SET': None,
    'JWT_AUTH_COOKIE_HEADER_PAYLOAD': 'edx-jwt-cookie-header-payload',
    'JWT_AUTH_COOKIE_SIGNATURE': 'edx-jwt-cookie-signature',
}

SOCIAL_AUTH_REDIRECT_IS_HTTPS = False

# Request the user's permissions in the ID token
EXTRA_SCOPE = ['permissions']

LOGIN_REDIRECT_URL = '/api-docs/'
# END AUTHENTICATION CONFIGURATION

# Other service locations
LMS_BASE_URL = None
DISCOVERY_BASE_URL = None


# OPENEDX-SPECIFIC CONFIGURATION
PLATFORM_NAME = 'Your Platform Name Here'
# END OPENEDX-SPECIFIC CONFIGURATION

# Set up logging for development use (logging to stdout)
LOGGING = get_logger_config(debug=DEBUG, dev_env=True, local_loglevel='DEBUG')

# API key for segment.io event tracking
SEGMENT_KEY = None

# Publicly-exposed base URLs for service and API
API_ROOT = 'http://127.0.0.1:8000/api'

CERTIFICATE_LANGUAGES = {
    'en': 'English',
    'es_419': 'Spanish',
}

CSRF_COOKIE_SECURE = False
EXTRA_APPS = []
SERVICE_USER = 'registrar_service_user'
SESSION_EXPIRE_AT_BROWSER_CLOSE = False
STATICFILES_STORAGE = 'django.contrib.staticfiles.storage.StaticFilesStorage'
CSRF_TRUSTED_ORIGINS = []
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
    }
}
EDX_DRF_EXTENSIONS = {
    "OAUTH2_USER_INFO_URL": "http://127.0.0.1:8000/oauth2/user_info"
}

# How long (in seconds) we keep program details from Discovery in the cache.
# Defaults to 24 hours.
PROGRAM_CACHE_TIMEOUT = 60 * 60 * 24
