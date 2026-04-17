'use client';

import { useMutation } from '@tanstack/react-query';
import { toast } from 'sonner';
import { processVideoCleanupAction } from '../actions/video-cleanup.actions';
import type { ActionResult, VideoCleanupResponse, VideoCleanupFormValues } from '../types';

interface ProcessVideoInput {
  readonly formData: FormData;
}

export function useVideoCleanup() {
  const processVideo = useMutation<ActionResult<VideoCleanupResponse>, Error, ProcessVideoInput>({
    mutationFn: ({ formData }) => processVideoCleanupAction(formData),
    onSuccess: (result) => {
      if (!result.success) {
        toast.error(result.error ?? 'Processing failed');
        return;
      }
      toast.success('Video processed successfully');
    },
    onError: () => {
      toast.error('Something went wrong');
    },
  });

  return { processVideo, data: processVideo.data };
}
