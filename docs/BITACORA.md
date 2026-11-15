# 🧭 Bitácora — Vigía Caminante UNO Q

Registro de lo que pasó mientras se construyó este proyecto: qué se rompió, qué se descartó y
por qué, y qué conviene recordar la próxima vez que se enfrente algo parecido.

**Versión final**: 1.0.0 · **Estado**: aprobado tras 3 ciclos de auditoría, 13 hallazgos
corregidos, 38 tests en verde, corriendo en la placa.

> Este documento no es parte del producto. Es memoria de ingeniería: sirve para no repetir
> errores y para que la próxima persona entienda por qué el código está como está.

---

## Índice

1. [Punto de partida y restricciones](#1-punto-de-partida-y-restricciones)
2. [Línea de tiempo](#2-línea-de-tiempo)
3. [Problemas encontrados y cómo se resolvieron](#3-problemas-encontrados-y-cómo-se-resolvieron)
4. [Vías descartadas y por qué](#4-vías-descartadas-y-por-qué)
5. [Decisiones técnicas y su justificación](#5-decisiones-técnicas-y-su-justificación)
6. [Lo que casi sale mal (y cómo se detectó)](#6-lo-que-casi-sale-mal-y-cómo-se-detectó)
7. [Lecciones aprendidas](#7-lecciones-aprendidas)
8. [Checklist para el próximo proyecto](#8-checklist-para-el-próximo-proyecto)
9. [Números finales](#9-números-finales)

---

## 1. Punto de partida y restricciones

El proyecto nació con un objetivo acotado: **un semáforo de riesgo de colisión sobre la Arduino
UNO Q**, alimentado por el MJPEG de un teléfono Android.

Las restricciones no fueron negociables y condicionaron casi todas las decisiones posteriores:

| Restricción | Origen | Consecuencia |
|---|---|---|
| **Sin `pip`** y sin instalar paquetes | Disco raíz con ~768 MB libres; OpenCV ya viene compilado para aarch64 | Solo `stdlib` + `cv2` + `numpy` |
| **Sin `sudo` sin contraseña** | Configuración del usuario `arduino` en la placa | Nada de `systemd` de sistema, ni `/etc`, ni reglas udev |
| **Sin `systemd` de sistema** para el servicio | Consecuencia de lo anterior | Arranque por `cron @reboot` + script propio |
| **Sin GPU ni acelerador NN** | Hardware | Visión clásica en CPU, no deep learning |
| **Puerto 8030** (8000/8010/8020 ocupados) | Otros tres servicios en la misma placa | Un solo puerto, sin tocar a los vecinos |
| **Todo el procesamiento en la placa** | Requisito de diseño | El navegador solo muestra JPEG ya anotados |
| **Frame de entrada vertical 1080×1920** | IP Webcam en un teléfono en la mano | Coordenadas normalizadas para no depender del tamaño |

### Lo que ya estaba resuelto en la placa

- `python3` 3.13.5, `cv2` 4.10.0, `numpy` 2.2.4.
- SSH en el puerto 2222 con la clave `~/.ssh/unoq_key`.
- Red con IP fija `192.168.1.5`.

Eso permitió empezar directo a programar, sin provisioning.

---

## 2. Línea de tiempo

| Fase | Qué pasó | Resultado |
|---|---|---|
| **Diseño** | Se fijó el pipeline: MOG2 → contornos → tracker IoU → score heurístico → histéresis → overlay → JPEG | `PLAN.md` con fórmulas y umbrales |
| **Implementación** | 8 módulos Python, 4 archivos web, 2 scripts bash, 2 archivos de test | Servicio funcionando en la placa |
| **Auditoría ciclo 1** | Veredicto: **INCIDENCIAS**. 12 hallazgos (3 críticos) | Plan de correcciones I-001…I-012 |
| **Corrección ciclo 1** | Se corrigieron los 12 y se agregaron tests de regresión | 32 tests en verde |
| **Auditoría ciclo 2** | I-001…I-012 verificados; aparece **I-013** (atomicidad de configuración) | 1 hallazgo mayor nuevo |
| **Corrección ciclo 2** | `actualizar_ajustes_si` (CAS) + 6 tests deterministas con `Barrier` | 38 tests en verde |
| **Auditoría ciclo 3** | Veredicto: **APROBADO — SIN INCIDENCIAS** | Listo para documentar y publicar |

### Métricas de la versión final

| Métrica | Valor |
|---|---|
| FPS de proceso | 10.3 – 14.0 |
| Latencia | 36 – 122 ms (mediana ≈ 55 ms) |
| RSS | 190 – 230 MB |
| CPU | 120 – 155 % de 400 % |
| Tamaño del proyecto | < 1 MB (sin logs) |
| Tests | 38 OK |

---

## 3. Problemas encontrados y cómo se resolvieron

### 3.1 El falso negativo que motivó todo (I-001)

**Síntoma**: un objeto que llenaba el cuadro no producía rojo. El sistema quedaba verde en el
peor caso posible.

**Causa**: el filtro de contornos descartaba `area_rel > 0.7` para evitar falsos positivos por
sombras grandes. Razonable en abstracto, letal en la práctica: **el objeto que llena el cuadro es
exactamente el que está por chocar**.

**Por qué no se detectó al principio**: los tests usaban objetos de tamaño "normal"
(`area_rel` 0.01–0.15). Nadie probó el caso degenerado. El bug vivía justo en el borde del
dominio que los tests no cubrían.

**Solución, en cuatro capas**:

1. `area_maxima_rel` pasó de `0.7` a **`0.95`** y cambió de significado: ya no es un filtro, es
   un umbral de "amenaza máxima".
2. `_filtrar_detecciones` **dejó de descartar** por tamaño máximo. Solo filtra área mínima y
   aspecto.
3. `_score_individual` devuelve **`1.0`** si `area_rel >= area_maxima_rel`.
4. `_riesgo_global` **no suaviza** un score de `1.0`: se publica rojo en el mismo frame, sin
   esperar al EMA.

**Efecto colateral que hubo que resolver**: MOG2 devuelve **todo el cuadro** como foreground en
sus primeras aplicaciones. Con la nueva regla, el arranque daba rojo siempre. Se agregó un
calentamiento de **5 frames** (`max(2, min(6, bg_history // 40))`) durante el cual se publica
`NO_SIGNAL` con el mensaje `"Calibrando fondo de escena"`. Se silencia el artefacto de arranque
del modelo, no el riesgo real.

**Test que lo fija para siempre**: un frame sintético cubierto casi por completo debe dar `RED`, y
el arranque no debe dar falsa alarma.

**Lección**: los filtros "anti falso positivo" son peligrosos cuando el falso negativo es
catastrófico. Hay que preguntarse siempre: *¿qué caso del mundo real cae de este lado del
filtro?*

### 3.2 Divisiones por cero alcanzables por API (I-002 / I-003)

**Síntoma**: `POST /api/config` con `loom_critico=0` producía `ZeroDivisionError` en el hilo de
visión en cada frame. Con `watchdog_ms=0`, el servicio entraba en `NO_SIGNAL` permanente.

**Causa**: los ajustes llegaban desde la red sin validación de rangos y se usaban como
denominadores.

**Solución en dos capas** (deliberadamente redundante):

| Capa | `loom_critico` | `watchdog_ms` |
|---|---|---|
| API | 400 si `<= 1e-3` | 400 si `< 200` |
| Código | `max(loom_critico, 1e-6)` | `max(200.0, …)` en `load_config` y en el bucle |

**Por qué dos capas**: la API protege el camino HTTP, pero el valor también puede venir de
`config.env` editado a mano. La defensa en el punto de uso cubre ambos caminos.

**Lección**: todo valor que llega de fuera y se usa como divisor necesita un piso en el lugar
donde se usa, no solo en la puerta de entrada.

### 3.3 SSRF por la URL de la cámara (I-004)

**Síntoma**: `POST /api/config` aceptaba cualquier URL, incluida `http://127.0.0.1:8000/`. Eso
permitía usar la placa para golpear sus propios servicios (Detector de tarjetas, SuperFoto,
Pasillo Seguro) o el endpoint de metadata de un cloud.

**Solución**: `es_url_valida()` rechaza:

- loopback (`127.0.0.0/8`, `::1`, `localhost`);
- direcciones no especificadas (`0.0.0.0`, `::`);
- link-local (`169.254.0.0/16`, incluida `169.254.169.254`);
- la **IP propia** de la placa (constante `192.168.1.5` + las que resuelva el sistema);
- nombres de metadata conocidos.

**Lo que deliberadamente NO se bloqueó**: los rangos privados completos. La cámara legítima vive
en `192.168.1.9`; bloquear `192.168.0.0/16` habría roto el caso de uso. Es un ejemplo de que la
regla de seguridad correcta depende del contexto, no de una lista genérica.

**Lección**: al aplicar una mitigación, escribir también qué **no** se bloquea y por qué. Esa
nota evita que alguien "endurezca" la regla y rompa el producto.

### 3.4 El JPEG vacío que cortaba el stream (I-006)

**Síntoma**: esporádicamente, todos los navegadores dejaban de ver video. El `Content-Length: 0`
en una parte del multipart hace que el parser del navegador aborte la conexión.

**Causa**: `cv2.imencode` puede fallar (frame corrupto, memoria) y devolvía `b""`, que se
publicaba tal cual.

**Solución en tres niveles**:

1. `_encode_jpeg` valida `ok`, `buffer is not None` y `buffer.size > 0`.
2. Si falla, se reenvía el **último JPEG válido** (cache `_ultimo_jpeg_valido`).
3. Si nunca hubo uno, se envía `_JPEG_MINIMO`: un JPEG 16×16 gris embebido en base64 en el módulo.

**Lección**: en un protocolo de streaming, "cero bytes" no es un valor neutro: es un error de
protocolo. Hay que garantizar un byte válido siempre.

### 3.5 Frames MJPEG partidos (I-007)

**Síntoma**: con el lector propio (fallback cuando FFMPEG falla), algunas partes llegaban
cortadas y el lector reconectaba, perdiendo segundos de video.

**Causa**: el lector asumía que cada `read()` devolvía la parte completa.

**Solución**: si no hay `Content-Length`, se acumulan chunks (tope `_MAX_PARTE = 2 MB`) hasta
encontrar `\r\n--` o vencer el timeout. Nunca se reconecta por un frame partido.

**Lección**: los protocolos de streaming no garantizan límites de mensaje en los `read()`. Cualquier
parser tiene que ser incremental.

### 3.6 El watchdog que apagaba el video (I-011)

**Síntoma**: parpadeos de gris de un frame, intermitentes, con la cámara funcionando bien.

**Causa**: lectura-modificación-escritura clásica. El watchdog leía el estado, decidía "viejo",
y escribía `NO_SIGNAL` justo después de que la visión publicara un estado fresco. El estado fresco
se perdía.

**Solución**: `Estado.reemplazar_estado_si(fn)` hace la decisión **y** la escritura dentro del
mismo `with _lock_estado`. Además, la función de mutación aborta si la visión publicó dentro de la
ventana del watchdog (`edad_pub_ms <= watchdog_ms`).

**Lección**: si dos hilos escriben el mismo dato, "leer, decidir, escribir" es un bug esperando
ocurrir. Hace falta compare-and-set.

### 3.7 Atomicidad de la configuración (I-013)

**Síntoma**: dos `POST /api/config` concurrentes podían dejar `umbral_rojo_in = 0.60` y
`umbral_rojo_out = 0.65`, violando la invariante `rojo_out < rojo_in` que la validación de I-005
supuestamente garantizaba.

**Causa**: la secuencia era `get_ajustes()` → `_validar_ajustes()` → `set_ajustes()`. Dos hilos
leían la misma base, validaban contra ella y escribían por separado. Cada uno era válido; el
resultado combinado, no.

**Solución**: `Estado.actualizar_ajustes_si(nuevos, validador)` hace leer → fusionar → validar →
escribir bajo **un único** `_lock_config`. El segundo POST valida contra el resultado del primero.

**Restricciones que impuso** (y que están en el docstring porque son trampas):

1. El validador no puede llamar a `get_ajustes`/`set_ajustes` — `Lock` no reentrante → deadlock.
2. El validador no puede tomar `_lock_estado` — rompería el orden `_lock_config` → `_lock_estado`.
3. `_validar_ajustes` tuvo que refactorizarse para recibir el diccionario ya fusionado.

**Test determinista**: 8 hilos con `threading.Barrier` mandando lotes cruzados
(`rojo_in=0.60` / `rojo_out=0.65`). Sin `sleep` arbitrarios: la barrera garantiza concurrencia
real. Al final se verifica la invariante completa.

**Lección**: validar y escribir son **una sola operación**. Si hay un `lock` entre medio, no es
atómico, aunque lo parezca.

### 3.8 `#` que truncaba URLs (I-009)

**Síntoma**: una `CAM_URL` con fragmento (`http://host/video#alt`) se guardaba truncada.

**Causa**: el parser de `config.env` cortaba en el primer `#`, sin distinguir comentario de
contenido.

**Solución**: `#` solo inicia comentario al principio del valor o precedido de espacio. Los valores
entrecomillados se respetan tal cual.

**Lección**: los formatos tipo `.env` son más sutiles de lo que parecen. Cualquier parser propio
necesita tests con URLs, `#`, comillas y espacios.

### 3.9 El frame compartido mutado (I-008)

**Síntoma**: con `ANCHO_SALIDA=0` (resolución original), aparecían cajas y bordes "fantasma"
acumulados en frames posteriores.

**Causa**: `renderizar` dibuja in-place. Con `ancho_salida=0`, `_reducir_salida` devolvía el
**mismo** array que el capturador había publicado, así que se dibujaba sobre el frame compartido.

**Solución**: `_reducir_salida` devuelve `frame.copy()` cuando no hay reducción.

**Lección**: una API que dice "dibuja in-place" necesita que el llamador tenga la copia. Si la
función puede devolver el original, el contrato está roto. Mejor: la función que publica copia
siempre.

### 3.10 `setsid` y el PID que no existe

**Síntoma**: `servicio.sh start` reportaba éxito y el PID guardado estaba muerto.

**Causa**: `$!` después de `setsid nohup …` no es el PID del proceso Python: `setsid` puede
bifurcar.

**Solución**: no confiar en `$!`. El proceso Python escribe `data/vigia.pid` por sí mismo
(`os.getpid()`), y el script espera hasta 12 s a que ese archivo exista y el PID esté vivo.

**Lección**: cuando un wrapper (`setsid`, `nohup`, `timeout`) se interpone, el PID del shell no es
el PID del proceso. Que el proceso se identifique solo.

### 3.11 OpenCV tarda en importar

**Síntoma**: `servicio.sh start` decía "AVISO: /health aún no responde" en los primeros segundos.

**Causa**: importar `cv2` en el Cortex-A53 tarda varios segundos.

**Solución**: el script espera el pidfile hasta 12 s y luego sondea `/health` hasta 30 veces con
0.4 s de espera (12 s más). Suficiente y sin falsos negativos.

**Lección**: en ARM, los tiempos de arranque de librerías nativas son parte del diseño del script
de servicio. No asumir arranque instantáneo.

---

## 4. Vías descartadas y por qué

Estas son las decisiones de "no hacer". Están acá para que nadie las re-intente sin contexto.

### 4.1 Deep learning (YOLO / MobileNet / SSD)

| Ítem | Realidad en la placa |
|---|---|
| GPU | No hay |
| Acelerador NN | No hay |
| Disco libre | ~768 MB (los pesos ocuparían buena parte) |
| Instalación | Prohibida (`pip` no disponible, no se instala nada) |
| CPU | Un detector en CPU a 1080×1920 no llega a 10 fps en 4× A53 |

**Además, el argumento decisivo no es técnico**: en una feria, la pregunta "¿por qué se puso
rojo?" se responde con "está abajo, crece y baja rápido". Con una red neuronal, la respuesta es
"el modelo dijo 0.91". Para este caso de uso, la explicabilidad vale más que la precisión.

**Cuándo sí valdría**: si el objetivo fuera identificar *qué* es el objeto (persona vs. carrito),
no solo *si viene encima*.

### 4.2 `systemd` para el servicio

Descartado por la restricción de `sudo`: el usuario `arduino` no puede instalar unidades de
sistema ni recargar el daemon.

**Reemplazo**: `setsid nohup` + pidfile + `cron @reboot` con `sleep 20`. Menos elegante, cero
permisos, y funciona.

**Costo aceptado**: sin reinicio automático si el proceso muere. Mitigación: el proceso atrapa
todas las excepciones por hilo y no muere por un frame malo.

### 4.3 `rsync` para desplegar

La placa no tiene `rsync` y no se puede instalar.

**Reemplazo**: `tar -czf - | ssh 'tar -xzf -'`. Misma funcionalidad para este caso, sin
dependencias. Se limpian en el destino solo los directorios versionados (`src web tests scripts`)
y se conserva `config.env` y `data/`.

### 4.4 `pip` + entorno virtual

Descartado por disco y por riesgo: instalar `opencv-python` desde PyPI podría reemplazar el
OpenCV del sistema y romper el servicio además de llenar la partición.

**Reemplazo**: usar el `python3` del sistema con `cv2` y `numpy` ya compilados para aarch64.
`requirements.txt` quedó como documento, no como instalador.

### 4.5 Persistir los ajustes desde la API

`POST /api/config` solo cambia memoria. Se evaluó escribir `config.env` desde el handler y se
descartó:

- un error de tipeo persistido deja el servicio sin arrancar en el próximo reinicio;
- escribir archivos desde un handler HTTP abre vectores de path traversal;
- el caso de uso real (calibrar en la feria) no necesita persistencia inmediata.

**Reemplazo**: editar `config.env` y `restart` cuando la calibración está firme.

### 4.6 Dibujar el overlay en el navegador

Se evaluó mandar el video crudo + JSON y dibujar las cajas con `<canvas>` en el cliente.

Descartado porque:

- duplica el trabajo (dos representaciones del mismo frame);
- el `/snapshot.jpg` dejaría de ser representativo;
- un cliente lento mostraría cajas desincronizadas del video;
- el objetivo es que **la placa** haga todo y el navegador sea tonto.

### 4.7 Alertas por audio o vibración del teléfono

Descartado por alcance: la alerta visual (borde de color) es suficiente y no requiere una
segunda integración con el teléfono. El teléfono solo es cámara.

### 4.8 Grabar video o guardar historial

Descartado por disco y por privacidad. El sistema no persiste imágenes de las personas que pasan
por la feria.

### 4.9 Reutilizar el puerto de otro servicio

Los puertos 8000, 8010 y 8020 ya estaban tomados por otros tres proyectos de la misma placa.
Se eligió **8030** y se documentó el motivo, para que nadie lo cambie "porque el 8000 es más
cómodo".

---

## 5. Decisiones técnicas y su justificación

### 5.1 Visión clásica con MOG2

| Alternativa | Por qué no |
|---|---|
| Frame differencing simple | Se pierde con objetos que se detienen; MOG2 mantiene el modelo |
| Optical flow denso | Caro en CPU, y no aporta al problema del TTC |
| Detección de bordes | No distingue fondo de objeto en movimiento |
| Deep learning | Ver 4.1 |

MOG2 (`history=200`, `varThreshold=25`, sin sombras) es el mejor costo/beneficio: aprende el fondo
solo, tolera cambios lentos de luz y es barato.

### 5.2 Analizar a 320 px y mostrar a 540 px

| Configuración | Costo | Calidad |
|---|---|---|
| Analizar 1080 | Inviable (>100 ms solo el resize) | — |
| Analizar 640 | ~4× más caro que 320 | Marginalmente mejor |
| **Analizar 320** | ~14 ms resize + ~15 ms MOG2 | Suficiente para personas a 3 m |
| Mostrar 1080 | ~75 ms de JPEG | Idéntico en un celular |
| **Mostrar 540** | ~25 ms de JPEG | Se ve igual en 6" |

La clave: **analizar y mostrar son dos resoluciones distintas con dos objetivos distintos**. No
hay razón para que coincidan.

### 5.3 `INTER_LINEAR` para visión, `INTER_AREA` para mostrar

Medido en el Cortex-A53:

| Interpolación | Tiempo (1080→320) |
|---|---|
| `INTER_AREA` | ~80 ms |
| `INTER_LINEAR` | ~14 ms |

`INTER_AREA` es la correcta para reducir (promedia, evita aliasing), pero cuesta 5.7× más. Para la
visión, el `GaussianBlur(5,5)` posterior compensa el aliasing y el ahorro de 66 ms por frame es
decisivo. Para la salida visual, se hace **una vez** y la calidad se nota.

### 5.4 Score como máximo y no como promedio

`max(scores)` en lugar de `mean(scores)`.

Tres objetos inofensivos no deben sumar riesgo. Un objeto peligroso debe alcanzar para pintar
rojo. El promedio haría que un grupo de gente caminando diluyera la alerta del que viene de
frente.

### 5.5 Histéresis con cuatro umbrales y tiempo mínimo

Sin histéresis, el borde parpadearía entre dos colores en cada frame (el score oscila
naturalmente). Con histéresis sola, un pico de un frame dejaría el rojo demasiado tiempo. Con el
tiempo mínimo, se evita el "rojo de un frame" que nadie llega a ver.

Resultado: cuatro umbrales (`ambar_in/out`, `rojo_in/out`) + `min_tiempo_rojo_ms = 800`.

### 5.6 Tres candados en lugar de uno

Un lock global habría serializado: visión escribiendo frame → HTTP esperando para servir
`/api/estado`. Con tres candados independientes, el HTTP puede leer el estado mientras la visión
procesa, y el stream espera sobre una `Condition` en lugar de hacer polling.

Costo: hay que respetar el orden `_lock_config` → `_lock_estado`. Documentado en el código y
verificado en la auditoría.

### 5.7 Estados en dataclasses, no en diccionarios

`EstadoFrame` y `ObjetoRiesgo` tipados permiten:

- `a_dict()` con redondeo y normalización de `inf`/`NaN` a `null` en un solo lugar;
- `replace()` para copias y transformaciones sin mutar;
- errores de tipeo detectados al escribir, no en producción.

### 5.8 Logs rotativos de 5 MB × 2

El disco raíz tiene ~768 MB. Un log sin rotar puede llenarlo en una feria de dos días con
reconexiones. `RotatingFileHandler(5 MB, backupCount=2)` acota el total a ~15 MB. El script bash
replica la misma política (I-012) para el log de stdout.

---

## 6. Lo que casi sale mal (y cómo se detectó)

### El bug que los tests no veían

Los primeros tests cubrían objetos de `area_rel` entre 0.01 y 0.15. Todos pasaban. El bug I-001
vivía en `area_rel > 0.7`, un rango que **ningún test tocaba**. No fue un test el que lo encontró:
fue una auditoría que preguntó "¿qué pasa si el objeto llena el cuadro?".

**Conclusión operativa**: los tests cubren lo que el autor imaginó. La auditoría cubre lo que el
autor no imaginó. Hacen falta las dos.

### El `1.0` que el EMA frenaba

Al corregir I-001 con `score = 1.0`, el rojo seguía tardando ~3 frames porque el EMA subía
gradualmente (con `tau_ema=0.3` y ~12 fps, el primer paso solo lleva el score a ~0.4).

Se detectó revisando el pipeline completo, no el test unitario del score. El test verificaba
`_score_individual() == 1.0`, no que el **nivel publicado** fuera rojo.

**Fix**: bypass explícito del suavizado cuando `score_crudo >= 1.0`, y un test de pipeline
completo (`test_pipeline_completo_frame_lleno_va_a_red`) que verifica el nivel final, no el
intermedio.

**Lección**: testear la función no es testear el sistema. Hay que tener al menos un test que vaya
de punta a punta y mire la salida que ve el usuario.

### La validación que no era atómica

I-005 agregó todas las invariantes de umbrales y todos los tests pasaban. I-013 demostró que dos
POST concurrentes las rompían igual, porque la validación y la escritura eran operaciones
separadas.

**Lección**: "validado" no significa "garantizado" si hay concurrencia. La garantía necesita un
punto único de commit.

### El calentamiento que parecía un parche

Cuando se corrigió I-001, el arranque empezó a dar rojo (MOG2 devuelve todo el cuadro como
foreground al inicio). La primera reacción fue "bajemos el score durante los primeros frames",
que es exactamente el tipo de parche que oculta riesgo real.

La solución adoptada fue distinta y explícita: **se descartan solo los frames de calibración del
modelo**, se informa `NO_SIGNAL · "Calibrando fondo de escena"`, y la cantidad se deriva del
tamaño de la historia (`bg_history // 40`, acotado a 2–6). Es un artefacto conocido del
algoritmo, no una zona gris.

**Lección**: si hay que silenciar algo, silenciar la causa identificada y decirlo en el estado,
no bajar la sensibilidad "por las dudas".

---

## 7. Lecciones aprendidas

### Sobre el problema

1. **El caso catastrófico define el diseño.** Un falso positivo molesta; un falso negativo en una
   colisión puede lastimar. Los filtros deben ser asimétricos.
2. **La física explica el algoritmo.** "Abajo, grande y creciendo rápido" es una regla que
   cualquiera entiende. Eso hizo que la calibración y la depuración fueran conversaciones, no
   arqueología.
3. **El TTC es más útil que la distancia.** No hace falta saber a cuántos metros está: hace falta
   saber cuánto falta.

### Sobre la placa

4. **La UNO Q son dos computadoras.** El MPU Linux hace todo; el MCU Zephyr no participa. Mezclar
   los modelos mentales genera confusión innecesaria.
5. **Los recursos escasos son el disco y la paciencia.** 3.6 GB de RAM sobran; 768 MB de disco y
   un Cortex-A53 obligan a medir cada milisegundo.
6. **Sin `sudo` se diseña distinto.** `cron @reboot` y un pidfile reemplazan a `systemd` sin
   drama, si se acepta no tener reinicio automático.

### Sobre el código

7. **Medir antes de optimizar, pero medir de verdad.** `INTER_AREA` parecía "lo correcto"; medido,
   costaba 80 ms. El cambio a `INTER_LINEAR` multiplicó el margen de fps.
8. **Los candados necesitan un orden documentado.** `_lock_config` → `_lock_estado` no es
   evidente leyendo un método suelto; hay que escribirlo.
9. **Compare-and-set para todo lo que tenga dos escritores.** Estado (I-011) y configuración
   (I-013) son el mismo patrón aplicado dos veces.
10. **Los `b""` no son valores.** En streaming, un buffer vacío es un error de protocolo.
11. **Nunca compartir un array mutable entre hilos sin copia.** `renderizar` in-place exige que el
    llamador entregue un buffer propio.
12. **Un proceso de feria no puede morir.** Cada hilo atrapa y loguea; el servicio sigue.

### Sobre el proceso

13. **La auditoría encontró lo que los tests no.** Tres ciclos, 13 hallazgos, 3 críticos. Ninguno
    habría aparecido solo con más tests del mismo tipo.
14. **Los tests de concurrencia necesitan determinismo.** `threading.Barrier` en lugar de `sleep`:
    reproducible y rápido.
15. **Documentar la intención, no solo el código.** El comentario de `_calentamiento` explica por
    qué existe; sin eso, alguien lo borraría "porque no hace nada".

---

## 8. Checklist para el próximo proyecto

Para cualquier servicio de visión en tiempo real sobre una placa Linux embebida:

### Antes de escribir código

- [ ] Medir el hardware real: núcleos, RAM **libre**, disco **libre**, versión de Python y OpenCV.
- [ ] Verificar qué puertos están ocupados y **elegir uno que no colisione**.
- [ ] Decidir si hay `sudo`/`systemd`. Si no, planear `cron`/`init.d` desde el principio.
- [ ] Medir el costo de un frame completo (resize + detector + overlay + encode) antes de fijar
      la resolución.
- [ ] Preguntar: ¿cuál es el caso catastrófico? Diseñar los filtros para **no** taparlo.

### Durante la implementación

- [ ] Un hilo por responsabilidad, con `Event` de parada y `daemon=True`.
- [ ] Estado compartido en dataclasses, con `get_*` que devuelvan copia.
- [ ] Documentar el orden de adquisición de candados.
- [ ] Compare-and-set para cualquier dato con más de un escritor.
- [ ] Nunca publicar buffers vacíos en un stream.
- [ ] Atrapar excepciones por iteración en cada hilo; loguear y seguir.
- [ ] Validar **y** aplicar configuración en una sola operación atómica.
- [ ] Defensa en profundidad: piso/techo también en el punto de uso, no solo en la API.

### Antes de desplegar

- [ ] Suite de tests que corra en la placa (no mocks de la librería de visión).
- [ ] Al menos un test end-to-end que mire la salida que ve el usuario.
- [ ] Test de concurrencia con `Barrier` para la configuración en caliente.
- [ ] Script de servicio idempotente, con pidfile y rotación de logs.
- [ ] Script de despliegue sin dependencias que la placa no tenga (nada de `rsync` a ciegas).
- [ ] Verificar arranque desde cero: apagar, encender, comprobar que el servicio vuelve solo.

### Al documentar

- [ ] Distinguir explícitamente qué hardware **no** se usa (evita búsquedas inútiles de pinout).
- [ ] Tabla de estados con condiciones numéricas exactas.
- [ ] Tabla de campos de la API con unidades.
- [ ] Recetas de calibración síntoma → parámetro → dirección.
- [ ] Advertencias de seguridad y de límites, sin marketing.
- [ ] Números medidos, no estimados.

---

## 9. Números finales

| Ítem | Valor |
|---|---|
| Versión | 1.0.0 |
| Ciclos de auditoría | 3 |
| Hallazgos corregidos | 13 (3 críticos, 4 mayores, 6 menores) |
| Tests | 38 OK |
| Módulos Python | 8 (`main` + 7 del paquete) |
| Líneas de código (aprox.) | ~2 500 |
| FPS medidos | 10.3 – 14.0 |
| Latencia mediana | ~55 ms |
| RSS | 190 – 230 MB |
| CPU | 120 – 155 % de 400 % |
| Tamaño en disco | < 1 MB (sin logs) |
| Puertos usados | 8030 (uno solo) |
| Pines usados | ninguno |

---

<p align="center">
  <strong>Bitácora de Vigía Caminante UNO Q</strong><br>
  <em>Tres auditorías, trece correcciones y un semáforo que se explica solo.</em>
</p>
