from __future__ import annotations

import shutil
import subprocess
import re
import unicodedata
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


def _cleanup_job_workspace(
    *,
    job_uuid: str,
    keep_paths: list[Path],
    resolved_inputs: list[ResolvedInput],
) -> None:
    """Remove every intermediate artifact for a completed export job.

    Keeps only the files whose absolute paths match `keep_paths`, plus removes
    any downloaded remote-source input cached at <artifact_root>/_downloads/.
    Finally collapses empty subdirectories under the job folder.

    All errors are logged; this helper never raises to avoid masking a
    successful export response.
    """
    import logging
    logger = logging.getLogger(__name__)

    try:
        keep_set = {p.resolve() for p in keep_paths}
    except Exception:
        keep_set = set(keep_paths)

    # 1) Remove downloaded remote input caches belonging to this job.
    for resolved in resolved_inputs:
        if not getattr(resolved, 'downloaded', False):
            continue
        local_path = getattr(resolved, 'local_path', None)
        if not local_path:
            continue
        try:
            if Path(local_path).exists():
                Path(local_path).unlink()
                logger.info(f"_cleanup_job_workspace: removed download cache {local_path}")
        except Exception as exc:
            logger.warning(f"_cleanup_job_workspace: failed to remove {local_path}: {exc}")

    # 2) Walk the job workspace and remove every non-kept file, bottom up.
    job_dir = Path(settings.artifact_root) / job_uuid
    if not job_dir.exists():
        return

    try:
        entries = sorted(job_dir.rglob('*'), key=lambda p: len(p.parts), reverse=True)
    except Exception as exc:
        logger.warning(f"_cleanup_job_workspace: failed to scan {job_dir}: {exc}")
        return

    for path in entries:
        try:
            if path.is_file() or path.is_symlink():
                try:
                    resolved_path = path.resolve()
                except Exception:
                    resolved_path = path
                if resolved_path in keep_set:
                    continue
                path.unlink()
                logger.info(f"_cleanup_job_workspace: removed file {path}")
            elif path.is_dir():
                try:
                    path.rmdir()
                    logger.info(f"_cleanup_job_workspace: removed empty dir {path}")
                except OSError:
                    pass
        except Exception as exc:
            logger.warning(f"_cleanup_job_workspace: failed on {path}: {exc}")


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
        filler_cuts: list[tuple[float, float]] = []
        word_gap_cuts: list[tuple[float, float]] = []
        stutter_cuts: list[tuple[float, float]] = []
        transcription_diagnostics: dict[str, Any] = {}
        needs_transcription = (
            bool(request.pause_keywords)
            or request.detect_fillers
            or request.compact_word_gaps
            or request.detect_stutters
        )
        if needs_transcription:
            segments, transcription_diagnostics = self.transcription_service.transcribe(prepared_audio, language=request.language)
            transcription_diagnostics['transcription_text'] = ' | '.join(s.text.strip() for s in segments)[:600]
            if request.pause_keywords:
                pause_cuts = self._find_pause_cuts(segments, request.pause_keywords)
            transcription_diagnostics['pause_cut_ranges'] = [
                [round(s, 3), round(e, 3)] for s, e in pause_cuts
            ]
            if request.detect_fillers:
                filler_cuts = self._find_filler_cuts(segments, request.filler_terms)
            transcription_diagnostics['filler_cut_ranges'] = [
                [round(s, 3), round(e, 3)] for s, e in filler_cuts
            ]
            if request.compact_word_gaps:
                word_gap_cuts = self._find_word_gap_cuts(
                    segments,
                    gap_threshold_seconds=request.word_gap_threshold_seconds,
                    trim_to_seconds=request.word_gap_trim_to_seconds,
                    long_silence_threshold_seconds=request.silence_threshold_seconds,
                )
            transcription_diagnostics['word_gap_cut_ranges'] = [
                [round(s, 3), round(e, 3)] for s, e in word_gap_cuts
            ]
            if request.detect_stutters:
                stutter_cuts = self._find_stutter_cuts(
                    segments,
                    max_gap_seconds=request.stutter_max_gap_seconds,
                    max_token_chars=request.stutter_max_token_chars,
                )
            transcription_diagnostics['stutter_cut_ranges'] = [
                [round(s, 3), round(e, 3)] for s, e in stutter_cuts
            ]

        cut_ranges = (
            [(gap.start_seconds, gap.end_seconds) for gap in silence_gaps]
            + pause_cuts
            + filler_cuts
            + word_gap_cuts
            + stutter_cuts
        )
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
            'filler_cuts': len(filler_cuts),
            'word_gap_cuts': len(word_gap_cuts),
            'stutter_cuts': len(stutter_cuts),
            'keep_segments': len(keep_ranges),
            'output_path': str(output_path),
            **vad_diagnostics,
            **transcription_diagnostics,
        }
        if diagnostics_r2_error:
            diagnostics['r2_upload_error'] = diagnostics_r2_error

        if request.cleanup_intermediates:
            _cleanup_job_workspace(
                job_uuid=request.job_uuid,
                keep_paths=[output_path],
                resolved_inputs=resolved,
            )

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

    @staticmethod
    def _normalize(text: str) -> str:
        """Casefold + strip diacritics so 'PAUSA ACÁ' == 'pausa aca' == 'Pausa Acá'."""
        return ''.join(
            c for c in unicodedata.normalize('NFD', text.casefold())
            if unicodedata.category(c) != 'Mn'
        )

    def _find_pause_cuts(self, segments: list[TranscriptSegment], pause_keywords: list[str]) -> list[tuple[float, float]]:
        import logging
        logger = logging.getLogger(__name__)
        
        if not segments:
            logger.warning(f"_find_pause_cuts: No segments provided")
            return []

        # Deduplicate after normalization (e.g. 'PAUSA ACA' and 'PAUSA ACÁ' collapse to same)
        keywords_sorted = sorted(
            {self._normalize(kw) for kw in pause_keywords if kw.strip()},
            key=lambda kw: len(kw.split()),
            reverse=True,
        )
        
        logger.info(f"_find_pause_cuts: Input keywords: {pause_keywords}")
        logger.info(f"_find_pause_cuts: Normalized sorted keywords: {keywords_sorted}")
        logger.info(f"_find_pause_cuts: Number of segments: {len(segments)}")

        # Flatten every word from every segment into a single list so that keywords
        # split across two Whisper segment boundaries (common with small models) are
        # still detected via the global word sequence.
        flat: list[tuple[TranscriptWord, int]] = [
            (w, seg_idx)
            for seg_idx, seg in enumerate(segments)
            for w in seg.words
        ]
        
        logger.info(f"_find_pause_cuts: Total words in flat list: {len(flat)}")
        if flat:
            # Log first 20 words for debugging
            sample_words = [(self._normalize(w.text), w.start_seconds, w.end_seconds) for w, _ in flat[:20]]
            logger.info(f"_find_pause_cuts: Sample words (first 20): {sample_words}")

        clean = self._clean_transcript_token
        cuts: list[tuple[float, float]] = []

        if flat:
            # Word-level global scan — detects keywords that span Whisper segment boundaries.
            # consumed_starts tracks the flat-list index of each matched keyword's first word so
            # that a shorter keyword (e.g. 'pausa') cannot re-match at the same position already
            # claimed by a longer one (e.g. 'pausa aca').
            consumed_starts: set[int] = set()
            for kw in keywords_sorted:
                kw_parts = kw.split()
                n = len(kw_parts)
                logger.info(f"_find_pause_cuts: Scanning for keyword '{kw}' ({n} parts)")
                matches_found = 0
                for i in range(len(flat) - n + 1):
                    if i in consumed_starts:
                        continue
                    if not self._keyword_matches_at(flat, i, kw_parts):
                        continue
                    consumed_starts.add(i)
                    kw_end = flat[i + n - 1][0].end_seconds
                    seg_idx = flat[i][1]
                    kw_start = flat[i][0].start_seconds
                    seg_start = segments[seg_idx].start_seconds
                    seg_text = segments[seg_idx].text.strip()
                    
                    # Log segment context
                    if seg_idx > 0:
                        prev_seg_text = segments[seg_idx - 1].text.strip()
                        logger.info(f"_find_pause_cuts: Previous segment {seg_idx - 1}: '{prev_seg_text}'")
                    logger.info(f"_find_pause_cuts: Current segment {seg_idx}: '{seg_text}'")
                    logger.info(f"_find_pause_cuts: Keyword '{kw}' starts at {kw_start:.3f}, segment starts at {seg_start:.3f}, offset = {kw_start - seg_start:.3f}s")
                    
                    # If the keyword begins well into its segment the bad take is also
                    # inside that segment; otherwise look one segment back — UNLESS the
                    # previous segment ended with strong punctuation (. ! ?), which means
                    # the user finished a clean thought before saying "pausa". In that
                    # case the previous segment is good content and must be preserved.
                    if kw_start > seg_start + 0.5 or seg_idx == 0:
                        cut_start = seg_start
                        logger.info(f"_find_pause_cuts: Cutting from START of current segment ({cut_start:.3f}) - keyword is well into segment")
                    else:
                        prev_text = segments[seg_idx - 1].text.strip()
                        prev_ends_clean = prev_text.endswith(('.', '!', '?', '…'))
                        if prev_ends_clean:
                            cut_start = seg_start
                            logger.info(f"_find_pause_cuts: Previous segment ends with punctuation ('{prev_text[-30:]}'), keeping it. Cutting from current segment start ({cut_start:.3f})")
                        else:
                            cut_start = segments[seg_idx - 1].start_seconds
                            logger.info(f"_find_pause_cuts: Cutting from START of PREVIOUS segment ({cut_start:.3f}) - keyword is at beginning of current segment and previous didn't end cleanly")
                    
                    # IMPORTANT: Cut only to the END of the keyword, not the end of the segment.
                    # This preserves any content that comes AFTER the keyword in the same segment.
                    cut_end = kw_end
                    logger.info(f"_find_pause_cuts: Cut range: ({cut_start:.3f}, {cut_end:.3f}) - segment ends at {segments[seg_idx].end_seconds:.3f}")
                    cuts.append((cut_start, cut_end))
                    matches_found += 1
                    logger.info(f"_find_pause_cuts: Found match for '{kw}' at position {i}, cut from {cut_start:.3f} to {kw_end:.3f}")
                logger.info(f"_find_pause_cuts: Keyword '{kw}' found {matches_found} matches")

        if not cuts:
            logger.warning(f"_find_pause_cuts: Word-level scan found no cuts, trying fallback segment-level match")
            # Fallback: segment-level substring match.
            # Runs when there are no word timestamps OR when the word-level scan found nothing
            # (e.g. because the tiny model produced word tokens that differ from the keyword).
            for seg_idx, segment in enumerate(segments):
                normalized = ' '.join(clean(part) for part in segment.text.split())
                logger.debug(f"_find_pause_cuts: Segment {seg_idx} text: '{segment.text}' -> normalized: '{normalized}'")
                matched = next((kw for kw in keywords_sorted if kw in normalized or kw.replace(' ', '') in normalized.replace(' ', '')), None)
                if matched is not None:
                    cut_start = segments[seg_idx - 1].start_seconds if seg_idx > 0 else segment.start_seconds
                    cuts.append((cut_start, segment.end_seconds))
                    logger.info(f"_find_pause_cuts: Fallback found '{matched}' in segment {seg_idx}, cut from {cut_start:.3f} to {segment.end_seconds:.3f}")

        logger.info(f"_find_pause_cuts: Total cuts found: {len(cuts)}")
        return cuts

    def _find_filler_cuts(self, segments: list[TranscriptSegment], filler_terms: list[str]) -> list[tuple[float, float]]:
        import logging
        logger = logging.getLogger(__name__)

        fillers = {
            self._clean_transcript_token(term)
            for term in filler_terms
            if term.strip()
        }
        cuts: list[tuple[float, float]] = []

        for segment in segments:
            if segment.words:
                for word in segment.words:
                    token = self._clean_transcript_token(word.text)
                    if not self._is_filler_token(token, fillers):
                        continue
                    cut_start, cut_end = self._expand_cut_to_min_duration(
                        max(segment.start_seconds, word.start_seconds - 0.05),
                        min(segment.end_seconds, word.end_seconds + 0.05),
                        segment.start_seconds,
                        segment.end_seconds,
                    )
                    cuts.append((cut_start, cut_end))
                    logger.info(f"_find_filler_cuts: Found filler '{token}', cut from {cut_start:.3f} to {cut_end:.3f}")
                continue

            normalized_words = [self._clean_transcript_token(part) for part in segment.text.split()]
            if normalized_words and self._is_filler_token(normalized_words[0], fillers):
                cut_end = min(segment.end_seconds, segment.start_seconds + max(0.35, settings.render_min_segment_seconds))
                cuts.append((segment.start_seconds, cut_end))
                logger.info(f"_find_filler_cuts: Found leading filler '{normalized_words[0]}', cut from {segment.start_seconds:.3f} to {cut_end:.3f}")

        logger.info(f"_find_filler_cuts: Total cuts found: {len(cuts)}")
        return cuts

    def _find_word_gap_cuts(
        self,
        segments: list[TranscriptSegment],
        *,
        gap_threshold_seconds: float,
        trim_to_seconds: float,
        long_silence_threshold_seconds: float,
    ) -> list[tuple[float, float]]:
        import logging
        logger = logging.getLogger(__name__)

        words = sorted(
            [
                word
                for segment in segments
                for word in segment.words
                if self._clean_transcript_token(word.text)
            ],
            key=lambda word: word.start_seconds,
        )
        cuts: list[tuple[float, float]] = []

        for previous_word, next_word in zip(words, words[1:]):
            gap_start = previous_word.end_seconds
            gap_end = next_word.start_seconds
            gap_duration = gap_end - gap_start

            if gap_duration < gap_threshold_seconds:
                continue
            if gap_duration >= long_silence_threshold_seconds:
                continue
            if gap_duration <= trim_to_seconds:
                continue

            cut_start = gap_start + trim_to_seconds / 2
            cut_end = gap_end - trim_to_seconds / 2
            if cut_end - cut_start < settings.render_min_segment_seconds:
                continue

            cuts.append((cut_start, cut_end))
            logger.info(
                f"_find_word_gap_cuts: Gap {gap_duration:.3f}s between "
                f"'{previous_word.text}' and '{next_word.text}', cut from {cut_start:.3f} to {cut_end:.3f}"
            )

        logger.info(f"_find_word_gap_cuts: Total cuts found: {len(cuts)}")
        return cuts

    def _find_stutter_cuts(
        self,
        segments: list[TranscriptSegment],
        *,
        max_gap_seconds: float,
        max_token_chars: int,
    ) -> list[tuple[float, float]]:
        """Detect stuttered word starts (e.g. 'y y y vamos', 'v-v-vamos', 'es es esto')
        and remove all but the LAST occurrence so the intended word stays.

        Conservative rules — only cut when:
          - 2+ consecutive words have the SAME normalized token AND tight timing
            (gap between them <= max_gap_seconds), OR
          - a short stuttered token (<= 3 chars) is followed by a longer word that
            starts with the same prefix and shares tight timing
            (e.g. 'va', 'vamos' → cut 'va').
        """
        import logging
        logger = logging.getLogger(__name__)

        words = [
            word
            for segment in segments
            for word in segment.words
            if self._clean_transcript_token(word.text)
        ]
        if not words:
            logger.info("_find_stutter_cuts: no word timestamps available, skipping")
            return []

        tokens = [self._clean_transcript_token(word.text) for word in words]
        cuts: list[tuple[float, float]] = []
        n = len(tokens)
        index = 0
        while index < n:
            token = tokens[index]
            if not token or len(token) > max_token_chars:
                index += 1
                continue

            run_end = index
            while run_end + 1 < n:
                gap = words[run_end + 1].start_seconds - words[run_end].end_seconds
                if gap > max_gap_seconds or tokens[run_end + 1] != token:
                    break
                run_end += 1

            run_size = run_end - index + 1
            if run_size >= 2:
                # Cut every repeated occurrence except the LAST in the run.
                for k in range(index, run_end):
                    cuts.append(self._stutter_cut_range(words[k]))
                logger.info(
                    f"_find_stutter_cuts: repeat run of '{token}' x{run_size} "
                    f"between {words[index].start_seconds:.3f}-{words[run_end].end_seconds:.3f}, "
                    f"cut {run_size - 1} occurrences"
                )

                # Prefix-stutter add-on: 'v v vamos' / 'es es esto' → also cut the
                # last short repeat because the next word completes it.
                next_idx = run_end + 1
                if (
                    len(token) <= 3
                    and next_idx < n
                    and tokens[next_idx] != token
                    and len(tokens[next_idx]) > len(token)
                    and tokens[next_idx].startswith(token)
                    and (words[next_idx].start_seconds - words[run_end].end_seconds) <= max_gap_seconds
                ):
                    cuts.append(self._stutter_cut_range(words[run_end]))
                    logger.info(
                        f"_find_stutter_cuts: prefix-stutter '{token}' before "
                        f"'{tokens[next_idx]}' at {words[run_end].start_seconds:.3f}, "
                        f"cut last repeat too"
                    )

                index = run_end + 1
                continue

            # NOTE: We intentionally do NOT detect single-token prefix stutters
            # (e.g. 'v' before 'vamos' WITHOUT a prior repetition). Spanish has too
            # many short function words that are also prefixes of common content
            # words: 'de' before 'decisión', 'con' before 'conectores',
            # 'la' before 'lámpara', 'es' before 'esto', etc. Triggering on those
            # would produce false positives. Real stutters almost always REPEAT,
            # which is handled by the run-detection branch above.
            index += 1

        logger.info(f"_find_stutter_cuts: Total cuts found: {len(cuts)}")
        return cuts

    def _stutter_cut_range(self, word: Any) -> tuple[float, float]:
        cut_start = max(0.0, word.start_seconds - 0.02)
        cut_end = word.end_seconds + 0.05
        # Inflate to render_min_segment_seconds so _invert_cuts does not drop
        # the very short cut (single-syllable stutters are often < 0.25s).
        if cut_end - cut_start < settings.render_min_segment_seconds:
            missing = settings.render_min_segment_seconds - (cut_end - cut_start)
            cut_end += missing
        return cut_start, cut_end

    @staticmethod
    def _is_filler_token(token: str, fillers: set[str]) -> bool:
        if token in fillers:
            return True
        return any(
            re.fullmatch(pattern, token) is not None
            for pattern in (r'e+h+', r'e+m+', r'h?m{2,}', r'u+h+', r'u+m+')
        )

    @staticmethod
    def _expand_cut_to_min_duration(
        start: float,
        end: float,
        minimum_start: float,
        maximum_end: float,
    ) -> tuple[float, float]:
        minimum_duration = settings.render_min_segment_seconds
        duration = end - start
        if duration >= minimum_duration:
            return start, end

        missing = minimum_duration - duration
        start = max(minimum_start, start - missing / 2)
        end = min(maximum_end, end + missing / 2)

        if end - start < minimum_duration:
            if start <= minimum_start:
                end = min(maximum_end, start + minimum_duration)
            elif end >= maximum_end:
                start = max(minimum_start, end - minimum_duration)

        return start, end

    @staticmethod
    def _clean_transcript_token(text: str) -> str:
        return re.sub(r'[^0-9a-z]+', '', VideoExportService._normalize(text))

    @staticmethod
    def _looks_like_pause_marker(token: str) -> bool:
        return token in {'pausa', 'pauza', 'pauso', 'pausas', 'pausar', 'pause', 'pousa'} or (
            (token.startswith('paus') or token.startswith('pauz')) and len(token) <= 7
        )

    def _keyword_part_matches(self, token: str, keyword_part: str) -> bool:
        if token == keyword_part:
            return True
        if keyword_part in {'pausa', 'pauza'}:
            return self._looks_like_pause_marker(token)
        return False

    def _keyword_matches_at(self, flat: list[tuple[Any, int]], start: int, keyword_parts: list[str]) -> bool:
        tokens = [
            self._clean_transcript_token(flat[start + j][0].text)
            for j in range(len(keyword_parts))
        ]
        return all(
            self._keyword_part_matches(tokens[j], keyword_parts[j])
            for j in range(len(keyword_parts))
        )

    def _find_pause_bounds(self, segment: TranscriptSegment, keyword: str) -> tuple[float, float]:
        """Return (start, end) of the pause keyword inside the segment, using word timestamps."""
        if not segment.words:
            return segment.start_seconds, segment.end_seconds
        keyword_words = keyword.split()
        clean = lambda t: self._normalize(t.strip(" .,;:!?¡¿\"'()[]{}"))
        for i, word in enumerate(segment.words):
            if clean(word.text) != keyword_words[0]:
                continue
            if len(keyword_words) == 1:
                return word.start_seconds, word.end_seconds
            if all(
                i + j < len(segment.words) and clean(segment.words[i + j].text) == keyword_words[j]
                for j in range(1, len(keyword_words))
            ):
                last = segment.words[i + len(keyword_words) - 1]
                return word.start_seconds, last.end_seconds
        return segment.start_seconds, segment.end_seconds

    def _invert_cuts(
        self,
        cut_ranges: list[tuple[float, float]],
        duration_seconds: float,
    ) -> list[tuple[float, float]]:
        import logging
        logger = logging.getLogger(__name__)
        
        logger.info(f"_invert_cuts: Input cut_ranges: {cut_ranges}")
        logger.info(f"_invert_cuts: duration_seconds: {duration_seconds}")
        logger.info(f"_invert_cuts: render_min_segment_seconds: {settings.render_min_segment_seconds}")
        
        normalized = sorted(
            [
                (max(0.0, s), min(duration_seconds, e))
                for s, e in cut_ranges
                if e - s >= settings.render_min_segment_seconds
            ],
            key=lambda x: x[0],
        )
        
        logger.info(f"_invert_cuts: After filtering and sorting: {normalized}")
        
        keep: list[tuple[float, float]] = []
        cursor = 0.0
        for s, e in normalized:
            if s - cursor >= settings.render_min_segment_seconds:
                keep.append((cursor, s))
                logger.info(f"_invert_cuts: Keeping segment ({cursor:.3f}, {s:.3f})")
            cursor = max(cursor, e)
        if duration_seconds - cursor >= settings.render_min_segment_seconds:
            keep.append((cursor, duration_seconds))
            logger.info(f"_invert_cuts: Keeping final segment ({cursor:.3f}, {duration_seconds:.3f})")
        
        logger.info(f"_invert_cuts: Final keep_ranges: {keep}")
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

        if request.cleanup_intermediates:
            _cleanup_job_workspace(
                job_uuid=request.job_uuid,
                keep_paths=[output_path],
                resolved_inputs=resolved,
            )

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
