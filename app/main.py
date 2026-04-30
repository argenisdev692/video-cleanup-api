from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from arq import create_pool
from arq.connections import ArqRedis
from arq.jobs import Job, JobStatus
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel

from app.artifacts import ArtifactFileLocator
from app.config import settings
from app.export_service import VideoExportService, VideoMergeExportService
from app.schemas import (
    AnalysisRequest,
    AnalysisResponse,
    BatchAnalysisRequest,
    BatchEnqueueResponse,
    BatchExportRequest,
    BatchMergeExportRequest,
    EnqueuedJob,
    ExportRequest,
    ExportResponse,
    HealthResponse,
    JobStatusResponse,
    MergeExportRequest,
    MergeExportResponse,
)
from app.service import TutorialCleanupAnalysisService
from app.worker import build_redis_settings

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Open a single arq Redis pool shared across all enqueue requests.
    arq_pool: ArqRedis | None = None
    try:
        arq_pool = await create_pool(build_redis_settings())
        app.state.arq_pool = arq_pool
        logger.info('arq Redis pool initialised')
    except Exception as exception:  # noqa: BLE001
        app.state.arq_pool = None
        logger.warning('arq Redis pool unavailable: %s', exception)
    try:
        yield
    finally:
        if arq_pool is not None:
            try:
                await arq_pool.close(close_connection_pool=True)
            except TypeError:
                # Older redis-py: close() doesn't accept the kwarg.
                await arq_pool.close()


app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)
analysis_service = TutorialCleanupAnalysisService()
export_service = VideoExportService()
merge_export_service = VideoMergeExportService()
artifact_locator = ArtifactFileLocator()


async def get_arq_pool() -> ArqRedis:
    pool: ArqRedis | None = getattr(app.state, 'arq_pool', None)
    if pool is None:
        raise HTTPException(
            status_code=503,
            detail='Job queue unavailable: Redis pool not initialised. Check REDIS_URL.',
        )
    return pool


async def _enqueue(
    pool: ArqRedis,
    function_name: str,
    payload: BaseModel,
    job_uuid: str,
) -> EnqueuedJob:
    try:
        job = await pool.enqueue_job(
            function_name,
            payload.model_dump(mode='json'),
            _job_id=job_uuid,
        )
    except Exception as exception:  # noqa: BLE001
        logger.exception('Failed to enqueue %s for %s', function_name, job_uuid)
        return EnqueuedJob(
            job_uuid=job_uuid,
            job_id=None,
            queue_status='error',
            detail=str(exception),
        )

    if job is None:
        # arq returns None when a job with the same _job_id already exists.
        return EnqueuedJob(
            job_uuid=job_uuid,
            job_id=job_uuid,
            queue_status='duplicate',
            detail='A job with this job_uuid is already enqueued or in progress.',
        )

    return EnqueuedJob(
        job_uuid=job_uuid,
        job_id=job.job_id,
        queue_status='queued',
    )


@app.get('/')
def root() -> RedirectResponse:
    return RedirectResponse(url='/docs')


def require_api_token(authorization: str | None = Header(default=None)) -> None:
    expected_token = settings.api_token.strip()

    if expected_token == '':
        return

    if authorization is None or not authorization.startswith('Bearer '):
        raise HTTPException(
            status_code=401,
            detail='Not authenticated',
            headers={'WWW-Authenticate': 'Bearer'},
        )

    provided_token = authorization.removeprefix('Bearer ').strip()
    if provided_token != expected_token:
        raise HTTPException(
            status_code=401,
            detail='Invalid authentication credentials',
            headers={'WWW-Authenticate': 'Bearer'},
        )


@app.get('/health', response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status='ok',
        service=settings.app_name,
        version=settings.app_version,
    )


@app.post(
    '/analysis/jobs/sync',
    response_model=AnalysisResponse,
    responses={
        422: {'description': 'Invalid or missing input files'},
        500: {'description': 'Analysis processing error'},
    },
)
def analyze_sync(
    payload: AnalysisRequest,
    _: None = Depends(require_api_token),
) -> AnalysisResponse:
    try:
        return analysis_service.analyze(payload)
    except HTTPException:
        raise
    except FileNotFoundError as exception:
        raise HTTPException(status_code=422, detail=str(exception)) from exception
    except RuntimeError as exception:
        raise HTTPException(status_code=422, detail=str(exception)) from exception
    except Exception as exception:
        raise HTTPException(status_code=500, detail=str(exception)) from exception


