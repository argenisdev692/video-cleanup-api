from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import boto3
from botocore.client import Config as BotoConfig

from app.config import settings
from app.schemas import ArtifactsPayload


class ArtifactWriter:
    def __init__(self) -> None:
        self._s3_client: Any | None = None

    def _get_s3_client(self) -> Any:
        if self._s3_client is None:
            if not all([settings.r2_endpoint, settings.r2_access_key_id, settings.r2_secret_access_key, settings.r2_bucket_name]):
                raise RuntimeError('R2 storage configuration is incomplete')
            
            self._s3_client = boto3.client(
                's3',
                endpoint_url=settings.r2_endpoint,
                aws_access_key_id=settings.r2_access_key_id,
                aws_secret_access_key=settings.r2_secret_access_key,
                config=BotoConfig(signature_version='s3v4'),
            )
        return self._s3_client

    def upload_to_r2(self, *, local_path: Path, remote_key: str) -> str:
        s3 = self._get_s3_client()
        s3.upload_file(str(local_path), settings.r2_bucket_name, remote_key)

        public_url = f"{settings.r2_public_base_url}/{remote_key}"
        return public_url

    def delete_from_r2(self, *, remote_key: str) -> None:
        s3 = self._get_s3_client()
        s3.delete_object(Bucket=settings.r2_bucket_name, Key=remote_key)

    def extract_r2_key(self, url: str) -> str | None:
        if not url:
            return None
        candidate = url.strip()
        public_base = (settings.r2_public_base_url or '').rstrip('/')
        if public_base and candidate.startswith(public_base + '/'):
            return candidate[len(public_base) + 1:]
        endpoint_base = (settings.r2_endpoint or '').rstrip('/')
        bucket = settings.r2_bucket_name or ''
        if endpoint_base and bucket and candidate.startswith(f'{endpoint_base}/{bucket}/'):
            return candidate[len(endpoint_base) + len(bucket) + 2:]
        return None
    def write(
        self,
        *,
        job_uuid: str,
        internal_alignment_payload: list[dict[str, Any]] | None,
        edit_plan_payload: list[dict[str, Any]],
        report_markdown: str,
        extra_json_payloads: dict[str, Any] | None = None,
        extra_artifact_paths: dict[str, str] | None = None,
    ) -> ArtifactsPayload:
        base_dir = Path(settings.artifact_root) / job_uuid
        base_dir.mkdir(parents=True, exist_ok=True)

        edit_plan_path = base_dir / 'edit-plan.json'
        report_path = base_dir / 'report.md'

        edit_plan_path.write_text(
            json.dumps(edit_plan_payload, ensure_ascii=False, indent=2),
            encoding='utf-8',
        )
        report_path.write_text(report_markdown, encoding='utf-8')

        extra_artifacts: dict[str, str | None] = {}

        if internal_alignment_payload is not None:
            internal_alignment_path = base_dir / 'internal-alignment.json'
            internal_alignment_path.write_text(
                json.dumps(internal_alignment_payload, ensure_ascii=False, indent=2),
                encoding='utf-8',
            )

        for filename, payload in (extra_json_payloads or {}).items():
            target_path = base_dir / filename
            target_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding='utf-8',
            )
            extra_artifacts[filename.replace('-', '_').replace('.json', '_path')] = str(target_path)

        for key, value in (extra_artifact_paths or {}).items():
            extra_artifacts[key] = value

        return ArtifactsPayload(
            report_md_path=str(report_path),
            edit_plan_json_path=str(edit_plan_path),
            **extra_artifacts,
        )
