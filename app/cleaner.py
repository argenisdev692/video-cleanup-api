from __future__ import annotations

import contextlib
import shutil
import subprocess
import wave
from pathlib import Path

from app.config import settings
from app.models import CleanedAudio, PreparedAudio


class VoiceCleanerService:
    def clean(self, prepared_audio: PreparedAudio, *, job_uuid: str) -> CleanedAudio:
        ffmpeg_path = shutil.which(settings.ffmpeg_binary)
        if ffmpeg_path is None:
            raise RuntimeError('ffmpeg is required to clean and normalize the tutorial audio')

        output_dir = Path(settings.artifact_root) / job_uuid / 'cleaned'
        output_dir.mkdir(parents=True, exist_ok=True)
        cleaned_path = output_dir / 'voice-clean.wav'

        filter_chain = self._build_filter_chain()
        command = [
            ffmpeg_path,
            '-y',
            '-i',
            str(prepared_audio.prepared_path),
            '-af',
            filter_chain,
            str(cleaned_path),
        ]

        completed = subprocess.run(command, capture_output=True, text=True)
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or 'ffmpeg failed to clean the voice track')

        duration_seconds = self._read_wav_duration(cleaned_path)

        return CleanedAudio(
            source_path=prepared_audio.prepared_path,
            cleaned_path=cleaned_path,
            filter_chain=filter_chain,
            sample_rate=prepared_audio.sample_rate,
            duration_seconds=duration_seconds,
        )

    def _build_filter_chain(self) -> str:
        gate_threshold = 10 ** (settings.clean_gate_threshold_db / 20)
        gate_range = 10 ** (settings.clean_gate_range_db / 20)
        comp_threshold = 10 ** (settings.clean_comp_threshold_db / 20)
        comp_makeup = 10 ** (settings.clean_comp_makeup_db / 20)

        click_filter = (
            f'adeclick=w=55:o=75:a={settings.clean_adeclick_amplitude}'
        )
        highpass_100 = f'highpass=f={settings.clean_highpass_hz}:poles=4'

        filters = [
            # 1. NOISE REDUCTION — afftdn 12dB, track noise enabled
            f'afftdn=nr={settings.clean_nr_amount}:nf={settings.clean_afftdn_nf}:tn=1',
            # 2. CLICK REMOVAL — Threshold 200 ≈ a=2, Max Spike Width 20 samples ≈ m=2
            click_filter,
            # 3. HIGH-PASS FILTER — 100Hz, 24dB/octave (4 poles)
            highpass_100,
            # 4. FILTER CURVE EQ — gentle roll-off from 60Hz toward 100Hz, rest flat
            'highpass=f=60:poles=2',
            # 5. NOISE GATE — -40dB threshold, -100dB reduction, attack 10ms, hold 25ms, decay 250ms
            (
                f'agate=threshold={gate_threshold:.5f}'
                f':range={gate_range:.8f}'
                f':attack={settings.clean_gate_attack}'
                f':hold={settings.clean_gate_hold}'
                f':release={settings.clean_gate_release}'
            ),
            # 6. COMPRESSOR — -12dB threshold, ratio 3.1, knee 5dB, attack 0.2ms, release 100ms
            (
                f'acompressor=threshold={comp_threshold:.4f}'
                f':ratio={settings.clean_comp_ratio}'
                f':knee={settings.clean_comp_knee_db}'
                f':attack={settings.clean_comp_attack_ms}'
                f':release={settings.clean_comp_release_ms}'
                f':makeup={comp_makeup:.4f}'
            ),
            # 7. NORMALIZE — DC removal via ultra-low HP, then loudnorm TP=-1.0 dBFS
            'highpass=f=5:poles=1',
            f'loudnorm=I={settings.clean_target_lufs}:TP={settings.clean_true_peak}:LRA={settings.clean_lra}',
            # 8. AMPLIFY — +6.144dB with hard limiter at 0 dBFS, no clipping
            f'volume={settings.clean_amplify_db}dB',
            'alimiter=level_in=1:level_out=1:limit=1.0:attack=5:release=50:asc=1',
            # 9. KEYBOARD CLICK REMOVAL — second adeclick pass for short transients
            click_filter,
            # 10. ANTI-KEYBOARD HIGH-PASS — 100Hz, 24dB/oct to eliminate mechanical thump
            highpass_100,
        ]
        return ','.join(filters)

    def _read_wav_duration(self, path: Path) -> float:
        with contextlib.closing(wave.open(str(path), 'rb')) as handle:
            frames = handle.getnframes()
            frame_rate = handle.getframerate() or settings.audio_sample_rate
            return frames / float(frame_rate)
