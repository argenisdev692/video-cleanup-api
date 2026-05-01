---
description: Deploy two Railway services (API + worker) from this repo, sharing Redis
---

# Railway: API + Worker desde el mismo repo

## Topología final

```
+--------------------+        +-----------------------+        +-----------------+
| video-cleanup-api  |  --->  |   redis (Railway)     |  --->  | cleanup-worker  |
|  (uvicorn)         |  enq   |   REDIS_URL           |  pop   | (arq worker)    |
+--------------------+        +-----------------------+        +-----------------+
        ^                                                                |
        |  POST /jobs/video-export, /jobs/video-export/batch, etc.       |
        |  GET /jobs/{job_id}                                            |
        +----------------------------------------------------------------+
```

- **`video-cleanup-api`**: Servicio Railway existente. Recibe HTTP, encola jobs en Redis vía arq.
- **`cleanup-worker`**: Servicio Railway nuevo, **mismo repo, misma imagen Docker**, sólo cambia el start command.
- **`redis`**: Plugin de Railway que ya creaste. Comparte `REDIS_URL` con ambos servicios.

---

## 1. Pre-requisitos

- Repo conectado a Railway.
- Plugin Redis ya creado (visible en `docs/env-railway`: `REDIS_URL=redis://default:...@redis.railway.internal:6379`).
- `arq==0.26.3` ya está en `requirements.txt` (raíz).

---

## 2. Servicio API (existente)

Si ya está deployado, sólo confirma:

- **Source**: este repo, branch `main`.
- **Dockerfile**: el que está en la raíz.
- **Start Command**: dejar vacío para usar el `CMD` del Dockerfile, o explícito:
  ```
  uvicorn app.main:app --host 0.0.0.0 --port $PORT
  ```
- **Variables de entorno**: todas las de `docs/env-railway`, incluyendo:
  - `REDIS_URL` (referenciar el plugin Redis con `${{Redis.REDIS_URL}}`)
  - `TUTORIAL_CLEANUP_*`
  - `R2_*`
  - `TUTORIAL_CLEANUP_API_TOKEN`

> **Importante**: marca `REDIS_URL` como variable **referenciada** desde el plugin Redis (`${{Redis.REDIS_URL}}`) para que se resuelva al hostname interno `redis.railway.internal`.

---

## 3. Servicio Worker (nuevo)

### 3.1 Crear servicio

1. En el mismo proyecto Railway, click **New** → **GitHub Repo** → selecciona el mismo repo.
2. Railway detectará el `Dockerfile` y empezará a construir.

### 3.2 Configuración

- **Name**: `video-cleanup-worker`
- **Source**: mismo repo, mismo branch (`main`).
- **Builder**: Dockerfile (auto-detectado).
- **Custom Start Command** (Settings → Deploy → Custom Start Command):
  ```
  arq app.worker.WorkerSettings
  ```
  Esto sobrescribe el `CMD` del Dockerfile. **No hace falta otro Dockerfile**.

- **No expongas puerto público**: el worker no sirve HTTP. En Settings → Networking deja sin Public Domain.

### 3.3 Variables de entorno

Copia las mismas que la API. Lo más rápido:

1. Settings → Variables → **Raw Editor**.
2. Pega TODO el contenido de `docs/env-railway`.
3. **Reemplaza** `REDIS_URL` por la referencia al plugin: `${{Redis.REDIS_URL}}`.

Variables mínimas obligatorias para el worker:

```
REDIS_URL=${{Redis.REDIS_URL}}
TUTORIAL_CLEANUP_API_TOKEN=...
TUTORIAL_CLEANUP_ARTIFACT_ROOT=/data/artifacts
TUTORIAL_CLEANUP_ALLOW_REMOTE_DOWNLOADS=true
TUTORIAL_CLEANUP_R2_ACCOUNT_ID=...
TUTORIAL_CLEANUP_R2_ACCESS_KEY_ID=...
TUTORIAL_CLEANUP_R2_SECRET_ACCESS_KEY=...
TUTORIAL_CLEANUP_R2_BUCKET_NAME=...
TUTORIAL_CLEANUP_R2_ENDPOINT=...
TUTORIAL_CLEANUP_R2_PUBLIC_BASE_URL=...
TUTORIAL_CLEANUP_STORAGE_DISK=r2
```

### 3.4 Volúmenes (opcional)

Si quieres que el worker persista artefactos entre re-deploys:

- Settings → Volumes → **New Volume**
- Mount path: `/data/artifacts`
- Esto evita reprocesar al re-deployar; pero si los outputs se suben a R2 igual, puede prescindirse.

> ⚠️ La API y el worker NO comparten volumen. Si el cliente pide `/download/{job_uuid}` a la API y el worker corre en otro container, el archivo no estará. Por eso el worker debe subir todo a **R2** y devolver `storage_url`. El cliente descarga desde R2, no desde la API.

