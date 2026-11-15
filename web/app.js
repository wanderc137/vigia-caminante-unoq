/* Vigía Caminante UNO Q — lógica de la interfaz.
 * Polling liviano de /api/estado y control del modo por /api/config.
 */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);

  const ETIQUETAS = {
    GREEN: "PASO LIBRE",
    AMBER: "ATENCIÓN",
    RED: "¡PELIGRO!",
    NO_SIGNAL: "SIN SEÑAL",
  };

  const el = {
    body: document.body,
    badge: $("badge"),
    video: $("video"),
    velo: $("velo"),
    mensaje: $("mensaje"),
    score: $("score"),
    barra: $("barra-score"),
    ttc: $("ttc"),
    fps: $("fps"),
    objetos: $("objetos"),
    url: $("url"),
    aplicar: $("aplicar"),
    aviso: $("aviso"),
    modo: $("modo"),
    latencia: $("latencia"),
    edad: $("edad"),
    uptime: $("uptime"),
    botones: Array.from(document.querySelectorAll(".boton[data-modo]")),
  };

  let modoActual = "camara";
  let ultimaUrl = "";
  let ultimoNivel = "";
  let fallos = 0;
  let temporizador = null;

  // ------------------------------------------------------------------
  const fmtNum = (v, dec = 0, sufijo = "") =>
    (v === null || v === undefined || Number.isNaN(Number(v)))
      ? "—"
      : Number(v).toFixed(dec) + sufijo;

  const fmtSegundos = (v) => {
    if (v === null || v === undefined) return "—";
    const n = Number(v);
    if (!Number.isFinite(n)) return "—";
    return n.toFixed(1) + " s";
  };

  function pintar(d) {
    const nivel = ETIQUETAS[d.nivel] ? d.nivel : "NO_SIGNAL";
    if (nivel !== ultimoNivel) {
      el.body.dataset.nivel = nivel;
      el.badge.textContent = ETIQUETAS[nivel];
      el.badge.title = d.mensaje || ETIQUETAS[nivel];
      ultimoNivel = nivel;
    } else if (d.mensaje) {
      el.badge.title = d.mensaje;
    }

    const score = Number(d.score ?? 0);
    el.score.textContent = Math.round(score * 100);
    el.barra.style.width = Math.max(0, Math.min(100, score * 100)) + "%";
    el.ttc.textContent = fmtSegundos(d.ttc);
    el.fps.textContent = fmtNum(d.fps, 1);
    el.objetos.textContent = String(d.n_objetos ?? 0);
    el.latencia.textContent = fmtNum(d.latencia_ms, 0, " ms");
    el.edad.textContent = Number.isFinite(Number(d.frame_age_ms))
      ? fmtNum(d.frame_age_ms, 0, " ms")
      : "—";
    el.uptime.textContent = fmtNum(d.uptime_s, 0, " s");
    el.modo.textContent = d.modo || "—";
    el.mensaje.textContent = d.mensaje || "";

    if (d.camara_url && document.activeElement !== el.url) {
      el.url.value = d.camara_url;
      ultimaUrl = d.camara_url;
    }

    modoActual = d.modo || modoActual;
    el.botones.forEach((b) => b.classList.toggle("activo", b.dataset.modo === modoActual));

    // "Detener" deja el análisis en pausa: sin frames no es un fallo, y el
    // usuario no debe quedarse mirando "conectando…" para siempre.
    const detenido = String(d.modo || "").toLowerCase() === "detenido";
    if (detenido) {
      el.badge.textContent = "DETENIDO";
      el.badge.title = "El análisis está en pausa";
      el.aviso.textContent = "";
    }

    // el velo aparece si no hay ningún frame todavía, o si está en pausa
    const sinFrame = nivel === "NO_SIGNAL" && !Number.isFinite(Number(d.timestamp));
    el.velo.textContent = detenido ? "Análisis detenido — pulsá ▶ Iniciar" : "conectando…";
    el.velo.classList.toggle("oculto", !(sinFrame || detenido));
  }

  function error(msg) {
    el.aviso.textContent = msg;
    el.body.dataset.nivel = "NO_SIGNAL";
    el.badge.textContent = ETIQUETAS.NO_SIGNAL;
    ultimoNivel = "NO_SIGNAL";
  }

  // ------------------------------------------------------------------
  async function consultarEstado() {
    try {
      const r = await fetch("/api/estado", { cache: "no-store" });
      if (!r.ok) throw new Error("HTTP " + r.status);
      const d = await r.json();
      pintar(d);
      fallos = 0;
      el.aviso.textContent = "";
    } catch (e) {
      fallos += 1;
      if (fallos > 2) error("Sin conexión con el servicio (" + e.message + ")");
    }
    programar();
  }

  function programar() {
    clearTimeout(temporizador);
    temporizador = setTimeout(consultarEstado, 400);
  }

  // ------------------------------------------------------------------
  async function cambiarConfig(cambios, textoOk) {
    try {
      const r = await fetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(cambios),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.error || ("HTTP " + r.status));
      el.aviso.textContent = textoOk;
      setTimeout(() => { if (el.aviso.textContent === textoOk) el.aviso.textContent = ""; }, 2500);
      return true;
    } catch (e) {
      el.aviso.textContent = "Error: " + e.message;
      return false;
    }
  }

  el.botones.forEach((boton) => {
    boton.addEventListener("click", async () => {
      const modo = boton.dataset.modo;
      const nombres = { camara: "Cámara iniciada", demo: "Modo demo activo", detenido: "Análisis detenido" };
      el.botones.forEach((b) => (b.disabled = true));
      await cambiarConfig({ modo }, nombres[modo] || "Modo actualizado");
      el.botones.forEach((b) => (b.disabled = false));
      consultarEstado();
    });
  });

  async function aplicarUrl() {
    const url = el.url.value.trim();
    if (!url) { el.aviso.textContent = "Escribí la URL de la cámara"; return; }
    el.aplicar.disabled = true;
    const ok = await cambiarConfig({ camara_url: url }, "Cámara actualizada");
    el.aplicar.disabled = false;
    if (ok) {
      ultimaUrl = url;
      reconectarVideo();
    }
  }

  el.aplicar.addEventListener("click", aplicarUrl);
  el.url.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") { ev.preventDefault(); aplicarUrl(); }
  });

  // ------------------------------------------------------------------
  function reconectarVideo() {
    // fuerza al navegador a reabrir el stream cuando se cambia de cámara
    const base = "/stream?t=" + Date.now();
    el.video.src = base;
    el.velo.classList.remove("oculto");
    setTimeout(() => el.velo.classList.add("oculto"), 1500);
  }

  el.video.addEventListener("error", () => {
    el.velo.classList.remove("oculto");
    setTimeout(reconectarVideo, 2500);
  });

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) { clearTimeout(temporizador); consultarEstado(); }
  });

  consultarEstado();
})();
