# Video Cleanup API - Next.js Integration Guide

## Base URL
```
https://video-cleanup-api-production.up.railway.app
```

## Authentication

All endpoints require Bearer token authentication except `/health`.

```typescript
const API_URL = process.env.TUTORIAL_CLEANUP_API_BASE_URL || 'https://video-cleanup-api-production.up.railway.app';
const API_TOKEN = process.env.TUTORIAL_CLEANUP_API_TOKEN || 'your-token-here';

const headers = {
  'Authorization': `Bearer ${API_TOKEN}`,
  'Content-Type': 'application/json',
};
```

## Endpoints

### 1. Health Check
`GET /health`

No authentication required.

```typescript
const response = await fetch(`${API_URL}/health`);
const data = await response.json();
// { status: 'ok', service: 'video-cleanup-api', version: '0.2.0' }
```

### 2. Analyze Video (Main Endpoint)
`POST /analysis/jobs/sync`

Analyzes a video, removes fillers/silences, and applies title overlays.

**Request Body:**
```typescript
interface AnalysisRequest {
  job_uuid: string;
  title: string;
  language?: string; // default: 'es'
  target_duration_minutes?: number; // default: 10, min: 1, max: 240
  max_duration_minutes?: number; // default: 15, min: 1, max: 240
  source: {
    video_path: string; // URL or local path to video
    script_pdf_path: string; // URL or local path to PDF script
  };
  rules?: {
    pause_keyword?: string; // default: 'PAUSA'
    silence_threshold_seconds?: number; // default: 3
    detect_fillers?: boolean; // default: true
    detect_repeated_words?: boolean; // default: true
    detect_self_corrections?: boolean; // default: true
    store_artifacts?: boolean; // default: true
  };
  editorial_prompt: string; // Description of the video content
  title_overlays?: Array<{
    video_path: string;
    start_seconds: number;
    duration_seconds: number;
    title?: string;
  }>;
}
```

**Example Request:**
```typescript
const requestBody = {
  job_uuid: 'unique-job-id-123',
  title: 'Tutorial Python para Principiantes',
  language: 'es',
  target_duration_minutes: 10,
  max_duration_minutes: 15,
  source: {
    video_path: 'https://your-r2-bucket.r2.cloudflarestorage.com/tutorial-video.mp4',
    script_pdf_path: 'https://your-r2-bucket.r2.cloudflarestorage.com/script.pdf',
  },
  rules: {
    pause_keyword: 'PAUSA',
    silence_threshold_seconds: 3,
    detect_fillers: true,
    detect_repeated_words: true,
    detect_self_corrections: true,
    store_artifacts: true,
  },
  editorial_prompt: 'Tutorial introductorio sobre Python para principiantes, cubriendo variables, funciones y estructuras básicas.',
  title_overlays: [
    {
      video_path: 'https://your-r2-bucket.r2.cloudflarestorage.com/overlay-1.mp4',
      start_seconds: 5,
      duration_seconds: 3,
      title: 'Introducción',
    },
  ],
};

const response = await fetch(`${API_URL}/analysis/jobs/sync`, {
  method: 'POST',
  headers,
  body: JSON.stringify(requestBody),
});

const data = await response.json();
```

**Response:**
```typescript
interface AnalysisResponse {
  job_uuid: string;
  status: string;
  summary: {
    original_duration_seconds: number;
    estimated_final_duration_seconds: number;
    time_saved_seconds: number;
    learning_objectives_met: boolean;
  };
  coverage: {
    sections: Array<{
      title?: string;
      expected_minutes?: number;
      actual_minutes?: number;
      status?: string;
    }>;
    missing_topics: string[];
    overextended_topics: string[];
  };
  edit_plan: Array<{
    start?: string;
    end?: string;
    action?: string;
    reason?: string;
    observation?: string;
    confidence?: number;
  }>;
  artifacts?: {
    cleaned_audio_path?: string;
    clean_video_path?: string;
    final_video_path?: string;
    remotion_manifest_path?: string;
    report_md_path?: string;
    edit_plan_json_path?: string;
    storage_url?: string;
  };
  diagnostics: Record<string, any>;
}
```

