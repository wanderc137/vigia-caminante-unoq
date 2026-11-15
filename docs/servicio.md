# 🛰️ Vigía Caminante UNO Q — Servicio en la placa

**Manual de operación real · versión 1.0.0 · Arduino UNO Q `192.168.1.5`**

Este documento es el **runbook de la feria**: qué corre en la placa, cómo se entra, cómo se arranca y se apaga, qué revisar en 60 segundos y qué hacer cuando algo no responde. No repite la teoría del proyecto (está en el [`README`](../README.md)), ni el detalle del código (está en [`developer.md`](developer.md)), ni las recetas de calibración (están en [`datasheet.md`](datasheet.md)).

> 🚨 **Importante.**
> La placa es un equipo **compartido**: además del Vigía corren otros tres servicios. La regla de oro es simple: **tocá únicamente los archivos, procesos y puertos del Vigía**. Nunca hagas `pkill python3`, nunca mates procesos ajenos y nunca ocupes los puertos `8000`, `8010` o `8020`.

---

## 📑 Índice

1. [Qué corre y dónde](#1-qué-corre-y-dónde)
2. [Cómo se accede](#2-cómo-se-accede)
3. [Comandos de operación](#3-comandos-de-operación)
4. [Arranque automático y guardianes](#4-arranque-automático-y-guardianes)
5. [Convivencia con los otros servicios](#5-convivencia-con-los-otros-servicios)
6. [Actualizar el servicio](#6-actualizar-el-servicio)
7. [Checklist de feria (60 segundos)](#7-checklist-de-feria-60-segundos)
8. [Resolución de problemas](#8-resolución-de-problemas)
9. [Desactivar todo (y volver a activarlo)](#9-desactivar-todo-y-volver-a-activarlo)
10. [Referencia rápida](#10-referencia-rápida)
11. [Lo que no se hace](#11-lo-que-no-se-hace)

---

## 1. Qué corre y dónde

El desarrollo se hace en la notebook y se copia a la placa. Son dos copias del mismo proyecto:

| Ubicación | Rol | Ruta |
|---|---|---|
| Notebook | Desarrollo, edición, tests | `/home/jma/Documents/Proyects/OpenCode/AG.CreadorContenido/proyectos/vigia-caminante-unoq` |
| Arduino UNO Q | Producción / feria | `/home/arduino/vigia-caminante-unoq` |

### La placa

| Dato | Valor |
|---|---|
| Modelo | Arduino UNO Q |
| SO | Debian 13 (`trixie`) aarch64 |
| Usuario | `arduino` |
| CPU | QCC711, 4 núcleos |
| RAM | 3.6 GB (≈ 2.2 GB libres en operación normal) |
| Disco | 9.8 GB, con apenas **≈ 768 MB libres (92 % usado)** |
| Red | `wlan0` con IP fija `192.168.1.5` |
| Acceso | SSH en el puerto **2222** |
| Sudo | **No hay sudo sin contraseña**; no se usa `apt` ni `systemd` del sistema |

> 💡 **Nota.**
> Todo el Vigía corre en el **lado Linux** de la UNO Q (Python + OpenCV). El STM32U585 y Zephyr **no se usan** en este proyecto: no hay sketch `.ino`, no hay GPIO ni ADC. Por eso no se compila con `arduino-cli`; se despliega por SSH.

### El servicio

| Dato | Valor |
|---|---|
| Puerto HTTP | **8030** |
| Página local | <http://192.168.1.5:8030/> |
| Health check | <http://192.168.1.5:8030/health> |
| Estado JSON | <http://192.168.1.5:8030/api/estado> |
| Cámara (teléfono) | <http://192.168.1.9:8080/video> (MJPEG) |
| Proceso | `python3 -u src/main.py` |
| PID | `data/vigia.pid` |
| Log de la aplicación | `data/vigia.log` |
| Log de arranque / stdout | `data/vigia.stdout.log` |
| Script de control | `scripts/servicio.sh` |

### Topología

```text
┌──────────────────────┐        WiFi         ┌───────────────────────────┐
│ Teléfono             │  MJPEG :8080/video  │ Arduino UNO Q             │
│ IP Webcam            │ ──────────────────► │ 192.168.1.5               │
│ 192.168.1.9          │                     │                           │
└──────────────────────┘                     │  Vigía :8030              │
                                             │   ├─ /        (página)    │
┌──────────────────────┐        HTTP         │   ├─ /stream  (MJPEG)     │
│ Notebook / navegador │ ◄─────────────────► │   ├─ /api/estado          │
│ (o el celular del    │      :8030          │   └─ /health              │
│  visitante)          │                     │                           │
└──────────────────────┘                     └───────────────────────────┘
```

El teléfono es la **cámara**; la placa es la **cabeza que piensa**; el navegador es la **vidriera**. Si el teléfono se apaga o cambia de IP, el servicio sigue vivo, pero muestra `NO_SIGNAL` y borde gris. Eso **no es una caída del servicio**.

---

## 2. Cómo se accede

### SSH

Desde la notebook:

```bash
ssh -i ~/.ssh/unoq_key -p 2222 arduino@192.168.1.5
```

| Detalle | Valor |
|---|---|
| Usuario | `arduino` |
| Host | `192.168.1.5` |
| Puerto | `2222` |
| Clave privada | `~/.ssh/unoq_key` (en la notebook) |

> ⚠️ **Atención.**
> El puerto **22 está filtrado** en esta placa: el único acceso **SSH** habilitado es el **2222**. No intentes “arreglarlo” abriendo el 22: no hay sudo y no hace falta.

### Qué se puede y qué no se puede hacer

| Sí se puede | No se puede / no se debe |
|---|---|
| Editar archivos dentro de `/home/arduino/vigia-caminante-unoq` | Usar `sudo`, `apt`, `systemctl` del sistema |
| Arrancar, parar y reiniciar **el Vigía** | Tocar procesos o archivos de los otros servicios |
| Editar el crontab **del usuario `arduino`** | Modificar `/etc`, reglas udev o servicios del sistema |
| Escuchar en puertos > 1024 (el 8030 ya está asignado) | Ocupar `8000`, `8010` o `8020` |
| Leer logs y borrar logs viejos del proyecto | Borrar archivos de otros proyectos |

### Probar la conexión

```bash
ssh -i ~/.ssh/unoq_key -p 2222 arduino@192.168.1.5 'hostname; whoami; date'
```

Debería responder algo parecido a:

```text
uno-q
arduino
Sun Nov 15 14:39:00 UTC 2026
```

Si el SSH falla, revisá que la notebook esté en la misma red WiFi y que la placa tenga la IP `192.168.1.5`:

```bash
ping -c 2 192.168.1.5
```

---

## 3. Comandos de operación

Todos se ejecutan **dentro de la placa**, desde la raíz del proyecto desplegado:

```bash
cd /home/arduino/vigia-caminante-unoq
./scripts/servicio.sh <comando>
```

### Tabla maestra

| Comando | Qué hace | Cuándo usarlo |
|---|---|---|
| `start` | Arranca el servicio si no está vivo. Es **idempotente**. | Después de un corte, o si `status` dice `DETENIDO`. |
| `stop` | Detiene el proceso con SIGTERM y, si no muere en 10 s, SIGKILL. | Mantenimiento puntual. **Ojo: los guardianes lo reviven en ≤ 2 min.** |
| `restart` | `stop` + `start`. | Después de cambiar código o configuración. |
| `status` | Muestra PID, uptime, CPU/RSS, health, nivel actual y estado del arranque. | **El comando que más vas a usar.** |
| `logs` | Muestra las últimas 60 líneas del log de la app y 15 del stdout. | Para ver qué pasó. |
| `logs N` | Muestra las últimas `N` líneas del log de la app. | `logs 200` para investigar. |
| `logs -f` | Sigue el log en vivo (60 líneas iniciales). | Depurar en el momento. `Ctrl+C` para salir. |
| `boot-enable` | Agrega la línea `@reboot` al crontab. | Dejar el arranque automático configurado. |
| `boot-disable` | Quita **todas** las líneas del Vigía del crontab (arranque + guardianes). | Para apagar de verdad el servicio. |

### `start`

```bash
./scripts/servicio.sh start
```

- Si el PID vive, imprime `Servicio ya activo (PID …) en el puerto 8030.` y **no hace nada**. Por eso es seguro llamarlo desde cron cada 2 minutos.
- Si no vive, rota logs si superan 5 MB, lanza `setsid nohup python3 -u src/main.py` y espera hasta 12 s a que aparezca el PID.
- Después espera hasta 12 s a que `http://127.0.0.1:8030/health` responda.
- Salida esperada:

```text
Arrancando Vigía Caminante…
OK — PID 9940, escuchando en http://0.0.0.0:8030/
```

> 💡 **Nota.**
> `setsid nohup` desacopla el proceso de la sesión SSH. Podés cerrar la terminal y el servicio sigue corriendo.

### `stop`

```bash
./scripts/servicio.sh stop
```

- Si no está corriendo: `Servicio no está corriendo.` (y sale con éxito).
- Si está corriendo: manda SIGTERM, espera hasta 10 s y, si sigue vivo, manda SIGKILL.
- Salida esperada:

```text
Deteniendo PID 9940…
Detenido.
```

> ⚠️ **Cuidado.**
> En esta placa hay **guardianes activos**. Si hacés `stop` y no desactivás el cron, el servicio vuelve solo en **2 minutos o menos**. Para un apagado real, usá `boot-disable` **y después** `stop` (ver [§9](#9-desactivar-todo-y-volver-a-activarlo)).

### `restart`

```bash
./scripts/servicio.sh restart
```

Hace `stop` y `start` en una sola operación. Es el comando estándar después de desplegar código nuevo o de tocar `config.env`. Tarda unos 10–20 s entre que baja y vuelve a responder.

### `status`

```bash
./scripts/servicio.sh status
```

Salida típica **con cámara andando**:

```text
Estado    : ACTIVO (PID 9940)
Uptime    : 02:14:37
CPU / RSS : 128.4% / 214 MB
Health    : ok (http://192.168.1.5:8030/health)
Nivel     : GREEN (score 0.18, 12.4 fps)
Arranque  : automático activado (cron @reboot)
```

Salida típica **sin cámara** (teléfono apagado o IP cambiada):

```text
Estado    : ACTIVO (PID 9940)
Uptime    : 00:01:45
CPU / RSS : 12.5% / 186 MB
Health    : ok (http://192.168.1.5:8030/health)
Nivel     : NO_SIGNAL (score 0.00, 0.0 fps)
Arranque  : automático activado (cron @reboot)
```

| Campo | Significado |
|---|---|
| `Estado` | `ACTIVO (PID …)` si el PID del archivo existe; `DETENIDO` si no. |
| `Uptime` | Tiempo desde que arrancó ese PID. |
| `CPU / RSS` | `%CPU` promedio desde el arranque (no instantáneo) y memoria residente. |
| `Health` | Resultado de consultar `/health`. Si dice `SIN RESPUESTA`, el proceso puede estar vivo pero colgado. |
| `Nivel` | Último nivel calculado: `NO_SIGNAL`, `GREEN`, `AMBER` o `RED`, con score y FPS. |
| `Arranque` | Si hay **alguna** línea del Vigía en el crontab. No distingue arranque de guardianes. |

> 💡 **Tip.**
> `NO_SIGNAL` con `0.0 fps` y CPU baja es lo normal cuando el teléfono no está transmitiendo. No reinicies por eso: primero revisá la cámara en <http://192.168.1.9:8080/video>.

Para confirmar que están las **tres** líneas de cron (arranque + dos guardianes):

```bash
crontab -l | grep vigia-caminante-unoq
```

Deberían aparecer exactamente tres líneas (ver [§4](#4-arranque-automático-y-guardianes)).

### `logs`

```bash
./scripts/servicio.sh logs        # 60 líneas de la app + 15 de stdout
./scripts/servicio.sh logs 200    # 200 líneas de la app
./scripts/servicio.sh logs -f     # seguir en vivo
```

- El log “bueno” es `data/vigia.log` (aplicación). `data/vigia.stdout.log` guarda el arranque y los errores tempranos.
- La rotación es automática al arrancar: si un log supera **5 MB**, se guardan hasta dos backups (`.1` y `.2`).
- `logs -f` sigue solo el log de la aplicación. Si el proceso no arranca, mirá `data/vigia.stdout.log`.

### `boot-enable`

```bash
./scripts/servicio.sh boot-enable
```

Agrega esta línea al crontab del usuario `arduino`:

```cron
@reboot sleep 20; /home/arduino/vigia-caminante-unoq/scripts/servicio.sh start >/dev/null 2>&1
```

- Es idempotente: si ya existe **cualquier** línea del Vigía (no solo la de `@reboot`), avisa que
  ya está configurado y no agrega nada.
- **No agrega los guardianes**. Esos ya están instalados aparte (ver [§4](#4-arranque-automático-y-guardianes)).
- Mensaje: `Arranque automático activado (@reboot, retardo 20 s).`

### `boot-disable`

```bash
./scripts/servicio.sh boot-disable
```

Quita del crontab **todas las líneas que contengan la ruta del proyecto**, es decir:

1. la línea `@reboot`,
2. el guardián de proceso cada 2 minutos,
3. el guardián de salud cada 10 minutos.

No toca las líneas de SuperFoto ni de Pasillo Seguro. Mensaje: `Arranque automático desactivado.`

> 🚨 **Importante.**
> `boot-enable` **no vuelve a agregar los guardianes**. Si vas a reactivar el servicio completo después de un `boot-disable`, primero `boot-enable`, después `start`, y volvé a agregar a mano las dos líneas de guardián si las querés (están en [§4](#4-arranque-automático-y-guardianes)).

---

## 4. Arranque automático y guardianes

### Las tres líneas del crontab

El crontab del usuario `arduino` tiene, para el Vigía, exactamente estas líneas:

```cron
@reboot sleep 20; /home/arduino/vigia-caminante-unoq/scripts/servicio.sh start >/dev/null 2>&1
*/2 * * * * /home/arduino/vigia-caminante-unoq/scripts/servicio.sh start >/dev/null 2>&1 # vigia-guardian-proceso
*/10 * * * * curl -fsS -m 5 http://127.0.0.1:8030/health >/dev/null 2>&1 || /home/arduino/vigia-caminante-unoq/scripts/servicio.sh restart >/dev/null 2>&1 # vigia-guardian-salud
```

| Línea | Cuándo corre | Qué hace |
|---|---|---|
| `@reboot` | Al bootear | Espera 20 s y arranca el servicio. |
| `*/2 * * * *` | Cada 2 minutos | Llama a `start`. Si el PID vive, no hace nada (idempotente). |
| `*/10 * * * *` | Cada 10 minutos | Consulta `/health`; si no responde, hace `restart`. |

### Por qué `sleep 20`

No es un capricho: al bootear, la placa tiene que levantar WiFi, pedir DHCP, montar el filesystem y arrancar **otros dos servicios** que también tienen su `@reboot` (SuperFoto y Pasillo Seguro). Si el Vigía arranca al mismo tiempo:

- puede quedar sin red y fallar al resolver o conectar a la cámara;
- compite por CPU y disco justo cuando todos importan OpenCV;
- puede chocar con la carga inicial de los otros servicios.

Veinte segundos es el punto medio medido: alcanza para que la red esté lista y para no pelear por los primeros segundos de CPU. OpenCV tarda además unos **6 s** en importar, así que el primer `/health` exitoso puede demorar unos 25–30 s desde el encendido.

### Qué garantizan los guardianes

| Falla | Detección | Recuperación |
|---|---|---|
| El proceso muere (crash, OOM, SIGKILL) | Guardián de proceso, cada 2 min | `start` lo levanta en **≤ 2 min** |
| El proceso vive pero está colgado (sin responder HTTP) | Guardián de salud, cada 10 min | `restart` en **≤ 10 min** |
| La placa se reinicia | `@reboot` | Arranca solo a los 20 s |
| El teléfono se apaga o cambia de IP | **No es una falla del servicio** | El Vigía sigue vivo en `NO_SIGNAL`; se corrige la cámara |

> ⚠️ **Atención.**
> El guardián de salud solo verifica que el HTTP responda. Si el hilo de visión se cuelga pero el HTTP sigue contestando, el guardián **no lo detecta**. Para eso está la revisión visual del checklist: FPS y borde.

> ⚠️ **Cuidado.**
> Si el servicio entra en un **crash-loop** (arranca, muere a los pocos segundos, el guardián lo vuelve a arrancar), vas a ver reinicios cada 2 minutos y logs repetidos. En ese caso: `boot-disable`, `stop`, y ejecutá en primer plano para leer el error real (ver [§8.1](#81-el-servicio-no-arranca)).

### Verificar que los guardianes están

```bash
crontab -l | grep vigia-caminante-unoq
```

Tres líneas = arranque + guardián de proceso + guardián de salud. Si ves menos, alguien corrió `boot-enable` sobre un crontab incompleto o `boot-disable` y no volvió a agregarlas.

### Probar la recuperación (antes de la feria)

Con tiempo y sin público, podés matar el proceso a propósito y cronometrar:

```bash
./scripts/servicio.sh status          # anotá el PID
kill <PID>                            # muerte limpia, no SIGKILL
sleep 130
./scripts/servicio.sh status          # debería estar ACTIVO con otro uptime
```

No lo hagas durante la demostración: el guardián puede tardar hasta 2 minutos y la página queda sin video mientras tanto.

---

## 5. Convivencia con los otros servicios

La placa no es exclusiva del Vigía. Estos son los servicios que conviven:

| Puerto | Servicio | Regla |
|---|---|---|
| `8000` | Detector de tarjetas | No tocar archivos, procesos ni puerto |
| `8010` | SuperFoto | No tocar archivos, procesos ni puerto |
| `8020` | Pasillo Seguro | No tocar archivos, procesos ni puerto |
| `8030` | **Vigía Caminante** | Nuestro único territorio |

> 🚨 **Peligro.**
> **Nunca uses `pkill python3` ni `killall python3`.** En la placa hay varios procesos Python y vas a tumbar servicios ajenos. Matá siempre por PID propio (`data/vigia.pid`) o usá `servicio.sh`.

### Consumo medido del Vigía

| Métrica | Con cámara | Sin cámara |
|---|---|---|
| CPU | **100–155 %** de 400 % (4 núcleos) | 12–41 % |
| RAM (RSS) | **200–230 MB** | 171–191 MB |
| FPS | **11–14 fps** | 0 fps (`NO_SIGNAL`) |
| Latencia | 36–122 ms (mediana 55 ms) | — |

La placa tiene 3.6 GB de RAM; en operación normal quedan **≈ 2.2 GB libres**. El Vigía no es el problema de memoria. El recurso ajustado es el **disco**.

### Disco: por qué el proyecto pesa menos de 2 MB

El disco raíz está al **92 %** (9.8 GB totales, **≈ 768 MB libres**). Por eso el proyecto se mantiene deliberadamente chico:

- copia desplegada: **≈ 940 KB**;
- con `data/` y logs: **< 2 MB**;
- nada de modelos de IA, datasets, imágenes de prueba ni entornos virtuales dentro del proyecto;
- los logs rotan a los 5 MB y se conservan como máximo dos backups por archivo.

No copies datasets, videos ni modelos grandes a la placa. Si necesitás espacio, borrá logs viejos del proyecto (`data/vigia.log.1`, `data/vigia.log.2`), nunca archivos de otros servicios.

### Comprobar el estado de la placa

```bash
df -h /                      # espacio libre
free -m                      # memoria
ss -ltnp | grep -E ':(8000|8010|8020|8030)'   # puertos y procesos
```

Salida esperada de puertos: cuatro líneas, una por servicio, escuchando en `0.0.0.0`. Si falta la del 8030, el Vigía no está escuchando.

---

## 6. Actualizar el servicio

### Desde la notebook (forma normal)

```bash
cd /home/jma/Documents/Proyects/OpenCode/AG.CreadorContenido/proyectos/vigia-caminante-unoq
./scripts/desplegar.sh
```

El script:

1. copia el proyecto con **`tar` por SSH** (la placa **no tiene `rsync`**);
2. reemplaza en la placa `src/`, `web/`, `tests/` y `scripts/`;
3. **conserva** `config.env` y `data/`;
4. ajusta `CAM_URL` y `PS_PORT` según variables de entorno o valores por defecto;
5. da permisos de ejecución a los scripts;
6. ejecuta `boot-enable` y `restart` (salvo que uses `--sin-arranque`).

Termina mostrando el `status` y la URL final. Esperá **15–20 s** después del `restart` antes de abrir la página, y hacé un refresco fuerte (`Ctrl+Shift+R`) para descartar caché del navegador.

> ⚠️ **Atención.**
> `desplegar.sh` **sobrescribe `CAM_URL` y `PS_PORT` en `config.env`** en cada despliegue. Si cambiaste la IP del teléfono o el puerto a mano en la placa, el próximo deploy los pisa. Para conservarlos, pasalos como variables:
>
> ```bash
> CAM_URL=http://192.168.1.9:8080/video PS_PORT=8030 ./scripts/desplegar.sh
> ```

### Si solo cambiaste configuración

No hace falta desplegar todo. En la placa:

```bash
cd /home/arduino/vigia-caminante-unoq
nano config.env
./scripts/servicio.sh restart
./scripts/servicio.sh status
```

### `--sin-arranque`

```bash
./scripts/desplegar.sh --sin-arranque
```

Copia el código pero **no** ejecuta `boot-enable` ni `restart`. Útil para preparar cambios sin cortar la demo. Después hay que reiniciar a mano:

```bash
ssh -i ~/.ssh/unoq_key -p 2222 arduino@192.168.1.5 \
  '/home/arduino/vigia-caminante-unoq/scripts/servicio.sh restart'
```

---

## 7. Checklist de feria (60 segundos)

Antes de abrir el stand, con el teléfono ya transmitiendo:

```bash
ssh -i ~/.ssh/unoq_key -p 2222 arduino@192.168.1.5
cd /home/arduino/vigia-caminante-unoq
```

| # | Chequeo | Comando / acción | OK si… |
|---|---|---|---|
| 1 | Servicio activo | `./scripts/servicio.sh status` | `Estado: ACTIVO`, `Health: ok` |
| 2 | API de salud | `curl -s localhost:8030/health` | JSON con `"status": "ok"` |
| 3 | Estado de visión | `curl -s localhost:8030/api/estado` | `nivel` distinto de `NO_SIGNAL`, `fps` ≥ 10 |
| 4 | Página | Abrir <http://192.168.1.5:8030/> | Carga el título y el video |
| 5 | Borde | Mirar el marco de la página | Verde/ámbar/rojo según la escena, **no gris** |
| 6 | Métricas | Mirar FPS y latencia en pantalla | FPS 11–14, latencia < 150 ms |
| 7 | Demo | Tocar el botón **Demo** | Recorre los niveles y vuelve |
| 8 | Cámara directa | Abrir <http://192.168.1.9:8080/video> | Se ve el video del teléfono |
| 9 | Disco | `df -h /` | ≥ 500 MB libres |
| 10 | Guardianes | `crontab -l \| grep vigia-caminante-unoq` | 3 líneas |

Versión compacta para copiar y pegar:

```bash
./scripts/servicio.sh status
curl -s localhost:8030/health; echo
curl -s localhost:8030/api/estado; echo
df -h /
crontab -l | grep vigia-caminante-unoq
```

Si los pasos 1–3 fallan, andá a [§8](#8-resolución-de-problemas). Si el borde está gris pero el `status` está `ACTIVO`, el problema es la cámara, no el servicio.

---

## 8. Resolución de problemas

### 8.1 El servicio no arranca

```bash
./scripts/servicio.sh start
./scripts/servicio.sh logs 80
tail -n 40 data/vigia.stdout.log
```

| Síntoma en el log | Causa probable | Qué hacer |
|---|---|---|
| `ModuleNotFoundError: No module named 'cv2'` | Falta OpenCV en el Python de la placa | **No instalar nada sin autorización.** Verificar con `python3 -c 'import cv2; print(cv2.__version__)'`; si falta, avisar al responsable. En esta placa OpenCV 4.10.0 ya está instalado. |
| `Address already in use` | El puerto 8030 está ocupado | Ver [§8.3](#83-el-puerto-8030-está-ocupado). |
| `Permission denied` sobre `data/` | Permisos del directorio | `ls -ld data`; el dueño debe ser `arduino`. No uses `sudo`: corregí con `chmod`/`chown` solo si tenés permiso. |
| `No space left on device` | Disco lleno | Borrar logs viejos **del proyecto**, `df -h /`. |
| Nada en el log | El proceso muere antes de escribir | Ejecutar en primer plano: `python3 -u src/main.py`. |
| `config.env` inválido | Variable mal escrita | Comparar con `config.example`; `restart`. |

Para ver el error real sin el silencio de `nohup`:

```bash
cd /home/arduino/vigia-caminante-unoq
python3 -u src/main.py
```

`Ctrl+C` para cortar. Esto ocupa la terminal y el puerto; no lo dejes así durante la feria.

### 8.2 Arranca pero `/health` no responde

1. Esperá **20–30 s**: OpenCV tarda en importar y el primer frame puede demorar.
2. Verificá que esté escuchando:

   ```bash
   ss -ltnp | grep 8030
   curl -v --max-time 3 http://127.0.0.1:8030/health
   ```

3. Si el proceso está vivo pero no contesta, reiniciá:

   ```bash
   ./scripts/servicio.sh restart
   ```

4. Si sigue sin responder, mirá los logs y probá en primer plano (§8.1). El guardián de salud también hará `restart` solo en **≤ 10 min**, pero no esperes si hay público.

> 💡 **Nota.**
> `health` puede fallar si el hilo HTTP está saturado por el stream MJPEG y muchos visitantes. Cerrá pestañas viejas y volvé a probar.

### 8.3 El puerto 8030 está ocupado

```bash
ss -ltnp | grep 8030
```

- Si el PID es **el nuestro** (`data/vigia.pid`), probably quedó un proceso huérfano: `./scripts/servicio.sh stop` y `start`.
- Si el PID es de **otro servicio**, **no lo mates**. El Vigía no debe usar los puertos 8000/8010/8020, y esos servicios no deben usar el 8030. Si hay conflicto real, cambiá el puerto del Vigía:

  ```bash
  cd /home/arduino/vigia-caminante-unoq
  nano config.env      # PS_PORT=8040
  ./scripts/servicio.sh restart
  ```

> ⚠️ **Cuidado.**
> El guardián de salud tiene **hardcodeado** `http://127.0.0.1:8030/health`. Si cambiás `PS_PORT`, tenés que editar esa línea del crontab (`crontab -e`) o el guardián va a reiniciar el servicio cada 10 minutos para siempre. También cambia la URL pública.

### 8.4 El proceso murió

```bash
./scripts/servicio.sh status
./scripts/servicio.sh logs 200
grep -nE 'Traceback|Killed|SIGTERM|MemoryError|OOM' data/vigia.log data/vigia.stdout.log
```

> 💡 **Nota.** Los logs viven en `data/` (`vigia.log`, `vigia.stdout.log` y sus `.1`/`.2`); no
> hay un directorio `logs/`. El comando `servicio.sh logs` ya sabe dónde buscarlos.

| Señal | Interpretación |
|---|---|
| `Traceback` | Error de Python; leer la última línea. |
| `Killed` / `SIGKILL` | Lo mató el sistema (OOM) o un humano. |
| `SIGTERM` | Lo paró `servicio.sh` o el sistema. |
| Reinicios cada 2 min | Crash-loop: desactivar guardianes y depurar en primer plano. |

> 🚨 **Importante.**
> Que la cámara no responda **no mata** el servicio: el Vigía queda en `NO_SIGNAL` y reintenta con backoff. Si el proceso murió, la causa es otra.

Si entra en crash-loop:

```bash
./scripts/servicio.sh boot-disable   # quita arranque y guardianes
./scripts/servicio.sh stop
python3 -u src/main.py               # leer el error real
```

Cuando esté resuelto, `boot-enable`, `start` y volver a agregar los guardianes.

### 8.5 El teléfono cambió de IP

El teléfono debería tener IP fija `192.168.1.9`, pero si el router le dio otra:

1. Averiguá la IP nueva en la app IP Webcam o en el router.
2. Probala en el navegador: `http://IP_NUEVA:8080/video`.
3. Cambiala **desde la página** del Vigía (campo de URL de cámara → **Aplicar**) o por API.
4. Para que quede permanente:

   ```bash
   cd /home/arduino/vigia-caminante-unoq
   nano config.env       # CAM_URL=http://IP_NUEVA:8080/video
   ./scripts/servicio.sh restart
   ```

> ⚠️ **Atención.**
> Si después corrés `desplegar.sh` desde la notebook sin pasar `CAM_URL`, el script va a reescribir `config.env` con la IP por defecto `192.168.1.9`. Pasá `CAM_URL=http://IP_NUEVA:8080/video ./scripts/desplegar.sh` o reservá la IP del teléfono en el router DHCP.

Verificación:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://192.168.1.9:8080/shot.jpg
```

`200` = cámara accesible. `000` o timeout = red/IP/app.

### 8.6 El borde queda gris

El gris significa `NO_SIGNAL` o “todavía no hay suficientes frames”. Árbol de decisión:

1. **¿El modo está en Detenido?** La página tiene botones **Detener / Iniciar**. Si está detenido, Iniciar.
2. **¿La cámara responde?** Abrí <http://192.168.1.9:8080/video>. Si no se ve, el problema es el teléfono (pantalla apagada, app cerrada, WiFi, IP).
3. **¿Recién arrancó?** Los primeros 5 frames se descartan para calibrar. Esperá 2–3 s.
4. **¿El `status` dice `NO_SIGNAL`?** Confirmá `curl -s localhost:8030/api/estado`; si `frame_age_ms` es `null`, no llegan frames.
5. **¿La página quedó vieja?** Refresco fuerte (`Ctrl+Shift+R`) o probá en una pestaña incógnito.
6. **¿El snapshot funciona?** `curl -s -o /tmp/s.jpg -w '%{http_code}\n' localhost:8030/snapshot.jpg`; si da `200`, la visión anda y el problema es el navegador.

> 💡 **Nota.**
> Un borde gris **no es un fallo de seguridad**: es ausencia de señal. Nunca lo interpretes como “verde” ni como “no hay riesgo”.

### 8.7 Otros problemas

| Síntoma | Causa probable | Solución |
|---|---|---|
| La página no carga | Servicio caído o puerto mal | `status`, `ss -ltnp \| grep 8030` |
| El video se congela | WiFi o teléfono | Probar `:8080/video` directo; reiniciar app del teléfono |
| SSH no conecta | Red o puerto | `ping 192.168.1.5`; usar **siempre** `-p 2222` |
| Disco lleno | Logs o datos | `df -h /`; borrar logs viejos del proyecto |
| Todo lento | Otro servicio consumiendo CPU | `ps aux --sort=-%cpu \| head`; **no matar procesos ajenos** |

---

## 9. Desactivar todo (y volver a activarlo)

### Apagado real

```bash
cd /home/arduino/vigia-caminante-unoq
./scripts/servicio.sh boot-disable
./scripts/servicio.sh stop
```

`boot-disable` quita las **tres** líneas del Vigía (arranque + guardianes). `stop` baja el proceso. Verificación:

```bash
crontab -l | grep vigia-caminante-unoq   # no debe devolver nada
./scripts/servicio.sh status             # DETENIDO
ss -ltnp | grep 8030                     # sin líneas
```

Los otros servicios siguen intactos: sus líneas de cron y sus puertos no se tocan.

> ⚠️ **Cuidado.**
> Si solo hacés `stop` y no `boot-disable`, el guardián de proceso lo levanta en **≤ 2 minutos**. No existe “stop definitivo” sin desactivar el cron.

### Volver a activar

```bash
./scripts/servicio.sh boot-enable
./scripts/servicio.sh start
```

Esto deja el arranque al booteo y el servicio corriendo, pero **no reinstala los guardianes**. Para recuperarlos, editá el crontab:

> 💡 **Nota.** `boot-enable` detecta cualquier línea del Vigía, no solo la de `@reboot`. Si
> borraste únicamente el `@reboot` y dejaste los guardianes, no la repone: agregala a mano en
> `crontab -e`.

```bash
crontab -e
```

y agregá las dos líneas:

```cron
*/2 * * * * /home/arduino/vigia-caminante-unoq/scripts/servicio.sh start >/dev/null 2>&1 # vigia-guardian-proceso
*/10 * * * * curl -fsS -m 5 http://127.0.0.1:8030/health >/dev/null 2>&1 || /home/arduino/vigia-caminante-unoq/scripts/servicio.sh restart >/dev/null 2>&1 # vigia-guardian-salud
```

Verificá con `crontab -l | grep vigia-caminante-unoq`: tienen que ser tres líneas.

### Desactivar solo los guardianes (dejar el arranque)

Editá `crontab -e` y borrá las líneas `*/2` y `*/10`, dejando solo la de `@reboot`. Útil para depurar sin que el servicio se reviva solo.

---

## 10. Referencia rápida

```bash
# Acceso
ssh -i ~/.ssh/unoq_key -p 2222 arduino@192.168.1.5
cd /home/arduino/vigia-caminante-unoq

# Día a día
./scripts/servicio.sh status
./scripts/servicio.sh start
./scripts/servicio.sh restart
./scripts/servicio.sh logs -f
./scripts/servicio.sh logs 200

# Arranque automático
./scripts/servicio.sh boot-enable
./scripts/servicio.sh boot-disable

# Verificación
curl -s localhost:8030/health; echo
curl -s localhost:8030/api/estado; echo
ss -ltnp | grep 8030
crontab -l | grep vigia-caminante-unoq

# Actualizar desde la notebook
cd /home/jma/Documents/Proyects/OpenCode/AG.CreadorContenido/proyectos/vigia-caminante-unoq
./scripts/desplegar.sh
```

| Recurso | URL |
|---|---|
| Página del Vigía | <http://192.168.1.5:8030/> |
| Stream MJPEG | <http://192.168.1.5:8030/stream> |
| Snapshot | <http://192.168.1.5:8030/snapshot.jpg> |
| Estado JSON | <http://192.168.1.5:8030/api/estado> |
| Health | <http://192.168.1.5:8030/health> |
| Cámara directa | <http://192.168.1.9:8080/video> |

---

## 11. Lo que no se hace

- ❌ **No usar `sudo`, `apt`, `pip` ni `systemctl` del sistema.** No hay sudo sin contraseña y la placa es compartida.
- ❌ **No usar `pkill python3` ni `killall python3`.** Hay varios servicios Python; matá solo el PID del Vigía.
- ❌ **No tocar los puertos `8000`, `8010` ni `8020`**, ni los archivos o procesos de Detector de tarjetas, SuperFoto o Pasillo Seguro.
- ❌ **No borrar `data/vigia.pid` con el proceso vivo**: `status` y `stop` pierden el rastro.
- ❌ **No editar solo en la placa** y olvidarse de la notebook: el próximo `desplegar.sh` pisa `src/`, `web/`, `tests/` y `scripts/`.
- ❌ **No exponer el puerto 8030 a Internet.** No tiene autenticación; es para la red local de la feria.
- ❌ **No copiar modelos, datasets ni videos** a la placa: quedan ≈ 768 MB libres.
- ❌ **No cambiar `PS_PORT` sin actualizar el guardián de salud** (está hardcodeado a 8030) y la URL pública.
- ❌ **No asumir que gris = seguro.** Gris es `NO_SIGNAL`, no “sin riesgo”.
- ❌ **No usar `rsync`**: no está instalado en la placa. El despliegue es con `tar` por SSH.

---

*Documento de operación · Vigía Caminante UNO Q v1.0.0 · mantener junto al equipo en la feria.*
