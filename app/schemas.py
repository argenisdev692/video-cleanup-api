from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.config import settings


class SourcePayload(BaseModel):
    video_path: str | None = None
    video_paths: list[str] = Field(default_factory=list)
    title_video_paths: list[str] = Field(default_factory=list)
    script_pdf_path: str | None = None

    @model_validator(mode='after')
    def validate_video_input(self) -> 'SourcePayload':
        if not self.video_path and not self.video_paths:
            raise ValueError('Either video_path or video_paths must be provided')
        return self


class TitleOverlay(BaseModel):
    video_path: str
    start_seconds: float
    duration_seconds: float
    title: str | None = None


class RulesPayload(BaseModel):
    pause_keywords: list[str] = Field(default_factory=lambda: [
        'PAUSA ACA',
        'PAUSA ACÁ',
        'PAUSA A CA',
        'PAUSA A CÁ',
        'PAUSAACA',
        'PAUSAACÁ',
        'PASA ACA',
        'PASA ACÁ',
        'PAUZA ACA',
        'PAUZA ACÁ',
        'PAUSA',
        'PAUZA',
    ])
    silence_threshold_seconds: float = 3.0
    silence_trim_to_seconds: float | None = None
    detect_fillers: bool = True
    detect_repeated_words: bool = True
    detect_self_corrections: bool = True
    store_artifacts: bool = True


class AnalysisRequest(BaseModel):
    job_uuid: str
    title: str = Field(min_length=1, max_length=255)
    language: str = 'es'
    target_duration_minutes: int = Field(default=60, ge=1, le=240)
    max_duration_minutes: int = Field(default=70, ge=1, le=240)
    source: SourcePayload
    rules: RulesPayload
    editorial_prompt: str | None = None
    title_overlays: list[TitleOverlay] = Field(default_factory=list)

    @model_validator(mode='after')
    def validate_durations(self) -> 'AnalysisRequest':
        if self.max_duration_minutes < self.target_duration_minutes:
            raise ValueError('max_duration_minutes must be greater than or equal to target_duration_minutes')
        return self


class SummaryPayload(BaseModel):
    original_duration_seconds: int
    estimated_final_duration_seconds: int
    time_saved_seconds: int
    learning_objectives_met: bool


class CoverageSection(BaseModel):
    title: str | None = None
    expected_minutes: float | None = None
    actual_minutes: float | None = None
    status: str | None = None


class CoveragePayload(BaseModel):
    sections: list[CoverageSection] = Field(default_factory=list)
    missing_topics: list[str] = Field(default_factory=list)
    overextended_topics: list[str] = Field(default_factory=list)


class EditPlanItem(BaseModel):
    start: str | None = None
    end: str | None = None
    action: str | None = None
    reason: str | None = None
    observation: str | None = None
    confidence: float | None = None


class ArtifactsPayload(BaseModel):
    model_config = ConfigDict(extra='allow')

    cleaned_audio_path: str | None = None
    clean_video_path: str | None = None
    final_video_path: str | None = None
    remotion_manifest_path: str | None = None
    report_md_path: str | None = None
    edit_plan_json_path: str | None = None
    storage_url: str | None = None


class AnalysisResponse(BaseModel):
    job_uuid: str
    status: str = 'completed'
    summary: SummaryPayload
    coverage: CoveragePayload
    edit_plan: list[EditPlanItem] = Field(default_factory=list)
    artifacts: ArtifactsPayload | None = None
    diagnostics: dict[str, Any] = Field(default_factory=dict)


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str


class ExportRequest(BaseModel):
    job_uuid: str
    video_paths: list[str] = Field(min_length=1)
    silence_threshold_seconds: float = Field(default=3.0, ge=0.1, le=10.0)
    silence_trim_to_seconds: float | None = Field(default=None, ge=0.1, le=5.0)
    pause_keywords: list[str] = Field(default_factory=lambda: [
        'PAUSA ACA',
        'PAUSA ACÁ',
        'PAUSA A CA',
        'PAUSA A CÁ',
        'PAUSAACA',
        'PAUSAACÁ',
        'PASA ACA',
        'PASA ACÁ',
        'PAUZA ACA',
        'PAUZA ACÁ',
        'PAUSA',
        'PAUZA',
    ])
    detect_fillers: bool = True
    filler_terms: list[str] = Field(default_factory=lambda: list(settings.filler_terms))
    compact_word_gaps: bool = True
    word_gap_threshold_seconds: float = Field(default=0.65, ge=0.2, le=2.0)
    word_gap_trim_to_seconds: float = Field(default=0.35, ge=0.05, le=0.6)
    detect_stutters: bool = True
    stutter_max_gap_seconds: float = Field(default=0.4, ge=0.05, le=1.5)
    stutter_max_token_chars: int = Field(default=5, ge=1, le=10)
    pause_backtrack_silence_threshold_seconds: float = Field(default=0.4, ge=0.1, le=2.0)
    pause_backtrack_max_seconds: float = Field(default=8.0, ge=1.0, le=30.0)
    cleanup_intermediates: bool = True
    cleanup_remote_inputs: bool = True
    language: str = 'es'

    @model_validator(mode='after')
    def validate_video_paths(self) -> 'ExportRequest':
        if not self.video_paths:
            raise ValueError('video_paths must contain at least one path')
        return self


class ExportResponse(BaseModel):
    job_uuid: str
    status: str = 'completed'
    output_path: str
    storage_url: str | None = None
    duration_seconds: float
    silence_cuts: int
    diagnostics: dict[str, Any] = Field(default_factory=dict)


class MergeExportRequest(BaseModel):
    job_uuid: str
    video_paths: list[str] = Field(min_length=1)
    cleanup_intermediates: bool = True
    cleanup_remote_inputs: bool = True


class MergeExportResponse(BaseModel):
    job_uuid: str
    status: str = 'completed'
    output_path: str
    storage_url: str | None = None
    duration_seconds: float
    diagnostics: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Async queue (arq + Redis) schemas
# ---------------------------------------------------------------------------


class EnqueuedJob(BaseModel):
    job_uuid: str
    job_id: str | None = None
    queue_status: str  # queued | duplicate | error
    detail: str | None = None


class BatchExportRequest(BaseModel):
    items: list[ExportRequest] = Field(min_length=1, max_length=100)


class BatchAnalysisRequest(BaseModel):
    items: list[AnalysisRequest] = Field(min_length=1, max_length=100)


class BatchMergeExportRequest(BaseModel):
    items: list[MergeExportRequest] = Field(min_length=1, max_length=100)


class BatchEnqueueResponse(BaseModel):
    total: int
    queued: int
    duplicates: int
    errors: int
    jobs: list[EnqueuedJob]


class JobStatusResponse(BaseModel):
    job_id: str
    status: str  # deferred | queued | in_progress | complete | not_found
    success: bool | None = None
    result: Any | None = None
    enqueue_time: str | None = None
    start_time: str | None = None
    finish_time: str | None = None
    function: str | None = None
    queue_name: str | None = None
    error: str | None = None