### 3. Get Artifact
`GET /artifacts/{job_uuid}/{artifact_key}`

Download specific artifacts from a job.

**Artifact Keys:**
- `clean-video` - Clean master video
- `remotion-manifest` - Remotion manifest JSON
- `cleaned-audio` - Cleaned audio file
- `edit-plan` - Edit plan JSON
- `report` - Report markdown

```typescript
const jobUuid = 'unique-job-id-123';
const artifactKey = 'clean-video';

const response = await fetch(
  `${API_URL}/artifacts/${jobUuid}/${artifactKey}`,
  { headers }
);

// Returns file (video/audio/json/md)
```

### 4. Download Final Video
`GET /download/{job_uuid}`

Download the final processed video with title overlays applied.

```typescript
const jobUuid = 'unique-job-id-123';

const response = await fetch(
  `${API_URL}/download/${jobUuid}`,
  { headers }
);

// Returns video file (video/mp4)
```

## Next.js Integration Example

### Upload to R2 First, Then Process

```typescript
// 1. Upload files to R2
async function uploadToR2(file: File, key: string): Promise<string> {
  const formData = new FormData();
  formData.append('file', file);

  const response = await fetch(`${R2_UPLOAD_URL}/${key}`, {
    method: 'PUT',
    body: file,
  });

  return `${R2_PUBLIC_URL}/${key}`;
}

// 2. Process video
async function processVideo(videoFile: File, pdfFile: File) {
  // Upload to R2
  const videoUrl = await uploadToR2(videoFile, `videos/${Date.now()}-${videoFile.name}`);
  const pdfUrl = await uploadToR2(pdfFile, `scripts/${Date.now()}-${pdfFile.name}`);

  // Process
  const response = await fetch(`${API_URL}/analysis/jobs/sync`, {
    method: 'POST',
    headers,
    body: JSON.stringify({
      job_uuid: `job-${Date.now()}`,
      title: 'My Tutorial',
      source: {
        video_path: videoUrl,
        script_pdf_path: pdfUrl,
      },
      editorial_prompt: 'Description of the video content',
    }),
  });

  return await response.json();
}

// 3. Download result
async function downloadFinalVideo(jobUuid: string): Promise<Blob> {
  const response = await fetch(`${API_URL}/download/${jobUuid}`, {
    headers,
  });
  return await response.blob();
}
```

### React Hook Example

```typescript
import { useState } from 'react';

export function useVideoCleanup() {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const processVideo = async (videoUrl: string, pdfUrl: string, title: string) => {
    setLoading(true);
    setError(null);

    try {
      const response = await fetch(`${API_URL}/analysis/jobs/sync`, {
        method: 'POST',
        headers,
        body: JSON.stringify({
          job_uuid: `job-${Date.now()}`,
          title,
          source: { video_path: videoUrl, script_pdf_path: pdfUrl },
          editorial_prompt: title,
        }),
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${await response.text()}`);
      }

      return await response.json();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
      throw err;
    } finally {
      setLoading(false);
    }
  };

  return { processVideo, loading, error };
}
```

## Error Handling

```typescript
try {
  const response = await fetch(`${API_URL}/analysis/jobs/sync`, {
    method: 'POST',
    headers,
    body: JSON.stringify(requestBody),
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || 'Processing failed');
  }

  const data = await response.json();
  return data;
} catch (error) {
  console.error('API Error:', error);
  throw error;
}
```

## Environment Variables

```env
# Next.js .env.local
TUTORIAL_CLEANUP_API_BASE_URL=https://video-cleanup-api-production.up.railway.app
TUTORIAL_CLEANUP_API_TOKEN=your-token-here
```

## Notes

- All endpoints except `/health` require Bearer token authentication
- File paths can be URLs (http/https) or local paths
- For production, upload files to R2/S3 first, then pass URLs
- Max duration is 240 minutes (4 hours)
- Processing is synchronous, may take time for large videos
