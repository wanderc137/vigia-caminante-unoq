# 📘 Vigía Caminante UNO Q — Manual de usuario

**Versión 1.0.0** · Arduino UNO Q (QCC711 · Debian 13 aarch64)

Este documento es el manual de operación. Explica qué es el sistema, qué hace falta para
ponerlo a andar, cómo leer lo que muestra, cómo calibrarlo y qué hacer cuando algo no funciona.
Para entender el código, ver [`developer.md`](developer.md). Para el relato de decisiones y
tropiezos, ver [`BITACORA.md`](BITACORA.md). Para la operación real del servicio en la placa
(SSH, guardianes, checklist de feria y apagado), ver [`servicio.md`](servicio.md).

---

## Índice

1. [Qué es](#1-qué-es)
2. [Qué necesitás](#2-qué-necesitás)
3. [Cómo funciona, en una página](#3-cómo-funciona-en-una-página)
4. [Puesta en marcha paso a paso](#4-puesta-en-marcha-paso-a-paso)
5. [Leer la pantalla](#5-leer-la-pantalla)
6. [Tabla de estados](#6-tabla-de-estados)
7. [Cambiar la cámara y el modo](#7-cambiar-la-cámara-y-el-modo)
8. [La API de estado, campo por campo](#8-la-api-de-estado-campo-por-campo)
9. [Guía de calibración](#9-guía-de-calibración)
10. [Resolución de problemas](#10-resolución-de-problemas)
11. [Especificaciones técnicas](#11-especificaciones-técnicas)
12. [Librerías y dependencias](#12-librerías-y-dependencias)
13. [Compatibilidad con otras placas](#13-compatibilidad-con-otras-placas)
14. [Mantenimiento y buenas prácticas](#14-mantenimiento-y-buenas-prácticas)
15. [Advertencias](#15-advertencias)
16. [Glosario](#16-glosario)

---

## 1. Qué es

**Vigía Caminante UNO Q** es un asistente de percepción visual. Un teléfono Android con la app
**IP Webcam** transmite video por WiFi; la **Arduino UNO Q** lo analiza en tiempo real y publica
una página web con el video anotado y un **borde de color** que indica si hay riesgo de choque.

| El borde se pone… | Cuando… |
|---|---|
| 🟩 **Verde** | El camino está libre |
| 🟨 **Ámbar** | Hay un objeto moviéndose en el corredor o acercándose |
| 🟥 **Rojo** | El choque es inminente |
| ⬜ **Gris** | No hay señal de cámara, o el análisis está detenido |

### Qué problema resuelve

Caminar por una feria mirando una pantalla es peligroso. El sistema responde una sola pregunta
— *¿me voy a chocar con algo?* — con una señal que se entiende sin leer números.

### Qué NO hace

- No identifica personas ni objetos (no dice "peatón": dice "objeto #7").
- No graba video ni guarda historial.
- No manda notificaciones al teléfono.
- No reemplaza la atención de quien camina.
- No es un sistema de seguridad certificado.

---

## 2. Qué necesitás

### Hardware

| Elemento | Detalle mínimo | Notas |
|---|---|---|
| **Arduino UNO Q** | Cualquier unidad con el MPU Linux funcionando | Es la que procesa todo |
| Alimentación | USB-C, 5 V / 3 A | El cargador de una notebook sirve |
| **Teléfono Android** | Android 8 o superior, cámara trasera | Debe poder instalar IP Webcam |
| **Router WiFi** | 2.4 GHz o 5 GHz, red `192.168.1.0/24` | Ambos equipos en la misma red |
| Navegador | Chrome/Firefox/Safari actualizado | En el celular o en una notebook |

No hacen falta sensores, cables de señal, resistencias, protoboard ni level shifters.

### Software ya presente en la placa

| Componente | Versión verificada |
|---|---|
| Debian GNU/Linux | 13 (trixie), aarch64 |
| Python | 3.13.5 |
| OpenCV | 4.10.0 |
| numpy | 2.2.4 |

Comprobación:

```bash
python3 -c "import cv2, numpy, sys; print(sys.version.split()[0], cv2.__version__, numpy.__version__)"
# 3.13.5 4.10.0 2.2.4
```

### Software en el teléfono

- **IP Webcam** (Pavel Khlebovich), gratuita. Se configura en modo servidor y se pulsa
  *Start server*. La app muestra la IP y el puerto, típicamente `192.168.1.9:8080`.

### Acceso a la placa

```bash
# clave y puerto por defecto del proyecto
ssh -i ~/.ssh/unoq_key -p 2222 arduino@192.168.1.5
```

> La placa **no tiene `sudo` sin contraseña** y **no usa systemd de sistema** para este servicio.
> Por eso el arranque automático se hace con `cron @reboot`. No hace falta `sudo` para nada.

---

## 3. Cómo funciona, en una página

```
 TELÉFONO                  ARDUINO UNO Q                        NAVEGADOR
 IP Webcam                 Debian 13 / Python 3.13               (celular/PC)
 192.168.1.9:8080          192.168.1.5:8030
      │                          │                                   │
      │  MJPEG /video            │                                   │
      └─────────────────────────►│                                   │
                                 │ 1. reduce a 320 px                │
                                 │ 2. sustracción de fondo (MOG2)    │
                                 │ 3. contornos → cajas              │
                                 │ 4. tracker por IoU → IDs          │
                                 │ 5. score de riesgo por objeto     │
                                 │ 6. suavizado + histéresis          │
                                 │ 7. dibuja borde/cajas/HUD         │
                                 │ 8. codifica JPEG                  │
                                 │                                   │
                                 │  /stream (MJPEG anotado) ────────►│
                                 │  /api/estado (JSON cada 400 ms)──►│
                                 ◄──── /api/config (cambiar modo) ───┘
```

### La idea física, sin fórmulas

Cuando algo se te viene encima, en la imagen pasa tres cosas a la vez:

1. Está **cada vez más abajo** (más cerca del borde inferior).
2. **Crece** de tamaño.
3. Crece **cada vez más rápido**.

El sistema mide esas tres cosas y, con la geometría de la perspectiva, estima cuánto falta para
el contacto (TTC, *time to contact*). Si ese tiempo es chico, el borde se pone rojo.

No hay red neuronal: hay contornos, restas de imágenes y umbrales. Eso es deliberado — se puede
explicar y depurar en plena feria.

### Los cinco procesos internos

| Proceso | Qué hace |
|---|---|
| Capturador | Baja el MJPEG del teléfono |
| Demo | Genera una escena falsa cuando no hay teléfono |
| Visión | Analiza, puntúa y dibuja |
| HTTP | Sirve la página, el video y la API |
| Vigilante (watchdog) | Si el frame se pone viejo, pone gris |

---

## 4. Puesta en marcha paso a paso

### 4.1 Encender la placa

1. Conectá el USB-C. Esperá ~30 s.
2. Verificá que responde:

```bash
ping -c 3 192.168.1.5
ssh -i ~/.ssh/unoq_key -p 2222 arduino@192.168.1.5 'uptime'
```

### 4.2 Preparar el teléfono

1. Instalá **IP Webcam** desde Play Store.
2. Conectá el teléfono a la **misma WiFi** que la UNO Q.
3. Abrí la app → *Start server*.
4. La app muestra una URL, por ejemplo `http://192.168.1.9:8080`.
5. (Opcional) En *Settings* → *Video preferences*: resolución **1080×1920**, orientación vertical.

**Verificación desde la placa** (esto descarta la mayoría de los problemas):

```bash
ssh -i ~/.ssh/unoq_key -p 2222 arduino@192.168.1.5 \
  "curl -s -m 5 -o /dev/null -w '%{http_code}\n' http://192.168.1.9:8080/shot.jpg"
# 200  = el teléfono responde
# 000  = no hay ruta / la app está cerrada / IP equivocada
```

### 4.3 Verificar el servicio

```bash
ssh -i ~/.ssh/unoq_key -p 2222 arduino@192.168.1.5
cd ~/vigia-caminante-unoq
./scripts/servicio.sh status
```

Salida esperada (ejemplo):

```
Estado    : ACTIVO (PID 4123)
Uptime    : 00:12:47
CPU / RSS : 134% / 214 MB
Health    : ok (http://192.168.1.5:8030/health)
Nivel     : GREEN (score 0.03, 12.4 fps)
Arranque  : automático activado (cron @reboot)
```

Si dice `DETENIDO`:

```bash
./scripts/servicio.sh start
./scripts/servicio.sh logs 60
```

> 💡 **Los guardianes ya están instalados.** Además del `@reboot`, hay un cron cada 2 minutos
> que llama a `start` (si el PID vive, no hace nada) y otro cada 10 minutos que consulta
> `/health` y hace `restart` si no responde. Por eso, si el proceso muere, vuelve solo en
> **≤ 2 minutos**; si se cuelga, en **≤ 10 minutos**. Para confirmar que están las tres líneas:
>
> ```bash
> crontab -l | grep vigia-caminante-unoq
> ```
>
> El campo `Arranque` de `status` solo indica que hay alguna línea del Vigía, no que estén los
> guardianes. Detalle completo en [`servicio.md`](servicio.md#4-arranque-automático-y-guardianes).

### 4.4 Abrir la página

En el celular o en una notebook conectada a la misma WiFi:

```
http://192.168.1.5:8030/
```

Deberías ver el video con el borde **verde** y el panel de métricas.

### 4.5 Primera prueba de humo

| Prueba | Resultado esperado |
|---|---|
| Nadie frente a la cámara | Borde **verde**, `Objetos 0` |
| Una persona se cruza de lejos | Borde **ámbar**, aparece un ID con score |
| Esa persona camina directo a la cámara | Borde **rojo**, `TTC` bajo (0.3–1.0 s) |
| Se va | **Ámbar** y luego **verde** (sin parpadeos) |
| Cerrás IP Webcam | **Gris** en ≤ 3 s, mensaje "SIN SEÑAL" |
| Reabrís IP Webcam | Vuelve a **verde** solo, sin tocar nada |

### 4.6 Probar sin teléfono (modo demo)

Si el teléfono no está disponible o la WiFi falla, el modo demo muestra el semáforo completo:

1. En la página, pulsar **Demo**.
2. En ~10 s se ve el ciclo: verde → ámbar → rojo → ámbar → verde, repetido.
3. Pulsar **Iniciar** para volver a la cámara real.

```bash
# equivalente por API
curl -s -X POST http://192.168.1.5:8030/api/config \
     -H 'Content-Type: application/json' -d '{"modo":"demo"}'
```

### 4.7 Si el teléfono cambia de IP

Cuando el router le da otra IP al teléfono, el sistema se queda en gris. Dos formas de
arreglarlo:

**Desde la página** (no reinicia nada): escribir la URL nueva en el campo y pulsar **Aplicar**.

**Desde la terminal:**

```bash
curl -s -X POST http://192.168.1.5:8030/api/config \
     -H 'Content-Type: application/json' \
     -d '{"camara_url":"http://192.168.1.9:8080/video"}'
```

**Permanente** (sobrevive reinicios): editar `config.env` y reiniciar.

```bash
cd ~/vigia-caminante-unoq
sed -i 's#^CAM_URL=.*#CAM_URL=http://192.168.1.9:8080/video#' config.env
./scripts/servicio.sh restart
```

> 💡 Recomendación: fijá la IP del teléfono en el router (reserva DHCP) para que no cambie.

---

## 5. Leer la pantalla

```
┌──────────────────────────────────────────────────────────┐
│  👁  Vigía Caminante   UNO Q          [ PASO LIBRE ]     │ ← badge de estado
├──────────────────────────────────────────────────────────┤
│      ┌════════════════════════════════════════════┐      │
│      ║                                            ║      │
│      ║   VIDEO CON EL BORDE DEL COLOR DEL NIVEL   ║      │ ← borde = riesgo
│      ║                                            ║      │
│      ║        [ ID3  71%  TTC 0.8s ]              ║      │ ← caja + etiqueta
│      ║         ┌───────────────┐                  ║      │
│      ║         │               │                  ║      │
│      ║         └───────────────┘                  ║      │
│      ║  PELIGRO                                   ║      │ ← HUD
│      ║  score 71%   TTC 0.8s                      ║      │
│      ║  12.4 fps   54 ms                          ║      │
│      ║  objetos 1   modo camara                   ║      │
│      └════════════════════════════════════════════┘      │
│      ¡PELIGRO! Objeto acercándose de frente              │ ← mensaje
├────────────┬────────────┬────────────┬───────────────────┤
│  RIESGO    │    TTC     │    FPS     │     OBJETOS       │
│    71%     │   0.8 s    │   12.4     │        1          │
├────────────┴────────────┴────────────┴───────────────────┤
│  URL: [ http://192.168.1.9:8080 ]              [Aplicar] │
│  [ Iniciar ]  [ Detener ]  [ Demo ]                      │
├──────────────────────────────────────────────────────────┤
│  Modo: camara  Latencia: 54 ms  Frame: 38 ms  Up: 812 s  │
└──────────────────────────────────────────────────────────┘
```

| Elemento | Qué informa | Cómo leerlo |
|---|---|---|
| **Borde del video** | Nivel de riesgo | Es la señal principal. Todo lo demás es diagnóstico |
| **Badge superior** | Nivel, en palabras | `PASO LIBRE` / `ATENCIÓN` / `¡PELIGRO!` / `SIN SEÑAL` |
| **Cajas de colores** | Cada objeto detectado | Color = nivel individual de ese objeto |
| **Etiqueta de la caja** | `ID`, `%` y `TTC` | `ID7 71% TTC 0.8s` = objeto 7, score 71 %, contacto en 0.8 s |
| **HUD superior izquierdo** | Nivel, score, TTC, fps, latencia, objetos, modo | Igual que lo que publica la API |
| **Tarjeta Riesgo** | Score global 0–100 % | Se mueve suave por el suavizado; el color cambia con histéresis |
| **Tarjeta TTC** | Segundos hasta el contacto | `—` significa que no hay objeto acercándose |
| **Tarjeta FPS** | Frames procesados por segundo | 10–14 es lo normal en la placa |
| **Tarjeta Objetos** | Cantidad de objetos seguidos | 0–4 típico; más sugiere ruido |
| **Mensaje bajo el video** | Explicación textual | "Calibrando fondo de escena" al arrancar |
| **Modo / Latencia / Frame / Up** | Diagnóstico | `Frame` alto = red lenta. Ver "va a tirones" |
| **Velo "conectando…"** | Aún no hay imagen | Desaparece al primer frame |

---

## 6. Tabla de estados

### Niveles

| Nivel | Color | Borde | Texto | Significado operativo |
|---|---|---|---|---|
| `NO_SIGNAL` | `#6b7280` gris | Gris | `SIN SEÑAL` / `ANALISIS DETENIDO` | **No hay información.** No significa "vía libre" |
| `GREEN` | `#22c55e` verde | Verde | `PASO LIBRE` | Nada relevante en el corredor |
| `AMBER` | `#f59e0b` ámbar | Ámbar | `ATENCION` | Objeto en el corredor o acercándose: mirá |
| `RED` | `#ef4444` rojo | Rojo | `PELIGRO` | Contacto estimado inminente: frená |

### Condiciones exactas (umbrales por defecto)

| Transición | Condición |
|---|---|
| → `RED` | `score ≥ 0.70` |
| `RED` → `AMBER` | `score ≤ 0.55` **y** ≥ 800 ms en rojo |
| → `AMBER` | `score ≥ 0.40` |
| `AMBER` → `GREEN` | `score ≤ 0.25` |
| → `NO_SIGNAL` | `frame_age_ms > 2000` o `modo = detenido` |

### Modos de operación

| Modo | Qué hace | Cuándo usarlo |
|---|---|---|
| `camara` | Analiza el MJPEG del teléfono | Operación normal |
| `demo` | Genera una escena sintética con un caminante | Sin teléfono, para mostrar o probar la UI |
| `detenido` | No analiza; publica gris "ANALISIS DETENIDO" | Pausa, o para ahorrar CPU |

### Mensajes del sistema y qué significan

| Mensaje | Nivel | Significado | Acción |
|---|---|---|---|
| `Esperando señal de cámara` | Gris | Arrancó, aún sin frame | Esperar 3–5 s |
| `Calibrando fondo de escena` | Gris | Los 5 primeros frames entrenan MOG2 | Esperar < 1 s |
| `SIN SEÑAL: sin frames de la cámara` | Gris | Pasaron > 2 s sin frame | Ver "no hay señal" |
| `Sin conexión con la cámara (reintento N)` | Gris | Falló la conexión, reintentando con backoff | Revisar teléfono/WiFi |
| `URL de cámara inválida` | Gris | La URL no pasó la validación | Corregir la URL |
| `Análisis detenido` | Gris | Modo `detenido` | Pulsar **Iniciar** |
| `Frame vacío` | Gris | Llegó un frame degenerado | Transitorio; se ignora |
| `Paso libre` | Verde | Sin riesgo | — |
| `Atención: objeto en el corredor` | Ámbar | Objeto dentro del corredor | Observar |
| `¡PELIGRO! Objeto acercándose de frente` | Rojo | TTC bajo / looming alto | Frenar |

---

## 7. Cambiar la cámara y el modo

### Desde la página

| Control | Efecto | ¿Persiste al reiniciar? |
|---|---|---|
| Campo **URL** + **Aplicar** | Cambia la fuente de video sin reiniciar | ❌ |
| **Iniciar** | `modo=camara` | ❌ |
| **Detener** | `modo=detenido` | ❌ |
| **Demo** | `modo=demo` | ❌ |

### Desde la API

```bash
# cambiar de modo
curl -s -X POST http://192.168.1.5:8030/api/config \
     -H 'Content-Type: application/json' -d '{"modo":"demo"}'

# cambiar la URL
curl -s -X POST http://192.168.1.5:8030/api/config \
     -H 'Content-Type: application/json' \
     -d '{"camara_url":"http://192.168.1.9:8080/video"}'

# cambiar umbrales en caliente
curl -s -X POST http://192.168.1.5:8030/api/config \
     -H 'Content-Type: application/json' \
     -d '{"umbral_rojo_in":0.62,"min_tiempo_rojo_ms":1000}'
```

### Para que sea permanente

`POST /api/config` **solo cambia la memoria**. Para que sobreviva a un reinicio, editá
`config.env` y reiniciá:

```bash
cd ~/vigia-caminante-unoq
nano config.env        # por ejemplo: MODO=demo  o  CAM_URL=http://192.168.1.9:8080/video
./scripts/servicio.sh restart
```

### Ajustes que acepta `POST /api/config`

| Clave | Rango válido |
|---|---|
| `umbral_rojo_in` | `0 < umbral_ambar_out < umbral_ambar_in < umbral_rojo_out < umbral_rojo_in < 1` |
| `umbral_rojo_out` | ídem (monotonía estricta) |
| `umbral_ambar_in` | ídem |
| `umbral_ambar_out` | ídem |
| `min_tiempo_rojo_ms` | ≥ 0 |
| `ttc_critico` | > 0.1 |
| `vel_y_critica` | > 1.0 |
| `vel_y_min_ttc` | ≥ 0 |
| `loom_critico` | > 0.001 |
| `tau_ema` | > 0.01 |
| `area_minima_px` | ≥ 1 |
| `area_maxima_rel` | `(0, 0.99]` |
| `watchdog_ms` | ≥ 200 |

Si **una sola** clave del lote es inválida, se rechaza **todo** con HTTP 400 y nada cambia.

```bash
$ curl -s -X POST http://192.168.1.5:8030/api/config \
       -H 'Content-Type: application/json' -d '{"umbral_rojo_in":0.50}'
{"error":"umbrales incoherentes: se exige 0 < umbral_ambar_out < umbral_ambar_in < umbral_rojo_out < umbral_rojo_in < 1","campo":["umbral_rojo_in"]}
```

---

## 8. La API de estado, campo por campo

```bash
curl -s http://192.168.1.5:8030/api/estado | python3 -m json.tool
```

### Campos de primer nivel

| Campo | Tipo | Unidad | Significado |
|---|---|---|---|
| `nivel` | string | — | `NO_SIGNAL` \| `GREEN` \| `AMBER` \| `RED` |
| `score` | float \| null | 0..1 | Riesgo global suavizado. `null` si no es finito |
| `ttc` | float \| null | s | Menor tiempo al contacto entre objetos. `null` = sin estimación |
| `fps` | float | fps | Ritmo real del hilo de visión (media exponencial) |
| `latencia_ms` | float | ms | Tiempo de cómputo del frame (sin red) |
| `frame_age_ms` | float | ms | Antigüedad del frame analizado al publicarse |
| `objetos` | array | — | Lista de objetos seguidos (ver abajo) |
| `n_objetos` | int | — | `len(objetos)` |
| `modo` | string | — | `camara` \| `demo` \| `detenido` |
| `camara_url` | string | — | URL base configurada del teléfono |
| `mensaje` | string | — | Explicación legible del estado |
| `timestamp` | float | s epoch | Cuándo se publicó el estado |
| `uptime_s` | float | s | Segundos desde el arranque del servicio |
| `ajustes` | object | — | Solo las claves cambiadas en caliente por la API |

> `null` en `score` o `ttc` significa "no calculable" (infinito o NaN), no cero.

### Campos de cada objeto en `objetos[]`

| Campo | Tipo | Unidad | Significado |
|---|---|---|---|
| `id` | int | — | Identificador persistente mientras el objeto se siga viendo |
| `bbox` | [int×4] | px | `[x, y, w, h]` en píxeles del frame original (1080×1920) |
| `bbox_norm` | [float×4] | 0..1 | Igual, normalizado al tamaño del cuadro |
| `area_rel` | float | 0..1 | Área de la caja / área del cuadro |
| `velocidad_px_s` | float | px/s | Módulo de la velocidad del centro |
| `velocidad_y` | float | px/s | Componente vertical. **Positivo = se acerca** |
| `crecimiento_area_s` | float | 1/s | `(dA/dt)/A`: ritmo de crecimiento relativo (looming) |
| `ttc` | float \| null | s | Tiempo estimado al contacto para ese objeto |
| `en_corredor` | bool | — | Si su centro cae en la banda de colisión |
| `nivel_individual` | string | — | `GREEN` \| `AMBER` \| `RED` para ese objeto |
| `score` | float \| null | 0..1 | Score individual (el global es el máximo) |

### Cómo leerlo en la práctica

| Pregunta | Campo a mirar |
|---|---|
| ¿Por qué está rojo? | `objetos[i].score`, `objetos[i].ttc`, `objetos[i].crecimiento_area_s` |
| ¿Está viendo algo que no existe? | `objetos[i].area_rel` chico + `en_corredor: false` → probable ruido |
| ¿Va lento el sistema? | `fps` bajo + `latencia_ms` alto |
| ¿La red es el problema? | `frame_age_ms` alto con `latencia_ms` normal |
| ¿Se cayó la cámara? | `nivel: NO_SIGNAL` + `frame_age_ms` creciendo |
| ¿Qué se cambió en caliente? | `ajustes` |

### Otros endpoints

| Endpoint | Respuesta |
|---|---|
| `GET /health` | `{"status":"ok","servicio":"vigia-caminante-unoq","version":"1.0.0","timestamp":…,"uptime_s":…}` |
| `GET /api/config` | `{"puerto":8030,"camara_url":…,"modo":…,"ajustes":{…},"umbrales":{"rojo_in":…,"rojo_out":…,"ambar_in":…,"ambar_out":…}}` |
| `GET /snapshot.jpg` | JPEG anotado (nunca vacío) |
| `GET /stream` | MJPEG `multipart/x-mixed-replace` |
| `GET /api/status` | Alias de `/api/estado` |

Ejemplo de sondeo continuo:

```bash
while true; do
  curl -s http://192.168.1.5:8030/api/estado | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(d['nivel'], d['score'], d['ttc'], d['n_objetos'], d['fps'])"
  sleep 1
done
```

---

## 9. Guía de calibración

### Principio general

El sistema compara un **score** (0 a 1) con cuatro **umbrales**. Calibrar es elegir esos umbrales
para tu escena. No se calibra "la cámara": se calibra la frontera entre "molesto" y "útil".

```
 score  0.0 ──────── 0.25 ──────── 0.40 ──────── 0.55 ──────── 0.70 ──────── 1.0
         │           │            │            │            │
         │  GREEN    │  (zona     │   AMBER    │  (zona     │    RED
         │           │   muerta)  │            │   muerta)  │
         └───────────┴────────────┴────────────┴────────────┴──────────►
                 ambar_out    ambar_in     rojo_out     rojo_in
```

Las dos "zonas muertas" son la histéresis: evitan que el color parpadee.

### Procedimiento recomendado (10 minutos)

1. **Poné el modo demo** y confirmá que el ciclo de colores funciona.
   ```bash
   curl -s -X POST http://192.168.1.5:8030/api/config \
        -H 'Content-Type: application/json' -d '{"modo":"demo"}'
   ```
2. **Volvé a cámara** y dejá la escena vacía 10 s. Mirá `score`: debería quedar < 0.15.
3. **Cruce de frente a 3 m.** Anotá el `score` máximo. Debería llegar a 0.40–0.60 (ámbar).
4. **Caminá directo a la cámara.** Anotá el `score` máximo. Debería superar 0.70 (rojo).
5. **Ajustá `umbral_rojo_in`** justo por debajo del score del paso 4 (por ejemplo 0.65) y por
   encima del paso 3 (por ejemplo 0.60). Si los dos valores se solapan, subí `area_minima_px` o
   bajá `corredor_x_max`/`corredor_x_min` para achicar el corredor.
6. **Repetí 3 veces** para confirmar que no parpadea.

### Tabla de síntomas → ajuste

| Síntoma | Ajuste | Dirección | Efecto secundario |
|---|---|---|---|
| Rojo con sombras o piso texturado | `umbral_rojo_in` | ↑ 0.70 → 0.78 | Menos falsos positivos, más lentitud para alertar |
| Nunca llega a rojo | `umbral_rojo_in` | ↓ 0.70 → 0.62 | Más falsos positivos |
| Rojo demasiado pronto | `ttc_critico` | ↓ 2.0 → 1.2 | Exige más urgencia real |
| Reacciona tarde | `tau_ema` | ↓ 0.3 → 0.15 | Score más nervioso |
| Borde parpadea | `min_tiempo_rojo_ms` | ↑ 800 → 1200 | El rojo tarda más en soltar |
| Detecta cosas lejanas irrelevantes | `area_minima_px` | ↑ 200 → 400 | Puede perder objetos delgados |
| No ve personas delgadas | `area_minima_px` | ↓ 200 → 120 | Más ruido |
| Se dispara al mover la cámara | `bg_history` | ↑ 200 → 400 | Se adapta más lento a la luz |
| Solo importa el centro | `corredor_x_min` / `corredor_x_max` | 0.25/0.75 → 0.35/0.65 | Ignora los costados |
| Solo importa lo cercano | `corredor_y_min` | ↑ 0.35 → 0.50 | Menos anticipación |
| Se marea con luz que cambia | `bg_var_threshold` | ↑ 25 → 40 | Menos sensible al movimiento suave |

### Calibración fina del corredor

El corredor es la zona donde un objeto "va a chocar". Por defecto es una banda central que
empieza al 35 % de la altura:

```
 y=0    ┌──────────────────────┐
        │                      │
 y=0.35 ├──────┬────────┬──────┤  ← empieza el corredor
        │      │        │      │
        │      │CORREDOR│      │   x de 0.25 a 0.75
        │      │        │      │
 y=1.0  └──────┴────────┴──────┘  ← borde inferior = "acá estoy yo"
```

Un objeto **dentro** del corredor y que **baja** es el caso de riesgo. Uno que se cruza por un
costado no debería disparar rojo.

### Verificar sin adivinar

```bash
# ver qué objetos está detectando y con qué números
curl -s http://192.168.1.5:8030/api/estado | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('nivel:', d['nivel'], 'score:', d['score'])
for o in d['objetos']:
    print('  ID%2d score=%s area=%s vy=%s ttc=%s corredor=%s' % (
        o['id'], o['score'], o['area_rel'], o['velocidad_y'],
        o['ttc'], o['en_corredor']))"
```

### Cuándo reiniciar el fondo

MOG2 aprende el fondo solo. Se recalibra cuando cambia mucho la escena (encendés otra luz, movés
la cámara de lugar). Para forzarlo: cambiar de modo y volver, o reiniciar el servicio.

```bash
./scripts/servicio.sh restart
```

---

## 10. Resolución de problemas

### 10.1 No hay señal (borde gris)

| Comprobación | Comando | Resultado sano |
|---|---|---|
| ¿Está el servicio vivo? | `./scripts/servicio.sh status` | `ACTIVO` |
| ¿Responde el HTTP? | `curl -s http://192.168.1.5:8030/health` | `{"status":"ok",…}` |
| ¿Responde el teléfono? | `curl -s -o /dev/null -w '%{http_code}' http://192.168.1.9:8080/shot.jpg` | `200` |
| ¿Hay ruta al teléfono? | `ping -c 3 192.168.1.9` | 0 % pérdida |
| ¿Qué dice el log? | `./scripts/servicio.sh logs 40` | `Camara: stream abierto…` |
| ¿Cuánto hace del último frame? | `curl -s …/api/estado` → `frame_age_ms` | < 2000 |

Causas típicas y solución:

| Causa | Solución |
|---|---|
| IP Webcam cerrada o en pausa | Reabrir la app y pulsar *Start server* |
| El teléfono cambió de IP | Actualizar `camara_url` (sección 7) |
| El teléfono está en otra red (datos móviles) | Apagar datos móviles; conectar a la WiFi |
| El router aísla clientes (AP isolation) | Desactivar el aislamiento de clientes en el router |
| Batería del teléfono en ahorro extremo | Desactivar la optimización de batería para IP Webcam |
| URL con error de tipeo | `curl` a la URL exacta desde la placa |

### 10.2 Va a tirones

| Comprobación | Qué mirar |
|---|---|
| `fps` en `/api/estado` | < 8 indica problema de cómputo o red |
| `latencia_ms` | > 150 ms: la placa está saturada |
| `frame_age_ms` | > 300 ms con latencia normal: la **red** es el cuello de botella |

Soluciones, en orden de impacto:

1. Bajar `ANCHO_PROCESO` de 320 a 256.
2. Bajar `ANCHO_SALIDA` de 540 a 420 (o `JPEG_CALIDAD` de 70 a 60).
3. Acercar el teléfono al router, usar 5 GHz, alejarse de microondas.
4. Bajar la resolución en IP Webcam a 720×1280.
5. Cerrar pestañas con `/stream` abierto: cada una consume CPU y ancho de banda.

### 10.3 Marca rojo sin que haya nadie

| Causa | Cómo confirmarlo | Solución |
|---|---|---|
| Sombras que se mueven | `area_rel` grande y `en_corredor: true` sin persona visible | Subir `umbral_rojo_in`; encender luz difusa |
| Textura de piso muy contrastada | Muchos objetos chicos con IDs cambiando | Subir `area_minima_px` |
| La cámara se movió | Ráfaga de rojo justo después de mover el teléfono | Fijar el teléfono; esperar 2 s |
| Cambio brusco de luz (se abre una puerta) | Ráfaga corta e intensa | Subir `min_tiempo_rojo_ms` no ayuda: subir `bg_var_threshold` |
| Persona real que no ves (reflejo, pantalla) | `n_objetos ≥ 1` persistente | Revisar el encuadre |

Diagnóstico rápido: pedir un snapshot y mirar qué está enmarcado.

```bash
curl -s http://192.168.1.5:8030/snapshot.jpg -o /tmp/vigia.jpg
# abrir /tmp/vigia.jpg y mirar las cajas dibujadas
```

### 10.4 Nunca llega a rojo

| Causa | Solución |
|---|---|
| Umbral alto para la escena | Bajar `umbral_rojo_in` a 0.62 |
| `ttc_critico` muy corto | Subir a 2.5–3.0 |
| El corredor no cubre por dónde viene la gente | Ampliar `corredor_x_min`/`corredor_x_max` |
| `area_minima_px` alto y la persona está lejos | Bajar a 120 |
| `tau_ema` alto (suaviza demasiado) | Bajar a 0.2 |
| El objeto se acerca pero no "baja" en la imagen | Es geometría: apuntar la cámara más abajo |

**Prueba de control**: activar el demo. Si el demo llega a rojo, el problema es de calibración,
no del código.

### 10.5 La página no carga

| Comprobación | Comando | Esperado |
|---|---|---|
| Servicio activo | `./scripts/servicio.sh status` | `ACTIVO (PID …)` |
| Puerto escuchando | `ss -ltn \| grep 8030` | `0.0.0.0:8030` |
| Puerto ocupado por otro | `ss -ltnp \| grep 8030` | Solo `python3` |
| Firewall | `curl` desde la propia placa | `{"status":"ok"}` |
| Misma red | `ip -4 addr` en el cliente | `192.168.1.x` |

Si el puerto está ocupado por otro proceso, cambiar `PS_PORT` en `config.env` (por ejemplo a
`8040`) y reiniciar.

```bash
./scripts/servicio.sh stop
ss -ltn | grep 8030          # debe estar vacío
./scripts/servicio.sh start
```

> ⚠️ **El guardián de salud tiene hardcodeado `http://127.0.0.1:8030/health`.** Si cambiás
> `PS_PORT`, también tenés que editar esa línea del crontab (`crontab -e`); si no, el guardián
> reiniciará el servicio cada 10 minutos intentando consultar el puerto viejo. Ver
> [`servicio.md`](servicio.md#83-el-puerto-8030-está-ocupado).

### 10.6 El servicio no arranca

```bash
cd ~/vigia-caminante-unoq
tail -n 60 data/vigia.stdout.log
```

| Error en el log | Causa | Solución |
|---|---|---|
| `ModuleNotFoundError: cv2` | Faltan las librerías del sistema | Verificar `python3 -c "import cv2"`; no instalar sin autorización |
| `Address already in use` | Puerto 8030 tomado | Cambiar `PS_PORT` o liberar el puerto |
| `Permission denied` al escribir `data/` | Permisos del directorio | `mkdir -p data && chmod u+w data` |
| Nada en el log | Falló el arranque del shell | Ejecutar a mano: `python3 src/main.py` y ver el error |

### 10.7 El video se congela pero la página responde

El stream MJPEG no se reconecta solo si el navegador corta. `app.js` lo reintenta a los 2.5 s.
Si igual queda congelado:

1. Recargar la página (F5).
2. Verificar `frame_age_ms` en `/api/estado`: si crece, el problema es la cámara.
3. Verificar `fps`: si es 0, el hilo de visión no está procesando.

### 10.8 La API devuelve 400 al cambiar ajustes

El mensaje de error dice exactamente qué está mal:

```json
{"error": "umbrales incoherentes: se exige 0 < umbral_ambar_out < umbral_ambar_in < umbral_rojo_out < umbral_rojo_in < 1",
 "campo": ["umbral_rojo_in"]}
```

Corregir el valor y reenviar. Si querés cambiar varios umbrales a la vez, mandalos **juntos** en
un solo POST, porque la validación se hace sobre el estado resultante.

```bash
# correcto: bajar toda la escalera en un solo lote
curl -s -X POST http://192.168.1.5:8030/api/config \
     -H 'Content-Type: application/json' \
     -d '{"umbral_rojo_in":0.62,"umbral_rojo_out":0.50,"umbral_ambar_in":0.36,"umbral_ambar_out":0.22}'
```

---

## 11. Especificaciones técnicas

### Placa anfitriona

| Parámetro | Valor |
|---|---|
| Modelo | Arduino UNO Q |
| MPU Linux | QCC711, 4 núcleos aarch64 |
| RAM | 3.6 GB (≈2.9 GB libres al arrancar) |
| Almacenamiento | 9.8 GB raíz, ~768 MB libres |
| Sistema operativo | Debian GNU/Linux 13 (trixie) aarch64 |
| MCU real-time | STM32U585 (Cortex-M33 + TrustZone) — **no usado** |
| RTOS del MCU | Zephyr — **no usado** |
| Lógica | 3.3 V (no usada en este proyecto) |
| Alimentación | USB-C 5 V |
| Red | `wlan0`, IP fija `192.168.1.5` |

### Servicio

| Parámetro | Valor |
|---|---|
| Lenguaje | Python 3.13.5 |
| Proceso | `python3 src/main.py` |
| Puerto | **8030/TCP** (8000, 8010 y 8020 están ocupados por otros servicios de la placa) |
| Hilos | 5 (cámara, demo, visión, HTTP, watchdog) |
| Usuario | `arduino` (sin `sudo`) |
| Arranque automático | `cron @reboot` con 20 s de retardo + guardián de proceso cada 2 min + guardián de salud cada 10 min |
| PID | `data/vigia.pid` |
| Log principal | `data/vigia.log` (5 MB × 2 backups) |
| Log de stdout | `data/vigia.stdout.log` |
| Apagado | SIGTERM/SIGINT con parada limpia |

### Visión

| Parámetro | Valor por defecto |
|---|---|
| Resolución de entrada | 1080×1920 (vertical) |
| Ancho de proceso | 320 px (`ANCHO_PROCESO`) |
| Ancho de salida | 540 px (`ANCHO_SALIDA`) |
| Detector | MOG2 (`history=200`, `varThreshold=25`, sin sombras) |
| Morfología | cierre 7×7, apertura 5×5 (elipse) |
| Área mínima | 200 px (en resolución de proceso) |
| Aspecto máximo | 5:1 horizontal, 8:1 vertical |
| Tracking | IoU ≥ 0.3, caducidad 0.5 s |
| Calentamiento | 5 frames |
| Suavizado | EMA con τ = 0.3 s |
| Rojo mínimo | 800 ms |
| Watchdog | 2000 ms (mínimo 200) |

### Rendimiento medido en placa

| Métrica | Rango medido |
|---|---|
| FPS de proceso | 10.3 – 14.0 |
| Latencia | 36 – 122 ms (mediana ≈ 55 ms) |
| RSS | 190 – 230 MB |
| CPU | 120 – 155 % de 400 % |

### Red

| Parámetro | Valor |
|---|---|
| Teléfono | `192.168.1.9:8080`, `/video` (MJPEG), `/shot.jpg` |
| UNO Q | `192.168.1.5:8030` |
| Protocolo | HTTP/1.1, sin TLS |
| Streaming | `multipart/x-mixed-replace; boundary=frame` |
| Polling de estado | 400 ms desde `app.js` |
| Cifrado | **Ninguno** (LAN de confianza) |

---

## 12. Librerías y dependencias

### Lo que usa el programa

| Librería | Versión | Origen | Para qué |
|---|---|---|---|
| `cv2` (OpenCV) | 4.10.0 | **Preinstalada en la placa** | Captura MJPEG, MOG2, morfología, dibujo, JPEG |
| `numpy` | 2.2.4 | **Preinstalada en la placa** | Arrays de imagen |
| `threading`, `http.server`, `json`, `urllib`, `signal`, `logging`, `dataclasses`, `unittest`, `ipaddress`, `socket` | stdlib 3.13 | Incluidas con Python | Hilos, servidor, API, validación, logs, tests |

**No hay ninguna dependencia externa adicional.** `requirements.txt` es solo documental.

### Instalación

**No hay que instalar nada.** En la placa:

```bash
python3 -c "import cv2, numpy; print(cv2.__version__, numpy.__version__)"
# 4.10.0 2.2.4
```

> 🚫 **No uses `pip install`, `apt install`, ni entornos virtuales.** El disco raíz de la placa es
> chico a propósito y OpenCV ya está compilado para aarch64. Instalar `opencv-python` desde PyPI
> puede reemplazar el OpenCV del sistema y romper el servicio (además de llenar el disco).

### En un equipo de desarrollo (opcional)

Solo si querés correr los tests fuera de la placa. Requiere OpenCV y numpy locales:

```bash
# Debian/Ubuntu, con autorización explícita del dueño del equipo
python3 -c "import cv2, numpy" || echo "faltan cv2/numpy: consultar antes de instalar"
```

Si faltan, **no instales por tu cuenta**: preguntá. Los tests están pensados para correr en la
placa, que ya los tiene.

### Actualizar el proyecto

```bash
# desde la notebook de desarrollo
cd /home/jma/Documents/Proyects/OpenCode/AG.CreadorContenido/proyectos/vigia-caminante-unoq
./scripts/desplegar.sh
```

El script conserva `config.env` y `data/` en la placa, y reinicia el servicio. Usa `tar` por
SSH porque la placa **no tiene `rsync`**.

> ⚠️ `desplegar.sh` reescribe `CAM_URL` y `PS_PORT` en `config.env` en cada ejecución. Si los
> cambiaste a mano, pasalos como variables (`CAM_URL=… PS_PORT=… ./scripts/desplegar.sh`) o se
> perderán. Después del despliegue, `./scripts/servicio.sh status` debe decir `ACTIVO`; esperá
> 15–20 s antes de recargar la página. Runbook completo en [`servicio.md`](servicio.md#6-actualizar-el-servicio).

---

## 13. Compatibilidad con otras placas

Este proyecto **no es un sketch**: no se compila con `arduino-cli` ni se flashea por USB. Se
despliega como un servicio Python sobre Linux. Por eso la compatibilidad depende de que la placa
tenga **Linux + Python 3 + OpenCV**, no de que sea "Arduino".

| Placa / plataforma | ¿Corre? | Motivo |
|---|---|---|
| **Arduino UNO Q** (QCC711, Debian 13) | ✅ **Sí, es el objetivo** | Python 3.13 + OpenCV 4.10 preinstalados |
| Arduino UNO R3 / R4 (AVR / Renesas) | ❌ No | Sin Linux, sin OpenCV, RAM y flash insuficientes |
| Arduino Portenta X8 (Linux + MCU) | ⚠️ Probable | Tiene Linux aarch64; habría que instalar OpenCV y adaptar el despliegue |
| Arduino Portenta H7 (Mbed) | ❌ No | Sin sistema de archivos Linux ni OpenCV |
| Arduino GIGA R1 (STM32H7) | ❌ No | Firmware bare-metal/Mbed; no corre Python/OpenCV |
| Raspberry Pi 4 / 5 (Raspberry Pi OS) | ✅ Sí, trivial | Mismo stack (Linux + Python + OpenCV); cambiar `scripts/desplegar.sh` |
| Raspberry Pi Zero 2 W | ⚠️ Sí, más lento | Menos CPU: bajar `ANCHO_PROCESO` a 240 y `ANCHO_SALIDA` a 420 |
| Notebook Linux x86_64 | ✅ Sí, para desarrollo | Sirve para probar la lógica; los fps no son representativos |
| Android (Termux) | ⚠️ Posible | Requiere compilar OpenCV; no recomendado |
| ESP32 / ESP8266 | ❌ No | Sin Linux, sin OpenCV; habría que reescribir la visión en C |
| Windows / macOS | ⚠️ Parcial | El código es portable, pero `servicio.sh` y `desplegar.sh` son bash |

### Qué habría que cambiar en otra placa Linux

| Cambio | Dónde |
|---|---|
| IP, usuario, puerto SSH y clave | `scripts/desplegar.sh` (`UNOQ_HOST`, `UNOQ_USER`, `UNOQ_SSH_PORT`, `UNOQ_LLAVE`) |
| Arranque automático | `scripts/servicio.sh` (cron) → systemd si hay `sudo` |
| Puerto | `PS_PORT` en `config.env` |
| Resolución de proceso | `ANCHO_PROCESO` según la CPU disponible |

### Diferencias clave con un Arduino Uno clásico

| Aspecto | Arduino Uno clásico | Arduino UNO Q (este proyecto) |
|---|---|---|
| Arquitectura | AVR 8 bits @ 16 MHz | MPU QCC711 4× aarch64 + MCU STM32U585 |
| Memoria | 2 KB RAM | 3.6 GB RAM |
| ADC | 10 bits (0–1023) | 12 bits (0–4095) — **no usado aquí** |
| Lógica | 5 V | **3.3 V** — requiere level shifters para 5 V |
| Runtime | Bare metal / AVR | **Zephyr RTOS** en el MCU, **Debian Linux** en el MPU |
| Lenguaje | C++ (Arduino) | Python 3 (lado Linux) |
| Carga de código | USB, `arduino-cli upload` | `tar` + `ssh` (servicio), o ADB/OpenOCD (firmware) |
| Librerías | AVR-specific | Las de AVR **no** son compatibles con Zephyr |

---

## 14. Mantenimiento y buenas prácticas

### Rutina diaria de feria

| Cuándo | Qué |
|---|---|
| Al abrir | `servicio.sh status`; limpiar el lente del teléfono; confirmar borde verde |
| Cada 2 h | Mirar `logs 20` por si hay reconexiones; verificar batería del teléfono |
| Al cerrar | Dejar corriendo (consume ~130 % de CPU), o apagar de verdad con `servicio.sh boot-disable` y después `servicio.sh stop`; un `stop` solo se revive en ≤ 2 min |
| Después de la feria | Respaldar `config.env` si se calibró; revisar `data/vigia.log` |

### Rutina semanal

```bash
# tamaño de logs
du -sh ~/vigia-caminante-unoq/data/*

# errores en el log
grep -iE "error|exception|traceback" ~/vigia-caminante-unoq/data/vigia.log | tail -20

# estado del disco (la raíz es chica)
df -h /
```

La rotación de logs es automática (5 MB × 2 = máximo ~15 MB por archivo de log). No hace falta
`logrotate`.

### Respaldar la calibración

```bash
scp -i ~/.ssh/unoq_key -P 2222 \
  arduino@192.168.1.5:~/vigia-caminante-unoq/config.env \
  ./config.env.respaldo
```

### Actualizar sin perder la configuración

`desplegar.sh` **conserva** `config.env` y `data/` en la placa (solo borra `src/`, `web/`,
`tests/`, `scripts/` antes de copiar).

### Fijar la IP del teléfono

En el router, buscar el dispositivo Android y asignarle una reserva DHCP (`192.168.1.9`). Eso
elimina la causa más común de "se puso gris".

---

## 15. Advertencias

| ⚠️ Riesgo | Detalle |
|---|---|
| **No es seguridad certificada** | Es un asistente heurístico. No reemplaza la atención humana. No hay redundancia ni validación formal |
| **Gris ≠ vía libre** | Gris significa "no tengo información". Nunca interpretarlo como seguro |
| **Depende del WiFi** | Si la red cae, el sistema queda ciego. Un WiFi saturado degrada fps y aumenta la latencia |
| **Imagen sin cifrar** | El video viaja en HTTP plano. Cualquiera en la LAN puede verlo |
| **No exponer 8030 a internet** | Sin autenticación. `POST /api/config` permite cambiar la cámara y los umbrales |
| **Lente sucio o contraluz** | Degrada la detección sin aviso. Limpiar el lente y evitar apuntar a ventanas |
| **Score heurístico** | Sensible a cambios de luz, sombras y texturas de piso. Es para un pasillo, no para exteriores |
| **Cámara en movimiento** | Mover el teléfono dispara el score durante ~1 s |
| **Un cliente pesado por stream** | Cada pestaña abierta con `/stream` consume CPU y ancho de banda |
| **Sin historial** | No hay grabación ni eventos; al reiniciar se pierde el estado |
| **3.3 V si agregás hardware** | No hay pines en uso, pero la lógica de la UNO Q es de 3.3 V: level shifters para 5 V |
| **ADC de 12 bits** | Si algún día usás entradas analógicas, el rango es 0–4095, no 0–1023 |
| **Zephyr ≠ AVR** | El lado MCU corre Zephyr; las librerías AVR no son compatibles |
| **No instalar paquetes en la placa** | El disco raíz tiene ~768 MB libres. Instalar OpenCV desde PyPI puede romper el sistema |

---

## 16. Glosario

| Término | Significado |
|---|---|
| **MJPEG** | Secuencia de imágenes JPEG enviadas como un stream HTTP multipart |
| **MOG2** | *Mixture of Gaussians v2*: algoritmo de sustracción de fondo de OpenCV |
| **Contorno** | Curva cerrada que delimita una región blanca en una máscara binaria |
| **Bounding box** | Rectángulo `x, y, w, h` que encierra un objeto detectado |
| **IoU** | *Intersection over Union*: solapamiento entre dos cajas, de 0 a 1 |
| **Tracking** | Mantener el mismo ID para un objeto a lo largo de los frames |
| **Looming** | Crecimiento relativo del tamaño aparente de un objeto que se acerca |
| **TTC** | *Time to contact*: segundos estimados hasta el contacto |
| **Score** | Número de 0 a 1 que resume el riesgo de un objeto o de la escena |
| **EMA** | Media móvil exponencial: suaviza el score en el tiempo |
| **Histéresis** | Usar umbrales distintos para subir y bajar de nivel, para evitar parpadeos |
| **Watchdog** | Vigilante que detecta ausencia de frames y publica `NO_SIGNAL` |
| **Corredor** | Banda de la imagen donde un objeto se considera en trayectoria de choque |
| **Backoff** | Espera creciente entre reintentos de conexión (1, 2, 4, … 30 s) |
| **SSRF** | *Server-Side Request Forgery*: forzar al servidor a pedir URLs internas; el proyecto lo bloquea |
| **Modo demo** | Escena sintética que reemplaza al teléfono para probar la UI |

---

<p align="center">
  <strong>Vigía Caminante UNO Q v1.0.0</strong><br>
  Manual de usuario · Arduino UNO Q · QCC711 aarch64 · Debian 13<br>
  <em>Si el borde está gris, no es que esté todo bien: es que no se ve nada.</em>
</p>
