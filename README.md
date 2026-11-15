# 🛰️ Vigía Caminante — Arduino UNO Q

![versión](https://img.shields.io/badge/versi%C3%B3n-1.0.0-22c55e)
![placa](https://img.shields.io/badge/placa-Arduino%20UNO%20Q-00979D)
![host](https://img.shields.io/badge/host-QCC711%20·%20aarch64-0b0f14)
![python](https://img.shields.io/badge/python-3.13.5-3776AB)
![opencv](https://img.shields.io/badge/OpenCV-4.10.0-5C3EE8)
![tests](https://img.shields.io/badge/tests-38%20OK-22c55e)
![sin%20GPU](https://img.shields.io/badge/GPU-no%20usa-f59e0b)
![sin%20nube](https://img.shields.io/badge/nube-no%20usa-f59e0b)

> Un teléfono camina por la feria. La UNO Q mira lo que ve el teléfono, calcula si algo
> se le va a venir encima y lo grita con un borde de color: **verde**, **ámbar**, **rojo**.
> Todo el análisis ocurre en la placa. Nada sale a internet.

---

## 📑 Índice

1. [Qué es esto](#-qué-es-esto)
2. [El problema que resuelve](#-el-problema-que-resuelve)
3. [Cómo funciona (principio físico)](#-cómo-funciona-principio-físico)
4. [El semáforo de riesgo](#-el-semáforo-de-riesgo)
5. [Hardware y software](#-hardware-y-software)
6. [⚠️ Esto no es un sketch: no hay pinout](#️-esto-no-es-un-sketch-no-hay-pinout)
7. [Diagrama de conexión](#-diagrama-de-conexión)
8. [Arquitectura del software](#-arquitectura-del-software)
9. [Puesta en marcha](#-puesta-en-marcha)
10. [Servicio en la placa (producción)](#servicio-produccion)
11. [Cómo se opera en la feria](#-cómo-se-opera-en-la-feria)
12. [La página web](#-la-página-web)
13. [API en 30 segundos](#-api-en-30-segundos)
14. [Calibración](#-calibración)
15. [Rendimiento medido](#-rendimiento-medido)
16. [Tests](#-tests)
17. [Estructura del repositorio](#-estructura-del-repositorio)
18. [Problemas frecuentes](#-problemas-frecuentes)
19. [Advertencias y límites](#️-advertencias-y-límites)
20. [Notas de ingeniería que valen la pena](#-notas-de-ingeniería-que-valen-la-pena)
21. [Estado del proyecto](#-estado-del-proyecto)

---

## 🎯 Qué es esto

**Vigía Caminante UNO Q** es un asistente visual de colisión. Un teléfono Android con la app
**IP Webcam** transmite video por WiFi. La **Arduino UNO Q** lo consume, detecta objetos que se
mueven, estima si alguno se acerca de frente y publica una página web con el video anotado.

El resultado se lee de un vistazo, sin mirar números:

```
┌──────────────────────────────────────────────┐
│                                              │
│   ████████████████████████████████████████   │  ← borde VERDE:  paso libre
│   ██                                    ██   │
│   ██        [ ID7  62%  TTC 1.4s ]      ██   │
│   ██         ┌──────────────┐           ██   │
│   ██         │              │           ██   │
│   ██         │   peatón     │           ██   │
│   ██         │              │           ██   │
│   ██         └──────────────┘           ██   │
│   ██  PASO LIBRE                        ██   │
│   ██  score 28%   TTC —                 ██   │
│   ██  12.5 fps   55 ms                 ██   │
│   ████████████████████████████████████████   │
│                                              │
└──────────────────────────────────────────────┘
```

- **VERDE** → nadie viene de frente.
- **ÁMBAR** → hay algo en el corredor, ojo.
- **ROJO** → choque inminente.
- **GRIS** → se perdió la señal de la cámara.

El borde no es decoración: es la única señal que hace falta mirar mientras se camina.

### Lo que **no** es

| No es… | Porque… |
|---|---|
| Un sistema de seguridad certificado | Es un asistente heurístico, no reemplaza la atención humana |
| Deep learning | Corre visión clásica en CPU: MOG2 + contornos + tracking + heurística |
| Un proyecto de firmware | No compila `.ino`, no toca el STM32, no usa GPIO |
| Un servicio en la nube | Todo ocurre en la placa, en la LAN, sin internet |
| Un grabador de video | No persiste imágenes ni historial; solo estado en vivo |

---

## 🧩 El problema que resuelve

En una feria científica la gente camina mirando pantallas, no el piso. Un chico con un teléfono
grabando puede cruzarse con otro visitante sin verlo. La pregunta operativa es simple:

> **¿Estoy a punto de chocar con algo?**

Y la respuesta útil no es una lista de objetos: es un semáforo. Por eso el proyecto entero está
optimizado para una sola salida — **un color en el borde de la pantalla** — y todo lo demás
(score, TTC, fps, cajas, IDs) está ahí para poder explicar *por qué* cambió ese color cuando
alguien pregunta en el stand.

---

## 🔬 Cómo funciona (principio físico)

### La geometría del susto

Una cámara proyecta el mundo así: un objeto de altura real `H` a distancia `Z` aparece en el
sensor con tamaño

```
        s = f · H / Z            (f = distancia focal en píxeles)
```

De ahí salen los dos fenómenos que el sistema mide:

1. **Se agranda.** Si `Z` baja, `s` sube. El ritmo de crecimiento relativo de área se llama
   *looming* y vale, aproximadamente, `(dA/dt)/A ≈ 2·v/Z`.
2. **Se viene abajo.** En una imagen vertical, "más abajo" es "más cerca": el punto de apoyo del
   objeto baja por el cuadro a medida que se aproxima. La UNO Q usa `y` creciente como
   sinónimo de cercanía.

Con esas dos cosas se estima el **tiempo al contacto** (TTC, *time to contact*):

```
        τ = (alto_frame − y_centro) / v_y        válido solo si v_y > 20 px/s
        τ = ∞                                    si el objeto no baja lo suficiente
```

`τ` es el corazón del proyecto. No mide distancia: mide **cuánto falta**. Si `τ` es chico, el
choque es inminente, sin importar si el objeto es una persona, una caja o una pared.

### La intuición que hace todo fácil de explicar

> Si algo **está abajo**, **crece rápido** y **baja rápido**, te va a pegar.

Cada una de esas tres frases es un término del score. No hay caja negra que justificar: cuando
alguien pregunta "¿por qué se puso rojo?", se le muestra el `score` y el `TTC` en la API, y se
puede señalar la caja con su ID.

### El pipeline, frame por frame

```
 frame 1080×1920 del teléfono
        │
        ▼
 ① reducir a 320 px de ancho + gris + blur 5×5      ← 14 ms en el Cortex-A53
        │
        ▼
 ② MOG2 (sustracción de fondo) → máscara binaria
    cierre 7×7 + apertura 5×5 → contornos externos
        │
        ▼
 ③ filtrar: área mínima 200 px, aspecto máximo 5:1
    (un objeto que llena el cuadro NO se filtra: es amenaza máxima)
        │
        ▼
 ④ tracker por IoU ≥ 0.3: ID persistente, vx/vy, dA/dt
        │
        ▼
 ⑤ por objeto: 5 pesos → score 0..1
      cercanía² 25% · velocidad 20% · looming 25% · área 15% · TTC 15%
        │
        ▼
 ⑥ global: max(scores) → EMA (τ=0.3 s) → histéresis con 800 ms de rojo mínimo
        │
        ▼
 ⑦ overlay: borde 12 px + cajas + etiquetas + HUD
        │
        ▼
 ⑧ JPEG calidad 70 a 540 px  →  /stream  →  navegador
```

**Sustracción de fondo (MOG2)** significa: el sistema aprende cómo se ve la escena vacía y todo
lo que *no* coincide con ese fondo es "algo que se movió". Es barato, es explicable y funciona
con luz de feria. Su precio: si movés la cámara de golpe, *todo* parece moverse durante un
segundo, y la placa lo resuelve con un calentamiento de 5 frames al arrancar
(`NO_SIGNAL · "Calibrando fondo de escena"`) y con la histéresis del score.

---

## 🚦 El semáforo de riesgo

| Nivel | Color | Borde | Significado | Condición de entrada |
|---|---|---|---|---|
| `NO_SIGNAL` | gris `#6b7280` | gris | Sin cámara, sin frames o análisis detenido | `frame_age_ms > watchdog_ms` (2000 ms) o `modo = detenido` |
| `GREEN` | verde `#22c55e` | verde | Paso libre | `score < 0.40` |
| `AMBER` | ámbar `#f59e0b` | ámbar | Hay algo en el corredor o acercándose | `score ≥ 0.40` |
| `RED` | rojo `#ef4444` | rojo | Colisión inminente | `score ≥ 0.70` |

Y para **bajar** de nivel se exige más que para subir (histéresis):

| Transición | Se mantiene si… |
|---|---|
| `RED → AMBER` | `score ≤ 0.55` **y** ya pasaron 800 ms en rojo |
| `AMBER → GREEN` | `score ≤ 0.25` |

```
                     score ≥ 0.70
        GREEN ──────────────────────► RED
          ▲   ◄──────────────────┐     │
          │      score ≤ 0.25    │     │ score ≤ 0.55
          │                      │     │ + 800 ms
          │   score ≥ 0.40   ┌───┴─────▼───┐
          └───────────────── │    AMBER    │
             ◄──────────────  └─────────────┘
                score ≤ 0.25
```

Sin histéresis el borde parpadearía entre dos colores en cada frame y sería inservible. Esa es
toda la razón de los cuatro umbrales en lugar de dos.

**Máquina de estados completa** (incluida la señal):

```
   ┌────────────┐  frames frescos   ┌────────────┐
   │ NO_SIGNAL  │ ────────────────► │  GREEN     │
   │  (gris)    │ ◄──────────────── │  (verde)   │
   └────────────┘  watchdog vencido └─────┬──────┘
         ▲                                │ score ≥ 0.40
         │                                ▼
         │   modo=detenido          ┌────────────┐
         │   o sin frames           │   AMBER    │
         └──────────────────────────│  (ámbar)   │
                                    └─────┬──────┘
                                          │ score ≥ 0.70
                                          ▼
                                    ┌────────────┐
                                    │    RED     │
                                    │   (rojo)   │
                                    └────────────┘
```

---

## 🧰 Hardware y software

### La placa

| Ítem | Detalle |
|---|---|
| Placa | **Arduino UNO Q** |
| Co-procesador Linux (MPU) | **QCC711**, 4 núcleos **aarch64** |
| RAM | **3.6 GB** (≈2.9 GB libres) |
| Sistema operativo del MPU | **Debian GNU/Linux 13 (trixie)** aarch64 |
| Python | **3.13.5** (system-wide) |
| Visión | **OpenCV 4.10.0** + **numpy 2.2.4** (ya preinstalados) |
| Microcontrolador real-time | **STM32U585** (Cortex-M33 con TrustZone) — **no se usa** |
| RTOS del MCU | **Zephyr RTOS** — **no se usa en este proyecto** |
| Alimentación | USB-C 5 V |
| Red | WiFi de la feria, IP fija `192.168.1.5` |
| Puerto del servicio | **8030/TCP** |
| Almacenamiento del proyecto | < 1 MB (sin logs) |

> ⚠️ **Los puertos 8000, 8010 y 8020 de esta misma placa están ocupados por otros servicios**
> (Detector de tarjetas, SuperFoto, Pasillo Seguro). Este proyecto **no los toca**: usa 8030 y nada más.

### El resto del sistema

| Componente | Detalle |
|---|---|
| Teléfono | Android con **IP Webcam** (Pavel Khlebovich), IP `192.168.1.9` |
| Stream del teléfono | `http://192.168.1.9:8080/video` (MJPEG, 1080×1920 vertical) |
| Snapshot del teléfono | `http://192.168.1.9:8080/shot.jpg` (solo para diagnóstico) |
| Router | WiFi 2.4/5 GHz, ambos equipos en `192.168.1.0/24` |
| Cliente | Cualquier navegador: `http://192.168.1.5:8030/` |
| PC de desarrollo | Linux con `ssh`, `tar`, `curl` (no hace falta `rsync` en la placa) |

---

## ⚠️ Esto no es un sketch: no hay pinout

Este proyecto **no usa el microcontrolador**. No hay firmware, no hay `.ino`, no hay `setup()` ni
`loop()`, no se compila con `arduino-cli`, no se flashea por SWD, no hay sensores cableados y
**no hay pines que conectar**.

Todo corre como un proceso Python en el lado Linux de la UNO Q (el QCC711). Si buscabas un
pinout para replicar el hardware: no existe, y esa es la respuesta correcta. Lo que sí hay es una
**tabla de conexión lógica**:

### Tabla de conexión lógica

| "Pin" / Interfaz | Modo | Componente conectado | Notas |
|---|---|---|---|
| `wlan0` — salida a `192.168.1.9:8080` | Cliente HTTP (consumidor) | Teléfono Android con IP Webcam | MJPEG en `/video`, 1080×1920. Timeout 5 s, reconexión con backoff 1→30 s |
| `wlan0` — `192.168.1.5:8030` | Servidor HTTP (productor) | Navegador del visitante (celular o notebook) | Sirve `/`, `/stream`, `/api/estado`, `/api/config`, `/snapshot.jpg`, `/health` |
| `wlan0` — DHCP/estática | Red | Router WiFi de la feria | Ambos extremos en `192.168.1.0/24` |
| USB-C | Alimentación | Fuente 5 V / 3 A | La placa se alimenta por USB-C |
| GPIO 0–13, A0–A5 | **NO USADO** | — | Sin sensores ni actuadores |
| I²C / SPI / UART | **NO USADO** | — | Sin periféricos |
| STM32U585 (MCU) | **NO USADO** | — | El firmware Zephyr queda intacto |
| ADC 12 bits | **NO USADO** | — | No se lee ninguna entrada analógica |

---

## 🔌 Diagrama de conexión

No hay cables de señal. Hay tres equipos y una red. Este es el cableado completo del proyecto:

```
                    ┌─────────────────────────────────────────┐
                    │            ROUTER WiFi                  │
                    │         red 192.168.1.0/24              │
                    └───────┬─────────────────────┬───────────┘
                            │                     │
              WiFi 2.4/5 GHz│                     │WiFi 2.4/5 GHz
                            │                     │
        ┌───────────────────▼──────┐   ┌──────────▼─────────────────────┐
        │  TELÉFONO ANDROID        │   │   ARDUINO UNO Q                │
        │  IP Webcam               │   │   QCC711 · Debian 13 aarch64   │
        │  192.168.1.9             │   │   192.168.1.5                  │
        │                          │   │                                │
        │  :8080/video   MJPEG ────┼──►│  CamaraThread  (cliente HTTP)  │
        │  :8080/shot.jpg  JPEG    │   │  VisionThread  (OpenCV/MOG2)   │
        │                          │   │  Servidor HTTP :8030           │
        └──────────────────────────┘   └──────────┬─────────────────────┘
                                                  │
                                                  │ HTTP :8030
                                                  │ (LAN, sin cifrar)
                                     ┌────────────▼─────────────┐
                                     │  NAVEGADOR               │
                                     │  http://192.168.1.5:8030 │
                                     │  celular o notebook      │
                                     └──────────────────────────┘
```

### Paso a paso del conexionado

1. **Alimentá la UNO Q** por USB-C (5 V). Esperá ~30 s a que Debian arranque y levante WiFi.
2. **Conectá la UNO Q a la WiFi de la feria.** Verificá que tenga la IP fija `192.168.1.5`:
   ```bash
   ssh -i ~/.ssh/unoq_key -p 2222 arduino@192.168.1.5 'ip -4 addr show wlan0'
   ```
3. **Conectá el teléfono a la misma WiFi** y abrí IP Webcam. En la app, anotá la IP que muestra
   (debe ser `192.168.1.9`) y presioná *Start server*.
4. **Verificá el teléfono desde la UNO Q** (esto descarta el 90 % de los problemas):
   ```bash
   ssh -i ~/.ssh/unoq_key -p 2222 arduino@192.168.1.5 \
     'curl -s -m 5 -o /dev/null -w "%{http_code}\n" http://192.168.1.9:8080/shot.jpg'
   # debe imprimir 200
   ```
5. **No hay nada más que cablear.** Ni resistencias, ni level shifters, ni pull-ups, ni
   alimentación a 3.3 V: no hay pines involucrados.

> 🧠 **Por qué no hay hardware**: la "cámara" del sistema es el teléfono, y el "cable" es el
> WiFi. La UNO Q solo aporta CPU, memoria y una IP. Cualquier agregado físico (sensores,
> relés, displays) sería un proyecto distinto.

### Diagrama de despliegue (desarrollo → placa)

```
  NOTEBOOK DE DESARROLLO                        ARDUINO UNO Q
  ~/Documents/…/proyectos/vigia-caminante-unoq  /home/arduino/vigia-caminante-unoq
  ┌───────────────────────┐    SSH :2222       ┌──────────────────────────────┐
  │ scripts/desplegar.sh  │───(tar|ssh)───────►│ src/   web/   scripts/       │
  │ ~/.ssh/unoq_key       │   sin rsync        │ config.env (no versionado)   │
  └───────────────────────┘                    │ data/  (pid, logs)           │
                                               └──────────────────────────────┘
```

---

## 🏗️ Arquitectura del software

Cinco hilos, un proceso, cero dependencias exóticas.

```
                      ┌──────────────────────┐
   teléfono ──MJPEG──►│ CamaraThread         │──┐
                      │ cv2 o lector propio  │  │  frame crudo + timestamp
                      └──────────────────────┘  │  (Condition, versión)
                                                ▼
                      ┌──────────────────────┐  ┌───────────────────────┐
   modo demo ────────►│ DemoThread           │─►│  Estado (thread-safe) │
   (sin teléfono)     │ escena sintética     │  │  · frame crudo        │
                      └──────────────────────┘  │  · JPEG anotado       │
                                                │  · estado lógico      │
                      ┌──────────────────────┐  │  · ajustes calientes  │
                      │ VisionThread         │◄─┤                       │
                      │ resize→MOG2→tracker  │  └───────────┬───────────┘
                      │ score→overlay→JPEG   │              │
                      └──────────────────────┘              │
                                                            ▼
                      ┌──────────────────────┐  ┌───────────────────────┐
                      │ Watchdog (main)      │  │ HTTP ThreadingServer  │
                      │ frame viejo→GREEN?   │  │ :8030 · 1 hilo/cliente│
                      │ NO, → NO_SIGNAL      │  └───────────┬───────────┘
                      └──────────────────────┘              │
                                                            ▼
                                                       navegador
```

| Hilo | Archivo | Responsabilidad |
|---|---|---|
| `CamaraThread` | `src/vigia/camara.py` | Lee MJPEG del teléfono. Intenta OpenCV/FFMPEG; si falla usa un parser multipart propio. Reconexión con backoff 1→30 s. Detecta cambios de URL en caliente |
| `DemoThread` | `src/vigia/demo.py` | Genera un pasillo en perspectiva con un caminante que se acerca en 10 s. Permite mostrar el semáforo sin teléfono |
| `VisionThread` | `src/vigia/vision.py` | Reduce, detecta, trackea, puntúa, dibuja y codifica. Nunca muere por una excepción |
| `http` | `src/vigia/servidor.py` | `ThreadingHTTPServer`: UI estática, `/stream`, API JSON, snapshots |
| `watchdog` | `src/main.py` | Si el frame tiene más de 2 s, fuerza `NO_SIGNAL`. Usa compare-and-set para no pisar un estado fresco |

**Candados**: tres, independientes, para no serializar todo el pipeline.

| Candado | Protege | Quién escribe | Quién lee |
|---|---|---|---|
| `_cond` (Condition) | frame crudo, versión, JPEG anotado | cámara/demo, visión | visión, HTTP |
| `_lock_estado` | `EstadoFrame` (nivel, score, objetos) | visión, watchdog | HTTP, visión, watchdog |
| `_lock_config` | modo, URL de cámara, ajustes | HTTP | todos |

Orden global de adquisición: **`_lock_config` → `_lock_estado`**. Nunca al revés. Eso evita
interbloqueos entre el hilo HTTP (que cambia modo) y el watchdog (que cambia estado).

Sin `cv2.Mat` compartido y mutable: el frame que se anota es **siempre una copia**.

---

## 🚀 Puesta en marcha

### 1) Requisitos

| Requisito | Cómo se comprueba |
|---|---|
| UNO Q encendida y en la red con `192.168.1.5` | `ping 192.168.1.5` |
| SSH por el puerto **2222** con la clave `~/.ssh/unoq_key` | `ssh -i ~/.ssh/unoq_key -p 2222 arduino@192.168.1.5 true` |
| OpenCV y numpy en la placa | `python3 -c "import cv2,numpy;print(cv2.__version__,numpy.__version__)"` → `4.10.0 2.2.4` |
| Teléfono con IP Webcam en `192.168.1.9:8080` | `curl -s -o /dev/null -w '%{http_code}' http://192.168.1.9:8080/shot.jpg` → `200` |
| Puerto 8030 libre | `ss -ltn \| grep 8030` sin resultados |

> 🚫 **No se instala nada.** No hay `pip install`, no hay `sudo apt`, no hay entorno virtual. La
> placa ya trae todo lo necesario y el disco raíz es chico a propósito.

### 2) Configurar

```bash
# en la placa
cd ~/vigia-caminante-unoq
cp -n config.example config.env
nano config.env          # ajustar CAM_URL si el teléfono cambió de IP
```

Variables principales (todas con default seguro si faltan):

| Clave | Default | Qué hace |
|---|---|---|
| `PS_HOST` / `PS_PORT` | `0.0.0.0` / `8030` | Dónde escucha el servidor web |
| `CAM_URL` | `http://192.168.1.9:8080` | Base del teléfono; acepta también la URL completa `/video` |
| `MODO` | `camara` | `camara`, `demo` o `detenido` al arrancar |
| `ANCHO_PROCESO` | `320` | Ancho de trabajo de la visión (más chico = más fps) |
| `ANCHO_SALIDA` | `540` | Ancho del video entregado (`0` = resolución original) |
| `JPEG_CALIDAD` | `70` | Calidad del stream |
| `BORDE_GROSOR` | `12` | Grosor del borde de riesgo |
| `BG_HISTORY` / `BG_VAR_THRESHOLD` | `200` / `25` | Memoria y sensibilidad del fondo MOG2 |
| `AREA_MINIMA_PX` | `200` | Ignorar manchas menores a esto (a 320 px de ancho) |
| `AREA_MAXIMA_REL` | `0.95` | A partir de esta fracción del cuadro, **amenaza máxima** (no se descarta) |
| `MORPH_CLOSE_K` / `MORPH_OPEN_K` | `7` / `5` | Cierre y apertura morfológica |
| `ASPECTO_MAX` | `5.0` | Descarta formas extremadamente alargadas |
| `CORREDOR_Y_MIN` / `CORREDOR_X_MIN` / `CORREDOR_X_MAX` | `0.35` / `0.25` / `0.75` | Corredor de colisión en coordenadas normalizadas |
| `PESO_CERCANIA` / `PESO_VELOCIDAD` / `PESO_LOOMING` / `PESO_AREA` / `PESO_TTC` | `0.25` / `0.20` / `0.25` / `0.15` / `0.15` | Pesos del score (suman 1.0) |
| `VEL_Y_CRITICA` / `VEL_Y_MIN_TTC` | `150` / `20` | px/s de acercamiento; umbral mínimo para estimar TTC |
| `LOOM_CRITICO` | `2.0` | Crecimiento relativo de área por segundo que satura el término looming |
| `AREA_PEQUENA` / `AREA_GRANDE` | `0.02` / `0.20` | Áreas de referencia para el término de tamaño |
| `TTC_CRITICO` | `2.0` | Segundos; escala de decaimiento del término TTC |
| `TAU_EMA` | `0.3` | Constante de suavizado del score |
| `UMBRAL_ROJO_IN` / `UMBRAL_ROJO_OUT` | `0.70` / `0.55` | Histéresis del rojo |
| `UMBRAL_AMBAR_IN` / `UMBRAL_AMBAR_OUT` | `0.40` / `0.25` | Histéresis del ámbar |
| `MIN_TIEMPO_ROJO_MS` | `800` | Tiempo mínimo en rojo (anti-parpadeo) |
| `WATCHDOG_MS` | `2000` | Antigüedad máxima del frame antes de `NO_SIGNAL` (mínimo 200) |
| `FPS_TARGET` | `15.0` | Ritmo objetivo del hilo de visión |
| `LOG_LEVEL` / `LOG_FILE` | `INFO` / `data/vigia.log` | Registro, con rotación 5 MB × 2 |

`config.env` **no se versiona**. `config.example` es la plantilla.

### 3) Desplegar

```bash
# desde la notebook, en la raíz del proyecto
cd /home/jma/Documents/Proyects/OpenCode/AG.CreadorContenido/proyectos/vigia-caminante-unoq
./scripts/desplegar.sh
```

El script empaqueta con `tar` (la placa **no tiene rsync**), copia por SSH al puerto 2222, crea
`config.env` si no existe, ajusta `CAM_URL` y `PS_PORT`, da permisos a `scripts/*.sh`, habilita
`@reboot` y reinicia el servicio. Termina mostrando `servicio.sh status`.

Se puede parametrizar por variables de entorno:

```bash
UNOQ_HOST=192.168.1.5 UNOQ_SSH_PORT=2222 UNOQ_LLAVE=~/.ssh/unoq_key \
CAM_URL=http://192.168.1.9:8080/video PS_PORT=8030 \
./scripts/desplegar.sh

./scripts/desplegar.sh --sin-arranque    # copia sin reiniciar el servicio
```

### 4) Arrancar y verificar

```bash
ssh -i ~/.ssh/unoq_key -p 2222 arduino@192.168.1.5
cd ~/vigia-caminante-unoq

./scripts/servicio.sh status     # PID, uptime, CPU/RSS, health, nivel actual
./scripts/servicio.sh restart
./scripts/servicio.sh logs 80
./scripts/servicio.sh logs -f
./scripts/servicio.sh boot-enable    # @reboot; los guardianes ya están instalados
```

Verificación rápida desde cualquier equipo de la LAN:

```bash
curl -s http://192.168.1.5:8030/health | python3 -m json.tool
curl -s http://192.168.1.5:8030/api/estado | python3 -m json.tool
curl -s http://192.168.1.5:8030/snapshot.jpg -o /tmp/vigia.jpg && file /tmp/vigia.jpg
```

### 5) ¿Y `arduino-cli compile` / `upload`?

**No aplican a este proyecto**, y conviene decirlo fuerte para que nadie pierda una tarde:

| Paso habitual de un sketch | En este proyecto |
|---|---|
| `arduino-cli compile --fqbn arduino:zephyr:unoq` | ❌ No hay `.ino` ni firmware que compilar |
| `arduino-cli upload -p /dev/ttyACM0 …` | ❌ No se flashea el STM32; se despliega con `tar` + `ssh` |
| `arduino-cli monitor` | ❌ El "monitor" es `scripts/servicio.sh logs -f` |

El FQBN `arduino:zephyr:unoq` identifica a la placa, pero el código que corre aquí es **Python
sobre Debian**, no Zephyr. Si en algún momento quisieras volver a flashear el lado MCU (no es
necesario para este proyecto), ese sería el camino:

```bash
# SOLO si querés reflashear el STM32. NO es parte de este proyecto.
arduino-cli board list          # debe listar la UNO Q (ej. /dev/ttyACM0)
arduino-cli core install arduino:zephyr
arduino-cli compile --fqbn arduino:zephyr:unoq <sketch_dir>
arduino-cli upload -p /dev/ttyACM0 --fqbn arduino:zephyr:unoq <sketch_dir>
arduino-cli monitor -p /dev/ttyACM0 --config baudrate=115200
```

> 🔑 Si `adb` o el upload fallan por permisos, revisá que exista la regla udev para el USB ID
> `2341:0078` (`/etc/udev/rules.d/99-arduino-unoq.rules`). Tocar reglas udev requiere `sudo`:
> pedí autorización antes, no lo hagas por tu cuenta.

### 6) Ejecución manual (para depurar)

```bash
cd ~/vigia-caminante-unoq

# con config.env
python3 src/main.py

# con otro archivo de configuración
python3 src/main.py /ruta/a/otro.env
```

En primer plano se ve el log por consola, con `Ctrl+C` se apaga limpio (SIGINT/SIGTERM).

---

<a id="servicio-produccion"></a>
## 🛰️ Servicio en la placa (producción)

En la UNO Q el Vigía corre como servicio en **<http://192.168.1.5:8030/>**. Arranca solo al
bootear (cron `@reboot` con 20 s de espera) y tiene **dos guardianes**: uno lo levanta si el
proceso muere (cada 2 min) y otro lo reinicia si `/health` deja de responder (cada 10 min).
No hay que hacer nada para que vuelva tras un corte.

Los cuatro comandos del día a día, desde `~/vigia-caminante-unoq` en la placa:

```bash
./scripts/servicio.sh status      # ¿está vivo? PID, uptime, CPU/RSS, health y nivel
./scripts/servicio.sh start       # arrancar (idempotente: si vive, no hace nada)
./scripts/servicio.sh restart     # después de cambiar código o config.env
./scripts/servicio.sh logs -f     # ver el log en vivo (Ctrl+C para salir)
```

> ⚠️ `stop` **no es definitivo**: el guardián de proceso lo revive en ≤ 2 minutos. Para apagarlo
> de verdad hay que hacer `boot-disable` y después `stop`.

Manual completo de operación, acceso SSH, checklist de feria y resolución de problemas:
**[`docs/servicio.md`](docs/servicio.md)**.

---

## 🎪 Cómo se opera en la feria

### Encendido (2 minutos, en orden)

| # | Acción | Verificación |
|---|---|---|
| 1 | Alimentar la UNO Q por USB-C | El LED de la placa enciende |
| 2 | Conectar el teléfono a la WiFi de la feria | El teléfono tiene IP `192.168.1.9` |
| 3 | Abrir **IP Webcam** → *Start server* | La app muestra `http://192.168.1.9:8080` |
| 4 | Abrir `http://192.168.1.5:8030/` en un navegador | Se ve el video con borde **verde** |
| 5 | Caminar hacia la cámara | El borde pasa a **ámbar** y luego a **rojo** |
| 6 | Alejarse | Vuelve a **ámbar** y después a **verde** |

Si el paso 4 falla, el problema está en la red o en el servicio, no en la visión:
probalo primero en **modo demo** (botón *Demo*), que no necesita teléfono.

### Durante la demostración

| Situación | Qué hacer |
|---|---|
| Querés mostrar el semáforo sin teléfono | Botón **Demo** → ciclo `verde → ámbar → rojo → ámbar → verde` cada 10 s |
| El teléfono cambió de IP | Pegar la URL nueva en el campo y pulsar **Aplicar** (no reinicia el proceso) |
| Querés congelar la alerta | Botón **Detener** → borde gris, "ANALISIS DETENIDO" |
| Volver a la cámara real | Botón **Iniciar** |
| Alguien pregunta "¿por qué rojo?" | Abrir `/api/estado` y mostrar `score`, `ttc` y `objetos[]` |

### Guion de stand (30 segundos)

1. "Este teléfono camina por la feria. Mirá la pantalla de la placa."
2. Alguien se cruza → el borde se pone **rojo**.
3. "No hay inteligencia artificial entrenada: es geometría. Mide cuánto tarda en chocar."
4. Mostrar `/api/estado`: `score 0.91`, `ttc 0.4`, `n_objetos 1`.
5. "Corre en la placa, sin internet, a 12 fps."

---

## 🖥️ La página web

Una sola página, sin frameworks, sin build, sin CDN. `web/index.html` + `app.js` + `style.css`.

```
┌──────────────────────────────────────────────────────────┐
│  👁  Vigía Caminante   UNO Q          [ ¡PELIGRO! ]      │
├──────────────────────────────────────────────────────────┤
│                                                          │
│      ┌────────────────────────────────────────┐          │
│      │                                        │          │
│      │        VIDEO ANOTADO (MJPEG)           │          │
│      │        borde = nivel de riesgo         │          │
│      │                                        │          │
│      └────────────────────────────────────────┘          │
│      ¡PELIGRO! Objeto acercándose de frente              │
├────────────┬────────────┬────────────┬───────────────────┤
│  RIESGO    │    TTC     │    FPS     │     OBJETOS       │
│    91%     │   0.4 s    │   12.5     │        1          │
│  ▓▓▓▓▓▓▓▓  │            │            │                   │
├────────────┴────────────┴────────────┴───────────────────┤
│  URL de la cámara: [ http://192.168.1.9:8080 ] [Aplicar] │
│  [ Iniciar ]  [ Detener ]  [ Demo ]                      │
├──────────────────────────────────────────────────────────┤
│  Modo: camara   Latencia: 55 ms   Frame: 41 ms  Up: 812 s│
├──────────────────────────────────────────────────────────┤
│  verde paso libre · ámbar atención · rojo peligro · gris │
└──────────────────────────────────────────────────────────┘
```

- **Polling** de `/api/estado` cada 400 ms (liviano: es JSON de < 2 KB).
- El **badge** y el **borde** cambian por CSS con `body[data-nivel="…"]`, sin re-render.
- El video es `<img src="/stream">`: si el stream se corta, `app.js` lo reconecta solo a los 2.5 s.
- Responsive: pensado primero para celular vertical, funciona igual en notebook.
- Tema oscuro de alto contraste, legible a pleno sol de feria.

---

## 🔌 API en 30 segundos

| Método | Ruta | Devuelve |
|---|---|---|
| `GET` | `/` | UI (`web/index.html`) |
| `GET` | `/stream` | MJPEG anotado (`multipart/x-mixed-replace`) |
| `GET` | `/snapshot.jpg` | Último frame anotado en JPEG |
| `GET` | `/api/estado` | Estado completo en JSON (alias: `/api/status`) |
| `GET` | `/api/config` | Puerto, URL, modo, ajustes vigentes y umbrales base |
| `POST` | `/api/config` | Cambia URL, modo y umbrales **en memoria** |
| `GET` | `/health` | `{"status":"ok", …}` |

```bash
# estado en vivo
curl -s http://192.168.1.5:8030/api/estado | python3 -m json.tool

# cambiar de modo sin reiniciar
curl -s -X POST http://192.168.1.5:8030/api/config \
     -H 'Content-Type: application/json' -d '{"modo":"demo"}'

# cambiar la IP del teléfono en caliente
curl -s -X POST http://192.168.1.5:8030/api/config \
     -H 'Content-Type: application/json' \
     -d '{"camara_url":"http://192.168.1.9:8080/video"}'

# subir la sensibilidad al rojo (bajar el umbral)
curl -s -X POST http://192.168.1.5:8030/api/config \
     -H 'Content-Type: application/json' -d '{"umbral_rojo_in":0.62}'

# foto para el recuerdo
curl -s http://192.168.1.5:8030/snapshot.jpg -o vigia.jpg
```

Ejemplo de `/api/estado` con una persona acercándose:

```json
{
  "nivel": "RED",
  "score": 0.91,
  "ttc": 0.42,
  "fps": 12.4,
  "latencia_ms": 54.7,
  "frame_age_ms": 38,
  "n_objetos": 1,
  "objetos": [
    {
      "id": 7,
      "bbox": [402, 1105, 268, 590],
      "bbox_norm": [0.3722, 0.5755, 0.2481, 0.3073],
      "area_rel": 0.0763,
      "velocidad_px_s": 431.8,
      "velocidad_y": 415.2,
      "crecimiento_area_s": 3.41,
      "ttc": 0.42,
      "en_corredor": true,
      "nivel_individual": "RED",
      "score": 0.913
    }
  ],
  "modo": "camara",
  "camara_url": "http://192.168.1.9:8080/video",
  "mensaje": "¡PELIGRO! Objeto acercándose de frente",
  "timestamp": 1763215678.412,
  "uptime_s": 812.4,
  "ajustes": {}
}
```

`POST` inválido devuelve **400** y **no aplica nada** (el lote es atómico):

```bash
curl -s -X POST http://192.168.1.5:8030/api/config \
     -H 'Content-Type: application/json' -d '{"watchdog_ms":0}'
# {"error":"watchdog_ms debe ser >= 200","campo":["watchdog_ms"]}
```

---

## 🎚️ Calibración

El sistema viene calibrado para un pasillo de feria con luz artificial. Si tu escena es distinta,
se ajusta en tres lugares, de más rápido a más definitivo:

| Vía | Alcance | Persistente |
|---|---|---|
| `POST /api/config` | Cambia en caliente y se pierde al reiniciar | ❌ |
| `config.env` + `restart` | Definitivo | ✅ |
| Editar pesos en `config.py` | Cambio de comportamiento de fondo | ✅ (requiere redesplegar) |

### Recetas según el síntoma

| Síntoma | Qué tocar | En qué dirección |
|---|---|---|
| Marca **rojo** con sombras o con el piso | `umbral_rojo_in` ↑ o `area_minima_px` ↑ | Menos sensible |
| No marca **rojo** cuando alguien se viene encima | `umbral_rojo_in` ↓ (0.70 → 0.62) | Más sensible |
| Marca rojo demasiado pronto | `ttc_critico` ↓ (2.0 → 1.2) | Exige más urgencia |
| El borde parpadea rojo/ámbar | `min_tiempo_rojo_ms` ↑ (800 → 1200) | Más estable |
| Reacciona tarde a objetos rápidos | `tau_ema` ↓ (0.3 → 0.15) | Menos suavizado, más nervioso |
| Detecta cosas lejanas que no importan | `area_minima_px` ↑ (200 → 400) | Menos ruido |
| No detecta personas delgadas | `area_minima_px` ↓ (200 → 120) | Más sensible a lo chico |
| Todo el cuadro se pone rojo al mover la cámara | `bg_history` ↑ o esperar 5 frames | El fondo se recalibra |
| Solo importa el centro del pasillo | `corredor_x_min` ↑ / `corredor_x_max` ↓ | Corredor más angosto |

> ⚠️ Los umbrales deben mantener el orden `0 < ambar_out < ambar_in < rojo_out < rojo_in < 1`.
> Si mandás una combinación incoherente por la API, la respuesta es **400** y no se aplica nada.

### Cómo calibrar sin teléfono

El **modo demo** es el banco de pruebas: genera un caminante que se acerca en 10 s y siempre
produce el ciclo `verde → ámbar → rojo → ámbar → verde`. Sirve para verificar que la UI, el
overlay, el stream y la histéresis funcionan antes de tocar la cámara real.

```bash
curl -s -X POST http://192.168.1.5:8030/api/config -d '{"modo":"demo"}' \
     -H 'Content-Type: application/json'
```

### Cómo leer la calibración en vivo

```bash
# un renglón por segundo
while true; do
  curl -s http://192.168.1.5:8030/api/estado | python3 -c "
import sys, json
d = json.load(sys.stdin)
t = d['ttc']
print('%-9s score %.2f  ttc %-5s  obj %d  %.1f fps  %.0f ms' % (
    d['nivel'], d['score'] or 0.0, '-' if t is None else '%.2fs' % t,
    d['n_objetos'] or 0, d['fps'] or 0.0, d['latencia_ms'] or 0.0))"
  sleep 1
done
```

Para calibrar de verdad, mirá **`score`**, no el color: el color es el resultado de comparar el
score con los umbrales. Si un peatón que se acerca de frente te da `score 0.55`, sabés
exactamente cuánto bajar `umbral_rojo_in` (a 0.55 o menos) y no estás adivinando.

### Cálculo de ejemplo (para verificar a mano)

Con los defaults (`corredor_y_min=0.35`, `vel_y_critica=150`, `loom_critico=2.0`,
`area_pequena=0.02`, `area_grande=0.20`, `ttc_critico=2.0`):

| Objeto | cy | vy | area_rel | crec. | ttc | Score | Nivel |
|---|---|---|---|---|---|---|---|
| Peatón a media distancia | 0.72 | 180 | 0.10 | 1.2 /s | 1.4 s | **0.56** | ÁMBAR |
| Peatón encima | 0.90 | 420 | 0.35 | 3.5 /s | 0.4 s | **0.90** | ROJO |
| Objeto que llena el cuadro | — | — | ≥ 0.95 | — | — | **1.00** | ROJO (directo) |

Desglose del primero: `0.324·0.25 + 0.943·0.20 + 0.600·0.25 + 0.445·0.15 + 0.497·0.15 = 0.56`.

---

## ⚡ Rendimiento medido

Números reales tomados en la placa con el teléfono transmitiendo a 1080×1920:

| Métrica | Valor | Nota |
|---|---|---|
| FPS de proceso | **10.3 – 14.0** | El hilo de visión, con overlay y JPEG incluidos |
| Latencia por frame | **36 – 122 ms** (mediana ≈ **55 ms**) | De frame recibido a JPEG publicado |
| Memoria (RSS) | **190 – 230 MB** | De 3.6 GB totales (~6 %) |
| CPU | **120 – 155 %** de 400 % | Quedan ~2.5 núcleos libres |
| Captura | 1080×1920 MJPEG | Reducida a 320 px para analizar y 540 px para mostrar |
| Arranque | ~6 s hasta `health ok` | Importar OpenCV es lo más lento |

Decisiones que explican esos números:

| Decisión | Por qué |
|---|---|
| Analizar a **320 px** de ancho | MOG2 y contornos cuestan O(píxeles): 320 px es 11× menos trabajo que 1080 |
| **`INTER_LINEAR`** al reducir para visión | En el Cortex-A53, `INTER_AREA` tarda ~80 ms vs ~14 ms; el blur 5×5 posterior compensa el aliasing |
| **`INTER_AREA`** al reducir para mostrar | Se hace una sola vez por frame y da mejor calidad visual |
| Mostrar a **540 px** de ancho | Codificar 1080×1920 cuesta ~75 ms/frame; a 540 px baja a ~25 ms y en un celular se ve igual |
| **JPEG calidad 70** | La mitad de bytes que 90, sin artefactos visibles en movimiento |
| **Sin GPU, sin modelos** | No hay acelerador NN disponible y el disco raíz es chico; la visión clásica es explicable en el stand |

---

## 🧪 Tests

```bash
# en la placa (necesita cv2 + numpy)
cd ~/vigia-caminante-unoq
python3 -m unittest discover -s tests -v
```

**38 tests, todos en verde.** Cubren:

| Archivo | Qué verifica |
|---|---|
| `tests/test_riesgo.py` | Histéresis sube/baja, tiempo mínimo en rojo, no parpadeo en ámbar, score de objeto cercano/lejano, monotonía del score, tracking (ID persistente, separación, caducidad) |
| `tests/test_vision_sintetica.py` | El demo alcanza GREEN/AMBER/RED y vuelve a verde; escena vacía da verde sin objetos; endpoints HTTP reales (`/health`, `/api/estado`, `/snapshot.jpg`, `/`, `/app.js`, `/style.css`, `POST /api/config`) |
| `tests/test_correcciones_auditoria.py` | Objeto que llena el cuadro → RED; frame vacío no rompe; loopback/IP propia rechazadas y LAN aceptada; `loom_critico=0` y `watchdog_ms=0` → 400; umbrales incoherentes → 400; JPEG nunca vacío; `ANCHO_SALIDA=0` no muta el frame compartido; `#` no trunca URLs |
| `tests/test_i013_atomicidad.py` | 8+ POST concurrentes con `threading.Barrier` no dejan umbrales incoherentes; el validador corre dentro del candado; un lote rechazado no muta nada |

```bash
# un solo archivo
python3 -m unittest tests.test_riesgo -v

# un solo test
python3 -m unittest tests.test_correcciones_auditoria.TestObjetoGigante -v
```

---

## 📁 Estructura del repositorio

```
vigia-caminante-unoq/
├── README.md                     ← este archivo
├── config.example                ← plantilla de configuración (config.env NO se versiona)
├── requirements.txt              ← documental: numpy 2.2.4 + opencv-python 4.10.0 (ya instalados)
├── .gitignore                    ← ignora data/, config.env, __pycache__, logs, PLAN/AUDIT
│
├── src/
│   ├── main.py                   ← arranque: logging, PID, hilos, señales, watchdog
│   └── vigia/
│       ├── __init__.py           ← versión del paquete
│       ├── config.py             ← carga config.env con defaults seguros
│       ├── estado.py             ← estado compartido thread-safe (Condition + 3 locks)
│       ├── camara.py             ← cliente MJPEG con reconexión y backoff
│       ├── demo.py               ← escena sintética en perspectiva (modo demo)
│       ├── vision.py             ← MOG2 + tracker IoU + score + histéresis
│       ├── overlay.py            ← borde, cajas, HUD y codificación JPEG
│       └── servidor.py           ← HTTP: UI, /stream, API JSON, /snapshot.jpg
│
├── web/
│   ├── index.html                ← UI de una sola página
│   ├── app.js                    ← polling de /api/estado y control de modo
│   ├── style.css                 ← tema oscuro responsive (borde por data-nivel)
│   └── favicon.svg               ← icono
│
├── scripts/
│   ├── servicio.sh               ← start|stop|restart|status|logs|boot-enable|boot-disable
│   └── desplegar.sh              ← copia a la placa por tar+ssh y reinicia
│
├── tests/                        ← 38 unittest (ver sección Tests)
│
├── docs/
│   ├── datasheet.md              ← manual de usuario completo
│   ├── developer.md              ← guía para modificar el código
│   ├── servicio.md               ← operación real en la placa: arranque, guardianes, feria
│   └── BITACORA.md               ← decisiones, tropiezos y lecciones
│
└── data/                         ← runtime, NO versionado
    ├── vigia.pid
    ├── vigia.log                 ← log de la aplicación (5 MB × 2)
    └── vigia.stdout.log          ← stdout del proceso
```

---

## 🔧 Problemas frecuentes

| Síntoma | Causa probable | Solución rápida |
|---|---|---|
| Borde **gris**, "SIN SEÑAL" | Teléfono apagado, IP cambiada o WiFi caída | Abrir `http://192.168.1.9:8080` en el celular; si no responde, reiniciar IP Webcam |
| La página no carga en absoluto | Servicio caído o puerto ocupado | `./scripts/servicio.sh status` y `logs 60` |
| Video a tirones | WiFi saturada o `ANCHO_PROCESO` alto | Bajar `ANCHO_PROCESO` a 256, o acercar el teléfono al router |
| Marca **rojo** sin nadie | Sombras, cambios de luz o textura del piso | Subir `umbral_rojo_in` y/o `area_minima_px` |
| Nunca llega a **rojo** | Umbral alto o `ttc_critico` corto | Bajar `umbral_rojo_in` a 0.62 y probar con el demo |
| El borde parpadea | Ruido de detección | Subir `min_tiempo_rojo_ms` y `tau_ema` |
| `Address already in use` | Otro proceso en 8030 | `ss -ltnp \| grep 8030`; elegir otro `PS_PORT` |
| Falla el despliegue | Clave SSH, IP o puerto 2222 | `ssh -i ~/.ssh/unoq_key -p 2222 arduino@192.168.1.5 true` |

Guía completa, con causa raíz y comandos de diagnóstico: **[`docs/datasheet.md`](docs/datasheet.md)**.

---

## ⚠️ Advertencias y límites

| ⚠️ | Detalle |
|---|---|
| **No es seguridad certificada** | Es un **asistente** de percepción. No reemplaza la atención humana ni habilita a nadie a caminar mirando el teléfono. No hay redundancia, ni SIL/PL, ni validación formal |
| **Depende del WiFi** | Si la red se cae o el teléfono se aleja, el borde se pone **gris**. Gris significa "no sé", no "vía libre" |
| **Imagen sin cifrar** | El MJPEG viaja como HTTP plano por la LAN. Cualquiera en la red puede verlo. No usar en redes ajenas |
| **No exponer 8030 a internet** | La API no tiene autenticación y `POST /api/config` cambia la cámara y los umbrales. Es un servicio de LAN |
| **Lente sucia / contraluz** | Un dedo en el lente o el sol de frente degradan la detección sin avisar. Limpiar el lente y evitar apuntar al ventanal |
| **Score heurístico** | Es sensible a cambios bruscos de luz, sombras proyectadas y texturas de piso muy contrastadas. Está pensado para un pasillo, no para un bosque |
| **Cámara en mano** | Si movés el teléfono, *todo* el cuadro se mueve: durante ~1 s el score sube sin que haya peligro. La histéresis lo amortigua, no lo elimina |
| **Un solo cliente pesado** | Cada cliente de `/stream` ocupa un hilo. Muchos navegadores a la vez compiten por CPU con la visión |
| **Sin historial** | No se graba video ni se guardan eventos. Al reiniciar, el estado se pierde |
| **3.3 V / level shifters** | No aplica aquí (no hay pines), pero **si agregás hardware** a la UNO Q, la lógica es de **3.3 V**: usar level shifters para periféricos de 5 V |
| **ADC de 12 bits** | Tampoco se usa, pero conviene saberlo: el ADC de la UNO Q es de **12 bits (0–4095)**, no de 10 bits como el Uno clásico |
| **Zephyr ≠ AVR** | El lado MCU corre **Zephyr RTOS** (STM32U585). Las librerías AVR-specific no son compatibles. Este proyecto lo esquiva corriendo todo en Linux |

---

## 🧠 Notas de ingeniería que valen la pena

### 1) El falso negativo que casi arruina todo (I-001)

La primera versión filtraba los contornos "demasiado grandes" para evitar falsos positivos:
`area_rel > 0.7` → descartar. Suena razonable hasta que se entiende la física: **el objeto que
llena el cuadro es exactamente el que está por chocar**. El filtro silenciaba el único caso que
importa.

Hoy:

- `AREA_MAXIMA_REL` es **0.95** y solo sirve como umbral de "amenaza máxima".
- Un objeto con `area_rel ≥ 0.95` recibe **score = 1.00** directo.
- Ese 1.00 **no pasa por el EMA**: la colisión inminente no admite el retardo del suavizado.
- El filtro de tamaño ya **no descarta** nada; solo el de área mínima (ruido) y el de aspecto.

Hay un test que lo fija: un objeto sintético que cubre > 95 % del cuadro debe dar `RED`.

### 2) El calentamiento de 5 frames (y por qué no es un parche)

MOG2 arranca sin modelo de fondo: en sus primeras aplicaciones devuelve **todo el cuadro** como
primer plano. Con la corrección I-001, ese blob gigante se puntuaría como amenaza máxima y el
servicio arrancaría en rojo, lo cual sería un falso positivo garantizado en cada reinicio.

La solución es honesta: se descartan **solo los frames de calibración** del modelo
(`max(2, min(6, bg_history // 40))` = **5 frames** con los defaults). Durante ese lapso el estado
publicado es `NO_SIGNAL` con el mensaje **"Calibrando fondo de escena"**. No se silencia riesgo
real: se silencia el arranque del modelo, que es un artefacto conocido.

### 3) Por qué visión clásica y no deep learning

| Criterio | Visión clásica (elegida) | Deep learning (descartada) |
|---|---|---|
| Dependencias | `cv2` + `numpy`, ya instalados | Pesos de decenas/cientos de MB |
| Disco | El proyecto pesa < 1 MB | El disco raíz tiene ~768 MB libres |
| CPU | 12 fps en 4× Cortex-A53 sin GPU | Inviable en CPU; no hay acelerador NN |
| Explicabilidad | "Está abajo, crece y baja rápido" | "El modelo dijo 0.91" |
| Depurable en feria | Sí, con `print` y `/api/estado` | No, sin tooling |

En un stand de feria, poder explicar **por qué** el borde se puso rojo vale más que 2 puntos de
precisión.

### 4) El detalle que evita que el sistema “se caiga solo”

Cada hilo atrapa sus excepciones y sigue vivo. El de visión, en particular: si un frame llegara
corrupto y `procesar` lanzara, el hilo loguea y continúa con el siguiente frame. Un servicio de
feria no puede morir por un frame raro.

---

## 🧭 Estado del proyecto

| Ítem | Estado |
|---|---|
| Versión | **1.0.0** |
| Tests | **38/38 OK** |
| Auditoría de código | **Aprobado — sin incidencias** (3 ciclos, 13 hallazgos corregidos) |
| Desplegado en placa | Sí, corriendo en `192.168.1.5:8030` |
| Repositorio público | **Publicado** — wanderc137/vigia-caminante-unoq |
| Licencia | **MIT** — archivo LICENSE, © 2026 wanderc137 |

### Documentación

| Documento | Para quién |
|---|---|
| **README.md** (este archivo) | Quien llega al proyecto y quiere entenderlo y levantarlo |
| [`docs/datasheet.md`](docs/datasheet.md) | Manual de usuario: operación paso a paso, calibración, problemas |
| [`docs/servicio.md`](docs/servicio.md) | Runbook de la placa: SSH, servicio, guardianes, checklist de feria, apagado |
| [`docs/developer.md`](docs/developer.md) | Guía de código: módulos, hilos, fórmulas, API, cómo extender |
| [`docs/BITACORA.md`](docs/BITACORA.md) | Decisiones, tropiezos y lecciones para el próximo proyecto |

---

<p align="center">
  <strong>Arduino UNO Q</strong> · QCC711 aarch64 + Debian 13 · STM32U585 + Zephyr (sin usar)<br>
  Visión clásica en CPU · 12 fps · sin GPU · sin nube · sin pines<br>
  <em>Un semáforo que se explica solo.</em>
</p>
