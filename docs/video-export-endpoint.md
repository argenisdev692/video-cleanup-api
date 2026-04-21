# POST /video-export

Endpoint que recibe un array de videos, los une, elimina silencios y exporta un MP4 HD listo para descarga.

---

## ¿Qué hace exactamente?

1. **Merge** — si envías múltiples videos, los concatena en orden (1920×1080, 30 fps)
2. **Silence removal** — usa VAD (Silero) para detectar y cortar zonas de silencio
3. **Export HD** — renderiza con los specs exactos (video 794 kbps + audio 298 kbps = 1092 kbps total)
4. **Upload a R2** — sube automáticamente a Cloudflare R2 si está configurado y devuelve `storage_url`

> 12 videos de 50 minutos en total no representan ningún problema. El merge se hace en un solo paso FFmpeg y el corte de silencios también. El único factor limitante es el tiempo de procesamiento del servidor (típicamente 3–8 min para 50 min de video).

---

## Especificaciones técnicas del export

| Parámetro        | Valor         |
|------------------|---------------|
| Resolución       | 1920 × 1080   |
| Frame rate       | 30 fps        |
| Video bitrate    | 794 kbps      |
| Audio bitrate    | 298 kbps      |
| Total bitrate    | 1092 kbps     |
| Audio canales    | 2 (estéreo)   |
| Sample rate      | 48 000 Hz     |
| Codec video      | H.264 (libx264) |
| Codec audio      | AAC           |
| Contenedor       | MP4 (faststart) |

---

## Request

**`POST /video-export`**

### Headers

```
Authorization: Bearer <tu_api_token>
Content-Type: application/json
```

### Body (JSON)

```json
{
  "job_uuid": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "video_paths": [
    "/data/videos/parte-01.mp4",
    "/data/videos/parte-02.mp4",
    "/data/videos/parte-03.mp4"
  ],
  "silence_threshold_seconds": 2.0
}
```

### Campos

| Campo                       | Tipo             | Requerido | Default | Descripción                                                   |
|-----------------------------|------------------|-----------|---------|---------------------------------------------------------------|
| `job_uuid`                  | `string`         | ✅        | —       | UUID único del job. Úsalo para identificar la descarga.       |
| `video_paths`               | `string[]`       | ✅        | —       | Rutas locales o URLs de los videos. Mínimo 1, sin límite.     |
| `silence_threshold_seconds` | `number (float)` | ❌        | `2.0`   | Duración mínima de silencio para cortar. Rango: `0.5` – `10` |

### Ejemplo desde Next.js

```typescript
const response = await fetch(`${API_URL}/video-export`, {
  method: 'POST',
  headers: {
    'Authorization': `Bearer ${process.env.API_TOKEN}`,
    'Content-Type': 'application/json',
  },
  body: JSON.stringify({
    job_uuid: crypto.randomUUID(),
    video_paths: [
      '/data/videos/clase-01.mp4',
      '/data/videos/clase-02.mp4',
    ],
    silence_threshold_seconds: 2.0,
  }),
})

const data = await response.json()
```

---

## Response

### 200 — Éxito

```json
{
  "job_uuid": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "completed",
  "output_path": "/tmp/vidula/tutorial-cleanup-api/a1b2c3d4-.../export/export.mp4",
  "storage_url": "https://cdn.tudominio.com/video-exports/a1b2c3d4-.../export.mp4",
  "duration_seconds": 2874.5,
  "silence_cuts": 47,
  "diagnostics": {
    "source_count": 12,
    "merged": true,
    "original_duration_seconds": 3120.0,
    "silence_cuts": 47,
    "keep_segments": 48,
    "output_path": "/tmp/vidula/.../export/export.mp4",
    "speech_region_count": 312,
    "speech_total_seconds": 2874.5,
    "vad_threshold": 0.5,
    "vad_min_speech_duration_ms": 200,
    "vad_min_silence_duration_ms": 600,
    "vad_use_onnx": false
  }
}
```

### Campos del response

| Campo               | Tipo            | Descripción                                                      |
|---------------------|-----------------|------------------------------------------------------------------|
| `job_uuid`          | `string`        | El mismo UUID que enviaste.                                      |
| `status`            | `string`        | Siempre `"completed"` si no hay error.                           |
| `output_path`       | `string`        | Ruta local del archivo en el servidor.                           |
| `storage_url`       | `string\|null`  | URL pública en R2. `null` si R2 no está configurado.             |
| `duration_seconds`  | `float`         | Duración final del video exportado (sin silencios).              |
| `silence_cuts`      | `int`           | Cantidad de segmentos de silencio que fueron eliminados.         |
| `diagnostics`       | `object`        | Metadata de debug: VAD, fuentes, segmentos, etc.                 |

---

## Descarga posterior

Si R2 está configurado, usa directamente `storage_url`:

```typescript
// Con storage_url (R2)
window.open(data.storage_url, '_blank')

// Sin R2 — descarga vía endpoint de la API
const downloadUrl = `${API_URL}/download/${data.job_uuid}`
window.open(downloadUrl, '_blank')
```

El endpoint `/download/{job_uuid}` sirve el archivo desde el servidor directamente.

---

## Errores

| Status | Causa                                                        |
|--------|--------------------------------------------------------------|
| `401`  | Token inválido o ausente.                                    |
| `422`  | Algún `video_path` no existe o ffmpeg falló.                 |
| `500`  | Error interno inesperado (ver `detail` en el response body). |

```typescript
if (!response.ok) {
  const error = await response.json()
  console.error('Export failed:', error.detail)
}
```
