from __future__ import annotations

from typing import Any

from arq import create_pool
from arq.jobs import Job, JobStatus

from app.worker import build_redis_settings


async def enqueue_job(task_name: str, payload: dict[str, Any], job_uuid: str) -> None:
    pool = await create_pool(build_redis_settings())
    try:
        await pool.enqueue_job(task_name, payload, _job_id=job_uuid)
    finally:
        await pool.close()


async def get_job_state(job_uuid: str) -> dict[str, Any]:
    pool = await create_pool(build_redis_settings())
    try:
        job = Job(job_uuid, redis=pool)
        status = await job.status()

        payload: dict[str, Any] = {
            'job_uuid': job_uuid,
            'status': status.value if isinstance(status, JobStatus) else str(status),
        }

        if status == JobStatus.complete:
            try:
                result = await job.result(timeout=1)
                payload['result'] = result
            except Exception as exc:
                payload['error'] = str(exc)
        elif status == JobStatus.in_progress:
            info = await job.info()
            if info is not None:
                payload['started_at'] = info.start_time.isoformat() if info.start_time else None
                payload['enqueued_at'] = info.enqueue_time.isoformat() if info.enqueue_time else None

        return payload
    finally:
        await pool.close()