### 3.5 Recursos

- El worker hace el trabajo pesado (whisper + ffmpeg). Recomendado:
  - CPU: ≥ 2 vCPU
  - RAM: ≥ 4 GB (8 GB si usas `transcription_model_size=small`/`medium`)
- La API puede ir más chica (1 vCPU / 1 GB) ya que sólo encola.
- `WorkerSettings.max_jobs = 1` serializa los jobs en un mismo container. Para paralelizar, sube `max_jobs` o escala réplicas del servicio worker en Railway.

---

## 4. Cómo se comunican

Ambos servicios resuelven `redis.railway.internal` por la red privada de Railway. No hace falta abrir Redis al público.

Verifica logs del worker tras deploy:

```
arq vX.Y.Z starting...
redis_version=X.Y.Z mem_usage=...
2 functions, 0 cron jobs
```

---

## 4.1 URLs en uso

### Backend (Railway)

| Servicio | URL pública |
|---|---|
| API | `https://video-cleanup-api-production.up.railway.app` |
| Health check | `https://video-cleanup-api-production.up.railway.app/health` |
| Docs (Swagger) | `https://video-cleanup-api-production.up.railway.app/docs` |
| Worker | sin URL (proceso background) |

### Frontend (local)

| Página | URL local | Endpoint backend que consume |
|---|---|---|
| Batch de exports | `http://localhost:3000/video-export-batch` | `POST /jobs/video-export/batch` |
| Merge de jobs | `http://localhost:3000/video-export-merge-job` | `POST /jobs/video-export-merge` |

---

## 5. Probar el flujo end-to-end

### 5.1 Encolar un export

```bash
curl -X POST https://video-cleanup-api-production.up.railway.app/jobs/video-export \
  -H "Authorization: Bearer $TUTORIAL_CLEANUP_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "job_uuid": "demo-001",
    "video_paths": ["https://.../video.mp4"]
  }'
```

Respuesta:
```json
{ "job_uuid": "demo-001", "job_id": "demo-001", "queue_status": "queued" }
```

### 5.2 Encolar lote

```bash
curl -X POST https://video-cleanup-api-production.up.railway.app/jobs/video-export/batch \
  -H "Authorization: Bearer $TUTORIAL_CLEANUP_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "items": [
      { "job_uuid": "batch-001", "video_paths": ["https://.../v1.mp4"] },
      { "job_uuid": "batch-002", "video_paths": ["https://.../v2.mp4"] },
      { "job_uuid": "batch-003", "video_paths": ["https://.../v3.mp4"] }
    ]
  }'
```

Respuesta:
```json
{
  "total": 3, "queued": 3, "duplicates": 0, "errors": 0,
  "jobs": [
    { "job_uuid": "batch-001", "job_id": "batch-001", "queue_status": "queued" },
    ...
  ]
}
```

### 5.3 Consultar estado

```bash
curl https://video-cleanup-api-production.up.railway.app/jobs/batch-001 \
  -H "Authorization: Bearer $TUTORIAL_CLEANUP_API_TOKEN"
```

Estados posibles: `deferred`, `queued`, `in_progress`, `complete`, `not_found`.

Cuando `status=complete` y `success=true`, el campo `result` contiene el `ExportResponse` (incluye `output_path`, `storage_url`, `duration_seconds`, `silence_cuts`, `diagnostics`).

### 5.4 Abortar un job

```bash
curl -X DELETE https://video-cleanup-api-production.up.railway.app/jobs/batch-001 \
  -H "Authorization: Bearer $TUTORIAL_CLEANUP_API_TOKEN"
```

### 5.5 Encolar lote de merge (unir varios MP4 sin re-procesar)

```bash
curl -X POST https://video-cleanup-api-production.up.railway.app/jobs/video-export-merge/batch \
  -H "Authorization: Bearer $TUTORIAL_CLEANUP_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "items": [
      {
        "job_uuid": "merge-curso-1",
        "video_paths": [
          "https://r2/.../parte-01-clean.mp4",
          "https://r2/.../parte-02-clean.mp4"
        ]
      }
    ]
  }'
```

### 5.6 Encolar lote de análisis

```bash
curl -X POST https://video-cleanup-api-production.up.railway.app/jobs/analysis/batch \
  -H "Authorization: Bearer $TUTORIAL_CLEANUP_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "items": [
      {
        "job_uuid": "analysis-001",
        "title": "Clase 1",
        "source": { "video_path": "https://r2/.../v1.mp4" },
        "rules": {}
      }
    ]
  }'
```

---

## 5.7 Caso completo: 30 partes de un video largo

Pipeline en 2 etapas: limpiar cada parte individualmente, luego unir los limpios.

### Etapa 1 — Encolar las 30 partes a limpiar

