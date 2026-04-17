'use client';

import React from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { Upload, FileVideo, FileText, Loader2, Download, CheckCircle } from 'lucide-react';
import { VideoCleanupFormSchema } from '../types';
import type { z } from 'zod';

type VideoCleanupFormValues = z.infer<typeof VideoCleanupFormSchema>;
import { useVideoCleanup } from '../hooks/useVideoCleanup';
import { cn } from '@/lib/utils';

export function VideoCleanupForm() {
  const { processVideo, data } = useVideoCleanup();
  const [originalFileName, setOriginalFileName] = React.useState<string>('');
  const [videoFilesList, setVideoFilesList] = React.useState<File[]>([]);

  const {
    register,
    handleSubmit,
    formState: { errors },
    setValue,
    watch,
  } = useForm({
    resolver: zodResolver(VideoCleanupFormSchema),
    defaultValues: {
      language: 'es',
      target_duration_minutes: 10,
      max_duration_minutes: 15,
      video_files: [],
    },
  });

  // Destructure onChange out of register for file inputs — if left in the spread,
  // RHF reads input.value (fake path string) from the DOM on submit instead of
  // the File object stored via setValue, causing arrayBuffer is not a function.
  const { onChange: _vfOnChange, ...videoFilesRegister } = register('video_files');
  const { onChange: _sfOnChange, ...scriptFileRegister } = register('script_file');

  const videoFiles = videoFilesList;
  const scriptFile = watch('script_file');

  const onSubmit = async (data: any, e?: React.BaseSyntheticEvent) => {
    e?.preventDefault();
    const formData = new FormData();
    formData.append('title', data.title);
    formData.append('language', data.language || 'es');
    formData.append('target_duration_minutes', String(data.target_duration_minutes || 10));
    formData.append('max_duration_minutes', String(data.max_duration_minutes || 15));
    if (data.editorial_prompt) {
      formData.append('editorial_prompt', data.editorial_prompt);
    }
    data.video_files.forEach((file: File) => {
      formData.append('video_files', file);
    });
    if (data.script_file) {
      formData.append('script_file', data.script_file);
    }

    await processVideo.mutateAsync({ formData });
  };

  const handleFileChange = (field: 'video_files' | 'script_file') => (
    e: React.ChangeEvent<HTMLInputElement>
  ) => {
    const files = e.target.files;
    if (files && files.length > 0) {
      if (field === 'video_files') {
        const newFiles = Array.from(files);
        setVideoFilesList(newFiles);
        setValue(field, newFiles);
        if (newFiles[0]) {
          setOriginalFileName(newFiles[0].name);
        }
      } else {
        setValue(field, files[0]);
      }
    }
  };

  return (
    <form onSubmit={handleSubmit(onSubmit)} method="post" className="space-y-6 max-w-2xl mx-auto p-6">
      <div className="space-y-4">
        <h1 className="text-3xl font-bold" style={{ color: 'var(--text-primary)' }}>
          Video Cleanup
        </h1>
        <p style={{ color: 'var(--text-secondary)' }}>
          Upload your tutorial video and script to automatically remove fillers, silence, and optimize content.
        </p>
      </div>

      <div className="space-y-4">
        <div>
          <label
            htmlFor="title"
            className="block text-sm font-medium mb-2"
            style={{ color: 'var(--text-primary)' }}
          >
            Title
          </label>
          <input
            id="title"
            type="text"
            {...register('title')}
            className="w-full px-4 py-2 rounded-lg border focus:outline-none focus:ring-2"
            style={{
              background: 'var(--input-bg)',
              borderColor: 'var(--input-border)',
              color: 'var(--input-text)',
            }}
            placeholder="e.g., Python Tutorial for Beginners"
          />
          {errors.title && (
            <p className="mt-1 text-sm" style={{ color: 'var(--accent-error)' }}>
              {errors.title.message}
            </p>
          )}
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div>
            <label
              htmlFor="language"
              className="block text-sm font-medium mb-2"
              style={{ color: 'var(--text-primary)' }}
            >
              Language
            </label>
            <select
              id="language"
              {...register('language')}
              className="w-full px-4 py-2 rounded-lg border focus:outline-none focus:ring-2"
              style={{
                background: 'var(--input-bg)',
                borderColor: 'var(--input-border)',
                color: 'var(--input-text)',
              }}
            >
              <option value="es">Spanish</option>
              <option value="en">English</option>
            </select>
          </div>
          <div>
            <label
              htmlFor="target_duration"
              className="block text-sm font-medium mb-2"
              style={{ color: 'var(--text-primary)' }}
            >
              Target Duration (min)
            </label>
            <input
              id="target_duration"
              type="number"
              {...register('target_duration_minutes', { valueAsNumber: true })}
              className="w-full px-4 py-2 rounded-lg border focus:outline-none focus:ring-2"
              style={{
                background: 'var(--input-bg)',
                borderColor: 'var(--input-border)',
                color: 'var(--input-text)',
              }}
              min={1}
              max={240}
            />
          </div>
        </div>

        <div>
          <label
            htmlFor="editorial_prompt"
            className="block text-sm font-medium mb-2"
            style={{ color: 'var(--text-primary)' }}
          >
            Editorial Prompt
          </label>
          <textarea
            id="editorial_prompt"
            {...register('editorial_prompt')}
            rows={4}
            className="w-full px-4 py-2 rounded-lg border focus:outline-none focus:ring-2 resize-none"
            style={{
              background: 'var(--input-bg)',
              borderColor: 'var(--input-border)',
              color: 'var(--input-text)',
            }}
            placeholder="Describe the video content and learning objectives..."
          />
          {errors.editorial_prompt && (
            <p className="mt-1 text-sm" style={{ color: 'var(--accent-error)' }}>
              {errors.editorial_prompt.message}
            </p>
          )}
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div>
            <label
              htmlFor="video_files"
              className="block text-sm font-medium mb-2"
              style={{ color: 'var(--text-primary)' }}
            >
              Video Files (multiple)
            </label>
            <div className="relative">
              <input
                id="video_files"
                type="file"
                accept="video/*"
                multiple
                {...videoFilesRegister}
                onChange={handleFileChange('video_files')}
                className="hidden"
              />
              <label
                htmlFor="video_files"
                className={cn(
                  'flex flex-col items-center justify-center gap-2 px-4 py-8 rounded-lg border-2 border-dashed cursor-pointer transition-all',
                  videoFiles && videoFiles.length > 0 ? 'border-solid' : ''
                )}
                style={{
                  borderColor: videoFiles && videoFiles.length > 0 ? 'var(--accent-success)' : 'var(--input-border)',
                  background: 'var(--input-bg)',
                }}
              >
                {videoFiles && videoFiles.length > 0 ? (
                  <>
                    <FileVideo size={24} style={{ color: 'var(--accent-success)' }} />
                    <span className="text-sm" style={{ color: 'var(--text-primary)' }}>
                      {videoFiles.length} video(s) selected
                    </span>
                    <ul className="text-xs mt-2 space-y-1" style={{ color: 'var(--text-secondary)' }}>
                      {videoFiles.map((file, index) => (
                        <li key={index}>{file.name}</li>
                      ))}
                    </ul>
                  </>
                ) : (
                  <>
                    <Upload size={24} style={{ color: 'var(--text-muted)' }} />
                    <span className="text-sm" style={{ color: 'var(--text-muted)' }}>
                      Upload videos (multiple)
                    </span>
                  </>
                )}
              </label>
            </div>
            {errors.video_files && (
              <p className="mt-1 text-sm" style={{ color: 'var(--accent-error)' }}>
                {errors.video_files.message}
              </p>
            )}
          </div>

          <div>
            <label
              htmlFor="script_file"
              className="block text-sm font-medium mb-2"
              style={{ color: 'var(--text-primary)' }}
            >
              Script PDF
            </label>
            <div className="relative">
              <input
                id="script_file"
                type="file"
                accept=".pdf"
                {...scriptFileRegister}
                onChange={handleFileChange('script_file')}
                className="hidden"
              />
              <label
                htmlFor="script_file"
                className={cn(
                  'flex items-center justify-center gap-2 px-4 py-8 rounded-lg border-2 border-dashed cursor-pointer transition-all',
                  scriptFile ? 'border-solid' : ''
                )}
                style={{
                  borderColor: scriptFile ? 'var(--accent-success)' : 'var(--input-border)',
                  background: 'var(--input-bg)',
                }}
              >
                {scriptFile ? (
                  <>
                    <FileText size={24} style={{ color: 'var(--accent-success)' }} />
                    <span className="text-sm" style={{ color: 'var(--text-primary)' }}>
                      {scriptFile.name}
                    </span>
                  </>
                ) : (
                  <>
                    <Upload size={24} style={{ color: 'var(--text-muted)' }} />
                    <span className="text-sm" style={{ color: 'var(--text-muted)' }}>
                      Upload script
                    </span>
                  </>
                )}
              </label>
            </div>
            {errors.script_file && (
              <p className="mt-1 text-sm" style={{ color: 'var(--accent-error)' }}>
                {String(errors.script_file.message)}
              </p>
            )}
          </div>
        </div>
      </div>

      <button
        type="submit"
        disabled={processVideo.isPending}
        className="w-full px-6 py-3 rounded-lg font-medium flex items-center justify-center gap-2 transition-all"
        style={{
          background: 'var(--grad-primary)',
          color: 'var(--text-primary)',
          opacity: processVideo.isPending ? 0.6 : 1,
          cursor: processVideo.isPending ? 'not-allowed' : 'pointer',
        }}
      >
        {processVideo.isPending ? (
          <>
            <Loader2 size={20} className="animate-spin" />
            Processing...
          </>
        ) : (
          'Process Video'
        )}
      </button>

      {data?.success && data.data?.artifacts?.storage_url && (
        <div
          className="p-6 rounded-lg border"
          style={{
            background: 'var(--success-bg)',
            borderColor: 'var(--accent-success)',
          }}
        >
          <div className="flex items-center gap-3 mb-4">
            <CheckCircle size={24} style={{ color: 'var(--accent-success)' }} />
            <h3 className="text-lg font-semibold" style={{ color: 'var(--text-primary)' }}>
              Video Processed Successfully
            </h3>
          </div>
          <div className="space-y-3 mb-4">
            <div style={{ color: 'var(--text-secondary)' }}>
              <span className="font-medium">Original Duration:</span> {data.data.summary?.original_duration_seconds}s
            </div>
            <div style={{ color: 'var(--text-secondary)' }}>
              <span className="font-medium">Final Duration:</span> {data.data.summary?.estimated_final_duration_seconds}s
            </div>
            <div style={{ color: 'var(--text-secondary)' }}>
              <span className="font-medium">Time Saved:</span> {data.data.summary?.time_saved_seconds}s
            </div>
            {data.data.edit_plan && data.data.edit_plan.length > 0 && (
              <div style={{ color: 'var(--text-secondary)' }}>
                <span className="font-medium">Cuts Made:</span> {data.data.edit_plan.length}
              </div>
            )}
          </div>
          <a
            href={data.data.artifacts.storage_url}
            download={originalFileName.replace(/\.[^/.]+$/, '') + '-cleaned.mp4'}
            className="inline-flex items-center gap-2 px-6 py-3 rounded-lg font-medium transition-all"
            style={{
              background: 'var(--accent-success)',
              color: 'var(--text-primary)',
            }}
          >
            <Download size={20} />
            Download Cleaned Video
          </a>
        </div>
      )}
    </form>
  );
}
