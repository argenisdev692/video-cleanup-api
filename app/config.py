from __future__ import annotations

from pathlib import Path
from tempfile import gettempdir

from pydantic import field_validator, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = 'Tutorial Cleanup API'
    app_version: str = '0.2.0'
    api_token: str = ''
    artifact_root: str = str(Path(gettempdir()) / 'vidula' / 'tutorial-cleanup-api')
    local_input_roots_str: str = ''
    path_map_from: str = ''

    @computed_field
    @property
    def local_input_roots(self) -> tuple[str, ...]:
        if not self.local_input_roots_str or self.local_input_roots_str.strip() == '':
            return ()
        # Split by comma
        if ',' in self.local_input_roots_str:
            return tuple(item.strip() for item in self.local_input_roots_str.split(',') if item.strip())
        # Single path
        return (self.local_input_roots_str.strip(),)

    path_map_to: str = ''
    allow_remote_downloads: bool = True
    download_timeout_seconds: int = 120
    prefer_existing_transcript_sidecars: bool = True
    enable_local_transcription: bool = True
    transcription_model_size: str = 'tiny'
    whisper_device: str = 'cpu'
    whisper_compute_type: str = 'int8'
    whisper_beam_size: int = 5
    whisper_word_timestamps: bool = True
    whisper_vad_filter: bool = False
    whisper_vad_min_silence_ms: int = 500
    default_language: str = 'es'
    words_per_minute: int = 130
    max_edit_plan_items: int = 30
    ffmpeg_binary: str = 'ffmpeg'
    audio_sample_rate: int = 16000
    clean_highpass_hz: int = 80
    clean_lowpass_hz: int = 12000
    clean_afftdn_nf: int = -25              # mas agresivo que -30, sin artefactos
    clean_gate_threshold: float = 0.015     # ~-36dB: silencia fondo entre palabras
    # EQ parametrico de voz (4 bandas, estilo Audacity Filter Curve EQ)
    clean_eq_warmth_gain: int = 2           # +2dB a 200Hz: cuerpo/calidez
    clean_eq_mud_cut: int = -2              # -2dB a 350Hz: corta muddiness
    clean_eq_presence_gain: int = 3         # +3dB a 2500Hz: presencia y claridad
    clean_eq_harsh_cut: int = -2            # -2dB a 5500Hz: doma agudeza/harshness
    # Compresor
    clean_comp_threshold: float = 0.125     # ~-18dB: donde empieza la compresion de voz
    clean_comp_ratio: float = 3.0           # 3:1 — estandar para voice-over
    clean_comp_makeup: int = 6              # +6dB makeup gain (era 4, mas volumen)
    # Loudness
    clean_target_lufs: float = -12.0        # -12 LUFS (mas fuerte, era -14)
    clean_true_peak: float = -1.5           # -1.5 dBTP — protege de clipping en decoders
    clean_lra: int = 7                      # rango de loudness: 7 LU para voz consistente
    render_video_codec: str = 'libx264'
    render_audio_codec: str = 'aac'
    render_crf: int = 18
    render_preset: str = 'medium'
    render_video_maxrate: str = '8M'      # bitrate maximo para 1080p HD
    render_video_bufsize: str = '16M'     # buffer = 2x maxrate
    render_audio_channels: int = 2        # stereo — el input es mono, se upmixea aqui
    render_audio_bitrate: str = '192k'    # 192kbps AAC — estandar para video HD
    render_audio_sample_rate: int = 48000 # 48kHz — estandar para video/broadcast
    render_min_segment_seconds: float = 0.25
    remotion_composition_id: str = 'TutorialCapcutClean'
    remotion_fps: int = 30
    remotion_width: int = 1920
    remotion_height: int = 1080
    vad_use_onnx: bool = False
    vad_threshold: float = 0.5
    vad_min_speech_duration_ms: int = 200
    vad_min_silence_duration_ms: int = 600
    vad_speech_pad_ms: int = 120
    filler_terms: tuple[str, ...] = (
        'eh',
        'emm',
        'mmm',
        'este',
        'ehh',
        'uh',
        'umm',
        'pues',
    )
    correction_terms: tuple[str, ...] = (
        'mejor dicho',
        'quiero decir',
        'perdón',
        'corrijo',
        'rectifico',
    )
    # R2 Storage Configuration
    r2_account_id: str = ''
    r2_access_key_id: str = ''
    r2_secret_access_key: str = ''
    r2_bucket_name: str = ''
    r2_endpoint: str = ''
    r2_public_base_url: str = ''
    model_config = SettingsConfigDict(env_prefix='TUTORIAL_CLEANUP_', extra='ignore')


settings = Settings()
