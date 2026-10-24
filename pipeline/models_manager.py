"""One big model in VRAM at a time (spec §4): the worker (with the embedding model) by day, the writer for the
cards. Through LM Studio's `lms` CLI, with explicit context and parallelism: LM Studio's JIT loading takes the
model's defaults (4 parallel slots, large context) and keeps a 27B half in RAM next to a 9B."""
import json
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass


class ModelLoadError(Exception):
    pass


@dataclass(frozen=True)
class LoadSpec:
    model: str
    context: int | None = None
    gpu: str | None = None  # offload ratio, None = LM Studio decides

    def args(self, ttl: int) -> list[str]:
        args = ['load', self.model, '--parallel', '1', '--ttl', str(ttl), '-y']
        if self.context:
            args += ['--context-length', str(self.context)]
        if self.gpu:
            args += ['--gpu', self.gpu]
        return args


class LmsModelManager:
    def __init__(self, ttl: int, run: Callable[[list[str]], str] | None = None, progress=lambda line: None):
        self.ttl = ttl
        self.run = run or _run_lms
        self.progress = progress

    def use(self, *specs: LoadSpec) -> None:
        """Exactly these models loaded: others are unloaded first, so VRAM is free for the big one."""
        loaded = self.loaded()
        wanted = {spec.model for spec in specs}
        for model in loaded - wanted:
            self.progress(f'unload {model}')
            self.run(['unload', model])
        for spec in specs:
            if spec.model not in loaded:
                self.progress(f'load {spec.model}')
                self.run(spec.args(self.ttl))

    def loaded(self) -> set[str]:
        items = json.loads(self.run(['ps', '--json']) or '[]')
        return {item.get('identifier') or item.get('modelKey') or item.get('path') for item in items} - {None}


class NoModelManager:
    """PIPELINE_MANAGE_MODELS=false: LM Studio loads models on demand (JIT) itself."""

    def use(self, *specs: LoadSpec) -> None:
        pass


def _run_lms(args: list[str]) -> str:
    executable = shutil.which('lms')
    if not executable:
        raise ModelLoadError('lms CLI not found: install it from LM Studio or set PIPELINE_MANAGE_MODELS=false')
    result = subprocess.run([executable, *args], capture_output=True, text=True, encoding='utf-8', timeout=600)
    if result.returncode:
        raise ModelLoadError(f'lms {" ".join(args)}: {result.stderr.strip() or result.stdout.strip()}')
    return result.stdout
