import { z } from 'zod/v4';

export const VideoCleanupRequestSchema = z.object({
  job_uuid: z.string().uuid(),
  title: z.string().min(1, 'Title is required'),
  language: z.string().default('es'),
  target_duration_minutes: z.number().min(1).max(240).default(10),
  max_duration_minutes: z.number().min(1).max(240).default(15),
  source: z.object({
    video_path: z.string().url('Video path must be a valid URL'),
    script_pdf_path: z.string().url('Script PDF path must be a valid URL'),
  }),
  rules: z.object({
    pause_keyword: z.string().default('PAUSA'),
    silence_threshold_seconds: z.number().default(3),
    detect_fillers: z.boolean().default(true),
    detect_repeated_words: z.boolean().default(true),
    detect_self_corrections: z.boolean().default(true),
    store_artifacts: z.boolean().default(true),
  }).optional(),
  editorial_prompt: z.string().min(1, 'Editorial prompt is required'),
  title_overlays: z.array(z.object({
    video_path: z.string(),
    start_seconds: z.number(),
    duration_seconds: z.number(),
    title: z.string().optional(),
  })).optional(),
});

export type VideoCleanupRequest = z.infer<typeof VideoCleanupRequestSchema>;

export const VideoCleanupResponseSchema = z.object({
  job_uuid: z.string(),
  status: z.string(),
  summary: z.object({
    original_duration_seconds: z.number(),
    estimated_final_duration_seconds: z.number(),
    time_saved_seconds: z.number(),
    learning_objectives_met: z.boolean(),
  }),
  coverage: z.object({
    sections: z.array(z.object({
      title: z.string().optional(),
      expected_minutes: z.number().nullable().optional(),
      actual_minutes: z.number().nullable().optional(),
      status: z.string().optional(),
    })),
    missing_topics: z.array(z.string()),
    overextended_topics: z.array(z.string()),
  }),
  edit_plan: z.array(z.object({
    start: z.string().optional(),
    end: z.string().optional(),
    action: z.string().optional(),
    reason: z.string().optional(),
    observation: z.string().optional(),
    confidence: z.number().optional(),
  })),
  artifacts: z.object({
    cleaned_audio_path: z.string().optional(),
    clean_video_path: z.string().optional(),
    final_video_path: z.string().optional(),
    remotion_manifest_path: z.string().optional(),
    report_md_path: z.string().optional(),
    edit_plan_json_path: z.string().optional(),
    storage_url: z.string().optional(),
  }).optional(),
  diagnostics: z.record(z.string(), z.unknown()),
});

export type VideoCleanupResponse = z.infer<typeof VideoCleanupResponseSchema>;

export const VideoCleanupFormSchema = z.object({
  title: z.string().min(1, 'Title is required'),
  language: z.string().default('es'),
  target_duration_minutes: z.number().min(1).max(240).default(10),
  max_duration_minutes: z.number().min(1).max(240).default(15),
  editorial_prompt: z.string().optional(),
  video_files: z.array(z.instanceof(File)).min(1, 'At least one video file is required'),
  script_file: z.any().optional(),
});

export type VideoCleanupFormValues = z.infer<typeof VideoCleanupFormSchema>;

export interface ActionResult<T = void> {
  readonly success: boolean;
  readonly data?: T;
  readonly error?: string;
}

export type ArtifactKey = 'clean-video' | 'remotion-manifest' | 'cleaned-audio' | 'edit-plan' | 'report';
