"""
Django settings: the local intelligence pipeline (sources, LLM, evaluation). Runs on the owner's PC only.

For the full list of settings and their values, see
https://docs.djangoproject.com/en/6.1/ref/settings/
"""

import os
from pathlib import Path

from .app_settings import load_settings

BASE_DIR = Path(__file__).resolve().parent.parent

# All environment-specific values come from env vars / .env, validated in config/app_settings.
ENV = load_settings(BASE_DIR / '.env')

SECRET_KEY = ENV.django.secret_key.get_secret_value()
DEBUG = ENV.django.debug
ALLOWED_HOSTS = ENV.django.allowed_hosts

# CI sets TEST_REPORTS_DIR: tests also write a JUnit report there (unittest-xml-reporting, a dev dependency)
if os.environ.get('TEST_REPORTS_DIR'):
    TEST_RUNNER = 'xmlrunner.extra.djangotestrunner.XMLTestRunner'
    TEST_OUTPUT_DIR = os.environ['TEST_REPORTS_DIR']
    TEST_OUTPUT_FILE_NAME = 'junit.xml'


INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'sources',
    'llm',
    'evaluation',
    'pipeline',
    'publisher',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',  # admin static under gunicorn, no web server in the local stack
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'


DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': ENV.db.name,
        'USER': ENV.db.user,
        'PASSWORD': ENV.db.password.get_secret_value(),
        'HOST': ENV.db.host,
        'PORT': ENV.db.port,
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / ENV.static.root

MEDIA_ROOT = BASE_DIR / 'media'  # downloaded stock photos, until they are on the site

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LM_STUDIO = ENV.lm_studio
COLLECTOR = ENV.collector
PIPELINE = ENV.pipeline
PUBLISHING = ENV.publishing
PIXABAY = ENV.pixabay
