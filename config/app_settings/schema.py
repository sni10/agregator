"""Typed configuration sections. Each section reads env vars with its own prefix and validates them."""
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Port = Annotated[int, Field(ge=1, le=65535)]
CommaList = Annotated[list[str], NoDecode]  # "a, b" in env -> ['a', 'b']


def split_commas(value):
    if isinstance(value, str):
        return [item.strip() for item in value.split(',') if item.strip()]
    return value


class Section(BaseSettings):
    model_config = SettingsConfigDict(extra='ignore', env_file_encoding='utf-8')


class DjangoSettings(Section):
    model_config = SettingsConfigDict(env_prefix='DJANGO_')

    secret_key: SecretStr
    debug: bool = False
    allowed_hosts: CommaList = []

    _split_hosts = field_validator('allowed_hosts', mode='before')(split_commas)

    @model_validator(mode='after')
    def check_production(self):
        if self.debug:
            return self
        key = self.secret_key.get_secret_value()
        if len(key) < 50 or key.startswith('django-insecure-'):
            raise ValueError('DJANGO_SECRET_KEY must be a random value of 50+ chars when DJANGO_DEBUG=false')
        if not self.allowed_hosts:
            raise ValueError('DJANGO_ALLOWED_HOSTS is required when DJANGO_DEBUG=false')
        return self


class DatabaseSettings(Section):
    model_config = SettingsConfigDict(env_prefix='DB_')

    name: str
    user: str
    password: SecretStr
    host: str = 'db'  # the compose service
    port: Port = 5432


class StaticSettings(Section):
    model_config = SettingsConfigDict(env_prefix='STATIC_')

    root: Path = Path('staticfiles')  # relative paths are resolved against the project root


class LMStudioSettings(Section):
    """Local inference server (LM Studio -> Developer -> Local Server), OpenAI-compatible API."""

    model_config = SettingsConfigDict(env_prefix='LM_STUDIO_')

    base_url: str = 'http://127.0.0.1:1234/v1'
    api_key: SecretStr = SecretStr('lm-studio')  # LM Studio ignores it unless auth is turned on
    worker_model: str = 'qwen/qwen3.5-9b'  # classification, extraction, claim checks
    writer_model: str = ''  # final English text; empty = worker_model
    embedding_model: str = 'text-embedding-nomic-embed-text-v1.5'
    # Thinking models (Qwen 3.5+) spend the whole max_tokens on reasoning and return empty content unless told
    # otherwise; LM Studio honours reasoning_effort. none = answer right away (extraction, checks).
    reasoning_effort: Literal['none', 'low', 'medium', 'high'] = 'none'
    timeout: int = Field(600, ge=10, description='One request, seconds: a large model offloaded to RAM is slow')
    max_retries: int = Field(2, ge=0, le=5, description='Repeats of a request whose JSON fails validation')


class CollectorSettings(Section):
    model_config = SettingsConfigDict(env_prefix='COLLECTOR_')

    user_agent: str = 'agregator-collector/0.1 (+https://github.com/sni10)'
    timeout: int = Field(20, ge=1, description='One HTTP request, seconds')
    full_text: bool = True  # also download article pages and extract the text (trafilatura); evidence needs it
    max_entries: int = Field(50, ge=1, description='Newest entries taken from one feed per run')


class PipelineSettings(Section):
    """Articles -> events -> facts -> card -> checks -> draft (pipeline/)."""

    model_config = SettingsConfigDict(env_prefix='PIPELINE_')

    window_days: int = Field(3, ge=1, description='Articles older than this are not grouped; events stop growing')
    group_threshold: float = Field(0.8, gt=0, lt=1, description='Embedding similarity to every event member')
    single_source_wait_hours: int = Field(6, ge=0, description='A one-source event waits this long for others')
    max_events: int = Field(10, ge=1, description='Events taken to facts and a card in one run (slow: minutes each)')
    min_facts: int = Field(3, ge=1, description='Fewer verified facts: no card')
    # Model loading through the lms CLI: one big model in VRAM at a time (spec §4)
    manage_models: bool = True
    worker_context: int = 8192
    writer_context: int = 16384
    writer_gpu: str = '0.6'  # offload ratio: 27B Q4 does not fit 12 GB whole
    idle_ttl: int = Field(300, ge=60, description='Seconds before LM Studio unloads an idle model it was told to load')


class PublishingSettings(Section):
    """The site's publishing API (agregator-backend): ready drafts are sent there (publisher/)."""

    model_config = SettingsConfigDict(env_prefix='PUBLISHING_')

    url: str = 'http://localhost:8002'  # the backend's dev compose; the container reaches it at host.docker.internal
    api_key: SecretStr = SecretStr('')  # the backend's PUBLISHING_API_KEY; empty -> nothing is sent
    timeout: float = Field(30, gt=0, description='Seconds per request')
    max_attempts: int = Field(10, ge=1, description='Then the job stays failed until resent from the admin')


class PixabaySettings(Section):
    """Stock photos for drafts (pipeline stage `images`): https://pixabay.com/api/docs/ shows the key when logged in."""

    model_config = SettingsConfigDict(env_prefix='PIXABAY_')

    api_key: SecretStr = SecretStr('')  # empty -> the stage is skipped, stories get the category cover
    timeout: float = Field(30, gt=0, description='Seconds per request')


class AppSettings(Section):
    django: DjangoSettings
    db: DatabaseSettings
    static: StaticSettings
    lm_studio: LMStudioSettings
    collector: CollectorSettings
    pipeline: PipelineSettings
    publishing: PublishingSettings
    pixabay: PixabaySettings
