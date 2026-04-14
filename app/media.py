from __future__ import annotations

import contextlib
import shutil
import subprocess
import wave
from pathlib import Path

from app.config import settings
from app.models import PreparedAudio, ResolvedInput


class AudioPreparationService:
    def prepare(self, media_input: ResolvedInput, *, job_uuid: str) -> PreparedAudio:
        output_dir = Path(settings.artifact_root) / job_uuid / 'prepared'
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / 'audio-16k-mono.wav'

        ffmpeg_path = shutil.which(settings.ffmpeg_binary)
        if ffmpeg_path is None:
            if media_input.local_path.suffix.lower() == '.wav':
                duration = self._read_wav_duration(media_input.local_path)
                return PreparedAudio(
                    source_path=media_input.local_path,
                    prepared_path=media_input.local_path,
                    sample_rate=settings.audio_sample_rate,
                    duration_seconds=duration,
                    prepared=False,
                )
            raise RuntimeError('ffmpeg is required to normalize non-wav media inputs')

        command = [
            ffmpeg_path,
            '-y',
            '-i',
            str(media_input.local_path),
            '-vn',
            '-ac',
            '1',
            '-ar',
            str(settings.audio_sample_rate),
            '-acodec',
            'pcm_s16le',
            str(output_path),
        ]
        completed = subprocess.run(command, capture_output=True, text=True)
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or 'ffmpeg failed to prepare the audio')

        duration = self._read_wav_duration(output_path)
        return PreparedAudio(
            source_path=media_input.local_path,
            prepared_path=output_path,
            sample_rate=settings.audio_sample_rate,
            duration_seconds=duration,
            prepared=True,
        )

    def _read_wav_duration(self, path: Path) -> float:
        with contextlib.closing(wave.open(str(path), 'rb')) as handle:
            frames = handle.getnframes()
            frame_rate = handle.getframerate() or settings.audio_sample_rate
            return frames / float(frame_rate)
