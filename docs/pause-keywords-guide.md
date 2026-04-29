# Guía: Borrar frases malas con "pausa" / "pausa aca"

Esta guía explica cómo usar los keywords de pausa para que la API borre automáticamente frases o palabras malas durante la grabación, sin necesidad de editar en post-producción.

Aplica al endpoint `/video-export`.

---

## Cómo funciona el algoritmo

Implementado en `_find_pause_cuts` (`app/export_service.py`).

1. Whisper transcribe tu audio palabra por palabra con timestamps.
2. La API busca cualquier keyword de tu lista `pause_keywords`.
3. Cuando encuentra el keyword, **retrocede palabra por palabra hasta encontrar un silencio >= 0.4s** (el `pause_backtrack_silence_threshold_seconds`).
4. Borra desde el inicio de la frase mala (justo después del silencio detectado) hasta el final del keyword.
5. Cap de seguridad: nunca retrocede más de `pause_backtrack_max_seconds` (8s por defecto), para no comerse una intro larga por accidente.

### Keywords reconocidos por defecto

```
PAUSA ACA
PAUSA ACÁ
PAUSA A CA
PAUSA A CÁ
PAUSAACA
PAUSAACÁ
PASA ACA
PASA ACÁ
PAUZA ACA
PAUZA ACÁ
PAUSA
PAUZA
```

La API normaliza (casefold + sin acentos) antes de comparar, así que `"Pausa Acá"`, `"PAUSA ACA"` y `"pausa aca"` se detectan igual.

---

## Ejemplos prácticos

### Ejemplo 1: frase mala corta

Decís durante la grabación:

```
"Entonces el sistema funciona así. [silencio 0.8s] sin alterar pausa sin modificar nada."
```

Flujo del backtrack:

| Palabra | tiempo (s) | gap previo | acción |
|---|---|---|---|
| `así.` | 5.0 – 5.4 | — | se queda |
| silencio 0.8s | 5.4 – 6.2 | — | borde detectado |
| `sin` | 6.2 – 6.4 | 0.8s STOP | borrado |
| `alterar` | 6.5 – 7.0 | 0.1s | borrado |
| `pausa` | 7.1 – 7.5 | 0.1s | borrado (keyword) |
| `sin` | 7.6 – 7.8 | gap normal | se queda |
| `modificar` | 7.9 – 8.4 | | se queda |
| `nada.` | 8.5 – 8.9 | | se queda |

Resultado final:

```
"Entonces el sistema funciona así. sin modificar nada."
```

---

### Ejemplo 2: frase mala larga (retrocede varias palabras)

Decís:

```
"... como te decía. [silencio 0.6s] todo el equipo trabajó muy duro pausa aca el grupo se esforzó mucho."
```

Backtrack desde `pausa aca`:

- `duro` — gap 0.1s — seguir
- `muy` — gap 0.05s — seguir
- `trabajó` — gap 0.05s — seguir
- `equipo` — gap 0.1s — seguir
- `el` — gap 0.05s — seguir
- `todo` — gap 0.05s — seguir
- `decía.` — gap **0.6s** — STOP

Resultado final:

```
"... como te decía. el grupo se esforzó mucho."
```

---

### Ejemplo 3: una sola palabra mal

Decís:

```
"...por ende [silencio 0.5s] martes pausa miércoles continuamos."
```

Backtrack desde `pausa`:

- `martes` — gap 0.05s — seguir
- `ende` — gap **0.5s** — STOP

Resultado final:

```
"...por ende miércoles continuamos."
```

---

## `pausa` vs `pausa aca`: cuál usar

Ambos disparan la misma lógica. La diferencia es el riesgo de falso positivo:

| Keyword | Cuándo usarlo | Riesgo |
|---|---|---|
| `pausa aca` | Contenido donde "pausa" podría aparecer naturalmente | Muy bajo |
| `pausa` sola | Contenido técnico donde nunca dirías "una pausa" | Alto si hablás de pausas como concepto |
| `pausaaca` (pegado) | Máxima seguridad, casi imposible que salga natural | Casi nulo |

**Recomendación**: si tu contenido menciona "pausa" como concepto (ej: "hagamos una pausa para revisar"), sacá `"PAUSA"` de la lista o reemplazala por un término más exótico como `"PAUSAACA"` o `"CORTA"`.

---

## Cómo confirmar que funcionó

En los logs del export vas a ver estas líneas:

```
_find_pause_cuts: Scanning for keyword 'pausa aca' (2 parts)
_find_pause_cuts: Found match for 'pausa aca' at position 47
_find_pause_cuts: backtrack stopped at silence 0.620s between 'decía.' and 'todo'
_find_pause_cuts: Final cut_start=12.345 (keyword at 18.900, walked back 6.555s)
```

- `walked back 6.5s` = retrocedió mucho (frase mala larga).
- `walked back 1.5s` = retrocedió poco (frase mala corta).
- `backtrack stopped at silence X.XXXs` = el silencio donde paró el retroceso. Este valor debe ser >= `pause_backtrack_silence_threshold_seconds`.

Si aparece `backtrack hit max window 8.0s` significa que no encontró silencio y aplicó el cap de seguridad — probablemente no dejaste suficiente aire antes de la frase mala.

---

## Tuning por request

Podés ajustar el comportamiento por request en el body del POST:

```json
{
  "job_uuid": "abc-123",
  "video_paths": ["https://..."],
  "pause_backtrack_silence_threshold_seconds": 0.4,
  "pause_backtrack_max_seconds": 8.0
}
```

### `pause_backtrack_silence_threshold_seconds` (default: 0.4)

Cuánto silencio tiene que haber para que el backtrack pare.

- **Bajar a 0.25**: si decís la frase mala inmediatamente después de la anterior sin respirar.
- **Subir a 0.6**: si querés que retroceda solo en silencios muy obvios, más conservador.

### `pause_backtrack_max_seconds` (default: 8.0)

Límite de seguridad. Nunca retrocede más de esto aunque no encuentre silencio.

- **Bajar a 4.0**: si nunca hacés frases malas largas y querés extra safety.
- **Subir a 15.0**: si a veces recitás párrafos enteros malos antes del keyword.

### Customizar la lista de keywords

```json
{
  "pause_keywords": [
    "PAUSA ACA",
    "PAUSA ACÁ",
    "CORTA",
    "BORRAR ESTO"
  ]
}
```

---

## Recomendación de uso para grabar

Para que la detección sea perfecta, grabá con este flujo:

1. **Tomá aire** antes de empezar la frase mala (deja un silencio natural >= 0.4s).
2. **Decí la frase mala** normal.
3. **Decí el keyword** ("pausa" o "pausa aca") con tono claro.
4. **Tomá aire de nuevo** (otro silencio >= 0.4s).
5. **Repetí la frase corregida**.

Si respetás el aire antes y después, la detección funciona exactamente donde querés.

---

## Combinación con otros cortes automáticos

Los cortes de `pausa` se combinan en una sola pasada con:

- **Silencios largos** (`silence_threshold_seconds`, default 2.0s).
- **Fillers** (`filler_terms`: eh, mm, este, o sea, etc.).
- **Word gaps** (gaps >0.55s se comprimen a 0.28s).
- **Stutters** (tartamudeos consecutivos).

Todos los cortes se fusionan y se renderiza un único mp4 limpio.
