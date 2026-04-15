from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from app.config import settings
from app.models import CleanedAudio, EditedMediaRender, ResolvedInput


class MediaEditingService:
    def render_clean_master(
        self,
        *,
        media_input: ResolvedInput,
        cleaned_audio: CleanedAudio,
        cut_ranges: list[tuple[float, float]],
        original_duration_seconds: float,
        job_uuid: str,
    ) -> EditedMediaRender:
        ffmpeg_path = shutil.which(settings.ffmpeg_binary)
        if ffmpeg_path is None:
            raise RuntimeError('ffmpeg is required to render the cleaned master video')

        output_dir = Path(settings.artifact_root) / job_uuid / 'render'
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / 'clean-master.mp4'

        keep_ranges = self._invert_cut_ranges(
            cut_ranges=cut_ranges,
            duration_seconds=original_duration_seconds,
        )

        if not keep_ranges:
            keep_ranges = [(0.0, max(original_duration_seconds, settings.render_min_segment_seconds))]

        if len(keep_ranges) == 1:
            start, end = keep_ranges[0]
            command = [
                ffmpeg_path,
                '-y',
                '-i',
                str(media_input.local_path),
                '-i',
                str(cleaned_audio.cleaned_path),
                '-ss',
                str(start),
                '-to',
                str(end),
                '-map',
                '0:v:0',
                '-map',
                '1:a:0',
                '-c:v',
                settings.render_video_codec,
                '-preset',
                settings.render_preset,
                '-crf',
                str(settings.render_crf),
                '-c:a',
                settings.render_audio_codec,
                '-shortest',
                str(output_path),
            ]
            completed = subprocess.run(command, capture_output=True, text=True)
            if completed.returncode != 0:
                raise RuntimeError(completed.stderr.strip() or 'ffmpeg failed to render the clean master video')
        else:
            filter_parts: list[str] = []
            concat_inputs: list[str] = []
            for index, (start, end) in enumerate(keep_ranges):
                filter_parts.append(f'[0:v]trim=start={start}:end={end},setpts=PTS-STARTPTS[v{index}]')
                filter_parts.append(f'[1:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS[a{index}]')
                concat_inputs.append(f'[v{index}][a{index}]')

            filter_parts.append(
                ''.join(concat_inputs) + f'concat=n={len(keep_ranges)}:v=1:a=1[outv][outa]'
            )

            command = [
                ffmpeg_path,
                '-y',
                '-i',
                str(media_input.local_path),
                '-i',
                str(cleaned_audio.cleaned_path),
                '-filter_complex',
                ';'.join(filter_parts),
                '-map',
                '[outv]',
                '-map',
                '[outa]',
                '-c:v',
                settings.render_video_codec,
                '-preset',
                settings.render_preset,
                '-crf',
                str(settings.render_crf),
                '-c:a',
                settings.render_audio_codec,
                str(output_path),
            ]
            completed = subprocess.run(command, capture_output=True, text=True)
            if completed.returncode != 0:
                raise RuntimeError(completed.stderr.strip() or 'ffmpeg failed to concat the clean master video')

        duration_seconds = round(sum(end - start for start, end in keep_ranges), 3)

        manifest_debug_path = output_dir / 'cut-ranges.json'
        manifest_debug_path.write_text(
            json.dumps(
                {
                    'keep_ranges': keep_ranges,
                    'cut_ranges': cut_ranges,
                    'duration_seconds': duration_seconds,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding='utf-8',
        )

        return EditedMediaRender(
            output_path=output_path,
            keep_ranges=keep_ranges,
            cut_ranges=cut_ranges,
            duration_seconds=duration_seconds,
        )

    def _invert_cut_ranges(
        self,
        *,
        cut_ranges: list[tuple[float, float]],
        duration_seconds: float,
    ) -> list[tuple[float, float]]:
        normalized_cuts = sorted(
            [
                (
                    max(0.0, start),
                    min(duration_seconds, end),
                )
                for start, end in cut_ranges
                if end - start >= settings.render_min_segment_seconds
            ],
            key=lambda item: item[0],
        )

        keep_ranges: list[tuple[float, float]] = []
        cursor = 0.0
        for start, end in normalized_cuts:
            if start - cursor >= settings.render_min_segment_seconds:
                keep_ranges.append((cursor, start))
            cursor = max(cursor, end)

        if duration_seconds - cursor >= settings.render_min_segment_seconds:
            keep_ranges.append((cursor, duration_seconds))

        return keep_ranges

    def apply_title_overlays(
        self,
        *,
        clean_video_path: Path,
        title_overlays: list[dict[str, Any]],
        job_uuid: str,
    ) -> Path:
        ffmpeg_path = shutil.which(settings.ffmpeg_binary)
        if ffmpeg_path is None:
            raise RuntimeError('ffmpeg is required to apply title overlays')

        output_dir = Path(settings.artifact_root) / job_uuid / 'render'
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / 'final-with-titles.mp4'

        if not title_overlays:
            # No overlays, just copy the clean video
            shutil.copy(clean_video_path, output_path)
            return output_path

        # Build ffmpeg filter_complex for overlays
        filter_parts: list[str] = []
        filter_parts.append(f'[0:v]copy[base]')

        for idx, overlay in enumerate(title_overlays):
            start = overlay['start_frame'] / settings.remotion_fps
            duration = overlay['duration_in_frames'] / settings.remotion_fps
            
            scale_filter = (
                f'[{idx+1}:v]scale={settings.remotion_width}:{settings.remotion_height}:'
                f'force_original_aspect_ratio=decrease,'
                f'pad={settings.remotion_width}:{settings.remotion_height}:(ow-iw)/2:(oh-ih)/2,'
                f'format=yuv420p[ov{idx}]'
            )
            filter_parts.append(scale_filter)
            overlay_filter = (
                f"[base]overlay={start}:enable='between(t,{start},{start+duration})'[tmp{idx}]"
            )
            filter_parts.append(overlay_filter)
            filter_parts.append(f'[tmp{idx}]copy[base]')

        command = [
            ffmpeg_path,
            '-y',
            '-i', str(clean_video_path),
        ]

        # Add all overlay video inputs
        for overlay in title_overlays:
            command.extend(['-i', str(overlay['video_path'])])

        command.extend([
            '-filter_complex',
            ';'.join(filter_parts),
            '-map', '[base]',
            '-map', '0:a',
            '-c:v', settings.render_video_codec,
            '-preset', settings.render_preset,
            '-crf', str(settings.render_crf),
            '-c:a', settings.render_audio_codec,
            '-shortest',
            str(output_path),
        ])

        completed = subprocess.run(command, capture_output=True, text=True)
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or 'ffmpeg failed to apply title overlays')

        return output_path
