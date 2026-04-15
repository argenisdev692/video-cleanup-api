1. Recibir múltiples videos → Concatenar con ffmpeg concat
2. Extraer audio → Limpiar con filtros existentes (highpass, lowpass, afftdn, loudnorm)
3. Transcribir con faster-whisper (word_timestamps=True)
4. Detectar cortes:
   - PAUSA (palabra clave + segmento previo)
   - Muletillas (regex sobre texto)
   - Repeticiones (palabras consecutivas)
   - Silencios/respiración (ffmpeg silencedetect)
5. Unir todos los rangos de corte
6. Aplicar cortes con filter_complex (ya existe en editor.py)
7. Renderizar video final limpio

# Actual (paths como strings)
source: SourcePayload  # video_path: str, script_pdf_path: str

# Con upload directo
video_file: UploadFile
script_pdf_file: UploadFile

# Configurar variables R2 en .env
TUTORIAL_CLEANUP_R2_ACCOUNT_ID=your_account_id
TUTORIAL_CLEANUP_R2_ACCESS_KEY_ID=your_key
TUTORIAL_CLEANUP_R2_SECRET_ACCESS_KEY=your_secret
TUTORIAL_CLEANUP_R2_BUCKET_NAME=vidula-tutorials
TUTORIAL_CLEANUP_R2_ENDPOINT=https://your-account.r2.cloudflarestorage.com
TUTORIAL_CLEANUP_R2_PUBLIC_BASE_URL=https://your-public-url

# Reconstruir Docker
cd services/tutorial-cleanup-api
docker-compose up -d --build

# Enviar request con títulos
curl -X POST http://localhost:8001/analysis/jobs/sync \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "job_uuid": "job-123",
    "title": "Tutorial Limpio",
    "language": "es",
    "target_duration_minutes": 60,
    "source": {
      "video_path": "/workspace/video.mp4",
      "script_pdf_path": "/workspace/script.pdf"
    },
    "rules": {
      "pause_keyword": "PAUSA",
      "store_artifacts": true
    },
    "editorial_prompt": "Eliminar muletillas y pausas",
    "title_overlays": [
      {
        "video_path": "/workspace/titles/intro.mp4",
        "start_seconds": 0,
        "duration_seconds": 6,
        "title": "Introducción"
      },
      {
        "video_path": "/workspace/titles/section1.mp4",
        "start_seconds": 30,
        "duration_seconds": 5,
        "title": "Sección 1"
      }
    ]
  }'

# Descargar video final
curl http://localhost:8001/download/job-123 \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -o final-video.mp4

----------------------------------------------------------

{
  "job_uuid": "tutorial-001",
  "title": "Curso Introducción a Python",
  "language": "es",
  "target_duration_minutes": 60,
  "max_duration_minutes": 70,
  "source": {
    "video_path": "/workspace/tutorials/python-intro.mp4",
    "script_pdf_path": "/workspace/tutorials/python-intro.pdf"
  },
  "rules": {
    "pause_keyword": "PAUSA",
    "silence_threshold_seconds": 3,
    "detect_fillers": true,
    "detect_repeated_words": true,
    "detect_self_corrections": true,
    "store_artifacts": true
  },
  "editorial_prompt": "Eliminar muletillas, pausas largas y repeticiones para mantener un ritmo dinámico",
  "title_overlays": [
    {
      "video_path": "/workspace/titles/intro-5s.mp4",
      "start_seconds": 0,
      "duration_seconds": 5,
      "title": "Introducción"
    },
    {
      "video_path": "/workspace/titles/section1-6s.mp4",
      "start_seconds": 45,
      "duration_seconds": 6,
      "title": "Variables y Tipos"
    },
    {
      "video_path": "/workspace/titles/section2-5s.mp4",
      "start_seconds": 120,
      "duration_seconds": 5,
      "title": "Funciones"
    }
  ]
}

summary.md vs proceso real: CORRECTO

  El flujo descrito es consistente con el código:

  1. Recibir video + PDF (URLs de R2)
  2. Extraer y limpiar audio
  3. Transcribir con faster-whisper
  4. Detectar cortes (PAUSA, muletillas, silencios, repeticiones)
  5. Aplicar cortes con ffmpeg filter_complex
  6. Renderizar video limpio + overlays de títulos

  Flujo Next.js (API.md): CORRECTO

  Upload video → R2
  Upload PDF  → R2
  POST /analysis/jobs/sync (URLs de R2) → respuesta con artifacts
  GET /download/{job_uuid} → video final