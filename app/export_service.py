from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from app.config import settings
from app.media import AudioPreparationService
from app.models import ResolvedInput, TranscriptSegment
from app.resolver import InputResolver
from app.schemas import ExportRequest, ExportResponse, MergeExportRequest, MergeExportResponse
from app.storage import ArtifactWriter
from app.transcription import TranscriptionService
from app.vad import VoiceActivityDetectionService


class VideoExportService:
    def __init__(
        self,
        input_resolver: InputResolver | None = None,
        audio_preparation_service: AudioPreparationService | None = None,
        vad_service: VoiceActivityDetectionService | None = None,
        artifact_writer: ArtifactWriter | None = None,
        transcription_service: TranscriptionService | None = None,
    ) -> None:
        self.input_resolver = input_resolver or InputResolver()
        self.audio_preparation_service = audio_preparation_service or AudioPreparationService()
        self.vad_service = vad_service or VoiceActivityDetectionService()
        self.artifact_writer = artifact_writer or ArtifactWriter()
        self.transcription_service = transcription_service or TranscriptionService()

    def export(self, request: ExportRequest) -> ExportResponse:
        resolved = [self.input_resolver.resolve(p, kind='video') for p in request.video_paths]

        working_video = (
            self._merge_videos(resolved, job_uuid=request.job_uuid)
            if len(resolved) > 1
            else resolved[0]
        )

        prepared_audio = self.audio_preparation_service.prepare(working_video, job_uuid=request.job_uuid)

        speech_regions, vad_diagnostics = self.vad_service.detect_speech_regions(prepared_audio)
        silence_gaps = self.vad_service.detect_silence_gaps(
            speech_regions,
            duration_seconds=prepared_audio.duration_seconds,
            minimum_gap_seconds=request.silence_threshold_seconds,
            trim_to_seconds=request.silence_trim_to_seconds,
        )

        pause_cuts: list[tuple[float, float]] = []
        if request.pause_keyword:
            segments, _ = self.transcription_service.transcribe(prepared_audio, language=request.language)
            pause_cuts = self._find_pause_cuts(segments, request.pause_keyword)

        cut_ranges = [(gap.start_seconds, gap.end_seconds) for gap in silence_gaps] + pause_cuts
        keep_ranges = self._invert_cuts(cut_ranges, prepared_audio.duration_seconds)

        if not keep_ranges:
            keep_ranges = [(0.0, prepared_audio.duration_seconds)]

        output_path = self._render(working_video, keep_ranges, job_uuid=request.job_uuid)
        duration_seconds = round(sum(e - s for s, e in keep_ranges), 3)

        storage_url: str | None = None
        if all([settings.r2_endpoint, settings.r2_access_key_id, settings.r2_secret_access_key, settings.r2_bucket_name]):
            try:
                remote_key = f'video-exports/{request.job_uuid}/export.mp4'
                storage_url = self.artifact_writer.upload_to_r2(
                    local_path=output_path,
                    remote_key=remote_key,
                )
            except Exception as exc:
                diagnostics_r2_error = str(exc)
            else:
                diagnostics_r2_error = None
        else:
            diagnostics_r2_error = None

        diagnostics: dict[str, Any] = {
            'source_count': len(request.video_paths),
            'merged': len(resolved) > 1,
            'original_duration_seconds': round(prepared_audio.duration_seconds, 3),
            'silence_cuts': len(silence_gaps),
            'pause_cuts': len(pause_cuts),
            'keep_segments': len(keep_ranges),
            'output_path': str(output_path),
            **vad_diagnostics,
        }
        if diagnostics_r2_error:
            diagnostics['r2_upload_error'] = diagnostics_r2_error

        return ExportResponse(
            job_uuid=request.job_uuid,
            status='completed',
            output_path=str(output_path),
            storage_url=storage_url,
            duration_seconds=duration_seconds,
            silence_cuts=len(silence_gaps),
            diagnostics=diagnostics,
        )

    def _merge_videos(self, inputs: list[ResolvedInput], *, job_uuid: str) -> ResolvedInput:
        ffmpeg_path = shutil.which(settings.ffmpeg_binary)
        if ffmpeg_path is None:
            raise RuntimeError('ffmpeg is required to merge videos')

        output_dir = Path(settings.artifact_root) / job_uuid / 'export-merge'
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / 'merged.mp4'

        filter_parts: list[str] = []
        concat_inputs: list[str] = []
        for i, _ in enumerate(inputs):
            filter_parts.append(
                f'[{i}:v]scale=1920:1080:force_original_aspect_ratio=decrease,'
                f'pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30[v{i}]'
            )
            filter_parts.append(
                f'[{i}:a]aformat=sample_rates={settings.export_audio_sample_rate}'
                f':channel_layouts=stereo[a{i}]'
            )
            concat_inputs.append(f'[v{i}][a{i}]')
        filter_parts.append(
            ''.join(concat_inputs) + f'concat=n={len(inputs)}:v=1:a=1[outv][outa]'
        )

        command = [ffmpeg_path, '-y']
        for r in inputs:
            command.extend(['-i', str(r.local_path)])
        command.extend([
            '-filter_complex', ';'.join(filter_parts),
            '-map', '[outv]',
            '-map', '[outa]',
            '-c:v', 'libx264',
            '-preset', 'fast',
            '-crf', '16',
            '-pix_fmt', 'yuv420p',
            '-c:a', 'aac',
            '-b:a', '320k',
            '-movflags', '+faststart',
            str(output_path),
        ])

        completed = subprocess.run(command, capture_output=True, text=True)
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or 'ffmpeg failed to merge videos')

        return ResolvedInput(
            kind='video',
            reference=str(output_path),
            local_path=output_path,
            source='export-merge',
        )

    def _render(
        self,
        video: ResolvedInput,
        keep_ranges: list[tuple[float, float]],
        *,
        job_uuid: str,
    ) -> Path:
        ffmpeg_path = shutil.which(settings.ffmpeg_binary)
        if ffmpeg_path is None:
            raise RuntimeError('ffmpeg is required to render the export')

        output_dir = Path(settings.artifact_root) / job_uuid / 'export'
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / 'export.mp4'

        encode_flags = [
            '-c:v', 'libx264',
            '-preset', settings.render_preset,
            '-b:v', settings.export_video_bitrate,
            '-maxrate', settings.export_video_maxrate,
            '-bufsize', settings.export_video_bufsize,
            '-r', '30',
            '-s', '1920x1080',
            '-pix_fmt', 'yuv420p',
            '-aspect', '16:9',
            '-c:a', 'aac',
            '-b:a', settings.export_audio_bitrate,
            '-ar', str(settings.export_audio_sample_rate),
            '-ac', str(settings.export_audio_channels),
            '-movflags', '+faststart',
        ]

        if len(keep_ranges) == 1:
            start, end = keep_ranges[0]
            command = [
                ffmpeg_path, '-y',
                '-i', str(video.local_path),
                '-ss', str(start),
                '-to', str(end),
            ] + encode_flags + [str(output_path)]
        else:
            filter_parts: list[str] = []
            concat_inputs: list[str] = []
            for i, (start, end) in enumerate(keep_ranges):
                filter_parts.append(
                    f'[0:v]trim=start={start}:end={end},setpts=PTS-STARTPTS[v{i}]'
                )
                filter_parts.append(
                    f'[0:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS[a{i}]'
                )
                concat_inputs.append(f'[v{i}][a{i}]')
            filter_parts.append(
                ''.join(concat_inputs)
                + f'concat=n={len(keep_ranges)}:v=1:a=1[outv][outa_raw]'
            )
            filter_parts.append(
                f'[outa_raw]aformat=sample_rates={settings.export_audio_sample_rate}'
                f':channel_layouts=stereo[outa]'
            )

            command = [
                ffmpeg_path, '-y',
                '-i', str(video.local_path),
                '-filter_complex', ';'.join(filter_parts),
                '-map', '[outv]',
                '-map', '[outa]',
            ] + encode_flags + [str(output_path)]

        completed = subprocess.run(command, capture_output=True, text=True)
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or 'ffmpeg failed to render export')

        return output_path

    def _find_pause_cuts(self, segments: list[TranscriptSegment], pause_keyword: str) -> list[tuple[float, float]]:
        cuts: list[tuple[float, float]] = []
        keyword = pause_keyword.casefold()
        previous: TranscriptSegment | None = None
        for segment in segments:
            if keyword in segment.text.casefold():
                pause_end = self._find_pause_end(segment, keyword)
                cut_start = previous.start_seconds if previous is not None else segment.start_seconds
                cuts.append((cut_start, pause_end))
            previous = segment
        return cuts

    def _find_pause_end(self, segment: TranscriptSegment, keyword: str) -> float:
        if not segment.words:
            return segment.end_seconds
        clean = lambda t: t.strip(" .,;:!?¡¿\"'()[]{}").casefold()
        for word in segment.words:
            if clean(word.text) == keyword:
                return word.end_seconds
        return segment.end_seconds

    def _invert_cuts(
        self,
        cut_ranges: list[tuple[float, float]],
        duration_seconds: float,
    ) -> list[tuple[float, float]]:
        normalized = sorted(
            [
                (max(0.0, s), min(duration_seconds, e))
                for s, e in cut_ranges
                if e - s >= settings.render_min_segment_seconds
            ],
            key=lambda x: x[0],
        )
        keep: list[tuple[float, float]] = []
        cursor = 0.0
        for s, e in normalized:
            if s - cursor >= settings.render_min_segment_seconds:
                keep.append((cursor, s))
            cursor = max(cursor, e)
        if duration_seconds - cursor >= settings.render_min_segment_seconds:
            keep.append((cursor, duration_seconds))
        return keep


