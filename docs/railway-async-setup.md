# Railway Async Setup Guide

Guía para desplegar esta API en Railway con cola de jobs async (arq + Redis), de modo
que los endpoints que procesan muchos videos no fallen por HTTP timeout.

Útil cuando migras el proyecto a otra cuenta de Railway, o cuando el entorno hay que
recrearlo desde cero.

---

## Por qué se necesita

Los endpoints `/analysis/jobs/sync`, `/video-export` y `/video-export-merge` ejecutan
Whisper + VAD + ffmpeg de forma síncrona. Para cargas grandes (p. ej. 16 videos de
20 min = ~5 h de procesamiento) Railway corta la petición HTTP antes de que termine
(timeout ~5–10 min). El resultado: 502 o connection reset.

La solución es **encolar el trabajo en Redis y procesarlo en un worker separado**.
El cliente recibe `job_uuid` al instante y hace polling a `/jobs/{id}` hasta que el
resultado esté listo.

---

## Arquitectura final (3 servicios en el mismo proyecto)

```
┌──────────────────────────────────────────────────────────────┐
│  Railway Project                                             │
│                                                              │
│  ┌────────────────┐                                          │
│  │  API           │                                          │
│  │  uvicorn       │─────┐                                    │
│  │  (HTTP público)│     │ enqueue_job()                      │
│  │                │     ▼                                    │
│  └────────────────┘   ┌──────────┐                           │
│         ▲             │  Redis   │                           │
│         │             │          │                           │
│         │ polling     └──────────┘                           │
│  GET /jobs/{id}             ▲                                │
│                             │ consume job                    │
│                             │                                │
│                       ┌─────┴──────────┐                     │
│                       │  Worker        │                     │
│                       │  arq           │                     │
│                       │  (sin HTTP)    │                     │
│                       │  ffmpeg +      │                     │
│                       │  Whisper + VAD │                     │
│                       └────────────────┘                     │
└──────────────────────────────────────────────────────────────┘
```

| Servicio | Qué hace | Tiene URL pública |
|----------|----------|-------------------|
| **API** | Recibe POST de clientes, encola job en Redis, devuelve `job_uuid`. Responde también `/jobs/{id}` para consultar estado. | Sí |
| **Worker** | Consume jobs de Redis, ejecuta el procesamiento pesado (ffmpeg, Whisper, VAD), sube resultado a R2. | No |
| **Redis** | Cola de jobs + almacenamiento de resultados (TTL 3 días). | No |

Los 3 servicios viven en el **mismo proyecto de Railway** y se comunican por red
interna (`*.railway.internal`).

---

## Paso a paso en Railway

### Paso 1 — Crear el servicio Redis

1. Entras al proyecto en Railway.
2. Arriba a la derecha: **"+ New"** → **"Database"** → **"Add Redis"**.
3. Railway crea el servicio `Redis` automáticamente con la variable interna
   `REDIS_URL` expuesta al proyecto.

Verifica que aparezca en la lista de servicios. No hace falta más configuración.

### Paso 2 — Configurar el servicio API (el que ya tienes)

Este es tu servicio existente con URL pública
(`https://xxx-production.up.railway.app`). Solo hay que ajustarlo:

1. Entra al servicio **API** → pestaña **Variables**.
2. Agrega:
   ```
   REDIS_URL=${{Redis.REDIS_URL}}
   ```
   La sintaxis `${{Redis.REDIS_URL}}` hace que Railway copie el valor real en runtime.
3. Verifica en **Settings** → **Deploy** → **Start Command** que esté **VACÍO**.
   Vacío significa "usa el CMD del Dockerfile" → arranca `uvicorn` → sirve HTTP.
4. Redeploy.

> ⚠️ **Error común:** poner `arq app.worker.WorkerSettings` como Start Command en
> este servicio. Si haces eso, arq arranca en lugar de uvicorn y la URL pública
> empieza a devolver **502 Application failed to respond**, porque arq no sirve HTTP.

### Paso 3 — Crear el servicio Worker (nuevo)

