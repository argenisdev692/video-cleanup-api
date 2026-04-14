from __future__ import annotations

import json
from pathlib import Path

from app.config import settings
from app.schemas import EditPlanItem, TitleOverlay


class RemotionManifestBuilder:
    def build(
        self,
        *,
        job_uuid: str,
        title: str,
        clean_video_path: str,
        target_duration_minutes: int,
        edit_plan: list[EditPlanItem],
        sections: list[str],
        title_overlays: list[TitleOverlay] | None = None,
    ) -> str:
        output_dir = Path(settings.artifact_root) / job_uuid / 'remotion'
        output_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = output_dir / 'manifest.json'
        duration_in_frames = max(
            settings.remotion_fps * 10,
            target_duration_minutes * 60 * settings.remotion_fps,
        )
        intro_frames = min(
            int(duration_in_frames * 0.12),
            int(settings.remotion_fps * 2.5),
        )
        section_count = max(1, len(sections))
        slot_frames = max(
            int(settings.remotion_fps * 1.8),
            int(max(settings.remotion_fps * 3, duration_in_frames - intro_frames) / section_count),
        )

        scene_titles = [
            {
                'title': section,
                'style': 'capcut_title_card',
                'index': index,
                'subtitle': f'Section {index}',
                'start_frame': intro_frames + ((index - 1) * slot_frames),
                'duration_in_frames': min(int(settings.remotion_fps * 2.2), slot_frames),
            }
            for index, section in enumerate(sections, start=1)
        ]

        # Convert title_overlays to frame-based manifest entries
        title_overlay_manifests = []
        if title_overlays:
            for overlay in title_overlays:
                title_overlay_manifests.append({
                    'video_path': overlay.video_path,
                    'title': overlay.title or f'Title {len(title_overlay_manifests) + 1}',
                    'start_frame': int(overlay.start_seconds * settings.remotion_fps),
                    'duration_in_frames': int(overlay.duration_seconds * settings.remotion_fps),
                })

        payload = {
            'composition_id': settings.remotion_composition_id,
            'fps': settings.remotion_fps,
            'width': settings.remotion_width,
            'height': settings.remotion_height,
            'input_props': {
                'job_uuid': job_uuid,
                'title': title,
                'clean_video_path': clean_video_path,
                'target_duration_minutes': target_duration_minutes,
                'duration_in_frames': duration_in_frames,
                'scene_titles': scene_titles,
                'edit_plan': [item.model_dump() for item in edit_plan],
                'title_overlays': title_overlay_manifests,
            },
        }

        manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        return str(manifest_path)
