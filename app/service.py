from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

import fitz

from app.cleaner import VoiceCleanerService
from app.config import settings
from app.editor import MediaEditingService
from app.media import AudioPreparationService
from app.models import CleanedAudio, EditCandidate, EditedMediaRender, PreparedAudio, SpeechRegion, TranscriptSegment
from app.remotion_manifest import RemotionManifestBuilder
from app.resolver import InputResolver
from app.schemas import AnalysisRequest, AnalysisResponse, ArtifactsPayload, CoveragePayload, CoverageSection, EditPlanItem, SummaryPayload, TitleOverlay
from app.storage import ArtifactWriter
from app.transcription import TranscriptionService
from app.vad import VoiceActivityDetectionService


class TutorialCleanupAnalysisService:
    def __init__(
        self,
        artifact_writer: ArtifactWriter | None = None,
        input_resolver: InputResolver | None = None,
        audio_preparation_service: AudioPreparationService | None = None,
        transcription_service: TranscriptionService | None = None,
        vad_service: VoiceActivityDetectionService | None = None,
        voice_cleaner_service: VoiceCleanerService | None = None,
        media_editing_service: MediaEditingService | None = None,
        remotion_manifest_builder: RemotionManifestBuilder | None = None,
    ) -> None:
        self.artifact_writer = artifact_writer or ArtifactWriter()
        self.input_resolver = input_resolver or InputResolver()
        self.audio_preparation_service = audio_preparation_service or AudioPreparationService()
        self.transcription_service = transcription_service or TranscriptionService()
        self.vad_service = vad_service or VoiceActivityDetectionService()
        self.voice_cleaner_service = voice_cleaner_service or VoiceCleanerService()
        self.media_editing_service = media_editing_service or MediaEditingService()
        self.remotion_manifest_builder = remotion_manifest_builder or RemotionManifestBuilder()

    def analyze(self, payload: AnalysisRequest) -> AnalysisResponse:
        video_paths = list(payload.source.video_paths) if payload.source.video_paths else []
        if payload.source.video_path and payload.source.video_path not in video_paths:
            video_paths.insert(0, payload.source.video_path)

        title_paths_set = {p.strip() for p in payload.source.title_video_paths}
        if len(video_paths) > 1:
            resolved_videos = [self.input_resolver.resolve(p, kind='video') for p in video_paths]
            media_input = self.media_editing_service.concat_videos(
                inputs=resolved_videos,
                job_uuid=payload.job_uuid,
            )
            protected_ranges = self._build_protected_ranges(video_paths, resolved_videos, title_paths_set)
        else:
            media_input = self.input_resolver.resolve(video_paths[0], kind='video')
            protected_ranges: list[tuple[float, float]] = []

        script_input = None
        if payload.source.script_pdf_path:
            script_input = self.input_resolver.resolve(payload.source.script_pdf_path, kind='script_pdf')

        script_text = self._load_script_text(script_input.local_path) if script_input is not None else ''
        script_sections = self._extract_script_sections(script_text, payload.title)
        script_tokens = set(self._tokenize(script_text))
        prepared_audio = self.audio_preparation_service.prepare(media_input, job_uuid=payload.job_uuid)

        transcript_segments: list[TranscriptSegment] = []
        alignment_source = 'unavailable'
        transcription_diagnostics: dict[str, Any] = {}

        if settings.prefer_existing_transcript_sidecars:
            transcript_segments, alignment_source = self._load_sidecar_transcript(media_input.local_path)

        if not transcript_segments and settings.enable_local_transcription:
            transcript_segments, transcription_diagnostics = self.transcription_service.transcribe(
                prepared_audio,
                language=payload.language,
            )
            alignment_source = f'internal-alignment:{prepared_audio.prepared_path.name}'

        if not transcript_segments:
            transcript_segments = self._split_plain_text_into_segments(self._fallback_transcript_text(payload))
            alignment_source = 'fallback:prompt+title'

        speech_regions: list[SpeechRegion] = []
        silence_regions: list[SpeechRegion] = []
        vad_diagnostics: dict[str, Any] = {}
        try:
            speech_regions, vad_diagnostics = self.vad_service.detect_speech_regions(prepared_audio)
            silence_regions = self.vad_service.detect_silence_gaps(
                speech_regions,
                duration_seconds=prepared_audio.duration_seconds,
                minimum_gap_seconds=payload.rules.silence_threshold_seconds,
                trim_to_seconds=payload.rules.silence_trim_to_seconds,
            )
        except Exception as exception:
            vad_diagnostics = {'vad_error': str(exception)}

        candidates = self._build_candidates(
            payload=payload,
            transcript_segments=transcript_segments,
            silence_regions=silence_regions,
            script_tokens=script_tokens,
            protected_ranges=protected_ranges,
        )
        selected_candidates = self._select_candidates(payload, candidates, transcript_segments)

        original_duration_seconds = self._resolve_original_duration_seconds(
            transcript_segments=transcript_segments,
            script_text=script_text,
            prepared_audio=prepared_audio,
        )
        total_saved_seconds = min(
            original_duration_seconds,
            int(round(sum(item.estimated_saved_seconds for item in selected_candidates))),
        )
        target_duration_seconds = payload.target_duration_minutes * 60
        estimated_final_duration_seconds = (
            max(1, original_duration_seconds - total_saved_seconds)
            if original_duration_seconds <= target_duration_seconds
            else max(target_duration_seconds, original_duration_seconds - total_saved_seconds)
        )

        transcript_text = self._join_transcript_text(transcript_segments)
        coverage = self._build_coverage(
            sections=script_sections,
            transcript_segments=transcript_segments,
            transcript_text=transcript_text,
            estimated_final_duration_seconds=estimated_final_duration_seconds,
        )
        learning_objectives_met = len(coverage.missing_topics) == 0 or len(script_sections) <= 1

        summary = SummaryPayload(
            original_duration_seconds=original_duration_seconds,
            estimated_final_duration_seconds=estimated_final_duration_seconds,
            time_saved_seconds=total_saved_seconds,
            learning_objectives_met=learning_objectives_met,
        )

        edit_plan = [
            EditPlanItem(
                start=self._format_timestamp(item.start_seconds),
                end=self._format_timestamp(item.end_seconds),
                action=item.action,
                reason=item.reason,
                observation=item.observation,
                confidence=round(item.confidence, 2),
            )
            for item in selected_candidates
        ]

        cleaned_audio = self.voice_cleaner_service.clean(prepared_audio, job_uuid=payload.job_uuid)
        cut_ranges = [(item.start_seconds, item.end_seconds) for item in selected_candidates]
        media_render = self.media_editing_service.render_clean_master(
            media_input=media_input,
            cleaned_audio=cleaned_audio,
            cut_ranges=cut_ranges,
            original_duration_seconds=float(original_duration_seconds),
            job_uuid=payload.job_uuid,
        )
        remotion_manifest_path = self.remotion_manifest_builder.build(
            job_uuid=payload.job_uuid,
            title=payload.title,
            clean_video_path=str(media_render.output_path),
            target_duration_minutes=payload.target_duration_minutes,
            edit_plan=edit_plan,
            sections=script_sections,
            title_overlays=payload.title_overlays,
        )

        # Apply title overlays if provided
        final_video_path = str(media_render.output_path)
        storage_url: str | None = None
        
        if payload.title_overlays:
            # Load manifest to get title overlays with frame-based timestamps
            with open(remotion_manifest_path, 'r', encoding='utf-8') as f:
                manifest = json.load(f)
            
            title_overlays_manifest = manifest.get('input_props', {}).get('title_overlays', [])
            
            if title_overlays_manifest:
                final_video_path = str(self.media_editing_service.apply_title_overlays(
                    clean_video_path=media_render.output_path,
                    title_overlays=title_overlays_manifest,
                    job_uuid=payload.job_uuid,
                ))
        
        diagnostics: dict[str, Any] = {
            'script_available': bool(script_text.strip()),
            'script_sections_detected': len(script_sections),
            'script_source': str(script_input.local_path) if script_input is not None else 'none',
            'media_source': str(media_input.local_path),
            'media_source_count': len(video_paths),
            'media_source_merged': media_input.source == 'concat',
            'internal_alignment_source': alignment_source,
            'internal_alignment_segments': len(transcript_segments),
            'internal_alignment_words': sum(len(segment.words) for segment in transcript_segments),
            'candidate_actions': len(candidates),
            'selected_actions': len(selected_candidates),
            'silence_regions': len(silence_regions),
            'prepared_audio': str(prepared_audio.prepared_path),
            'prepared_audio_duration_seconds': prepared_audio.duration_seconds,
            'cleaned_audio': str(cleaned_audio.cleaned_path),
            'clean_filter_chain': cleaned_audio.filter_chain,
            'clean_video_path': str(media_render.output_path),
            'remotion_manifest_path': remotion_manifest_path,
            **transcription_diagnostics,
            **vad_diagnostics,
        }

        # Upload to R2 storage if configured
        if all([settings.r2_endpoint, settings.r2_access_key_id, settings.r2_secret_access_key, settings.r2_bucket_name]):
            try:
                remote_key = f'tutorial-cleanups/{payload.job_uuid}/final.mp4'
                storage_url = self.artifact_writer.upload_to_r2(
                    local_path=Path(final_video_path),
                    remote_key=remote_key,
                )
            except Exception as e:
                diagnostics['r2_upload_error'] = str(e)

        if payload.delete_sources_on_success and storage_url:
            deleted, errors = self._delete_r2_sources(video_paths)
            diagnostics['r2_sources_deleted'] = deleted
            if errors:
                diagnostics['r2_sources_delete_errors'] = errors

        artifacts = None
        if payload.rules.store_artifacts:
            artifacts = self._write_artifacts(
                payload=payload,
                transcript_segments=transcript_segments,
                silence_regions=silence_regions,
                speech_regions=speech_regions,
                edit_plan=edit_plan,
                coverage=coverage,
                diagnostics=diagnostics,
                summary=summary,
                prepared_audio=prepared_audio,
                cleaned_audio=cleaned_audio,
                media_render=media_render,
                remotion_manifest_path=remotion_manifest_path,
                final_video_path=final_video_path,
                storage_url=storage_url,
            )

        return AnalysisResponse(
            job_uuid=payload.job_uuid,
            status='completed',
            summary=summary,
            coverage=coverage,
            edit_plan=edit_plan,
            artifacts=artifacts,
            diagnostics=diagnostics,
        )

    def _load_script_text(self, script_pdf_path: Path) -> str:
        if not script_pdf_path.exists() or script_pdf_path.suffix.lower() != '.pdf':
            return ''

        document = fitz.open(script_pdf_path)
        try:
            return '\n'.join(page.get_text('text') for page in document)
        finally:
            document.close()

    def _extract_script_sections(self, script_text: str, title: str) -> list[str]:
        normalized_lines = [line.strip() for line in script_text.splitlines() if line.strip()]
        sections: list[str] = []

        for line in normalized_lines:
            if len(line) > 120:
                continue
            if re.match(r'^(\d+(?:\.\d+)*)[\)\.-]?\s+', line) or line.isupper() or ':' in line:
                sections.append(line)

        if not sections and title.strip():
            sections.append(title.strip())

        unique_sections: list[str] = []
        seen: set[str] = set()
        for section in sections:
            key = section.casefold()
            if key in seen:
                continue
            seen.add(key)
            unique_sections.append(section)

        return unique_sections[:12]

    def _load_sidecar_transcript(self, media_path: Path) -> tuple[list[TranscriptSegment], str]:
        for candidate in self._build_transcript_candidates(media_path):
            if not candidate.exists() or not candidate.is_file():
                continue

            suffix = candidate.suffix.lower()
            if suffix == '.json':
                segments = self._parse_json_transcript(candidate)
            elif suffix in {'.srt', '.vtt'}:
                segments = self._parse_timed_text(candidate)
            else:
                segments = self._parse_plain_text(candidate)

            if segments:
                return segments, str(candidate)

        return [], 'unavailable'

    def _build_transcript_candidates(self, media_path: Path) -> list[Path]:
        candidates = [
            media_path.with_suffix('.transcript.json'),
            media_path.with_suffix('.json'),
            media_path.with_suffix('.srt'),
            media_path.with_suffix('.vtt'),
            media_path.with_suffix('.txt'),
            media_path.with_suffix('.md'),
            Path(str(media_path) + '.transcript.json'),
            Path(str(media_path) + '.txt'),
        ]

        unique: list[Path] = []
        seen: set[str] = set()
        for candidate in candidates:
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            unique.append(candidate)
        return unique

    def _parse_json_transcript(self, path: Path) -> list[TranscriptSegment]:
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            return []

        raw_segments = payload.get('segments') if isinstance(payload, dict) else payload
        if not isinstance(raw_segments, list):
            return []

        segments: list[TranscriptSegment] = []
        for item in raw_segments:
            if not isinstance(item, dict):
                continue
            text = str(item.get('text', '')).strip()
            if not text:
                continue
            try:
                start_seconds = float(item.get('start', 0) or 0)
                end_seconds = float(item.get('end', start_seconds) or start_seconds)
            except (TypeError, ValueError):
                continue
            segments.append(
                TranscriptSegment(
                    start_seconds=start_seconds,
                    end_seconds=max(end_seconds, start_seconds),
                    text=text,
                    words=[],
                )
            )
        return segments

    def _parse_timed_text(self, path: Path) -> list[TranscriptSegment]:
        content = path.read_text(encoding='utf-8', errors='ignore')
        blocks = re.split(r'\n\s*\n', content)
        segments: list[TranscriptSegment] = []

        for block in blocks:
            lines = [line.strip() for line in block.splitlines() if line.strip()]
            if not lines:
                continue
            timestamp_line = next((line for line in lines if '-->' in line), None)
            if timestamp_line is None:
                continue
            text_lines = [line for line in lines if '-->' not in line and not line.isdigit()]
            if not text_lines:
                continue
            start_raw, end_raw = [part.strip() for part in timestamp_line.split('-->', maxsplit=1)]
            segments.append(
                TranscriptSegment(
                    start_seconds=self._timestamp_to_seconds(start_raw),
                    end_seconds=self._timestamp_to_seconds(end_raw),
                    text=' '.join(text_lines).strip(),
                    words=[],
                )
            )
        return segments

    def _parse_plain_text(self, path: Path) -> list[TranscriptSegment]:
        content = path.read_text(encoding='utf-8', errors='ignore').strip()
        if not content:
            return []
        return self._split_plain_text_into_segments(content)

    def _split_plain_text_into_segments(self, text: str) -> list[TranscriptSegment]:
        chunks = [chunk.strip() for chunk in re.split(r'(?<=[.!?])\s+|\n+', text) if chunk.strip()]
        segments: list[TranscriptSegment] = []
        cursor = 0.0
        for chunk in chunks:
            duration = self._estimate_duration_seconds(chunk)
            segments.append(
                TranscriptSegment(
                    start_seconds=cursor,
                    end_seconds=cursor + duration,
                    text=chunk,
                    words=[],
                )
            )
            cursor += duration
        return segments

    def _fallback_transcript_text(self, payload: AnalysisRequest) -> str:
        chunks = [payload.title.strip()]
        if payload.editorial_prompt:
            chunks.append(payload.editorial_prompt.strip())
        return '\n'.join(chunk for chunk in chunks if chunk)

    def _build_candidates(
        self,
        *,
        payload: AnalysisRequest,
        transcript_segments: list[TranscriptSegment],
        silence_regions: list[SpeechRegion],
        script_tokens: set[str],
        protected_ranges: list[tuple[float, float]] | None = None,
    ) -> list[EditCandidate]:
        candidates: list[EditCandidate] = []
        fillers = tuple(term.casefold() for term in settings.filler_terms)
        correction_terms = tuple(term.casefold() for term in settings.correction_terms)
        pause_keywords = sorted(
            (kw.casefold() for kw in payload.rules.pause_keywords if kw.strip()),
            key=lambda kw: len(kw.split()),
            reverse=True,
        )

        previous_segment: TranscriptSegment | None = None
        for segment in transcript_segments:
            normalized = segment.text.casefold()
            duration = max(0.0, segment.end_seconds - segment.start_seconds)

            matched_keyword = next((kw for kw in pause_keywords if kw in normalized), None)
            if matched_keyword:
                pause_end = self._find_pause_end_seconds(segment, matched_keyword) or segment.end_seconds
                # Cut from the START of the previous segment (the mistaken take) through the pause marker
                cut_start = previous_segment.start_seconds if previous_segment is not None else segment.start_seconds
                candidates.append(
                    EditCandidate(
                        start_seconds=cut_start,
                        end_seconds=pause_end,
                        action='cut',
                        reason='pause_keyword',
                        observation=f'Corte por "{matched_keyword}": elimina la toma errónea anterior y el marcador de pausa.',
                        confidence=0.99,
                        estimated_saved_seconds=max(1.0, pause_end - cut_start),
                        priority=100,
                    )
                )

            if payload.rules.detect_fillers:
                filler_hits = self._detect_fillers(segment, fillers)
                for filler_hit in filler_hits:
                    candidates.append(
                        EditCandidate(
                            start_seconds=filler_hit[0],
                            end_seconds=filler_hit[1],
                            action='reduce',
                            reason='fillers',
                            observation=f'Muletilla detectada: {filler_hit[2]}.',
                            confidence=0.74,
                            estimated_saved_seconds=max(0.3, filler_hit[1] - filler_hit[0]),
                            priority=70,
                        )
                    )

            if payload.rules.detect_repeated_words:
                repeated_word_hits = self._detect_repeated_words(segment)
                for repeated_word_hit in repeated_word_hits:
                    candidates.append(
                        EditCandidate(
                            start_seconds=repeated_word_hit[0],
                            end_seconds=repeated_word_hit[1],
                            action='reduce',
                            reason='repeated_words',
                            observation=f'Palabra repetida por error: {repeated_word_hit[2]}.',
                            confidence=0.8,
                            estimated_saved_seconds=max(0.3, repeated_word_hit[1] - repeated_word_hit[0]),
                            priority=80,
                        )
                    )

            if payload.rules.detect_self_corrections and any(term in normalized for term in correction_terms):
                candidates.append(
                    EditCandidate(
                        start_seconds=segment.start_seconds,
                        end_seconds=segment.end_seconds,
                        action='cut',
                        reason='self_correction',
                        observation='Se detectó una autocorrección explícita.',
                        confidence=0.84,
                        estimated_saved_seconds=min(duration * 0.55, 3.0),
                        priority=90,
                    )
                )

            if script_tokens:
                overlap_ratio = self._compute_script_overlap_ratio(segment.text, script_tokens)
                if duration >= 8 and overlap_ratio < 0.15:
                    candidates.append(
                        EditCandidate(
                            start_seconds=segment.start_seconds,
                            end_seconds=segment.end_seconds,
                            action='reduce',
                            reason='off_script_drift',
                            observation='Fragmento con baja cobertura respecto al guion.',
                            confidence=0.69,
                            estimated_saved_seconds=min(duration * 0.5, 8.0),
                            priority=55,
                        )
                    )

            previous_segment = segment

        guarded = protected_ranges or []
        for silence_region in silence_regions:
            if any(
                silence_region.start_seconds < end and silence_region.end_seconds > start
                for start, end in guarded
            ):
                continue
            candidates.append(
                EditCandidate(
                    start_seconds=silence_region.start_seconds,
                    end_seconds=silence_region.end_seconds,
                    action='cut',
                    reason='long_silence',
                    observation=f'Silencio mayor a {payload.rules.silence_threshold_seconds}s detectado por VAD.',
                    confidence=0.9,
                    estimated_saved_seconds=silence_region.duration_seconds,
                    priority=85,
                )
            )

        return self._deduplicate_candidates(candidates)

    def _detect_fillers(self, segment: TranscriptSegment, fillers: tuple[str, ...]) -> list[tuple[float, float, str]]:
        hits: list[tuple[float, float, str]] = []
        if segment.words:
            for word in segment.words:
                normalized_word = word.text.strip(" .,;:!?¡¿\"'()[]{}").casefold()
                if normalized_word in fillers:
                    hits.append((word.start_seconds, word.end_seconds, normalized_word))
            return hits

        normalized_text = segment.text.casefold()
        for filler in fillers:
            if re.search(rf'\b{re.escape(filler)}\b', normalized_text):
                hits.append((segment.start_seconds, min(segment.end_seconds, segment.start_seconds + 1.0), filler))
        return hits

    def _detect_repeated_words(self, segment: TranscriptSegment) -> list[tuple[float, float, str]]:
        hits: list[tuple[float, float, str]] = []
        if segment.words:
            normalized_words = [
                word.text.strip(" .,;:!?¡¿\"'()[]{}").casefold()
                for word in segment.words
            ]
            for index in range(1, len(normalized_words)):
                if normalized_words[index] and normalized_words[index] == normalized_words[index - 1]:
                    hits.append(
                        (
                            segment.words[index - 1].start_seconds,
                            segment.words[index].end_seconds,
                            normalized_words[index],
                        )
                    )
            return hits

        words = self._tokenize(segment.text)
        for index in range(1, len(words)):
            if words[index] == words[index - 1]:
                hits.append((segment.start_seconds, min(segment.end_seconds, segment.start_seconds + 1.0), words[index]))
        return hits

    def _compute_script_overlap_ratio(self, text: str, script_tokens: set[str]) -> float:
        segment_tokens = self._tokenize(text)
        if not segment_tokens or not script_tokens:
            return 0.0
        overlap = sum(1 for token in segment_tokens if token in script_tokens)
        return overlap / max(len(segment_tokens), 1)

    def _deduplicate_candidates(self, candidates: list[EditCandidate]) -> list[EditCandidate]:
        unique: list[EditCandidate] = []
        seen: set[tuple[int, int, str, str]] = set()
        for item in sorted(candidates, key=lambda candidate: (candidate.start_seconds, candidate.end_seconds, -candidate.priority)):
            key = (int(item.start_seconds * 1000), int(item.end_seconds * 1000), item.action, item.reason)
            if key in seen:
                continue
            seen.add(key)
            unique.append(item)
        return unique

    def _select_candidates(
        self,
        payload: AnalysisRequest,
        candidates: list[EditCandidate],
        transcript_segments: list[TranscriptSegment],
    ) -> list[EditCandidate]:
        original_duration_seconds = self._resolve_original_duration_seconds(
            transcript_segments=transcript_segments,
            script_text='',
            prepared_audio=None,
        )
        minimum_required = max(0, original_duration_seconds - payload.max_duration_minutes * 60)
        desired_target = max(0, original_duration_seconds - payload.target_duration_minutes * 60)

        if not candidates:
            return []

        always_apply_reasons = {'pause_keyword', 'fillers', 'repeated_words', 'self_correction'}
        if desired_target <= 0 and minimum_required <= 0:
            critical_candidates = [
                candidate
                for candidate in sorted(candidates, key=lambda item: (-item.priority, -item.confidence, item.start_seconds))
                if candidate.priority >= 85 or candidate.action == 'cut' or candidate.reason in always_apply_reasons
            ]
            return sorted(critical_candidates[:settings.max_edit_plan_items], key=lambda item: item.start_seconds)

        selected: list[EditCandidate] = []
        accumulated = 0.0
        used_ranges: list[tuple[float, float]] = []

        # Always include explicit edit rules first (pause markers, fillers, repeated words)
        mandatory = [c for c in candidates if c.reason in always_apply_reasons]
        for candidate in sorted(mandatory, key=lambda item: item.start_seconds):
            if self._overlaps_existing(candidate, used_ranges):
                continue
            selected.append(candidate)
            used_ranges.append((candidate.start_seconds, candidate.end_seconds))
            accumulated += candidate.estimated_saved_seconds

        for candidate in sorted(candidates, key=lambda item: (-item.priority, -item.confidence, item.start_seconds)):
            if len(selected) >= settings.max_edit_plan_items:
                break
            if candidate.reason in always_apply_reasons:
                continue  # already added above
            if self._overlaps_existing(candidate, used_ranges):
                continue
            selected.append(candidate)
            used_ranges.append((candidate.start_seconds, candidate.end_seconds))
            accumulated += candidate.estimated_saved_seconds
            if accumulated >= desired_target and desired_target > 0:
                break

        if accumulated < minimum_required:
            for candidate in sorted(candidates, key=lambda item: (-item.estimated_saved_seconds, -item.confidence)):
                if candidate in selected or len(selected) >= settings.max_edit_plan_items:
                    continue
                if self._overlaps_existing(candidate, used_ranges):
                    continue
                selected.append(candidate)
                used_ranges.append((candidate.start_seconds, candidate.end_seconds))
                accumulated += candidate.estimated_saved_seconds
                if accumulated >= minimum_required:
                    break

        return sorted(selected, key=lambda item: item.start_seconds)

    def _overlaps_existing(self, candidate: EditCandidate, used_ranges: list[tuple[float, float]]) -> bool:
        for start, end in used_ranges:
            if candidate.start_seconds < end and candidate.end_seconds > start:
                return True
        return False

    def _resolve_original_duration_seconds(
        self,
        *,
        transcript_segments: list[TranscriptSegment],
        script_text: str,
        prepared_audio: PreparedAudio | None,
    ) -> int:
        if prepared_audio is not None and prepared_audio.duration_seconds > 0:
            return max(1, int(round(prepared_audio.duration_seconds)))
        if transcript_segments:
            return max(1, int(round(max(segment.end_seconds for segment in transcript_segments))))
        if script_text.strip():
            return max(60, self._estimate_duration_seconds(script_text))
        return 60

    def _join_transcript_text(self, transcript_segments: list[TranscriptSegment]) -> str:
        return ' '.join(segment.text for segment in transcript_segments).strip()

    def _build_coverage(
        self,
        *,
        sections: list[str],
        transcript_segments: list[TranscriptSegment],
        transcript_text: str,
        estimated_final_duration_seconds: int,
    ) -> CoveragePayload:
        if not sections:
            return CoveragePayload(sections=[], missing_topics=[], overextended_topics=[])

        transcript_tokens = set(self._tokenize(transcript_text))
        average_minutes = round((estimated_final_duration_seconds / 60) / max(len(sections), 1), 2)

        coverage_sections: list[CoverageSection] = []
        missing_topics: list[str] = []
        overextended_topics: list[str] = []

        for section in sections:
            section_tokens = set(self._tokenize(section))
            overlap = len(section_tokens & transcript_tokens)
            status = 'covered' if overlap > 0 else 'missing'

            related_segments = [
                segment
                for segment in transcript_segments
                if self._compute_script_overlap_ratio(segment.text, section_tokens) >= 0.2
            ]
            actual_minutes = round(
                sum(max(0.0, segment.end_seconds - segment.start_seconds) for segment in related_segments) / 60,
                2,
            )
            expected_minutes = max(0.5, average_minutes)
            coverage_sections.append(
                CoverageSection(
                    title=section,
                    expected_minutes=expected_minutes,
                    actual_minutes=actual_minutes if actual_minutes > 0 else None,
                    status=status,
                )
            )

            if overlap == 0:
                missing_topics.append(section)
            elif actual_minutes > expected_minutes * 1.4:
                overextended_topics.append(section)

        return CoveragePayload(
            sections=coverage_sections,
            missing_topics=missing_topics,
            overextended_topics=overextended_topics,
        )

    def _write_artifacts(
        self,
        *,
        payload: AnalysisRequest,
        transcript_segments: list[TranscriptSegment],
        silence_regions: list[SpeechRegion],
        speech_regions: list[SpeechRegion],
        edit_plan: list[EditPlanItem],
        coverage: CoveragePayload,
        diagnostics: dict[str, Any],
        summary: SummaryPayload,
        prepared_audio: PreparedAudio | None,
        cleaned_audio: CleanedAudio,
        media_render: EditedMediaRender,
        remotion_manifest_path: str,
        final_video_path: str,
        storage_url: str | None,
    ) -> ArtifactsPayload:
        internal_alignment_payload = [
            {
                'start': round(segment.start_seconds, 3),
                'end': round(segment.end_seconds, 3),
                'text': segment.text,
                'words': [
                    {
                        'start': round(word.start_seconds, 3),
                        'end': round(word.end_seconds, 3),
                        'text': word.text,
                        'probability': word.probability,
                    }
                    for word in segment.words
                ],
            }
            for segment in transcript_segments
        ]

        edit_plan_payload = [item.model_dump() for item in edit_plan]
        report_markdown = self._build_report_markdown(payload, summary, coverage, diagnostics, edit_plan)

        extra_json_payloads: dict[str, Any] = {
            'speech-regions.json': [
                {'start': round(region.start_seconds, 3), 'end': round(region.end_seconds, 3)}
                for region in speech_regions
            ],
            'silence-regions.json': [
                {'start': round(region.start_seconds, 3), 'end': round(region.end_seconds, 3)}
                for region in silence_regions
            ],
            'coverage.json': coverage.model_dump(),
            'diagnostics.json': diagnostics,
        }
        extra_artifact_paths: dict[str, str] = {
            'cleaned_audio_path': str(cleaned_audio.cleaned_path),
            'clean_video_path': str(media_render.output_path),
            'final_video_path': final_video_path,
            'remotion_manifest_path': remotion_manifest_path,
        }
        if storage_url:
            extra_artifact_paths['storage_url'] = storage_url
        if prepared_audio is not None:
            extra_artifact_paths['prepared_audio_path'] = str(prepared_audio.prepared_path)

        return self.artifact_writer.write(
            job_uuid=payload.job_uuid,
            internal_alignment_payload=internal_alignment_payload,
            edit_plan_payload=edit_plan_payload,
            report_markdown=report_markdown,
            extra_json_payloads=extra_json_payloads,
            extra_artifact_paths=extra_artifact_paths,
        )

    def _build_report_markdown(
        self,
        payload: AnalysisRequest,
        summary: SummaryPayload,
        coverage: CoveragePayload,
        diagnostics: dict[str, Any],
        edit_plan: list[EditPlanItem],
    ) -> str:
        lines = [
            f'# {payload.title}',
            '',
            f'- job_uuid: {payload.job_uuid}',
            f'- status: completed',
            f'- language: {payload.language}',
            f'- original_duration_seconds: {summary.original_duration_seconds}',
            f'- estimated_final_duration_seconds: {summary.estimated_final_duration_seconds}',
            f'- time_saved_seconds: {summary.time_saved_seconds}',
            '',
            '## Coverage',
        ]

        for section in coverage.sections:
            lines.append(
                f"- {section.title or 'Sin título'} | status={section.status} | expected={section.expected_minutes} | actual={section.actual_minutes}"
            )

        lines.extend(['', '## Diagnostics'])
        for key, value in diagnostics.items():
            lines.append(f'- {key}: {value}')

        lines.extend(['', '## Edit Plan'])
        for item in edit_plan:
            lines.append(f"- {item.start} → {item.end} | {item.action} | {item.reason} | {item.observation}")

        return '\n'.join(lines)

    def _tokenize(self, text: str) -> list[str]:
        return re.findall(r"[a-záéíóúñü0-9']+", text.casefold(), flags=re.IGNORECASE)

    def _estimate_duration_seconds(self, text: str) -> int:
        words = max(1, len(self._tokenize(text)))
        return max(1, int(math.ceil(words / settings.words_per_minute * 60)))

    def _timestamp_to_seconds(self, value: str) -> float:
        clean = value.replace(',', '.').strip()
        parts = clean.split(':')
        try:
            if len(parts) == 3:
                hours, minutes, seconds = parts
                return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
            if len(parts) == 2:
                minutes, seconds = parts
                return int(minutes) * 60 + float(seconds)
            return float(clean)
        except (TypeError, ValueError):
            return 0.0

    def _delete_r2_sources(self, video_paths: list[str]) -> tuple[list[str], dict[str, str]]:
        deleted: list[str] = []
        errors: dict[str, str] = {}
        for path in video_paths:
            key = self.artifact_writer.extract_r2_key(path)
            if not key:
                continue
            try:
                self.artifact_writer.delete_from_r2(remote_key=key)
                deleted.append(key)
            except Exception as exc:
                errors[key] = str(exc)
        return deleted, errors

    def _build_protected_ranges(
        self,
        video_paths: list[str],
        resolved_videos: list,
        title_paths_set: set[str],
    ) -> list[tuple[float, float]]:
        protected: list[tuple[float, float]] = []
        cursor = 0.0
        for path, resolved in zip(video_paths, resolved_videos):
            duration = self.media_editing_service.probe_video_duration(resolved.local_path)
            if path.strip() in title_paths_set:
                protected.append((cursor, cursor + duration))
            cursor += duration
        return protected

    def _find_pause_end_seconds(self, segment: TranscriptSegment, pause_keyword: str) -> float | None:
        if not segment.words:
            return None
        keyword_words = pause_keyword.split()
        clean = lambda t: t.strip(" .,;:!?¡¿\"'()[]{}").casefold()
        for i, word in enumerate(segment.words):
            if clean(word.text) != keyword_words[0]:
                continue
            if len(keyword_words) == 1:
                return word.end_seconds
            match = all(
                i + j < len(segment.words) and clean(segment.words[i + j].text) == kw
                for j, kw in enumerate(keyword_words[1:], 1)
            )
            if match:
                return segment.words[i + len(keyword_words) - 1].end_seconds
        return None

    def _format_timestamp(self, seconds: float) -> str:
        total_milliseconds = int(round(max(0.0, seconds) * 1000))
        hours, remainder = divmod(total_milliseconds, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        secs, milliseconds = divmod(remainder, 1000)
        if milliseconds == 0:
            return f'{hours:02d}:{minutes:02d}:{secs:02d}'
        return f'{hours:02d}:{minutes:02d}:{secs:02d}.{milliseconds:03d}'
