from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, RedirectResponse

from app.artifacts import ArtifactFileLocator
from app.config import settings
from app.export_service import VideoExportService, VideoMergeExportService
from app.jobs import enqueue_job, get_job_state
from app.schemas import AnalysisRequest, AnalysisResponse, ExportRequest, ExportResponse, HealthResponse, MergeExportRequest, MergeExportResponse
from app.service import TutorialCleanupAnalysisService


app = FastAPI(title=settings.app_name, version=settings.app_version)
analysis_service = TutorialCleanupAnalysisService()
export_service = VideoExportService()
merge_export_service = VideoMergeExportService()
artifact_locator = ArtifactFileLocator()


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


@app.post(
    '/analysis/jobs/async',
    status_code=202,
    responses={
        202: {'description': 'Job accepted and queued'},
        500: {'description': 'Queue error'},
    },
)
async def analyze_async_enqueue(
    payload: AnalysisRequest,
    _: None = Depends(require_api_token),
) -> dict:
    try:
        await enqueue_job('run_analysis', payload.model_dump(mode='json'), payload.job_uuid)
    except Exception as exception:
        raise HTTPException(status_code=500, detail=f'Failed to enqueue job: {exception}') from exception
    return {'job_uuid': payload.job_uuid, 'status': 'queued'}


@app.post(
    '/video-export/async',
    status_code=202,
    responses={
        202: {'description': 'Job accepted and queued'},
        500: {'description': 'Queue error'},
    },
)
async def video_export_async_enqueue(
    payload: ExportRequest,
    _: None = Depends(require_api_token),
) -> dict:
    try:
        await enqueue_job('run_export', payload.model_dump(mode='json'), payload.job_uuid)
    except Exception as exception:
        raise HTTPException(status_code=500, detail=f'Failed to enqueue job: {exception}') from exception
    return {'job_uuid': payload.job_uuid, 'status': 'queued'}


@app.post(
    '/video-export-merge/async',
    status_code=202,
    responses={
        202: {'description': 'Job accepted and queued'},
        500: {'description': 'Queue error'},
    },
)
async def video_export_merge_async_enqueue(
    payload: MergeExportRequest,
    _: None = Depends(require_api_token),
) -> dict:
    try:
        await enqueue_job('run_merge_export', payload.model_dump(mode='json'), payload.job_uuid)
    except Exception as exception:
        raise HTTPException(status_code=500, detail=f'Failed to enqueue job: {exception}') from exception
    return {'job_uuid': payload.job_uuid, 'status': 'queued'}


@app.get('/jobs/{job_uuid}')
async def get_job(
    job_uuid: str,
    _: None = Depends(require_api_token),
) -> dict:
    try:
        return await get_job_state(job_uuid)
    except Exception as exception:
        raise HTTPException(status_code=500, detail=f'Failed to query job: {exception}') from exception


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
