from __future__ import annotations

from faster_whisper import WhisperModel

from app.config import settings
from app.models import PreparedAudio, TranscriptSegment, TranscriptWord


class TranscriptionService:
    def __init__(self) -> None:
        self._model: WhisperModel | None = None

    def transcribe(self, prepared_audio: PreparedAudio, *, language: str) -> tuple[list[TranscriptSegment], dict[str, object]]:
        model = self._get_model()
        segments, info = model.transcribe(
            str(prepared_audio.prepared_path),
            language=language or settings.default_language,
            beam_size=settings.whisper_beam_size,
            word_timestamps=settings.whisper_word_timestamps,
            vad_filter=settings.whisper_vad_filter,
            vad_parameters={
                'min_silence_duration_ms': settings.whisper_vad_min_silence_ms,
            },
            condition_on_previous_text=False,
        )

        parsed_segments: list[TranscriptSegment] = []
        for segment in list(segments):
            text = str(segment.text).strip()
            if not text:
                continue
            words: list[TranscriptWord] = []
            if segment.words:
                for word in segment.words:
                    if word.start is None or word.end is None:
                        continue
                    words.append(
                        TranscriptWord(
                            start_seconds=float(word.start),
                            end_seconds=float(word.end),
                            text=str(word.word).strip(),
                            probability=float(word.probability) if word.probability is not None else None,
                        )
                    )

            parsed_segments.append(
                TranscriptSegment(
                    start_seconds=float(segment.start),
                    end_seconds=float(segment.end),
                    text=text,
                    words=words,
                )
            )

        diagnostics: dict[str, object] = {
            'detected_language': getattr(info, 'language', None),
            'language_probability': getattr(info, 'language_probability', None),
            'transcription_duration_seconds': prepared_audio.duration_seconds,
            'transcription_segments': len(parsed_segments),
            'transcription_word_timestamps': settings.whisper_word_timestamps,
            'transcription_model_size': settings.transcription_model_size,
            'transcription_device': settings.whisper_device,
            'transcription_compute_type': settings.whisper_compute_type,
        }
        return parsed_segments, diagnostics

    def _get_model(self) -> WhisperModel:
        if self._model is None:
            self._model = WhisperModel(
                settings.transcription_model_size,
                device=settings.whisper_device,
                compute_type=settings.whisper_compute_type,
            )
        return self._model
