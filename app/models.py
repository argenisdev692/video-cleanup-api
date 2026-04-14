from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class ResolvedInput:
    kind: str
    reference: str
    local_path: Path
    source: str
    downloaded: bool = False


@dataclass(slots=True)
class PreparedAudio:
    source_path: Path
    prepared_path: Path
    sample_rate: int
    duration_seconds: float
    prepared: bool


@dataclass(slots=True)
class TranscriptWord:
    start_seconds: float
    end_seconds: float
    text: str
    probability: float | None = None


@dataclass(slots=True)
class TranscriptSegment:
    start_seconds: float
    end_seconds: float
    text: str
    words: list[TranscriptWord] = field(default_factory=list)


@dataclass(slots=True)
class SpeechRegion:
    start_seconds: float
    end_seconds: float

    @property
    def duration_seconds(self) -> float:
        return max(0.0, self.end_seconds - self.start_seconds)


@dataclass(slots=True)
class EditCandidate:
    start_seconds: float
    end_seconds: float
    action: str
    reason: str
    observation: str
    confidence: float
    estimated_saved_seconds: float
    priority: int


@dataclass(slots=True)
class CleanedAudio:
    source_path: Path
    cleaned_path: Path
    filter_chain: str
    sample_rate: int
    duration_seconds: float


@dataclass(slots=True)
class EditedMediaRender:
    output_path: Path
    keep_ranges: list[tuple[float, float]]
    cut_ranges: list[tuple[float, float]]
    duration_seconds: float
