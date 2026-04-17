'use server';

import { randomUUID } from 'crypto';
import { PutObjectCommand, S3Client } from '@aws-sdk/client-s3';
import { VideoCleanupRequestSchema, VideoCleanupResponseSchema, type ActionResult, type VideoCleanupResponse } from '../types';

const API_URL = process.env.TUTORIAL_CLEANUP_API_BASE_URL || 'https://video-cleanup-api-production.up.railway.app';
const API_TOKEN = process.env.TUTORIAL_CLEANUP_API_TOKEN || '';

// Initialize R2/S3 client
const r2Client = new S3Client({
  region: 'auto',
  endpoint: process.env.TUTORIAL_CLEANUP_R2_ENDPOINT,
  credentials: {
    accessKeyId: process.env.TUTORIAL_CLEANUP_R2_ACCESS_KEY_ID || '',
    secretAccessKey: process.env.TUTORIAL_CLEANUP_R2_SECRET_ACCESS_KEY || '',
  },
  forcePathStyle: true,
});

const R2_BUCKET_NAME = process.env.TUTORIAL_CLEANUP_R2_BUCKET_NAME || '';
const R2_PUBLIC_BASE_URL = process.env.TUTORIAL_CLEANUP_R2_PUBLIC_BASE_URL || '';

async function uploadToR2(file: File, subfolder: string): Promise<string> {
  const bytes = await file.arrayBuffer();
  const buffer = Buffer.from(bytes);

  const filename = `${randomUUID()}-${file.name}`;
  const key = `${subfolder}/${filename}`;

  console.log('[VIDEO-CLEANUP] Uploading file:', filename, 'Size:', file.size, 'Type:', file.type);

  await r2Client.send(
    new PutObjectCommand({
      Bucket: R2_BUCKET_NAME,
      Key: key,
      Body: buffer,
      ContentType: file.type || 'application/octet-stream',
    })
  );

  const publicUrl = `${R2_PUBLIC_BASE_URL}/${key}`;
  console.log('[VIDEO-CLEANUP] File uploaded to:', publicUrl);
  console.log('[VIDEO-CLEANUP] R2_PUBLIC_BASE_URL:', R2_PUBLIC_BASE_URL);
  return publicUrl;
}

export async function processVideoCleanupAction(formData: FormData): Promise<ActionResult<VideoCleanupResponse>> {
  console.log('[VIDEO-CLEANUP] Starting process...');
  
  const title = formData.get('title') as string;
  const language = formData.get('language') as string || 'es';
  const targetDuration = Number(formData.get('target_duration_minutes')) || 10;
  const maxDuration = Number(formData.get('max_duration_minutes')) || 15;
  const editorialPrompt = formData.get('editorial_prompt') as string;
  const scriptFile = formData.get('script_file') as File;
  const videoFiles = formData.getAll('video_files') as File[];

  console.log('[VIDEO-CLEANUP] Form data:', { title, language, targetDuration, maxDuration, editorialPrompt });
  console.log('[VIDEO-CLEANUP] Video files count:', videoFiles?.length);
  console.log('[VIDEO-CLEANUP] Script file:', scriptFile?.name);

  if (!videoFiles || videoFiles.length === 0) {
    console.error('[VIDEO-CLEANUP] No video files provided');
    return { success: false, error: 'At least one video file is required' };
  }

  try {
    const jobId = randomUUID();
    console.log('[VIDEO-CLEANUP] Job ID:', jobId);
    console.log('[VIDEO-CLEANUP] API URL:', API_URL);
    console.log('[VIDEO-CLEANUP] R2 Bucket:', R2_BUCKET_NAME);
    console.log('[VIDEO-CLEANUP] R2 Public URL:', R2_PUBLIC_BASE_URL);

    let scriptUrl: string | undefined;
    if (scriptFile instanceof File && scriptFile.size > 0) {
      console.log('[VIDEO-CLEANUP] Uploading script to R2...');
      scriptUrl = await uploadToR2(scriptFile, `scripts/${jobId}`);
      console.log('[VIDEO-CLEANUP] Script uploaded:', scriptUrl);
    }

    const validVideoFiles = videoFiles
      .filter((f): f is File => f instanceof File && f.size > 0)
      .sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: 'base' }));

    if (validVideoFiles.length === 0) {
      console.error('[VIDEO-CLEANUP] No valid video File objects received from FormData');
      return { success: false, error: 'Video files could not be read — please try again' };
    }

    console.log('[VIDEO-CLEANUP] Uploading', validVideoFiles.length, 'video(s) to R2...');
    const videoUrls = await Promise.all(
      validVideoFiles.map((file, i) => {
        console.log(`[VIDEO-CLEANUP] Uploading video ${i + 1}/${validVideoFiles.length}:`, file.name);
        return uploadToR2(file, `videos/${jobId}`);
      }),
    );
    console.log('[VIDEO-CLEANUP] Videos uploaded:', videoUrls);

    const source: Record<string, unknown> =
      videoUrls.length === 1
        ? { video_path: videoUrls[0] }
        : { video_paths: videoUrls };

    if (scriptUrl) {
      source.script_pdf_path = scriptUrl;
    }

    const requestBody: any = {
      job_uuid: jobId,
      title,
      language,
      target_duration_minutes: targetDuration,
      max_duration_minutes: maxDuration,
      source,
      editorial_prompt: editorialPrompt,
      rules: {
        pause_keyword: 'PAUSA',
        silence_threshold_seconds: 3,
        detect_fillers: true,
        detect_repeated_words: true,
        detect_self_corrections: true,
        store_artifacts: true,
      },
    };

    console.log('[VIDEO-CLEANUP] Sending request to API...');
    console.log('[VIDEO-CLEANUP] Request body:', JSON.stringify(requestBody, null, 2));

    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
    };

    if (API_TOKEN) {
      headers['Authorization'] = `Bearer ${API_TOKEN}`;
      console.log('[VIDEO-CLEANUP] Using bearer token');
    } else {
      console.warn('[VIDEO-CLEANUP] No API token configured');
    }

    const response = await fetch(`${API_URL}/analysis/jobs/sync`, {
      method: 'POST',
      headers,
      body: JSON.stringify(requestBody),
    });

    console.log('[VIDEO-CLEANUP] API Response status:', response.status, response.statusText);

    if (!response.ok) {
      const errorText = await response.text();
      console.error('[VIDEO-CLEANUP] API Error:', errorText);
      return { success: false, error: errorText || 'Processing failed' };
    }

    const data = await response.json();
    console.log('[VIDEO-CLEANUP] API Response data:', JSON.stringify(data, null, 2));
    console.log('[VIDEO-CLEANUP] API Response type:', typeof data);
    console.log('[VIDEO-CLEANUP] API Response keys:', Object.keys(data));

    const parsed = VideoCleanupResponseSchema.safeParse(data);
    
    if (!parsed.success) {
      console.error('[VIDEO-CLEANUP] Validation error:', parsed.error);
      return { success: false, error: 'Invalid response from API' };
    }

    console.log('[VIDEO-CLEANUP] Processing successful');
    return { success: true, data: parsed.data };
  } catch (error) {
    console.error('[VIDEO-CLEANUP] Exception:', error);
    return { success: false, error: error instanceof Error ? error.message : 'Unknown error' };
  }
}
