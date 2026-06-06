from __future__ import annotations
import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


REGISTRY_PATH = Path.home() / '.depthfusion' / 'projects.json'


@dataclass
class ProjectEntry:
    slug: str
    name: str
    local_path: str
    github_url: str = ''
    registered_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_synced: Optional[str] = None
    description: str = ''


class ProjectRegistry:
    def __init__(self, registry_path: Path = REGISTRY_PATH):
        self._path = registry_path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict:
        if self._path.exists():
            return json.loads(self._path.read_text(encoding='utf-8'))
        return {}

    def _save(self, data: dict) -> None:
        tmp = self._path.with_suffix('.tmp')
        tmp.write_text(json.dumps(data, indent=2), encoding='utf-8')
        os.replace(tmp, self._path)

    def register(self, entry: ProjectEntry) -> ProjectEntry:
        data = self._load()
        data[entry.slug] = asdict(entry)
        self._save(data)
        return entry

    def list_projects(self) -> list[ProjectEntry]:
        data = self._load()
        return [ProjectEntry(**v) for v in data.values()]

    def get(self, slug: str) -> Optional[ProjectEntry]:
        data = self._load()
        if slug in data:
            return ProjectEntry(**data[slug])
        return None

    def update_last_synced(self, slug: str) -> None:
        data = self._load()
        if slug in data:
            data[slug]['last_synced'] = datetime.now(timezone.utc).isoformat()
            self._save(data)