El worker es **otra instancia** del mismo código — misma Docker image, mismas env
vars — pero con un Start Command distinto que arranca arq en vez de uvicorn.

1. En el mismo proyecto: **"+ New"** → **"GitHub Repo"** → selecciona **el mismo
   repositorio** que usa tu API.
2. Railway creará un segundo servicio. Renómbralo `worker` (Settings → Name).
3. **Settings** → **Deploy** → **Start Command**:
   ```
   arq app.worker.WorkerSettings
   ```
4. **Settings** → **Networking** → **Remove Domain** (si Railway generó uno).
   El worker no necesita exponerse a Internet.
5. **Variables**: copia **todas** las env vars del API
   (`TUTORIAL_CLEANUP_*`, `R2_*`, etc.) y agrega:
   ```
   REDIS_URL=${{Redis.REDIS_URL}}
   ```
6. Deploy.

### Verificación

En los **Deploy Logs** del servicio `worker` deberías ver algo como:

```
redis_version=7.2.5 mem_usage=1.76M clients_connected=3 db_keys=3
Starting worker for 3 functions: run_analysis, run_export, run_merge_export
```

Eso significa:
- ✅ El worker conectó a Redis correctamente.
- ✅ Detectó las 3 tareas registradas en `app/worker.py`.
- ✅ Está esperando jobs.

En los logs del servicio `API` debes seguir viendo logs normales de uvicorn y
peticiones HTTP. Si ves `Starting worker for ...` en el API, tienes el Start
Command mal configurado — revisa el paso 2.

---

## Cómo usar los endpoints async

Flujo desde el cliente (Laravel, Postman, curl):

```
POST /video-export/async
Body: { "job_uuid": "abc-123", "video_paths": [...] }

Response 202:
{
  "job_uuid": "abc-123",
  "status": "queued"
}
```

Luego, polling cada 10–30 s:

```
GET /jobs/abc-123

Response:
{ "status": "in_progress", "enqueued_at": "...", "started_at": "..." }

...eventualmente:
{
  "status": "complete",
  "result": {
    "output_path": "/data/artifacts/...",
    "storage_url": "https://pub-xxx.r2.dev/...",
    "duration_seconds": 1234.5,
    ...
  }
}
```

Los **3 endpoints async** disponibles:

| Endpoint síncrono (existente) | Endpoint async (nuevo) |
|-------------------------------|------------------------|
| `POST /analysis/jobs/sync` | `POST /analysis/jobs/async` |
| `POST /video-export` | `POST /video-export/async` |
| `POST /video-export-merge` | `POST /video-export-merge/async` |

El cuerpo (body) es idéntico en ambas versiones. Los sync siguen disponibles para
jobs chicos donde no haga falta encolar.

Para consultar estado se usa siempre `GET /jobs/{job_uuid}`.

Estados posibles que devuelve `/jobs/{id}`:

- `queued` — esperando en la cola.
- `in_progress` — el worker lo está procesando.
- `complete` — terminó, `result` contiene la respuesta.
- `not_found` — el `job_uuid` no existe o expiró (TTL 3 días).

---

## Limitación importante: R2 es obligatorio para async

Como API y worker son **contenedores distintos** en Railway, **no comparten disco**.
Esto significa:

- El worker procesa el video y lo guarda en `/data/artifacts/...` **en su propio
  contenedor**.
- El endpoint `GET /artifacts/{job_uuid}/{artifact_key}` del API servía archivos
  desde el disco local — con async **no va a encontrarlos** porque el API no los
  tiene.

**Solución:** R2 (Cloudflare) ya está configurado en `.env`. El worker sube el
video final a R2 y el `result` del job incluye `storage_url` (URL pública de R2).
El cliente debe **descargar desde R2 directamente**, no del endpoint `/artifacts`.

Si por alguna razón no quisieras R2, tendrías que fusionar API + worker en un solo
contenedor usando `supervisord`. Esto mata el beneficio de aislar CPU, pero evita
la dependencia de almacenamiento externo.

