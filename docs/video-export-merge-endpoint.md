# POST /video-export-merge

Endpoint que recibe un array de videos, los une en orden y exporta un MP4 HD. Sin limpieza de audio, sin corte de silencios — solo merge y encode final.

---

## ¿Qué hace exactamente?

1. **Merge** — concatena los videos en el orden que los envíes (normaliza a 1920×1080, 30 fps)
2. **Export HD** — renderiza con los mismos specs HD que `/video-export` (794 kbps video + 298 kbps audio)
3. **Upload a R2** — sube automáticamente a Cloudflare R2 si está configurado y devuelve `storage_url`

> Ideal para unir partes de una grabación sin ningún procesamiento adicional. Un solo comando FFmpeg hace el merge y el encode en un único paso.

---

## Especificaciones técnicas del export

| Parámetro     | Valor           |
|---------------|-----------------|
| Resolución    | 1920 × 1080     |
| Frame rate    | 30 fps          |
| Video bitrate | 794 kbps        |
| Audio bitrate | 298 kbps        |
| Total bitrate | 1092 kbps       |
| Audio canales | 2 (estéreo)     |
| Sample rate   | 48 000 Hz       |
| Codec video   | H.264 (libx264) |
| Codec audio   | AAC             |
| Contenedor    | MP4 (faststart) |

---

## Request

**`POST /video-export-merge`**

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
  ]
}
```

### Campos

| Campo         | Tipo       | Requerido | Descripción                                               |
|---------------|------------|-----------|-----------------------------------------------------------|
| `job_uuid`    | `string`   | ✅        | UUID único del job. Úsalo para identificar la descarga.   |
| `video_paths` | `string[]` | ✅        | Rutas locales o URLs de los videos en orden. Mínimo 1.    |

### Ejemplo desde Next.js

```typescript
const response = await fetch(`${API_URL}/video-export-merge`, {
  method: 'POST',
  headers: {
    'Authorization': `Bearer ${process.env.API_TOKEN}`,
    'Content-Type': 'application/json',
  },
  body: JSON.stringify({
    job_uuid: crypto.randomUUID(),
    video_paths: [
      '/data/videos/parte-01.mp4',
      '/data/videos/parte-02.mp4',
      '/data/videos/parte-03.mp4',
    ],
  }),
})

const data = await response.json()
```

### Tipos TypeScript

```typescript
interface MergeExportRequest {
  job_uuid: string
  video_paths: string[]
}

interface MergeExportResponse {
  job_uuid: string
  status: 'completed'
  output_path: string
  storage_url: string | null
  duration_seconds: number
  diagnostics: {
    source_count: number
    merged: boolean
    output_path: string
    r2_upload_error?: string
  }
}
```

---

## Response

### 200 — Éxito

```json
{
  "job_uuid": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "completed",
  "output_path": "/tmp/vidula/tutorial-cleanup-api/a1b2c3d4-.../merge-export/merge-export.mp4",
  "storage_url": "https://cdn.tudominio.com/video-exports/a1b2c3d4-.../merge-export.mp4",
  "duration_seconds": 3120.0,
  "diagnostics": {
    "source_count": 3,
    "merged": true,
    "output_path": "/tmp/vidula/.../merge-export/merge-export.mp4"
  }
}
```

### Campos del response

| Campo              | Tipo           | Descripción                                              |
|--------------------|----------------|----------------------------------------------------------|
| `job_uuid`         | `string`       | El mismo UUID que enviaste.                              |
| `status`           | `string`       | Siempre `"completed"` si no hay error.                   |
| `output_path`      | `string`       | Ruta local del archivo en el servidor.                   |
| `storage_url`      | `string\|null` | URL pública en R2. `null` si R2 no está configurado.     |
| `duration_seconds` | `number`       | Duración total del video exportado en segundos.          |
| `diagnostics`      | `object`       | Metadata: cantidad de fuentes, si se hizo merge, etc.    |

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

## Diferencias con `/video-export`

| Característica          | `/video-export` | `/video-export-merge` |
|-------------------------|-----------------|-----------------------|
| Merge de videos         | ✅              | ✅                    |
| Corte de silencios      | ✅              | ❌                    |
| Limpieza de audio       | ❌              | ❌                    |
| `silence_threshold_seconds` en request | ✅ | ❌             |
| `silence_cuts` en response | ✅           | ❌                    |
| Caso de uso             | Tutorial editado | Merge limpio sin edición |

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
  console.error('Merge export failed:', error.detail)
}
```