class VideoMergeExportService:
    def __init__(
        self,
        input_resolver: InputResolver | None = None,
        artifact_writer: ArtifactWriter | None = None,
    ) -> None:
        self.input_resolver = input_resolver or InputResolver()
        self.artifact_writer = artifact_writer or ArtifactWriter()

    def export(self, request: MergeExportRequest) -> MergeExportResponse:
        resolved = [self.input_resolver.resolve(p, kind='video') for p in request.video_paths]

        output_path = self._merge_and_render(resolved, job_uuid=request.job_uuid)
        duration_seconds = round(self._get_duration(output_path), 3)

        storage_url: str | None = None
        r2_error: str | None = None
        if all([settings.r2_endpoint, settings.r2_access_key_id, settings.r2_secret_access_key, settings.r2_bucket_name]):
            try:
                remote_key = f'video-exports/{request.job_uuid}/merge-export.mp4'
                storage_url = self.artifact_writer.upload_to_r2(
                    local_path=output_path,
                    remote_key=remote_key,
                )
            except Exception as exc:
                r2_error = str(exc)

        diagnostics: dict[str, Any] = {
            'source_count': len(request.video_paths),
            'merged': len(resolved) > 1,
            'output_path': str(output_path),
        }
        if r2_error:
            diagnostics['r2_upload_error'] = r2_error

        return MergeExportResponse(
            job_uuid=request.job_uuid,
            status='completed',
            output_path=str(output_path),
            storage_url=storage_url,
            duration_seconds=duration_seconds,
            diagnostics=diagnostics,
        )

    def _merge_and_render(self, inputs: list[ResolvedInput], *, job_uuid: str) -> Path:
        ffmpeg_path = shutil.which(settings.ffmpeg_binary)
        if ffmpeg_path is None:
            raise RuntimeError('ffmpeg is required to merge and render videos')

        output_dir = Path(settings.artifact_root) / job_uuid / 'merge-export'
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / 'merge-export.mp4'

        encode_flags = [
            '-c:v', 'libx264',
            '-preset', settings.render_preset,
            '-b:v', settings.export_video_bitrate,
            '-maxrate', settings.export_video_maxrate,
            '-bufsize', settings.export_video_bufsize,
            '-r', '30',
            '-pix_fmt', 'yuv420p',
            '-aspect', '16:9',
            '-c:a', 'aac',
            '-b:a', settings.export_audio_bitrate,
            '-ar', str(settings.export_audio_sample_rate),
            '-ac', str(settings.export_audio_channels),
            '-movflags', '+faststart',
        ]

        if len(inputs) == 1:
            command = [
                ffmpeg_path, '-y',
                '-i', str(inputs[0].local_path),
                '-vf', (
                    'scale=1920:1080:force_original_aspect_ratio=decrease,'
                    'pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30'
                ),
                '-af', f'aformat=sample_rates={settings.export_audio_sample_rate}:channel_layouts=stereo',
            ] + encode_flags + [str(output_path)]
        else:
            filter_parts: list[str] = []
            concat_inputs: list[str] = []
            for i, _ in enumerate(inputs):
                filter_parts.append(
                    f'[{i}:v]scale=1920:1080:force_original_aspect_ratio=decrease,'
                    f'pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30[v{i}]'
                )
                filter_parts.append(
                    f'[{i}:a]aformat=sample_rates={settings.export_audio_sample_rate}'
                    f':channel_layouts=stereo[a{i}]'
                )
                concat_inputs.append(f'[v{i}][a{i}]')
            filter_parts.append(
                ''.join(concat_inputs) + f'concat=n={len(inputs)}:v=1:a=1[outv][outa]'
            )

            command = [ffmpeg_path, '-y']
            for r in inputs:
                command.extend(['-i', str(r.local_path)])
            command.extend([
                '-filter_complex', ';'.join(filter_parts),
                '-map', '[outv]',
                '-map', '[outa]',
            ] + encode_flags + [str(output_path)])

        completed = subprocess.run(command, capture_output=True, text=True)
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or 'ffmpeg failed to merge and render videos')

        return output_path

    def _get_duration(self, path: Path) -> float:
        ffprobe_path = shutil.which('ffprobe')
        if ffprobe_path is None:
            return 0.0
        result = subprocess.run(
            [
                ffprobe_path, '-v', 'quiet',
                '-show_entries', 'format=duration',
                '-of', 'default=noprint_wrappers=1:nokey=1',
                str(path),
            ],
            capture_output=True,
            text=True,
        )
        try:
            return float(result.stdout.strip())
        except ValueError:
            return 0.0