@app.post(
    '/video-export',
    response_model=ExportResponse,
    responses={
        422: {'description': 'Invalid or missing input files'},
        500: {'description': 'Export processing error'},
    },
)
def video_export(
    payload: ExportRequest,
    _: None = Depends(require_api_token),
) -> ExportResponse:
    try:
        return export_service.export(payload)
    except HTTPException:
        raise
    except FileNotFoundError as exception:
        raise HTTPException(status_code=422, detail=str(exception)) from exception
    except RuntimeError as exception:
        raise HTTPException(status_code=422, detail=str(exception)) from exception
    except Exception as exception:
        raise HTTPException(status_code=500, detail=str(exception)) from exception


@app.post(
    '/video-export-merge',
    response_model=MergeExportResponse,
    responses={
        422: {'description': 'Invalid or missing input files'},
        500: {'description': 'Merge export processing error'},
    },
)
def video_export_merge(
    payload: MergeExportRequest,
    _: None = Depends(require_api_token),
) -> MergeExportResponse:
    try:
        return merge_export_service.export(payload)
    except HTTPException:
        raise
    except FileNotFoundError as exception:
        raise HTTPException(status_code=422, detail=str(exception)) from exception
    except RuntimeError as exception:
        raise HTTPException(status_code=422, detail=str(exception)) from exception
    except Exception as exception:
        raise HTTPException(status_code=500, detail=str(exception)) from exception


@app.get('/artifacts/{job_uuid}/{artifact_key}')
def get_artifact(
    job_uuid: str,
    artifact_key: str,
    _: None = Depends(require_api_token),
) -> FileResponse:
    try:
        artifact_path = artifact_locator.resolve(job_uuid=job_uuid, artifact_key=artifact_key)
    except FileNotFoundError as exception:
        raise HTTPException(status_code=404, detail=str(exception)) from exception

    if not artifact_path.exists() or not artifact_path.is_file():
        raise HTTPException(status_code=404, detail='Artifact file was not found')

    return FileResponse(path=artifact_path)


@app.get('/download/{job_uuid}')
def download_final_video(
    job_uuid: str,
    _: None = Depends(require_api_token),
) -> FileResponse:
    """Download the final processed video with title overlays applied."""
    output_dir = Path(settings.artifact_root) / job_uuid / 'render'
    final_video_path = output_dir / 'final-with-titles.mp4'
    
    # Fallback to clean master if final-with-titles doesn't exist
    if not final_video_path.exists():
        final_video_path = output_dir / 'clean-master.mp4'
    
    if not final_video_path.exists():
        raise HTTPException(status_code=404, detail='Final video not found')
    
    return FileResponse(
        path=final_video_path,
        filename=f'{job_uuid}-final.mp4',
        media_type='video/mp4',
    )


# ---------------------------------------------------------------------------
# Async queue endpoints (arq + Redis)
# Enqueue jobs here; the separate worker service (arq app.worker.WorkerSettings)
# consumes them.
# ---------------------------------------------------------------------------


@app.post('/jobs/video-export', response_model=EnqueuedJob)
async def enqueue_video_export(
    payload: ExportRequest,
    _: None = Depends(require_api_token),
    pool: ArqRedis = Depends(get_arq_pool),
) -> EnqueuedJob:
    return await _enqueue(pool, 'run_export', payload, payload.job_uuid)


@app.post('/jobs/video-export/batch', response_model=BatchEnqueueResponse)
async def enqueue_video_export_batch(
    payload: BatchExportRequest,
    _: None = Depends(require_api_token),
    pool: ArqRedis = Depends(get_arq_pool),
) -> BatchEnqueueResponse:
    results: list[EnqueuedJob] = []
    for item in payload.items:
        results.append(await _enqueue(pool, 'run_export', item, item.job_uuid))
    return _summarise_batch(results)