---

## Variables de entorno necesarias

Ambos servicios (API y worker) necesitan **las mismas env vars** del proyecto
(`.env.example` como referencia), más:

| Variable | De dónde sale | Requerida en |
|----------|---------------|--------------|
| `REDIS_URL` | `${{Redis.REDIS_URL}}` (auto) | API + Worker |

Si por lo que sea `REDIS_URL` no está disponible, el código cae a estas variables
sueltas (ya vienen en `.env.example`):

- `REDIS_HOST` (default: `localhost`)
- `REDIS_PORT` (default: `6379`)
- `REDIS_PASSWORD` (default: vacío)
- `REDIS_USERNAME` (default: `default`)

---

## Configuración interna (`app/worker.py`)

Valores por defecto en `WorkerSettings`:

| Parámetro | Valor | Significado |
|-----------|-------|-------------|
| `job_timeout` | 10 h | Máximo que puede durar un job antes de cancelarse |
| `keep_result` | 3 días | Cuánto vive el resultado en Redis para ser consultable |
| `max_jobs` | 1 | Número de jobs en paralelo por worker (1 = serializado, evita reventar CPU) |
| `max_tries` | 1 | Sin reintentos automáticos (evita procesar 2× por error idempotencia) |

Para procesar más jobs en paralelo: no subas `max_jobs`, es mejor **clonar el
servicio worker** (2 workers = 2 jobs en paralelo). Así no compiten por CPU dentro
del mismo contenedor.

---

## Costo aproximado en Railway

Comparado con 1 servicio sync, esta arquitectura suma 2 servicios más:

- **Redis** — ~$5/mes (plan hobby Railway)
- **Worker** — depende de RAM/CPU. Para videos con Whisper `tiny` en CPU, algo
  como 2 GB RAM / 1 vCPU (~$10–15/mes). Si subes el modelo a `small` o `medium`
  escalas RAM.
- **API** — puede quedar en un plan chico (~$5/mes) porque solo encola.

Total aprox. **$20–25/mes** para un proyecto personal con un worker.

---

## Debug rápido — errores comunes

### 502 en la URL pública del API
- Causa: Start Command del API mal configurado (está apuntando a arq en vez de
  uvicorn).
- Fix: borra el Start Command del API, redeploy.

### Logs del API muestran `Starting worker for ...`
- Mismo problema que 502: el API está arrancando arq. Borra el Start Command.

### `ModuleNotFoundError: No module named 'arq'`
- Railway cacheó el build viejo sin arq. Redeploy forzado o cambia algo en el
  Dockerfile para bustear la caché.

### El job queda en `queued` para siempre
- El worker no está corriendo, o no conecta a Redis.
- Revisa los logs del servicio worker: debe ver `Starting worker for 3 functions`.
- Verifica que `REDIS_URL` esté seteada en el worker.

### El job completa pero no se puede descargar el video
- R2 no está configurado o las credenciales fallaron. Revisa las `R2_*` en env
  vars del worker.
- En el campo `diagnostics.r2_upload_error` del resultado encuentras el motivo.

### Job tarda horas y nunca termina
- Probablemente es normal para cargas grandes. `job_timeout` está en 10 h.
- Si sospechas cuelgue real, entra a los logs del worker: debe mostrar progreso
  de Whisper / ffmpeg.

---

## Archivos del código relacionados

- `app/worker.py` — definición de tasks + `WorkerSettings` (configuración arq).
- `app/jobs.py` — helpers `enqueue_job()` y `get_job_state()`.
- `app/main.py` — endpoints `/async` y `GET /jobs/{id}`.
- `requirements.txt` — `arq==0.26.3`.
- `docker-compose.yml` — setup local con Redis + API + worker para probar antes
  de desplegar.

## Prueba local con docker-compose

```bash
docker-compose up --build
```

Levanta 3 contenedores: `redis`, `tutorial-cleanup-api` (puerto 8001),
`tutorial-cleanup-worker`. Replica la arquitectura de Railway en tu máquina.
