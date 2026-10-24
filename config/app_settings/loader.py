from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from pydantic import ValidationError

from .schema import AppSettings, Section


def load_settings(env_file: Path | None) -> AppSettings:
    """Load every section from the environment and `env_file` (env vars win).

    Validates all sections before failing, so one error message lists every bad variable.
    """
    sections, errors = {}, []
    for name, field in AppSettings.model_fields.items():
        section_cls = field.annotation
        try:
            sections[name] = section_cls(_env_file=env_file)
        except ValidationError as exc:
            errors += [_describe(section_cls, error) for error in exc.errors()]
    if errors:
        raise ImproperlyConfigured('Invalid configuration:\n' + '\n'.join(errors))
    return AppSettings(**sections)


def _describe(section_cls: type[Section], error) -> str:
    prefix = section_cls.model_config['env_prefix']
    var = prefix + str(error['loc'][0]).upper() if error['loc'] else prefix + '*'
    return f'  {var}: {error["msg"]}'