```bash
curl -X POST https://video-cleanup-api-production.up.railway.app/jobs/video-export/batch \
  -H "Authorization: Bearer $TUTORIAL_CLEANUP_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "items": [
      { "job_uuid": "curso-A-parte-01", "video_paths": ["https://r2/.../parte-01.mp4"] },
      { "job_uuid": "curso-A-parte-02", "video_paths": ["https://r2/.../parte-02.mp4"] },
      { "job_uuid": "curso-A-parte-03", "video_paths": ["https://r2/.../parte-03.mp4"] }
      // ... hasta curso-A-parte-30
    ]
  }'
```

Respuesta inmediata (en milisegundos, no espera procesamiento):
```json
{
  "total": 30, "queued": 30, "duplicates": 0, "errors": 0,
  "jobs": [
    { "job_uuid": "curso-A-parte-01", "job_id": "curso-A-parte-01", "queue_status": "queued" },
    { "job_uuid": "curso-A-parte-02", "job_id": "curso-A-parte-02", "queue_status": "queued" },
    ...
  ]
}
```

### Etapa 2 — Pollear cada parte hasta `complete`

```bash
# Para cada uno de los 30 job_id:
curl https://video-cleanup-api-production.up.railway.app/jobs/curso-A-parte-01 \
  -H "Authorization: Bearer $TUTORIAL_CLEANUP_API_TOKEN"
```

Cuando `status=complete`:
```json
{
  "job_id": "curso-A-parte-01",
  "status": "complete",
  "success": true,
  "result": {
    "job_uuid": "curso-A-parte-01",
    "output_path": "/data/artifacts/.../export.mp4",
    "storage_url": "https://r2/.../curso-A-parte-01-clean.mp4",
    "duration_seconds": 2840.5,
    "silence_cuts": 47,
    "diagnostics": { ... }
  }
}
```

Guarda los 30 `result.storage_url`.

### Etapa 3 — Unir los 30 limpios en un final

```bash
curl -X POST https://video-cleanup-api-production.up.railway.app/jobs/video-export-merge \
  -H "Authorization: Bearer $TUTORIAL_CLEANUP_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "job_uuid": "curso-A-final",
    "video_paths": [
      "https://r2/.../curso-A-parte-01-clean.mp4",
      "https://r2/.../curso-A-parte-02-clean.mp4",
      "...",
      "https://r2/.../curso-A-parte-30-clean.mp4"
    ]
  }'
```

Pollear `GET /jobs/curso-A-final` hasta `complete`. El `result.storage_url` final es el video unido.

### Throughput esperado

| Réplicas worker (`max_jobs=1`) | Tiempo procesando 30 × 50min |
|---|---|
| 1 réplica | ~10 h (secuencial) |
| 3 réplicas | ~3.5 h |
| 5 réplicas | ~2 h |
| 10 réplicas | ~1 h |

Configura las réplicas en Railway: **servicio worker → Settings → Deploy → Replicas**.

> Asume ~20 min de procesamiento por parte de 50min en CPU con `transcription_model_size=tiny`. Con `small` o `medium` multiplica x2-x4.

---

## 6. Endpoints async disponibles

| Método | Path | Función worker |
|---|---|---|
| POST | `/jobs/video-export` | `run_export` |
| POST | `/jobs/video-export/batch` | `run_export` (1 job por item) |
| POST | `/jobs/video-export-merge` | `run_merge_export` |
| POST | `/jobs/video-export-merge/batch` | `run_merge_export` |
| POST | `/jobs/analysis` | `run_analysis` |
| POST | `/jobs/analysis/batch` | `run_analysis` |
| GET  | `/jobs/{job_id}` | — (consulta estado) |
| DELETE | `/jobs/{job_id}` | — (abortar) |

Los endpoints sync (`/video-export`, `/video-export-merge`, `/analysis/jobs/sync`) siguen funcionando para llamadas inmediatas.

---

## 7. Troubleshooting

| Síntoma | Causa probable | Solución |
|---|---|---|
| `503 Job queue unavailable` al llamar `/jobs/*` | API no pudo conectar a Redis al arrancar | Verifica `REDIS_URL` en variables de la API; redeploy |
| Jobs encolan pero nunca cambian a `in_progress` | Worker caído o sin variables | Revisa logs del servicio `cleanup-worker`; confirma `arq` arrancó |
| `queue_status: "duplicate"` | Ya hay un job con ese `job_uuid` activo | Usa otro `job_uuid` o espera/aborta el anterior |
| Worker OOM con whisper | Modelo demasiado grande para la RAM asignada | Sube RAM o cambia `TUTORIAL_CLEANUP_TRANSCRIPTION_MODEL_SIZE=tiny` |
| API encola pero el worker dice "function not found" | Versiones desincronizadas API/worker | Redeploya ambos servicios desde el mismo commit |
