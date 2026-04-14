from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.config import settings
from app.models import ResolvedInput


class InputResolver:
    def __init__(self) -> None:
        self.download_root = Path(settings.artifact_root) / '_downloads'
        self.download_root.mkdir(parents=True, exist_ok=True)

    def resolve(self, reference: str, *, kind: str) -> ResolvedInput:
        normalized = self._apply_path_map(reference.strip())
        parsed = urlparse(normalized)

        if parsed.scheme in {'http', 'https'}:
            return self._download_remote(normalized, kind=kind)

        if parsed.scheme == 'file':
            file_path = Path(parsed.path)
            if file_path.exists():
                return ResolvedInput(kind=kind, reference=reference, local_path=file_path, source='file-url')

        local_path = self._resolve_local_path(normalized)
        if local_path is not None:
            return ResolvedInput(kind=kind, reference=reference, local_path=local_path, source='local')

        raise FileNotFoundError(f'Unable to resolve {kind} input: {reference}')

    def _apply_path_map(self, reference: str) -> str:
        mapped_from = settings.path_map_from.strip()
        mapped_to = settings.path_map_to.strip()
        if mapped_from and mapped_to and reference.lower().startswith(mapped_from.lower()):
            suffix = reference[len(mapped_from):].lstrip('\\/')
            return str(Path(mapped_to) / suffix.replace('\\', '/'))
        return reference

    def _resolve_local_path(self, reference: str) -> Path | None:
        candidate = Path(reference)
        if candidate.exists():
            return candidate

        for root in self._candidate_roots():
            combined = root / reference
            if combined.exists():
                return combined

        return None

    def _candidate_roots(self) -> list[Path]:
        roots = [Path.cwd()]
        raw_roots = settings.local_input_roots
        iterable_roots = (
            [part.strip() for part in raw_roots.split(';')]
            if isinstance(raw_roots, str)
            else list(raw_roots)
        )
        for raw in iterable_roots:
            raw = raw.strip()
            if not raw:
                continue
            roots.append(Path(raw))
        unique: list[Path] = []
        seen: set[str] = set()
        for root in roots:
            key = str(root)
            if key in seen:
                continue
            seen.add(key)
            unique.append(root)
        return unique

    def _download_remote(self, url: str, *, kind: str) -> ResolvedInput:
        if not settings.allow_remote_downloads:
            raise FileNotFoundError(f'Remote downloads are disabled for {kind}: {url}')

        parsed = urlparse(url)
        suffix = Path(parsed.path).suffix or ('.pdf' if kind == 'script_pdf' else '.bin')
        filename = hashlib.sha256(url.encode('utf-8')).hexdigest() + suffix
        destination = self.download_root / filename

        if not destination.exists():
            with httpx.Client(timeout=settings.download_timeout_seconds, follow_redirects=True) as client:
                response = client.get(url)
                response.raise_for_status()
                destination.write_bytes(response.content)

        return ResolvedInput(
            kind=kind,
            reference=url,
            local_path=destination,
            source='remote',
            downloaded=True,
        )
