from __future__ import annotations

import os
from typing import Any

from arq.connections import RedisSettings

from app.export_service import VideoExportService, VideoMergeExportService
from app.schemas import AnalysisRequest, ExportRequest, MergeExportRequest
from app.service import TutorialCleanupAnalysisService


_analysis_service: TutorialCleanupAnalysisService | None = None
_export_service: VideoExportService | None = None
_merge_export_service: VideoMergeExportService | None = None


def _get_analysis_service() -> TutorialCleanupAnalysisService:
    global _analysis_service
    if _analysis_service is None:
        _analysis_service = TutorialCleanupAnalysisService()
    return _analysis_service


def _get_export_service() -> VideoExportService:
    global _export_service
    if _export_service is None:
        _export_service = VideoExportService()
    return _export_service


def _get_merge_export_service() -> VideoMergeExportService:
    global _merge_export_service
    if _merge_export_service is None:
        _merge_export_service = VideoMergeExportService()
    return _merge_export_service


async def run_analysis(ctx: dict[str, Any], payload_dict: dict[str, Any]) -> dict[str, Any]:
    payload = AnalysisRequest.model_validate(payload_dict)
    result = _get_analysis_service().analyze(payload)
    return result.model_dump(mode='json')


async def run_export(ctx: dict[str, Any], payload_dict: dict[str, Any]) -> dict[str, Any]:
    payload = ExportRequest.model_validate(payload_dict)
    result = _get_export_service().export(payload)
    return result.model_dump(mode='json')


async def run_merge_export(ctx: dict[str, Any], payload_dict: dict[str, Any]) -> dict[str, Any]:
    payload = MergeExportRequest.model_validate(payload_dict)
    result = _get_merge_export_service().export(payload)
    return result.model_dump(mode='json')


def build_redis_settings() -> RedisSettings:
    url = (os.getenv('REDIS_URL') or '').strip()
    if url:
        return RedisSettings.from_dsn(url)

    password = (os.getenv('REDIS_PASSWORD') or '').strip() or None
    username = (os.getenv('REDIS_USERNAME') or '').strip() or 'default'
    return RedisSettings(
        host=os.getenv('REDIS_HOST', 'localhost'),
        port=int(os.getenv('REDIS_PORT', '6379') or '6379'),
        password=password,
        username=username if password else None,
    )


class WorkerSettings:
    functions = [run_analysis, run_export, run_merge_export]
    redis_settings = build_redis_settings()
    job_timeout = 60 * 60 * 10        # 10h per job — long batches of video
    keep_result = 60 * 60 * 24 * 3    # keep result in Redis for 3 days
    max_jobs = 1                      # serialize CPU-heavy video jobs per worker
    max_tries = 1                     # no automatic retry for idempotency reasons
