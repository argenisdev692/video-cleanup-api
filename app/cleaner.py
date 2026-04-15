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
        filters = [
            # 1. Corta ruido estructural: zumbidos bajos y hiss alto
            f'highpass=f={settings.clean_highpass_hz}',
            f'lowpass=f={settings.clean_lowpass_hz}',
            # 2. Reduccion de ruido FFT — elimina ruido de fondo constante (ventilador, AC, hiss)
            f'afftdn=nf={settings.clean_afftdn_nf}:tn=1',
            # 3. Noise gate — silencia el fondo entre palabras
            f'agate=threshold={settings.clean_gate_threshold}:range=0.06:attack=10:release=200',
            # 4. EQ parametrico de voz (estilo Audacity Filter Curve EQ)
            # +2dB a 200Hz — cuerpo y calidez, evita voz delgada
            f'equalizer=f=200:width_type=q:width=1.2:g={settings.clean_eq_warmth_gain}',
            # -2dB a 350Hz — corta muddiness/muddiness que engruesa la voz
            f'equalizer=f=350:width_type=q:width=1.5:g={settings.clean_eq_mud_cut}',
            # +3dB a 2500Hz — presencia y claridad, la voz "corta" y se entiende mejor
            f'equalizer=f=2500:width_type=q:width=1.0:g={settings.clean_eq_presence_gain}',
            # -2dB a 5500Hz — doma la agudeza/harshness sin apagar el aire
            f'equalizer=f=5500:width_type=q:width=1.0:g={settings.clean_eq_harsh_cut}',
            # 5. Compresor de voz — iguala dinamica y sube volumen con makeup gain
            f'acompressor=threshold={settings.clean_comp_threshold}:ratio={settings.clean_comp_ratio}:attack=5:release=80:makeup={settings.clean_comp_makeup}',
            # 6. Loudness normalization ITU-R BS.1770 — target -12 LUFS, fuerte y protegido
            f'loudnorm=I={settings.clean_target_lufs}:TP={settings.clean_true_peak}:LRA={settings.clean_lra}',
        ]
        return ','.join(filters)

    def _read_wav_duration(self, path: Path) -> float:
        with contextlib.closing(wave.open(str(path), 'rb')) as handle:
            frames = handle.getnframes()
            frame_rate = handle.getframerate() or settings.audio_sample_rate
            return frames / float(frame_rate)