@app.post('/jobs/video-export-merge', response_model=EnqueuedJob)
async def enqueue_video_export_merge(
    payload: MergeExportRequest,
    _: None = Depends(require_api_token),
    pool: ArqRedis = Depends(get_arq_pool),
) -> EnqueuedJob:
    return await _enqueue(pool, 'run_merge_export', payload, payload.job_uuid)


@app.post('/jobs/video-export-merge/batch', response_model=BatchEnqueueResponse)
async def enqueue_video_export_merge_batch(
    payload: BatchMergeExportRequest,
    _: None = Depends(require_api_token),
    pool: ArqRedis = Depends(get_arq_pool),
) -> BatchEnqueueResponse:
    results: list[EnqueuedJob] = []
    for item in payload.items:
        results.append(await _enqueue(pool, 'run_merge_export', item, item.job_uuid))
    return _summarise_batch(results)


@app.post('/jobs/analysis', response_model=EnqueuedJob)
async def enqueue_analysis(
    payload: AnalysisRequest,
    _: None = Depends(require_api_token),
    pool: ArqRedis = Depends(get_arq_pool),
) -> EnqueuedJob:
    return await _enqueue(pool, 'run_analysis', payload, payload.job_uuid)


@app.post('/jobs/analysis/batch', response_model=BatchEnqueueResponse)
async def enqueue_analysis_batch(
    payload: BatchAnalysisRequest,
    _: None = Depends(require_api_token),
    pool: ArqRedis = Depends(get_arq_pool),
) -> BatchEnqueueResponse:
    results: list[EnqueuedJob] = []
    for item in payload.items:
        results.append(await _enqueue(pool, 'run_analysis', item, item.job_uuid))
    return _summarise_batch(results)


@app.get('/jobs/{job_id}', response_model=JobStatusResponse)
async def get_job_status(
    job_id: str,
    _: None = Depends(require_api_token),
    pool: ArqRedis = Depends(get_arq_pool),
) -> JobStatusResponse:
    job = Job(job_id, pool)
    status: JobStatus = await job.status()

    if status == JobStatus.not_found:
        return JobStatusResponse(job_id=job_id, status=status.value)

    info = await job.info()

    def _iso(value: Any) -> str | None:
        return value.isoformat() if value is not None else None

    response = JobStatusResponse(
        job_id=job_id,
        status=status.value,
        enqueue_time=_iso(getattr(info, 'enqueue_time', None)),
        start_time=_iso(getattr(info, 'start_time', None)),
        finish_time=_iso(getattr(info, 'finish_time', None)),
        function=getattr(info, 'function', None),
        queue_name=getattr(info, 'queue_name', None),
    )

    # JobResult (only present when complete) carries success + result directly,
    # avoiding an extra await job.result() round-trip.
    success_attr = getattr(info, 'success', None)
    if success_attr is not None:
        response.success = bool(success_attr)
        result_attr = getattr(info, 'result', None)
        if response.success:
            response.result = result_attr
        else:
            response.error = str(result_attr) if result_attr is not None else 'job failed'
    elif status == JobStatus.complete:
        try:
            result: Any = await job.result(timeout=0.1)
            response.success = True
            response.result = result
        except Exception as exception:  # noqa: BLE001
            response.success = False
            response.error = str(exception)

    return response


@app.delete('/jobs/{job_id}', response_model=JobStatusResponse)
async def abort_job(
    job_id: str,
    _: None = Depends(require_api_token),
    pool: ArqRedis = Depends(get_arq_pool),
) -> JobStatusResponse:
    job = Job(job_id, pool)
    status: JobStatus = await job.status()
    if status == JobStatus.not_found:
        raise HTTPException(status_code=404, detail='Job not found')
    await job.abort()
    return JobStatusResponse(job_id=job_id, status='aborted')


def _summarise_batch(results: list[EnqueuedJob]) -> BatchEnqueueResponse:
    queued = sum(1 for r in results if r.queue_status == 'queued')
    duplicates = sum(1 for r in results if r.queue_status == 'duplicate')
    errors = sum(1 for r in results if r.queue_status == 'error')
    return BatchEnqueueResponse(
        total=len(results),
        queued=queued,
        duplicates=duplicates,
        errors=errors,
        jobs=results,
    )
