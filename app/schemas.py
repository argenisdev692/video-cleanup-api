from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    pause_keyword: str = 'PAUSA'
    silence_threshold_seconds: int = 3
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
    silence_threshold_seconds: float = Field(default=2.0, ge=0.5, le=10.0)

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
