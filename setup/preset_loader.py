from __future__ import annotations
from pathlib import Path
import structlog
import yaml
from setup.models import Preset
log: structlog.BoundLogger = structlog.get_logger(__name__)

class PresetLoader:

    def __init__(self, preset_dir: Path) -> None:
        self._preset_dir = preset_dir
        self._presets: dict[str, Preset] = {}
        self.load_all()

    def load_all(self) -> dict[str, Preset]:
        loaded: dict[str, Preset] = {}
        if not self._preset_dir.exists():
            log.warning('preset_loader.dir_missing', path=str(self._preset_dir))
            self._presets = loaded
            return loaded
        for yml_path in sorted(self._preset_dir.glob('*.yml')):
            try:
                raw = yaml.safe_load(yml_path.read_text(encoding='utf-8')) or {}
                preset = Preset(**raw)
                loaded[preset.name] = preset
                log.info('preset_loader.loaded', name=preset.name, path=str(yml_path))
            except Exception as exc:
                log.warning('preset_loader.parse_error', path=str(yml_path), error=str(exc), exc_info=exc)
        self._presets = loaded
        return loaded

    def get(self, name: str) -> Preset | None:
        return self._presets.get(name)

    def names(self) -> list[str]:
        return sorted(self._presets.keys())

    def reload(self) -> int:
        self.load_all()
        count = len(self._presets)
        log.info('preset_loader.reloaded', count=count)
        return count

    def save(self, preset: Preset, filename: str, overwrite: bool=False) -> Path:
        self._preset_dir.mkdir(parents=True, exist_ok=True)
        target = self._preset_dir / filename
        if target.exists() and (not overwrite):
            raise FileExistsError(f"preset file '{filename}' already exists in {self._preset_dir}. pass overwrite=true to replace it.")
        data = preset.model_dump()
        yml_text = yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False, indent=2)
        target.write_text(yml_text, encoding='utf-8')
        log.info('preset_loader.saved', name=preset.name, path=str(target))
        return target