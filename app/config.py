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
    # Step 1 — Noise Reduction
    clean_nr_amount: int = 12               # afftdn nr: reduccion de ruido en dB
    clean_afftdn_nf: int = -25              # afftdn nf: noise floor en dB
    # Step 2 & 9 — Click Removal (Audacity Threshold 200, Max Spike Width 20)
    clean_adeclick_amplitude: float = 2.0   # adeclick a: menor = mas sensible
    # Step 3 & 10 — High-Pass Filter 100Hz 24dB/oct
    clean_highpass_hz: int = 100            # frecuencia de corte en Hz
    # Step 5 — Noise Gate
    clean_gate_threshold_db: float = -40.0  # umbral del gate en dB
    clean_gate_range_db: float = -100.0     # reduccion de nivel en dB
    clean_gate_attack: float = 10.0         # attack en ms
    clean_gate_hold: float = 25.0           # hold en ms
    clean_gate_release: float = 250.0       # decay/release en ms
    # Step 6 — Compressor
    clean_comp_threshold_db: float = -12.0  # umbral del compresor en dB
    clean_comp_ratio: float = 3.1           # ratio de compresion
    clean_comp_knee_db: float = 5.0         # knee width en dB
    clean_comp_attack_ms: float = 0.2       # attack en ms
    clean_comp_release_ms: float = 100.0    # release en ms
    clean_comp_makeup_db: float = 0.0       # makeup gain en dB (0 = sin ganancia extra)
    # Step 7 — Normalize (loudnorm con true peak -1.0 dBFS)
    clean_target_lufs: float = -14.0        # target de loudness integrado
    clean_true_peak: float = -1.0           # true peak maximo en dBFS
    clean_lra: int = 7                      # rango de loudness LU
    # Step 8 — Amplify
    clean_amplify_db: float = 6.144         # amplificacion final en dB
    render_video_codec: str = 'libx264'
    render_audio_codec: str = 'aac'
    render_crf: int = 18
    render_preset: str = 'medium'
    render_video_maxrate: str = '10M'     # bitrate maximo para 1080p HD
    render_video_bufsize: str = '20M'     # buffer = 2x maxrate
    render_audio_channels: int = 2        # stereo — el input es mono, se upmixea aqui
    render_audio_bitrate: str = '320k'    # 320kbps AAC — alta calidad para video HD
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
