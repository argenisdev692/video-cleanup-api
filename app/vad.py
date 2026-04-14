from __future__ import annotations

from silero_vad import get_speech_timestamps, load_silero_vad, read_audio

from app.config import settings
from app.models import PreparedAudio, SpeechRegion


class VoiceActivityDetectionService:
    def __init__(self) -> None:
        self._model = None

    def detect_speech_regions(self, prepared_audio: PreparedAudio) -> tuple[list[SpeechRegion], dict[str, object]]:
        model = self._get_model()
        wav = read_audio(str(prepared_audio.prepared_path), sampling_rate=prepared_audio.sample_rate)
        timestamps = get_speech_timestamps(
            wav,
            model,
            sampling_rate=prepared_audio.sample_rate,
            return_seconds=True,
            threshold=settings.vad_threshold,
            min_speech_duration_ms=settings.vad_min_speech_duration_ms,
            min_silence_duration_ms=settings.vad_min_silence_duration_ms,
            speech_pad_ms=settings.vad_speech_pad_ms,
        )

        regions = [
            SpeechRegion(
                start_seconds=float(item['start']),
                end_seconds=float(item['end']),
            )
            for item in timestamps
            if isinstance(item, dict) and 'start' in item and 'end' in item
        ]

        diagnostics: dict[str, object] = {
            'speech_region_count': len(regions),
            'speech_total_seconds': round(sum(region.duration_seconds for region in regions), 3),
            'vad_threshold': settings.vad_threshold,
            'vad_min_speech_duration_ms': settings.vad_min_speech_duration_ms,
            'vad_min_silence_duration_ms': settings.vad_min_silence_duration_ms,
            'vad_use_onnx': settings.vad_use_onnx,
        }
        return regions, diagnostics

    def detect_silence_gaps(self, regions: list[SpeechRegion], *, duration_seconds: float, minimum_gap_seconds: float) -> list[SpeechRegion]:
        if not regions:
            if duration_seconds >= minimum_gap_seconds:
                return [SpeechRegion(start_seconds=0.0, end_seconds=duration_seconds)]
            return []

        gaps: list[SpeechRegion] = []
        cursor = 0.0
        for region in regions:
            if region.start_seconds - cursor >= minimum_gap_seconds:
                gaps.append(SpeechRegion(start_seconds=cursor, end_seconds=region.start_seconds))
            cursor = max(cursor, region.end_seconds)

        if duration_seconds - cursor >= minimum_gap_seconds:
            gaps.append(SpeechRegion(start_seconds=cursor, end_seconds=duration_seconds))

        return gaps

    def _get_model(self):
        if self._model is None:
            self._model = load_silero_vad(onnx=settings.vad_use_onnx)
        return self._model
