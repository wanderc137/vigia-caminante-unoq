# 🛠️ Vigía Caminante UNO Q — Guía del desarrollador

**Versión 1.0.0** · Python 3.13 · OpenCV 4.10 · numpy 2.2.4 · Debian 13 aarch64

Documento para quien va a leer, modificar o extender el código. Explica la arquitectura, el
contrato de cada módulo, las fórmulas exactas, el modelo de concurrencia y las decisiones que no
son obvias. Para operar el sistema, ver [`datasheet.md`](datasheet.md).

---

## Índice

1. [Mapa del código](#1-mapa-del-código)
2. [Arranque y ciclo de vida](#2-arranque-y-ciclo-de-vida)
3. [Arquitectura de hilos](#3-arquitectura-de-hilos)
4. [Candados y modelo de concurrencia](#4-candados-y-modelo-de-concurrencia)
5. [Flujo de datos](#5-flujo-de-datos)
6. [El modelo de riesgo](#6-el-modelo-de-riesgo)
7. [Fórmula del score](#7-fórmula-del-score)
8. [Máquina de estados e histéresis](#8-máquina-de-estados-e-histéresis)
9. [Overlay y codificación](#9-overlay-y-codificación)
10. [Contrato de la API](#10-contrato-de-la-api)
11. [Configuración](#11-configuración)
12. [Tests](#12-tests)
13. [Despliegue](#13-despliegue)
14. [Decisiones de rendimiento](#14-decisiones-de-rendimiento)
15. [Correcciones de auditoría](#15-correcciones-de-auditoría)
16. [Cómo extender](#16-cómo-extender)
17. [Notas sobre Zephyr y diferencias con AVR](#17-notas-sobre-zephyr-y-diferencias-con-avr)
18. [Deuda técnica y próximos pasos](#18-deuda-técnica-y-próximos-pasos)

---

## 1. Mapa del código

```
src/main.py                  Orquestador del proceso
src/vigia/__init__.py        __version__
src/vigia/config.py          Config (dataclass frozen) + load_config()
src/vigia/estado.py          EstadoFrame, ObjetoRiesgo, Estado (thread-safe)
src/vigia/camara.py          CamaraThread + _LectorMjpeg + validación de URL
src/vigia/demo.py            DemoThread (escena sintética)
src/vigia/vision.py          Tracker + ModeloRiesgo + VisionThread
src/vigia/overlay.py         renderizar, HUD, borde, JPEG
src/vigia/servidor.py        HTTP handler, rutas, API, validación de ajustes
web/                         UI (HTML/CSS/JS sin frameworks)
scripts/servicio.sh          Control del servicio sin sudo ni systemd
scripts/desplegar.sh         Copia a la placa por tar+ssh
tests/                       38 unittest
```

| Módulo | Responsabilidad única | No debe |
|---|---|---|
| `main.py` | Crear hilos, logging, PID, señales, watchdog | Contener lógica de visión |
| `config.py` | Leer `config.env` y producir un `Config` inmutable con defaults seguros | Leer estado en caliente |
| `estado.py` | Almacén compartido con candados | Saber de OpenCV más allá de `np.ndarray` |
| `camara.py` | Traer frames del teléfono | Analizar imágenes |
| `demo.py` | Generar frames sintéticos | Conocer la red |
| `vision.py` | Convertir frame → riesgo | Servir HTTP o dibujar la UI |
| `overlay.py` | Convertir (frame, estado) → JPEG | Decidir niveles |
| `servidor.py` | Exponer estado y aceptar configuración | Calcular riesgo |

---

## 2. Arranque y ciclo de vida

### `src/main.py`

| Función | Firma | Qué hace |
|---|---|---|
| `configurar_logging` | `(nivel: str, archivo: Path) -> None` | Consola + `RotatingFileHandler(5 MB, backupCount=2)`. Limpia handlers previos |
| `escribir_pid` | `(config) -> None` | Escribe `data/vigia.pid` con el PID actual |
| `borrar_pid` | `(config) -> None` | Borra el PID **solo si coincide** con el propio (evita pisar otra instancia) |
| `_bucle_watchdog` | `(estado, config, parar: Event) -> None` | Cada 250 ms verifica `edad_frame_ms()`; publica `NO_SIGNAL` si venció el plazo |
| `_apagar` | `(hilos, servidor, parar) -> None` | `parar.set()`, `detener()` en cada hilo, `join(3 s)`, `shutdown()` del servidor |
| `main` | `(argv: list \| None = None) -> int` | Punto de entrada. Devuelve 0 normal, 2 si el puerto está ocupado |

### Secuencia de arranque

```
main()
 ├─ load_config(argv[0] o None)          # config.env → config.example → env → defaults
 ├─ configurar_logging()                  # root logger + archivo rotativo
 ├─ Estado(camara_url=…, modo=…)          # estado inicial NO_SIGNAL
 ├─ escribir_pid()                        # data/vigia.pid
 ├─ signal.signal(SIGTERM|SIGINT, _senal) # parada limpia
 ├─ crear_servidor()                      # bind :8030 (falla rápido si está ocupado)
 ├─ hilo "http"      (daemon)             # serve_forever(poll_interval=0.5)
 ├─ CamaraThread / DemoThread / VisionThread .start()
 ├─ hilo "watchdog"  (daemon)
 └─ while not parar.wait(0.5): pass       # el main solo espera
     └─ finally: _apagar(...) + borrar_pid()
```

### Argumentos

```bash
python3 src/main.py                  # usa config.env (o config.example si falta)
python3 src/main.py /tmp/prueba.env  # usa un env alternativo (relativo a la raíz o absoluto)
```

### Logging

Formato: `%(asctime)s %(levelname)-7s %(threadName)-8s %(name)s: %(message)s`

```
2026-11-15 05:41:02 INFO    vision   vigia.vision: Vision: iniciando (proceso 320 px de ancho, objetivo 15.0 fps)
2026-11-15 05:41:03 INFO    camara   vigia.camara: Camara: stream abierto con OpenCV (1080x1920)
2026-11-15 05:41:08 WARNING watchdog vigia: Watchdog: sin frames recientes (edad 2014 ms)
```

---

## 3. Arquitectura de hilos

Cinco hilos, todos `daemon=True` salvo que el proceso termina por el `main`.

```
                      ┌─────────────────────────────┐
  IP Webcam ─────────►│ CamaraThread (name="camara")│──┐
                      │  run() → _capturar(url)     │  │
                      └─────────────────────────────┘  │  set_frame_crudo()
                                                       │  (Condition.notify_all)
                      ┌─────────────────────────────┐  │
  demo ──────────────►│ DemoThread (name="demo")    │──┤
                      │  run() → _generar_frame(t)  │  │
                      └─────────────────────────────┘  │
                                                       ▼
                      ┌─────────────────────────────┐  ┌──────────────────────────┐
                      │ VisionThread (name="vision")│◄─┤ Estado                   │
                      │  get_frame_crudo_con_version│  │  _cond: crudo + jpeg     │
                      │  modelo.procesar()          │  │  _lock_estado: EstadoFrame│
                      │  overlay.renderizar_y_codif.│  │  _lock_config: modo/url/aj│
                      └─────────────────────────────┘  └──────────────────────────┘
                                 │ set_frame_anotado()            ▲
                                 │ set_estado()                   │
                                 ▼                                │
                      ┌─────────────────────────────┐              │
                      │ ThreadingHTTPServer         │──────────────┘
                      │  (name="MainThread"+"http") │  get_estado(), get_frame_anotado()
                      └─────────────────────────────┘
                                 ▲
                      ┌──────────┴──────────────────┐
                      │ _bucle_watchdog             │  reemplazar_estado_si()
                      │  (name="watchdog")          │  set_frame_anotado()
                      └─────────────────────────────┘
```

### `CamaraThread` (`src/vigia/camara.py`)

| Miembro | Firma | Rol |
|---|---|---|
| `run` | `() -> None` | Bucle externo: espera modo `camara`, valida URL, captura, backoff |
| `detener` | `() -> None` | `_parar.set()` + suelta la conexión |
| `_capturar` | `(url: str) -> bool` | Intenta `cv2.VideoCapture`; si falla, `_LectorMjpeg` |
| `_conectar_cv2` | `(url) -> Optional[VideoCapture]` | `CAP_FFMPEG` primero, backend por defecto después; `BUFFERSIZE=1` |
| `_bucle_cv2` | `(cap, url) -> bool` | Lee hasta error / cambio de URL / parada |
| `_bucle_mjpeg` | `(lector, url) -> bool` | Ídem con el lector propio |
| `_soltar` | `() -> None` | Suelta la conexión al cambiar de modo |
| Atributos | `errores_seguidos`, `modo_lector` (`cv2` \| `mjpeg` \| `inactivo`) | Diagnóstico |

**Backoff**: arranca en `cam_reconnect_backoff_sec` (5 s), duplica hasta `_MAX_BACKOFF = 30 s`, y
vuelve a 1 s tras un éxito. No bloquea a los demás hilos: usa `Event.wait`, no `sleep`.

**Doble lector**: `cv2.VideoCapture` es más rápido, pero en algunos firmwares de IP Webcam el
buffer interno del decoder se atasca. El `_LectorMjpeg` parsea el multipart a mano
(`Content-Length`, resguardo de boundary, tope `_MAX_PARTE = 2 MB`) y decodifica con
`cv2.imdecode`. La decisión se registra en `modo_lector` y aparece en el log.

### `DemoThread` (`src/vigia/demo.py`)

| Miembro | Rol |
|---|---|
| `_crear_fondo()` | Pre-renderiza el pasillo una sola vez (degradado, líneas de fuga, corredor) |
| `_generar_frame(t)` | Copia el fondo y dibuja el caminante según `_triangular(t)` |
| `_triangular(t)` | `0 → 1 → 0` en `PERIODO_S = 10 s` (acercarse y alejarse) |
| Constantes | `Z_FONDO=6 m`, `Z_CERCA=0.6 m`, `K_PERSPECTIVA=624 px·m`, `K_TAMANO=260 px·m` |

Modelo de perspectiva: `y = HORIZONTE_PX + K_PERSPECTIVA / z`, `radio = K_TAMANO / z`. Eso
produce un objeto que baja y crece de forma físicamente coherente — que es justo lo que el
tracker mide. Además dibuja 3 cuadraditos de ruido que el filtro debe ignorar.

### `VisionThread` (`src/vigia/vision.py`)

| Miembro | Rol |
|---|---|
| `run()` | Bucle: lee modo, ajustes, frame con versión; procesa; publica JPEG y estado |
| `detener()` | `_parar.set()` |
| `_publicar_detenido()` | Estado `NO_SIGNAL` + mensaje "Análisis detenido" idempotente |
| `procesados` | Contador de frames analizados |
| `_fps` | Media exponencial (0.85/0.15) del fps instantáneo |

Detalle importante: el hilo **descarta frames repetidos** comparando `version`:

```python
frame, ts_captura, version = self.estado.get_frame_crudo_con_version()
if version == self._ultima_version:
    self._parar.wait(0.004)
    continue
```

Así la visión nunca procesa el mismo frame dos veces, y no gasta CPU cuando la cámara va más
lento que `fps_target`. Además duerme el sobrante del período para no saturar un núcleo.

### Servidor HTTP

`ServidorVigia(ThreadingHTTPServer)` con `daemon_threads = True` y `allow_reuse_address = True`.
Cada request se atiende en su propio hilo. `crear_servidor()` hace el bind; `run_server()` llama a
`serve_forever(poll_interval=0.5)`.

`/stream` **no hace busy-wait**: espera sobre la `Condition` del estado a que cambie la versión del
JPEG, con `timeout=2.0` para reenviar el último frame si no llega nada nuevo.

### Watchdog (`_bucle_watchdog` en `main.py`)

Cada 250 ms:

1. Lee `modo`, `ajustes` y `watchdog_ms` (con piso de 200 ms por defensa en profundidad).
2. Calcula `edad = estado.edad_frame_ms()`.
3. Si `modo == "detenido"` o `edad > watchdog_ms`, intenta publicar `NO_SIGNAL` con
   `reemplazar_estado_si()`.
4. La mutación se **aborta** si la visión publicó dentro de la ventana (`edad_pub_ms <= watchdog_ms`),
   para no pisar un estado fresco. También aborta si el estado ya es idéntico (evita reescrituras).
5. Cada 1 s regenera un JPEG de respaldo (`jpeg_sin_senal`) para que `/stream` nunca quede mudo.

Resultado: el gris aparece cuando corresponde y no parpadea.

---

## 4. Candados y modelo de concurrencia

### Los tres candados

| Candado | Tipo | Protege | Archivo |
|---|---|---|---|
| `_cond` | `Condition(Lock())` | `_frame_crudo`, `_ts_crudo`, `_version_crudo`, `_jpeg`, `_version_jpeg` | `estado.py` |
| `_lock_estado` | `Lock()` | `_estado: EstadoFrame` | `estado.py` |
| `_lock_config` | `Lock()` | `_modo`, `_camara_url`, `_ajustes` | `estado.py` |

Separarlos evita serializar el pipeline: el HTTP puede leer el estado mientras la visión escribe
un frame nuevo.

### Orden de adquisición

**Regla global: `_lock_config` → `_lock_estado`. Nunca al revés.**

| Método | Toma | Nota |
|---|---|---|
| `set_modo` | `_lock_config` y luego `_lock_estado` | Único que toma ambos, en ese orden |
| `actualizar_ajustes_si` | solo `_lock_config` | Nunca toma `_lock_estado` |
| `set_estado` / `get_estado` | solo `_lock_estado` | — |
| `set_frame_*` | solo `_cond` | — |

Ninguna ruta del código toma `_lock_estado` antes que `_lock_config`. Eso descarta interbloqueos
entre el hilo HTTP (que cambia modo) y el watchdog (que cambia estado).

### `_lock_config` es `Lock`, no `RLock`

Consecuencia de diseño: un validador que corra **dentro** de `actualizar_ajustes_si` **no puede**
llamar a `get_ajustes()`, `set_ajustes()` ni `actualizar_ajustes_si()` — se colgaría. Por eso
`_validar_ajustes` recibe el diccionario ya fusionado y trabaja solo con él y con `Config`.

### Atomicidad del estado

| Operación | Garantía |
|---|---|
| `set_estado` / `get_estado` | `get_estado` devuelve una **copia** (`copia()` con `replace` y lista nueva): nadie muta el estado publicado |
| `reemplazar_estado_si(fn)` | Lee, decide y escribe dentro del **mismo** `with _lock_estado` (compare-and-set). Elimina el RMW del watchdog |
| `actualizar_ajustes_si(nuevos, validador)` | Lee, fusiona, valida y escribe bajo un único `_lock_config` (compare-and-set de configuración) |
| Frames | El productor reemplaza la **referencia**; el consumidor recibe la referencia y nunca la muta (overlay copia) |

### Inmutabilidad efectiva

- `Config` es `@dataclass(frozen=True)`.
- `EstadoFrame` y `ObjetoRiesgo` son dataclasses mutables, pero se tratan como inmutables una vez
  publicadas: `get_estado()` devuelve copia y `_escalar_objetos` usa `replace`.
- El frame crudo es la única estructura potencialmente grande. Regla: **quien dibuja, copia**.

---

## 5. Flujo de datos

```
 ┌──────────────┐
 │ IP Webcam    │  MJPEG 1080×1920
 └──────┬───────┘
        │ cv2.VideoCapture o _LectorMjpeg
        ▼
 ┌──────────────────────────────────────────────┐
 │ Estado.set_frame_crudo(frame, t)             │  _cond: frame, ts, version++
 └──────┬───────────────────────────────────────┘
        │ get_frame_crudo_con_version()
        ▼
 ┌──────────────────────────────────────────────┐
 │ ModeloRiesgo.procesar(frame, t, previo)      │
 │   _preprocesar   → 320 px gris + blur        │
 │   _detectar_mov  → MOG2 + morfo + contornos  │
 │   _filtrar       → área mín, aspecto         │
 │   Tracker.update → IDs, vx/vy, dA/dt, ttc    │
 │   _score_indiv.  → score por objeto          │
 │   _riesgo_global → max + EMA + histéresis    │
 └──────┬───────────────────────────────────────┘
        │ EstadoFrame(nivel, score, ttc, objetos…)
        ▼
 ┌──────────────────────────────────────────────┐
 │ overlay.renderizar_y_codificar(frame, estado)│
 │   _reducir_salida  → 540 px (INTER_AREA)     │
 │   renderizar       → borde, cajas, HUD       │
 │   _encode_jpeg     → calidad 70              │
 └──────┬───────────────────────────────────────┘
        │ bytes JPEG
        ├──► Estado.set_frame_anotado(jpeg)  ──► /stream, /snapshot.jpg
        └──► Estado.set_estado(estado)       ──► /api/estado
```

### Orden de publicación (deliberado)

```python
jpeg = overlay.renderizar_y_codificar(frame, resultado, self.config)
self.estado.set_frame_anotado(jpeg)   # 1) primero la imagen
self.estado.set_estado(resultado)     # 2) después el JSON
```

Publicar primero el JPEG evita la ventana en la que `/api/estado` anuncia un nivel que la imagen
todavía no muestra.

### Latencia y edad del frame

| Métrica | Definición | Dónde se calcula |
|---|---|---|
| `latencia_ms` | `perf_counter()` al empezar → al terminar el procesamiento | `VisionThread.run` |
| `frame_age_ms` (visión) | `time.time()` al publicar − `ts_captura` | `VisionThread.run` |
| `frame_age_ms` (watchdog) | `time.time() − ts_crudo` | `_bucle_watchdog` |
| `fps` | Media exponencial de `1/Δt` entre publicaciones | `VisionThread.run` |

`frame_age_ms` alto con `latencia_ms` normal ⇒ el cuello de botella es la **red**.
Ambos altos ⇒ el cuello de botella es la **CPU**.

---

## 6. El modelo de riesgo

### 6.1 Preproceso — `_preprocesar`

```python
escala = ancho_proceso / ancho_original          # 320 / 1080 = 0.296
alto    = round(alto_original * escala)          # 1920 → 569
pequeno = cv2.resize(frame, (320, alto), INTER_LINEAR)
gris    = cv2.cvtColor(pequeno, BGR2GRAY)
gris    = cv2.GaussianBlur(gris, (5,5), 0)
```

Devuelve `(gris, escala)`. La `escala` se usa para devolver las cajas a coordenadas originales.

`INTER_LINEAR` es deliberado: en el Cortex-A53 de la placa, `INTER_AREA` tarda ~80 ms y
`INTER_LINEAR` ~14 ms. El blur posterior compensa el aliasing.

### 6.2 Detección — `_detectar_movimiento`

```python
mascara = self._bg.apply(gris)                              # MOG2
_, binaria = cv2.threshold(mascara, 200, 255, THRESH_BINARY) # sombras (127) fuera
binaria = cv2.morphologyEx(binaria, MORPH_CLOSE, kernel_cierre)  # 7×7 elipse
binaria = cv2.morphologyEx(binaria, MORPH_OPEN,  kernel_apertura) # 5×5 elipse
contornos, _ = cv2.findContours(binaria, RETR_EXTERNAL, CHAIN_APPROX_SIMPLE)
```

Umbral 200 sobre la salida de MOG2: el algoritmo marca sombras con 127 cuando
`detectShadows=True`; con `detectShadows=False` la máscara es 0/255, y el umbral 200 deja pasar
solo foreground firme.

### 6.3 Filtrado — `_filtrar_detecciones`

| Regla | Efecto |
|---|---|
| `area_frame <= 0` o `escala <= 0` | Devuelve `[]` (I-010) |
| `contourArea < area_minima_px` | Descarta ruido |
| `w < 4` o `h < 4` | Descarta líneas degeneradas |
| `w/h > aspecto_max` o `h/w > aspecto_max * 1.6` | Descarta formas alargadas (bordes, reflejos) |
| **Todo lo demás se conserva** | **No hay filtro por tamaño máximo** (I-001) |

Las cajas se reescalan a resolución original dividiendo por `escala`.

### 6.4 Tracking — `Tracker.update`

```python
update(detecciones: Sequence[BBox], timestamp: float,
       ancho: int|None, alto: int|None) -> List[ObjetoRiesgo]
```

Asignación greedy por IoU máximo:

1. Para cada detección, busca el track **libre** con mayor IoU.
2. Si `IoU > iou_threshold` (0.3) → reutiliza el ID.
3. Si no → crea un track nuevo con `id = _siguiente_id++`.
4. Actualiza cinemática:

```python
dt  = timestamp - track.t
vx  = (cx - track.centro[0]) / dt
vy  = (cy - track.centro[1]) / dt
crec = ((area_rel - track.area_rel) / max(track.area_rel, 1e-6)) / dt

if track.vistos > 1:                 # suavizado 50/50 para no disparar por jitter de máscara
    vx   = 0.5*vx   + 0.5*track.vx
    vy   = 0.5*vy   + 0.5*track.vy
    crec = 0.5*crec + 0.5*track.crecimiento
```

5. TTC por objeto:

```python
ttc = max(0.0, (alto_frame - cy) / vy)  if vy > vel_y_min_ttc  else inf
```

6. `en_corredor`:

```python
en_corredor = (centro_norm_y > corredor_y_min
               and corredor_x_min <= centro_norm_x <= corredor_x_max)
```

7. Caducidad: tracks no vistos en `max_age` (0.5 s) se eliminan.

### 6.5 Score individual — `_score_individual`

Ver [sección 7](#7-fórmula-del-score).

### 6.6 Riesgo global — `_riesgo_global`

```python
score_crudo = max([o.score for o in objetos] + [0.0])
score = 1.0 if score_crudo >= 1.0 else self._suavizar(score_crudo, estado_previo, dt)
nivel = self._avanzar_nivel(score, dt)
ttc   = min([o.ttc para objetos finitos y >= 0] + [inf])
```

Dos decisiones importantes:

- **El score global es el máximo**, no un promedio. Un solo objeto peligroso alcanza para pintar
  rojo; tres objetos inofensivos no suman riesgo.
- **Un score de 1.00 no se suaviza**. Si un objeto llena el cuadro, no hay tiempo para que el EMA
  suba: se publica rojo en el mismo frame.

### 6.7 Calentamiento

```python
self._calentamiento = max(2, min(6, config.bg_history // 40))   # = 5 con bg_history=200
```

Durante los primeros `_calentamiento` frames solo se llama a `self._bg.apply(gris)` (entrena) y se
publica `NO_SIGNAL` con mensaje `"Calibrando fondo de escena"`. Motivo: MOG2 devuelve todo el
cuadro como foreground al inicio y, con la corrección I-001, ese blob sería amenaza máxima.

---

## 7. Fórmula del score

### Caso especial: objeto que llena el cuadro

```python
if obj.area_rel >= area_maxima_rel:      # 0.95 por defecto
    return 1.0
```

Es el caso de colisión inminente. No se promedia, no se descuenta, no se filtra.

### Caso general

Sea `(nx, ny, nw, nh)` el bbox normalizado, `cy = ny + nh/2`.

| Término | Fórmula | Peso | Intuición |
|---|---|---|---|
| `w_cercania` | `clamp((cy − corredor_y_min) / (1 − corredor_y_min), 0, 1)²` | 0.25 | Cuanto más abajo, más cerca |
| `w_vel` | `σ((vy − vel_y_critica/2) / (vel_y_critica/4))` | 0.20 | Baja rápido → se acerca |
| `w_loom` | `clamp(max(0, crec) / loom_critico, 0, 1)` | 0.25 | Crece rápido → se acerca |
| `w_area` | `σ((area_rel − m) / ((a_gran − a_peq)/4))`, `m = (a_peq + a_gran)/2` | 0.15 | Ya ocupa mucho |
| `w_ttc` | `exp(−max(0, ttc) / ttc_critico)` | 0.15 | Falta poco |

```
score = clamp( 0.25·w_cercania + 0.20·w_vel + 0.25·w_loom
             + 0.15·w_area     + 0.15·w_ttc , 0, 1 )
```

`σ(x) = 1/(1+e^−x)`, con `x` recortado a `[−60, 60]` para evitar `overflow`.

### Suavizado (EMA)

```python
alpha = 1 − exp(−dt / tau_ema)          # dt recortado a [0.001, 0.5]
score = alpha·score_crudo + (1 − alpha)·score_previo
```

Si `score_previo <= 0` se devuelve el valor crudo (evita arrastrar un cero eterno).

`tau_ema` chico ⇒ el score sigue al instante. `tau_ema` grande ⇒ suave pero lento.
El bypass de `1.0` es la excepción explícita para colisión inminente.

### Ejemplo numérico (defaults)

**Objeto A** — `cy=0.72`, `vy=180`, `area_rel=0.10`, `crec=1.2`, `ttc=1.4`:

| Término | Cálculo | Valor |
|---|---|---|
| `w_cercania` | `((0.72−0.35)/0.65)²` | 0.3240 |
| `w_vel` | `σ((180−75)/37.5) = σ(2.8)` | 0.9427 |
| `w_loom` | `1.2/2.0` | 0.6000 |
| `w_area` | `σ((0.10−0.11)/0.045)` | 0.4447 |
| `w_ttc` | `exp(−1.4/2.0)` | 0.4966 |

`score = 0.0810 + 0.1885 + 0.1500 + 0.0667 + 0.0745 = 0.5607` → **AMBER**

**Objeto B** — `cy=0.90`, `vy=420`, `area_rel=0.35`, `crec=3.5`, `ttc=0.4`:

| Término | Valor |
|---|---|
| `w_cercania` | 0.7160 |
| `w_vel` | ≈ 1.0000 |
| `w_loom` | 1.0000 (saturado) |
| `w_area` | 0.9952 |
| `w_ttc` | 0.8187 |

`score ≈ 0.1790 + 0.2000 + 0.2500 + 0.1493 + 0.1228 = 0.9011` → **RED**

---

## 8. Máquina de estados e histéresis

### `_histeresis(score, nivel_actual, t_en_nivel_ms) -> str`

```python
if score >= umbral_rojo_in:                    return "RED"
if nivel_actual == "RED" and (score > umbral_rojo_out
                              or t_en_nivel_ms < min_tiempo_rojo_ms):
                                               return "RED"
if score >= umbral_ambar_in:                   return "AMBER"
if nivel_actual == "AMBER" and score > umbral_ambar_out:
                                               return "AMBER"
return "GREEN"
```

### `_avanzar_nivel(score, dt) -> str`

```python
self._t_en_nivel += max(0, dt)
nuevo = self._histeresis(score, self._nivel, self._t_en_nivel * 1000)
if nuevo != self._nivel:
    self._nivel = nuevo
    self._t_en_nivel = 0.0
return self._nivel
```

`_t_en_nivel` es el tiempo **continuo** dentro del nivel actual; se resetea al cambiar.

### Tabla de transiciones (defaults)

| Desde | Score | Condición extra | Resultado |
|---|---|---|---|
| cualquiera | ≥ 0.70 | — | `RED` |
| `RED` | 0.56 – 0.69 | — | `RED` (sostiene) |
| `RED` | ≤ 0.55 | `t_en_nivel < 800 ms` | `RED` (tiempo mínimo) |
| `RED` | ≤ 0.55 | `t_en_nivel ≥ 800 ms` | evalúa `AMBER`/`GREEN` |
| cualquiera | ≥ 0.40 | — | `AMBER` |
| `AMBER` | 0.26 – 0.39 | — | `AMBER` (sostiene) |
| `AMBER` | ≤ 0.25 | — | `GREEN` |
| `GREEN` | 0.26 – 0.39 | — | `GREEN` |

Las zonas `0.25–0.40` y `0.55–0.70` son las bandas muertas. Sin ellas, un score oscilando en el
umbral produciría un estroboscopio de colores.

### Diagrama

```
         score
         1.0 ┤                    ┌─────────── RED
             │              ╱─────┘
      0.70   ┼────────────╱──────────────────  rojo_in
             │        ╱
      0.55   ┼──────╱───────────────────────  rojo_out (sostiene RED)
             │    ╱
      0.40   ┼──╱───────────────────────────  ambar_in
             │ ╱
      0.25   ┼╱─────────────────────────────  ambar_out
             │
      0.0    ┴─────────────────────────────  GREEN
```

---

## 9. Overlay y codificación

### Funciones (`src/vigia/overlay.py`)

| Función | Firma | Rol |
|---|---|---|
| `renderizar_y_codificar` | `(frame, estado, config) -> bytes` | **Punto de entrada**. Reduce, dibuja, codifica. Nunca devuelve `b""` |
| `renderizar` | `(frame, estado, config) -> np.ndarray` | Dibuja in-place sobre el buffer recibido |
| `_reducir_salida` | `(frame, ancho_salida) -> (frame, escala)` | `INTER_AREA`; con `0` devuelve **copia** |
| `_escalar_objetos` | `(estado, escala) -> EstadoFrame` | Reescala bboxes para la salida sin mutar |
| `_dibujar_borde` | `(frame, color, grosor)` | Rectángulo de `borde_grosor` px |
| `_dibujar_bbox` | `(frame, obj, color)` | Caja + etiqueta `IDn NN% TTC x.xs` |
| `_dibujar_hud` | `(frame, estado)` | Panel superior: nivel, score/TTC, fps/ms, objetos/modo |
| `_mensaje_central` | `(frame, texto, color)` | Texto grande con fondo (gris / detenido) |
| `_encode_jpeg` | `(frame, calidad) -> bytes` | `imencode`; devuelve `b""` si falla |
| `frame_sin_senal` | `(config, mensaje) -> np.ndarray` | Frame de reemplazo cacheado por tamaño |
| `jpeg_sin_senal` | `(config, mensaje) -> bytes` | JPEG de respaldo garantizado |

### Colores (BGR en OpenCV, hex en CSS)

| Nivel | Hex | BGR |
|---|---|---|
| `NO_SIGNAL` | `#6b7280` | `(128, 114, 107)` |
| `GREEN` | `#22c55e` | `(94, 197, 34)` |
| `AMBER` | `#f59e0b` | `(11, 158, 245)` |
| `RED` | `#ef4444` | `(68, 68, 239)` |

La UI usa exactamente los mismos hex vía `body[data-nivel="…"]`, así el borde del `<img>` y el
borde dibujado en el JPEG coinciden.

### Garantía de JPEG no vacío (I-006)

```
_encode_jpeg → bytes válidos ────────────────► se devuelven y se cachean
             → b"" ──► ¿hay _ultimo_jpeg_valido? ── sí ─► se reenvía
                                                 └─ no ─► _JPEG_MINIMO (16×16 gris)
```

`_JPEG_MINIMO` es un JPEG base64 embebido en el módulo. Sirve para que el parser MJPEG del
navegador nunca reciba un `Content-Length: 0`, que lo haría cortar la conexión.

---

## 10. Contrato de la API

### `GET /`

Devuelve `web/index.html`. `Cache-Control: no-cache`.

### `GET /stream`

```
HTTP/1.1 200 OK
Content-Type: multipart/x-mixed-replace; boundary=frame
Cache-Control: no-store, no-cache, must-revalidate
Connection: close
```

Cada parte:

```
--frame\r\n
Content-Type: image/jpeg\r\n
Content-Length: <n>\r\n
\r\n
<bytes JPEG>\r\n
```

El bucle espera un JPEG **nuevo** (`esperar_frame_anotado(version, timeout=2.0)`); si no llega,
reenvía el último para que el cliente no corte.

### `GET /snapshot.jpg`

JPEG del último frame anotado. Si no hay, genera `jpeg_sin_senal` con la etiqueta del nivel actual.
Nunca 404, nunca vacío.

### `GET /api/estado` (alias `/api/status`)

```json
{
  "nivel": "GREEN|AMBER|RED|NO_SIGNAL",
  "score": 0.0,
  "ttc": null,
  "fps": 0.0,
  "latencia_ms": 0.0,
  "frame_age_ms": 0,
  "objetos": [],
  "n_objetos": 0,
  "modo": "camara|demo|detenido",
  "camara_url": "http://192.168.1.9:8080",
  "mensaje": "",
  "timestamp": 0.0,
  "uptime_s": 0.0,
  "ajustes": {}
}
```

- `ttc: null` cuando es `inf` o `NaN` (`_num()` normaliza para que `json.dumps` no explote).
- `ajustes` contiene **solo** lo cambiado en caliente por `POST /api/config`.
- Los floats se redondean: `score` 3 decimales, `ttc` 2, `fps` 2, `latencia_ms` 1.

Cada objeto de `objetos[]`:

```json
{
  "id": 1, "bbox": [0,0,0,0], "bbox_norm": [0,0,0,0],
  "area_rel": 0.0, "velocidad_px_s": 0.0, "velocidad_y": 0.0,
  "crecimiento_area_s": 0.0, "ttc": null, "en_corredor": false,
  "nivel_individual": "GREEN", "score": 0.0
}
```

### `GET /api/config`

```json
{
  "puerto": 8030,
  "camara_url": "http://192.168.1.9:8080",
  "modo": "camara",
  "ajustes": {},
  "umbrales": {"rojo_in": 0.7, "rojo_out": 0.55, "ambar_in": 0.4, "ambar_out": 0.25}
}
```

`umbrales` son los **defaults de `Config`**, no los ajustes activos. Los activos están en `ajustes`.

### `POST /api/config`

Cuerpo JSON, máximo **8192 bytes**. Acepta tres grupos:

| Grupo | Claves | Validación |
|---|---|---|
| Cámara | `camara_url` | `es_url_valida()`: `http(s)`, host con punto o `localhost`, no loopback / no especificada / no link-local / no IP propia |
| Modo | `modo` | `camara` \| `demo` \| `detenido` |
| Ajustes | las 13 de `CLAVES_AJUSTABLES` | Numéricos (rechaza `bool`), finitos, y `_validar_ajustes` sobre el estado fusionado |

Respuesta OK:

```json
{"ok": true, "cambios": {"modo": "demo"}, "camara_url": "...", "modo": "demo", "ajustes": {}}
```

Respuesta 400:

```json
{"error": "watchdog_ms debe ser >= 200", "campo": ["watchdog_ms"]}
```

#### `CLAVES_AJUSTABLES`

```
umbral_rojo_in, umbral_rojo_out, umbral_ambar_in, umbral_ambar_out,
min_tiempo_rojo_ms, ttc_critico, vel_y_critica, vel_y_min_ttc,
loom_critico, tau_ema, area_minima_px, area_maxima_rel, watchdog_ms
```

#### Invariantes de `_validar_ajustes`

| Invariante | Mensaje |
|---|---|
| Todos finitos | "los ajustes deben ser números finitos" |
| `0 < ambar_out < ambar_in < rojo_out < rojo_in < 1` | "umbrales incoherentes: …" |
| `tau_ema > 0.01` | "tau_ema debe ser > 0.01" |
| `ttc_critico > 0.1` | "ttc_critico debe ser > 0.1" |
| `vel_y_critica > 1.0` | "vel_y_critica debe ser > 1.0" |
| `vel_y_min_ttc >= 0` | "vel_y_min_ttc no puede ser negativo" |
| `min_tiempo_rojo_ms >= 0` | "min_tiempo_rojo_ms no puede ser negativo" |
| `area_minima_px >= 1` | "area_minima_px debe ser >= 1" |
| `0 < area_maxima_rel <= 0.99` | "area_maxima_rel debe estar en (0, 0.99]" |
| `loom_critico > 1e-3` | "loom_critico debe ser > 0.001" |
| `watchdog_ms >= 200` | "watchdog_ms debe ser >= 200" |

El lote es **atómico**: o se aplica completo o se rechaza completo.

### `GET /health`

```json
{"status":"ok","servicio":"vigia-caminante-unoq","version":"1.0.0","timestamp":1763215678.412,"uptime_s":812.4}
```

### Ejemplos con `curl`

```bash
BASE=http://192.168.1.5:8030

# salud
curl -s $BASE/health | python3 -m json.tool

# estado
curl -s $BASE/api/estado | python3 -m json.tool

# modo demo
curl -s -X POST $BASE/api/config -H 'Content-Type: application/json' \
     -d '{"modo":"demo"}'

# nueva cámara
curl -s -X POST $BASE/api/config -H 'Content-Type: application/json' \
     -d '{"camara_url":"http://192.168.1.10:8080/video"}'

# bajar la escalera de umbrales en un solo lote atómico
curl -s -X POST $BASE/api/config -H 'Content-Type: application/json' \
     -d '{"umbral_rojo_in":0.62,"umbral_rojo_out":0.50,"umbral_ambar_in":0.36,"umbral_ambar_out":0.22}'

# rechazo esperado (400)
curl -s -X POST $BASE/api/config -H 'Content-Type: application/json' \
     -d '{"loom_critico":0}'

# loopback rechazado (SSRF)
curl -s -X POST $BASE/api/config -H 'Content-Type: application/json' \
     -d '{"camara_url":"http://127.0.0.1:8000/"}'

# snapshot
curl -s $BASE/snapshot.jpg -o /tmp/vigia.jpg

# stream (3 segundos)
curl -s --max-time 3 $BASE/stream -o /tmp/stream.bin && ls -l /tmp/stream.bin
```

### Validación de URL — `es_url_valida`

```python
es_url_valida(url) -> bool
```

| Rechaza | Acepta |
|---|---|
| Esquema distinto de `http`/`https` | `http://192.168.1.9:8080/video` |
| Host sin punto y que no sea `localhost` | `http://camara.local:8080` |
| `127.0.0.0/8`, `::1`, `localhost`, `0.0.0.0`, `::` | `192.168.x.x`, `10.x.x.x`, `172.16-31.x.x` |
| `169.254.0.0/16` (metadata cloud incluida) | Cualquier host público resoluble |
| La IP propia (`192.168.1.5` + las del sistema) | — |

**No se bloquean los rangos privados completos**: la cámara legítima vive en `192.168.1.9`.
Bloquear `192.168.0.0/16` rompería el caso de uso.

---

## 11. Configuración

### Resolución del archivo (`load_config`)

```
1. ruta pasada por argumento (relativa a RAIZ o absoluta)
2. <RAIZ>/config.env
3. <RAIZ>/config.example
4. defaults internos (nada más)
```

`RAIZ = Path(__file__).resolve().parents[2]` → la raíz del proyecto.

### Parser de `config.env`

- Formato `CLAVE=VALOR`, sin comillas obligatorias.
- Líneas vacías y que empiezan con `#` se ignoran.
- **`#` solo comenta** si está al inicio del valor o precedido de espacio (I-009). Así
  `http://host/video#alt` no se trunca.
- Valores entrecomillados se respetan tal cual.
- Tipos: `_f` float, `_i` int redondeado, `_b` bool (`1|true|si|sí|yes|on`), `_s` string.

### Clamping en carga

`load_config` **nunca falla**: cada valor se recorta a un rango seguro.

| Clave | Clamp |
|---|---|
| `ANCHO_PROCESO` / `ALTO_PROCESO` | ≥ 64 |
| `FPS_TARGET` | 1.0 – 60.0 |
| `JPEG_CALIDAD` | 20 – 100 |
| `BORDE_GROSOR` | ≥ 2 |
| `ANCHO_SALIDA` | ≥ 0 (`0` = original) |
| `AREA_MAXIMA_REL` | 0.05 – 0.99 |
| `ASPECTO_MAX` | ≥ 1.5 |
| `CORREDOR_Y_MIN` / `CORREDOR_X_MIN` | 0.0 – 0.95 |
| `CORREDOR_X_MAX` | 0.05 – 1.0 |
| `VEL_Y_CRITICA` | ≥ 1.0 |
| `LOOM_CRITICO` | ≥ 0.01 |
| `TTC_CRITICO` | ≥ 0.1 |
| `TAU_EMA` | ≥ 0.01 |
| `WATCHDOG_MS` | ≥ 200 |
| `MODO` | `camara` \| `demo` \| `detenido` |

### `Config.url_mjpeg`

Acepta la base del teléfono (`http://192.168.1.9:8080`) o la URL completa del stream
(`…/video`, `…/videofeed`, `*.mjpg`, `*.mjpeg`) y devuelve siempre una URL MJPEG válida.

### Configuración en caliente vs persistente

| | `/api/config` | `config.env` |
|---|---|---|
| Alcance | Memoria del proceso | Archivo |
| Persiste al reiniciar | ❌ | ✅ |
| Valida invariantes | ✅ (400) | ✅ (clamp silencioso) |
| Requiere reinicio | ❌ | ✅ |
| Pasa por CAS atómico | ✅ | — |

`POST /api/config` **no escribe** `config.env` (decisión de seguridad y simplicidad: un error de
tipeo no deja el servicio sin arrancar).

---

## 12. Tests

```bash
cd ~/vigia-caminante-unoq
python3 -m unittest discover -s tests -v
# Ran 38 tests … OK
```

### Estructura

| Archivo | Tests | Cobertura |
|---|---|---|
| `tests/test_riesgo.py` | 9 | Histéresis, tiempo mínimo en rojo, bandas muertas, score cercano/lejano/monótono, tracking (ID, separación, caducidad) |
| `tests/test_vision_sintetica.py` | 6 | Demo alcanza GREEN/AMBER/RED y vuelve; escena vacía; HTTP real en puerto efímero; UI estática; `POST /api/config` |
| `tests/test_correcciones_auditoria.py` | 17 | Objeto que llena el cuadro, frame vacío, SSRF, validaciones, JPEG no vacío, copia del frame, `#` en URLs |
| `tests/test_i013_atomicidad.py` | 6 | Concurrencia con `threading.Barrier`, CAS de ajustes, validador bajo candado, rechazo sin mutación |
| **Total** | **38** | — |

### Patrones usados

| Patrón | Dónde | Por qué |
|---|---|---|
| Servidor real en puerto `0` (efímero) | `test_vision_sintetica` | Prueba el HTTP de verdad sin chocar con 8030 |
| `replace(load_config(), puerto=0, host="127.0.0.1")` | ídem | Config modificada sin tocar el archivo |
| `threading.Barrier(N)` | `test_i013_atomicidad` | Fuerza concurrencia real, sin `sleep` arbitrarios |
| Parcheo de `set_ajustes` para que lance | `test_i013_atomicidad` | Garantiza que la ruta HTTP **no** usa el setter viejo |
| `acquire(blocking=False)` dentro del validador | `test_i013_atomicidad` | Demuestra que el validador corre bajo `_lock_config` |
| Frames sintéticos con `DemoThread._generar_frame(t)` | `test_vision_sintetica` | Reproducible, sin teléfono ni red |

### Correr un test puntual

```bash
python3 -m unittest tests.test_riesgo.TestHisteresis.test_min_tiempo_rojo -v
python3 -m unittest discover -s tests -p "test_correcciones*" -v
```

### Requisitos

Los tests necesitan `cv2` y `numpy`, por eso se corren **en la placa**. No hay mocks de OpenCV:
se prueba el pipeline real sobre imágenes sintéticas.

---

## 13. Despliegue

### `scripts/desplegar.sh`

```bash
./scripts/desplegar.sh                 # copia + config.env + restart
./scripts/desplegar.sh --sin-arranque  # copia sin reiniciar
```

| Variable | Default | Uso |
|---|---|---|
| `UNOQ_LLAVE` | `~/.ssh/unoq_key` | Clave SSH |
| `UNOQ_USER` | `arduino` | Usuario remoto |
| `UNOQ_HOST` | `192.168.1.5` | IP de la placa |
| `UNOQ_SSH_PORT` | `2222` | Puerto SSH |
| `UNOQ_DIR` | `/home/arduino/vigia-caminante-unoq` | Destino |
| `CAM_URL` | `http://192.168.1.9:8080/video` | Se escribe en `config.env` |
| `PS_PORT` | `8030` | Se escribe en `config.env` |

Qué hace, en orden:

1. `mkdir -p` del destino.
2. `rm -rf src web tests scripts` en el destino (limpia solo lo versionado).
3. `tar -czf -` con exclusiones (`.git`, `data`, `config.env`, `__pycache__`, `*.pyc`, `*.log`,
   `PLAN.md`, `AUDIT_REPORT.md`, `.venv`) y `tar -xzf -` por SSH.
4. Crea `data/`, copia `config.example` → `config.env` si no existe, ajusta `CAM_URL` y `PS_PORT`
   con `sed`, y hace `chmod +x scripts/*.sh`.
5. Si no es `--sin-arranque`: `boot-enable` + `restart`.
6. Imprime `servicio.sh status`.

**No usa `rsync`**: la placa no lo tiene instalado y no se pueden instalar paquetes.

### `scripts/servicio.sh`

| Comando | Efecto |
|---|---|
| `start` | `setsid nohup python3 -u src/main.py >> data/vigia.stdout.log 2>&1 &`; espera el PID hasta 12 s; espera `/health` hasta 12 s |
| `stop` | `SIGTERM`, espera 10 s, `SIGKILL` si no muere |
| `restart` | `stop` + `start` |
| `status` | PID, `etime`, `%cpu`, RSS, `/health`, nivel/score/fps, estado de cron |
| `logs [N\|-f]` | `tail -n N` (60 por defecto) o `tail -f` |
| `boot-enable` | Agrega `@reboot sleep 20; .../servicio.sh start` al crontab (solo esa línea) |
| `boot-disable` | Quita **todas** las líneas del Vigía: `@reboot` + guardián de proceso + guardián de salud |

Detalles finos:

- **No usa `$!`**: `setsid` puede bifurcar y dejar ese PID muerto. Espera a que el proceso Python
  escriba `data/vigia.pid`.
- **Rotación manual antes de arrancar**: 5 MB, 2 backups (alineado con `backupCount=2` del
  `RotatingFileHandler`, I-012).
- **Puerto leído de `config.env`** con `sed` (no hardcodeado).
- **`boot-enable` es idempotente**: usa una marca (`vigia-caminante-unoq/scripts/servicio.sh`) y
  filtra la línea previa antes de agregar. **No agrega los guardianes**: esos viven en el crontab
  y se instalaron aparte (`*/2` llama a `start`; `*/10` consulta `/health` y hace `restart`).
- **`boot-disable` borra las tres líneas** porque filtra por la misma marca; deja intactas las
  líneas de los otros servicios.
- **Retardo de 20 s** en `@reboot` para que WiFi y el sistema de archivos estén listos.
- **El guardián de salud hardcodea el puerto 8030** (`curl http://127.0.0.1:8030/health`). Si se
  cambia `PS_PORT`, hay que editar el crontab; el script no lo actualiza.
- Operación y diagnóstico en la placa: [`servicio.md`](servicio.md).

### Verificación post-despliegue

```bash
ssh -i ~/.ssh/unoq_key -p 2222 arduino@192.168.1.5 \
  'cd ~/vigia-caminante-unoq && python3 -m unittest discover -s tests 2>&1 | tail -3 && ./scripts/servicio.sh status'
```

---

## 14. Decisiones de rendimiento

Medido en la placa: 10.3–14 fps, latencia 36–122 ms (mediana ≈ 55 ms), RSS 190–230 MB,
CPU 120–155 % de 400 %.

| Decisión | Alternativa descartada | Por qué |
|---|---|---|
| `INTER_LINEAR` al reducir para visión | `INTER_AREA` | En el Cortex-A53: **14 ms vs 80 ms**. El blur 5×5 posterior compensa el aliasing |
| `INTER_AREA` al reducir para mostrar | `INTER_LINEAR` | Se hace 1 vez por frame y la calidad visual importa; `ANCHO_SALIDA=540` ya reduce el costo |
| `ANCHO_PROCESO=320` | 640 | MOG2 + morfología cuestan O(píxeles): 320 es 4× menos trabajo que 640 y sigue detectando personas a 3 m |
| `ANCHO_SALIDA=540` | 1080 | Codificar 1080×1920 cuesta ~75 ms/frame; a 540 px ~25 ms. En un celular se ve igual |
| `JPEG_CALIDAD=70` | 90 | Mitad de bytes, sin artefactos visibles en movimiento; baja latencia de red |
| MOG2 en vez de deep learning | YOLO / MobileNet | Sin GPU ni acelerador NN, sin pesos en disco (768 MB libres), y **explicable en el stand** |
| Procesar solo frames nuevos (`version`) | Procesar el último siempre | Evita trabajo duplicado cuando la cámara va más rápida que `fps_target` |
| `/stream` con `Condition.wait` | `sleep` + reenviar | Sin busy-wait; cada cliente cuesta casi cero CPU cuando no hay frames nuevos |
| Estado con 3 candados | Un lock global | El HTTP puede leer mientras la visión escribe; no se serializa el pipeline |
| `RotatingFileHandler` 5 MB × 2 | Log sin límite | El disco raíz tiene ~768 MB: un log descontrolado lo llena |
| `daemon_threads=True` | Hilos no daemon | Al cerrar el proceso no quedan clientes colgados bloqueando la salida |

### Perfil de costo por frame (aproximado, 1080×1920 → 320×569)

| Etapa | Costo |
|---|---|
| Captura + decodificación MJPEG | fuera del hilo de visión (red) |
| `resize` 320 px | ~14 ms |
| gris + blur | ~3 ms |
| MOG2 + morfología + contornos | ~15 ms |
| tracker + scores | < 1 ms |
| `resize` salida a 540 px | ~8 ms |
| overlay (borde, cajas, HUD) | ~3 ms |
| `imencode` JPEG q70 | ~12 ms |
| **Total** | **~55 ms** |

---

## 15. Correcciones de auditoría

Tres ciclos, 13 hallazgos, todos corregidos. Los que cambian el diseño (no solo el código):

### I-001 (CRÍTICO) — Falso negativo en colisión

**Antes**: `area_rel > 0.7` → descartar el contorno. Un objeto que llena el cuadro —el caso de
choque inminente— se ignoraba y el sistema quedaba **verde** durante el accidente.

**Ahora**:

| Lugar | Cambio |
|---|---|
| `config.py` | `area_maxima_rel` default `0.95` (clamp 0.05–0.99) |
| `vision._filtrar_detecciones` | **No hay filtro por área máxima** |
| `vision._score_individual` | `area_rel >= area_maxima_rel` → `return 1.0` |
| `vision._riesgo_global` | `score_crudo >= 1.0` → `score = 1.0` (bypass del EMA) |
| `vision._calentamiento` | 5 frames de warm-up para que MOG2 no dispare el blob inicial |

Tests: `test_objeto_que_llena_el_cuadro_es_amenaza_maxima`,
`test_filtro_no_descarta_contorno_gigante`, `test_pipeline_completo_frame_lleno_va_a_red`,
`test_arranque_no_dispara_falsa_alarma`.

### I-002 / I-003 — Divisiones por cero y watchdog apagable

| Hallazgo | Fix en API | Fix en profundidad |
|---|---|---|
| `loom_critico=0` → `ZeroDivisionError` | 400 si `<= 1e-3` | `max(loom_critico, 1e-6)` en `_score_individual` |
| `watchdog_ms=0` → `NO_SIGNAL` permanente | 400 si `< 200` | `max(200.0, …)` en `_bucle_watchdog` y en `load_config` |

### I-004 (MAYOR) — SSRF

`es_url_valida` bloquea loopback, no especificadas, link-local, nombres de metadata y la IP
propia. **No bloquea rangos privados enteros** porque la cámara vive en `192.168.1.9`. El bloqueo
se aplica tanto en `POST /api/config` como en `CamaraThread.run` (defensa en profundidad).

### I-005 (MAYOR) — Validación de umbrales

`_validar_ajustes` valida el diccionario **fusionado** (vigentes + lote) contra todas las
invariantes. Un lote con un solo valor inválido se rechaza completo con 400. No hay aplicación
parcial.

### I-013 (MAYOR) — Atomicidad de la configuración

**Problema**: dos `POST` concurrentes leían la misma base, validaban por separado y escribían
umbrales incoherentes (`rojo_in=0.60` de A, `rojo_out=0.65` de B).

**Solución**: `Estado.actualizar_ajustes_si(nuevos, validador)`:

```python
def actualizar_ajustes_si(self, nuevos, validador):
    with self._lock_config:               # un único candado para todo
        fusionado = dict(self._ajustes)
        fusionado.update(nuevos)
        error = validador(fusionado)      # corre DENTRO del candado
        if error is not None:
            return error
        self._ajustes.update(nuevos)
        return None
```

Restricciones que esto impone (y que están documentadas en el docstring):

1. El validador **no puede** llamar a `get_ajustes`/`set_ajustes`/`actualizar_ajustes_si`
   (`Lock` no reentrante → deadlock).
2. El validador **no debe** tomar `_lock_estado` (rompería el orden `_lock_config` →
   `_lock_estado`).
3. `_validar_ajustes` parte de los defaults de `Config` y sobrescribe con el diccionario recibido;
   no consulta el estado compartido.

Tests: `test_post_concurrentes_cruzados_no_dejan_umbrales_incoherentes` (8 hilos con
`Barrier`), `test_cas_rechaza_lote_validado_contra_una_base_ya_cambiada`,
`test_aplicar_config_escribe_por_compare_and_set`,
`test_validador_se_ejecuta_dentro_del_candado_de_config`, `test_rechazo_no_muta_los_ajustes`.

### I-006 a I-012 (menores, igual de importantes)

| ID | Problema | Fix |
|---|---|---|
| I-006 | `_encode_jpeg` devolvía `b""` → stream roto | Cache `_ultimo_jpeg_valido` + `_JPEG_MINIMO` embebido |
| I-007 | Lector MJPEG reconectaba con frames partidos | Acumula chunks hasta 2 MB o timeout buscando `\r\n--` |
| I-008 | `renderizar` mutaba el frame compartido con `ANCHO_SALIDA=0` | `_reducir_salida` devuelve `frame.copy()` |
| I-009 | `#` truncaba URLs en `config.env` | Solo comenta al inicio del valor o tras espacio |
| I-010 | Frame vacío → `ZeroDivisionError` | `procesar` devuelve `NO_SIGNAL`; `_filtrar` devuelve `[]` si `area_frame <= 0` |
| I-011 | Watchdog pisaba el estado fresco (flicker) | `reemplazar_estado_si` (CAS) + chequeo de `edad_pub_ms` |
| I-012 | Un solo backup de log rotado | 2 backups, alineado con `RotatingFileHandler` |

---

## 16. Cómo extender

### Agregar un parámetro de configuración

1. **`config.example`**: agregar `MI_CLAVE=valor` con comentario.
2. **`config.py`**: agregar campo al dataclass `Config` y su parseo en `load_config`
   (`_f`/`_i`/`_b`/`_s`, con clamp).
3. **`servidor.py`**: si debe cambiarse en caliente, agregarlo a `CLAVES_AJUSTABLES` y a
   `_validar_ajustes`.
4. **Consumidor**: pasar a usar `getattr(config, "mi_clave")` o `self._p("mi_clave")` en el modelo.
5. **Test**: cubrir el default y el rechazo del valor inválido.

### Agregar un término al score

1. Elegir el peso y **renormalizar** la suma de pesos a 1.0 (o documentar que no suma 1).
2. Calcular el peso en `_score_individual`, entre 0 y 1.
3. Sumarlo a `score` con `self.config.peso_nuevo`.
4. Actualizar `PESO_*` en `config.example` y la tabla de [`README.md`](../README.md).
5. Test de monotonía: el score debe crecer cuando el fenómeno crece.

### Agregar un endpoint

1. En `VigiaHandler.do_GET`, añadir el `elif camino == "/api/nuevo"`.
2. Implementar `_serve_api_nuevo` usando `self.estado` y `self.config`.
3. Responder con `self._responder_json(...)` (ya pone `Content-Type`, `Content-Length` y
   `Cache-Control: no-store`).
4. Documentarlo en [`datasheet.md`](datasheet.md) y en la sección 10 de este archivo.

### Cambiar la fuente de video

`CamaraThread` solo necesita que `estado.get_camara_url()` devuelva algo que
`cv2.VideoCapture` pueda abrir. Para una webcam USB local:

```python
# en config.env
CAM_URL=http://192.168.1.9:8080   # se ignora si el modo es otro
```

Y en `camara.py`, en `_conectar_cv2`, admitir índices numéricos (`0`, `1`) para
`cv2.VideoCapture(int(...))`. Es un cambio de ~5 líneas. Ojo: `es_url_valida` rechazaría `"0"`,
así que hay que exceptuar ese caso explícitamente.

### Agregar otra salida de alerta (relé, LED, buzzer)

No lo hagas en el hilo de visión: bloquearía el pipeline. Patrón recomendado:

1. Un hilo nuevo que lea `estado.get_estado()` cada 100 ms.
2. Que actúe solo en los **cambios de nivel** (guardar el anterior).
3. Que tenga su propio `Event` de parada y se registre en `hilos` de `main.py`.
4. Nunca tocar GPIO desde Python sin verificar antes qué pines están libres y a qué voltaje.

### Agregar detección de personas (deep learning)

Posible pero costoso: no hay GPU, hay ~768 MB libres y `cv2.dnn` necesitaría descargar pesos (no
se puede, y no se debe, sin autorización). Si algún día se hace, el punto de inserción es
`_detectar_movimiento`: reemplazar contornos por cajas del modelo, dejando el resto del pipeline
(tracker, score, histéresis, overlay) intacto. El score seguiría siendo el mismo.

### Cambiar la UI

`web/` es HTML/CSS/JS plano. Sin build, sin npm. Reglas:

- El nivel se refleja en `body.dataset.nivel` (`GREEN`/`AMBER`/`RED`/`NO_SIGNAL`).
- `style.css` define `--nivel` por atributo: cambiar ahí y se propaga a badge, borde y tarjetas.
- `app.js` hace polling a `/api/estado` cada 400 ms; si agregás campos, no hace falta tocar el
  backend.

---

## 17. Notas sobre Zephyr y diferencias con AVR

Este proyecto **no toca el MCU**. Aun así, conviene dejar claro el modelo mental de la placa,
porque es la fuente de confusión más común.

### Dos computadoras en una placa

```
┌──────────────────────────────────────────────────────────────┐
│                       ARDUINO UNO Q                          │
│                                                              │
│  ┌────────────────────────┐      ┌─────────────────────────┐ │
│  │  QCC711 (MPU)          │      │  STM32U585 (MCU)        │ │
│  │  4× aarch64            │◄────►│  Cortex-M33 + TrustZone │ │
│  │  Debian 13             │ IPC  │  Zephyr RTOS            │ │
│  │  Python 3.13, OpenCV   │      │  Firmware .ino/.elf     │ │
│  │                        │      │                         │ │
│  │  ◄── ESTE PROYECTO     │      │  (no usado)             │ │
│  └────────────────────────┘      └─────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
```

| Aspecto | Lado Linux (QCC711) | Lado MCU (STM32U585) |
|---|---|---|
| Runtime | Debian 13 | Zephyr RTOS |
| Lenguaje | Python 3.13 | C/C++ (Arduino) |
| Se programa con | `ssh` + `tar` | `arduino-cli` + ADB/OpenOCD |
| Tiene red IP | Sí (WiFi) | No directamente |
| Tiene OpenCV | Sí | No |
| Se usa aquí | ✅ Todo | ❌ Nada |

### Diferencias con el Arduino Uno clásico

| Aspecto | Uno clásico (R3) | UNO Q |
|---|---|---|
| CPU | ATmega328P, 8 bits, 16 MHz | QCC711 4× aarch64 + STM32U585 |
| RAM | 2 KB | 3.6 GB (MPU) |
| Flash | 32 KB | 9.8 GB (raíz) |
| ADC | 10 bits (0–1023) | 12 bits (0–4095) |
| Lógica | **5 V** | **3.3 V** → level shifters para 5 V |
| Runtime | Bare metal | Linux + Zephyr |
| Librerías AVR | Compatibles | **No compatibles** |
| `delay()` | Bloquea el único hilo | No aplica: hay hilos y `Event.wait` |
| `setup()`/`loop()` | Obligatorios | No existen en Python |
| Carga de código | `arduino-cli upload` | `tar`+`ssh` (servicio) / ADB+OpenOCD (firmware) |

### Consecuencias prácticas para el código

- **No hay `delay()`**: se usa `Event.wait(t)` o `Condition.wait(t)`, que son interrumpibles.
- **No hay `millis()`**: se usa `time.time()` (epoch) para timestamps y `time.perf_counter()`
  para medir duraciones.
- **No hay `micros()` ni ISR**: la concurrencia se resuelve con hilos y candados, no con
  interrupciones.
- **Memoria abundante pero no infinita**: 3.6 GB alcanzan, pero el frame de 1080×1920 en BGR
  ocupa ~6 MB; por eso se procesa a 320 px y se publica a 540 px.
- **El disco es el recurso escaso** (~768 MB libres): por eso no hay dependencias pesadas, ni
  pesos de modelos, ni venv, ni logs sin rotar.

---

## 18. Deuda técnica y próximos pasos

| Tema | Estado | Nota |
|---|---|---|
| Persistencia de ajustes | ❌ | `POST /api/config` solo cambia memoria; para persistir hay que editar `config.env` |
| Autenticación | ❌ | La API es abierta en la LAN. No exponer a internet |
| TLS | ❌ | HTTP plano. El MJPEG es visible para cualquiera en la red |
| Historial de eventos | ❌ | No se guardan transiciones de nivel ni snapshots |
| Grabación de video | ❌ | Solo stream en vivo |
| Detección de personas (clases) | ❌ | Solo blobs en movimiento; "objeto" no es "peatón" |
| Múltiples cámaras | ❌ | Una sola fuente por proceso (`camara_url`) |
| Métricas Prometheus | ❌ | Solo `/api/estado` y logs |
| Test de carga con N clientes | ❌ | No se mide el impacto de muchos `/stream` simultáneos |
| Cobertura medida | ❌ | No se corre `coverage` (no está instalado, no se instala) |
| Rotación automática de `config.env` | ❌ | Cambios manuales, sin historial |
| Detección de cámara caída con alerta activa | ⚠️ Parcial | Gris es pasivo; no hay sonido ni parpadeo |

### Ideas de bajo costo y alto impacto

1. **Endpoint `/api/metricas`** con contadores (frames procesados, transiciones, reconexiones):
   ~20 líneas, sin dependencias.
2. **Persistir ajustes** en `data/ajustes.json` al aceptar un POST y leerlos al arrancar:
   resuelve el "se me olvidó la calibración al reiniciar".
3. **Sello de tiempo en el overlay**: ayuda a diagnosticar cortes de red en video.
4. **Doble cámara** (frontal + trasera del teléfono) como dos `camara_url` y un selector.
5. **Modo "solo alerta"**: página mínima sin métricas, para un segundo celular montado en el
   manubrio.

---

<p align="center">
  <strong>Vigía Caminante UNO Q v1.0.0</strong><br>
  Guía del desarrollador · Python 3.13 · OpenCV 4.10 · Debian 13 aarch64<br>
  <em>Cinco hilos, tres candados, una fórmula y ningún modelo entrenado.</em>
</p>
