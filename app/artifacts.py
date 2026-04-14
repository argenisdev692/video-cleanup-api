from __future__ import annotations

from pathlib import Path

from app.config import settings


class ArtifactFileLocator:
    _ARTIFACT_MAP = {
        'clean-video': ('render', 'clean-master.mp4'),
        'remotion-manifest': ('remotion', 'manifest.json'),
        'cleaned-audio': ('cleaned', 'voice-clean.wav'),
        'edit-plan': ('edit-plan.json',),
        'report': ('report.md',),
    }

    def resolve(self, *, job_uuid: str, artifact_key: str) -> Path:
        normalized_key = artifact_key.strip().lower()
        parts = self._ARTIFACT_MAP.get(normalized_key)

        if parts is None:
            raise FileNotFoundError(f'Unknown artifact key: {artifact_key}')

        return Path(settings.artifact_root) / job_uuid / Path(*parts)
