// JDM VIRAL CLIPS AI — panel. Todo sale de la API; estados vacíos honestos.
const SECCIONES = [["inicio","🏠 Inicio"],["centro","🔴 Centro LIVE"],["radar","📡 LIVE VIRAL RADAR"],["proyecto","🆕 Nuevo proyecto"],["importar","📥 Importar videos"],["analizar","🤖 Analizar con IA"],["clips","🎬 Clips generados"],["editor","✂️ Editor"],["redes","🌐 Mis redes sociales"],["publicar","📤 Publicar clips"],["calendario","📅 Calendario"],["historial","🕘 Historial"],["stats","📊 Estadísticas"],["config","⚙️ Configuración"],["admin","🛠 Administración"]];
let token = localStorage.getItem("jdm_token") || "";
let sesionElegida = localStorage.getItem("jdm_sesion") || "";
let motorConectado = null, ultimoEstadoMotor = null; // null = aún sin comprobar
let ivMotor = null, ivEventos = null, ivClips = null; // intervalos de auto-refresco por sección
let ivGrab = null, ivContador = null, grabEnCurso = null; // seguimiento de grabación
let clipAEliminar = null;
const $ = id => document.getElementById(id);
function setLiveMsg(txt, info = false) { const el = $("live-msg"); if (!el) return; el.textContent = txt; el.classList.toggle("info", !!info); }
function setEvMsg(txt, info = false) { const el = $("ev-msg"); if (!el) return; el.textContent = txt; el.classList.toggle("info", !!info); }
// La API serializa fechas sin zona ("2026-11-14 05:00:00"): se tratan como UTC.
function parseFechaAPI(s) {
  if (!s) return null;
  let t = String(s).trim();
  if (/([zZ]|[+-]\d{2}:?\d{2})$/.test(t)) return new Date(t);
  t = t.replace(" ", "T") + "Z";
  const d = new Date(t); return isNaN(d) ? null : d;
}
const fmtMS = s => { s = Math.max(0, Math.floor(Number(s) || 0)); return String(Math.floor(s / 60)).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0"); };
const fmtHMS = s => { s = Math.max(0, Math.floor(s)); return [Math.floor(s / 3600), Math.floor(s % 3600 / 60), s % 60].map(n => String(n).padStart(2, "0")).join(":"); };
const fmtMB = n => { n = Number(n) || 0; if (n < 1024) return n + " B"; if (n < 1048576) return Math.round(n / 1024) + " KB"; return (n / 1048576).toFixed(1) + " MB"; };
function guardarToken(t) {
  token = t || "";
  if (token) {
    localStorage.setItem("jdm_token", token);
    document.cookie = "jdm_token=" + encodeURIComponent(token) + "; Path=/; SameSite=Lax";
  } else {
    localStorage.removeItem("jdm_token");
    document.cookie = "jdm_token=; Path=/; Max-Age=0; SameSite=Lax";
  }
}
if (token) guardarToken(token);
function recordarSesion(id) {
  sesionElegida = id ? String(id) : "";
  if (sesionElegida) localStorage.setItem("jdm_sesion", sesionElegida);
  else localStorage.removeItem("jdm_sesion");
}
async function api(metodo, ruta, body) {
  const r = await fetch(ruta, {method: metodo, headers: {"Content-Type": "application/json", "Authorization": "Bearer " + token}, body: body ? JSON.stringify(body) : undefined});
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.detail || ("Error " + r.status));
  return d;
}
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
function tabla(rows, cols) {
  if (!rows.length) return "<p class='nota'>Sin datos todavía.</p>";
  return "<table><tr>" + cols.map(c => `<th>${c[0]}</th>`).join("") + "</tr>" +
    rows.map(r => "<tr>" + cols.map(c => `<td>${c[1](r)}</td>`).join("") + "</tr>").join("") + "</table>";
}
function ir(sec) {
  document.querySelectorAll("main section").forEach(s => s.classList.toggle("oculto", s.dataset.sec !== sec));
  document.querySelectorAll("#menu button").forEach(b => b.classList.toggle("act", b.dataset.sec === sec));
  // Auto-refresco por sección (sin recargar la página): chip de motor en «centro», eventos en «radar».
  if (ivMotor && sec !== "centro") { clearInterval(ivMotor); ivMotor = null; }
  if (ivEventos && sec !== "radar") { clearInterval(ivEventos); ivEventos = null; }
  if (ivClips && sec !== "clips") { clearInterval(ivClips); ivClips = null; }
  if (sec === "centro" && !ivMotor) ivMotor = setInterval(cargarMotor, 15000);
  if (sec === "radar" && !ivEventos) ivEventos = setInterval(cargarEventos, 15000);
  if (sec === "clips" && !ivClips) ivClips = setInterval(cargarClips, 20000);
  ({inicio: cargarStats, stats: cargarStats, radar: cargarEventos, analizar: cargarAnalizar, clips: cargarClips,
    editor: cargarEditor, redes: cargarRedes, publicar: () => {cargarClipSelects(); cargarPubs();},
    calendario: cargarCalendario, historial: cargarHistorial, config: cargarConfig, admin: cargarWorkers,
    centro: cargarSesiones, importar: cargarProyectos}[sec] || (() => {}))();
}
async function entrar() {
  try { const d = await api("POST", "/auth/login", {email: $("li-email").value, password: $("li-pass").value});
    token = d.token; guardarToken(token); mostrarApp(d.email);
  } catch (e) { $("li-msg").textContent = e.message; }
}
async function registrarse() {
  try { await api("POST", "/auth/register", {email: $("li-email").value, password: $("li-pass").value});
    $("li-msg").textContent = "Cuenta creada. Ahora pulsa ENTRAR."; }
  catch (e) { $("li-msg").textContent = e.message; }
}
// Tarjetas clicables (data-nav): UN solo handler delegado para todo el panel.
// Clic o Enter/Espacio sobre una tarjeta navega con ir() a su sección.
document.addEventListener("click", e => {
  const t = e.target.closest ? e.target.closest("[data-nav]") : null;
  if (t) ir(t.dataset.nav);
});
document.addEventListener("keydown", e => {
  if ((e.key === "Enter" || e.key === " ") && e.target.dataset && e.target.dataset.nav) {
    e.preventDefault(); ir(e.target.dataset.nav);
  }
});
function salir() { guardarToken(""); location.reload(); }
function mostrarApp(email) {
  $("login").classList.add("oculto"); $("app").classList.remove("oculto"); $("usr").textContent = email || "";
  $("menu").innerHTML = SECCIONES.map(([k, v]) => `<button data-sec="${k}" onclick="ir('${k}')">${v}</button>`).join("");
  ir("inicio");
}
// Destino de navegación de cada tarjeta de estadísticas (Inicio y Estadísticas).
// Solo secciones reales del panel (data-sec de index.html). Una etiqueta sin
// destino aquí se renderiza como tarjeta normal, SIN clic: nada de clics muertos.
// Nota: la tabla de trabajos vive en «analizar» (#trabajos); «admin» es la lista
// de trabajadores, no de trabajos — por eso «Trabajos pendientes» va a «analizar».
const NAV_STATS = {"Sesiones": "centro", "Grabando ahora": "centro", "Eventos": "radar", "Clips": "clips", "Proyectos": "analizar", "Trabajos pendientes": "analizar", "Publicaciones": "publicar", "Publicaciones por estado": "publicar", "Score promedio": "radar", "Mejor score": "radar", "Eventos ≥ umbral": "radar", "Eventos por modo": "radar"};
async function cargarStats() {
  try { const s = await api("GET", "/stats");
    const datos = {Sesiones: s.sesiones, "Grabando ahora": s.grabando, Eventos: s.eventos, Clips: s.clips, Proyectos: s.proyectos, "Trabajos pendientes": s.trabajos_pendientes, Publicaciones: s.publicaciones, "Score promedio": Math.round(s.score_promedio)};
    if (s.mejor_score !== undefined) { datos["Mejor score"] = s.mejor_score; datos["Eventos ≥ umbral"] = s.eventos_sobre_umbral; }
    if (s.publicaciones_por_estado) datos["Publicaciones por estado"] = Object.entries(s.publicaciones_por_estado).map(([k, v]) => `${k}: ${v}`).join(", ") || "—";
    if (s.eventos_por_modo) datos["Eventos por modo"] = Object.entries(s.eventos_por_modo).map(([k, v]) => `${k}: ${v}`).join(", ") || "—";
    const html = Object.entries(datos).map(([k, v]) => NAV_STATS[k]
      ? `<div class="card nav" role="button" tabindex="0" data-nav="${NAV_STATS[k]}" title="Abrir la sección">${k}<br><b>${v}</b></div>`
      : `<div class="card">${k}<br><b>${v}</b></div>`).join("");
    $("stats").innerHTML = html; $("stats2").innerHTML = html;
  } catch (e) { $("stats").innerHTML = "<p class='nota'>" + esc(e.message) + "</p>"; }
}
// Centro LIVE
async function crearSesion() {
  try { await api("POST", "/sessions", {title: $("ss-titulo").value || "Sesión LIVE", mode: $("ss-modo").value});
    setLiveMsg("Sesión creada."); cargarSesiones(); }
  catch (e) { setLiveMsg(e.message); }
}
// Estado del motor (trabajador): conectado solo si algún worker se vio hace ≤30 s.
async function cargarMotor() {
  const chip = $("wk-chip"); if (!chip) return;
  try { const ws = await api("GET", "/workers");
    const ahora = Date.now();
    const vivo = (ws || []).find(w => { const d = parseFechaAPI(w.last_seen); return d && (ahora - d.getTime()) <= 30000; });
    motorConectado = !!vivo;
    chip.textContent = vivo ? "🟢 Motor conectado" + (vivo.version ? " v" + vivo.version : "") : "🔴 TRABAJADOR DESCONECTADO";
    chip.className = "chip " + (vivo ? "chip-ok" : "chip-off");
    const btn = $("btn-iniciar"); if (btn) btn.disabled = !vivo;
    if (!vivo && ultimoEstadoMotor !== false) setLiveMsg("Sin trabajador conectado no se puede grabar: el trabajo quedaría pendiente.", true);
    ultimoEstadoMotor = motorConectado;
  } catch (e) { motorConectado = false;
    chip.textContent = "🔴 TRABAJADOR DESCONECTADO"; chip.className = "chip chip-off";
    const btn = $("btn-iniciar"); if (btn) btn.disabled = true;
    if (ultimoEstadoMotor !== false) setLiveMsg("Sin trabajador conectado no se puede grabar: el trabajo quedaría pendiente.", true);
    ultimoEstadoMotor = false;
  }
}
async function contarMomentos(sid) {
  if (!sid) return null;
  try { const ev = await api("GET", `/events?session_id=${sid}`); return Array.isArray(ev) ? ev.length : null; }
  catch (e) { return null; }
}
function pintarBtnRadar(ses, nMomentos) {
  const btn = $("btn-radar"); if (!btn) return;
  const cuenta = (nMomentos === null || nMomentos === undefined) ? "" : ` — ${nMomentos} momentos`;
  let etiqueta = "📡 ACTIVAR RADAR VIRAL";
  if (ses && ses.radar_activo) etiqueta = ses.radar_sin_fuente ? "📡 RADAR SIN FUENTE" : "📡 RADAR ANALIZANDO — DESACTIVAR";
  btn.innerHTML = etiqueta + ` <span id="radar-count">${esc(cuenta)}</span>`;
}
async function refrescarRadarUI(ses) {
  const sid = ses ? ses.id : sesionId();
  pintarBtnRadar(ses, await contarMomentos(sid));
}
// Distintivo + contador de grabación en vivo (desde started_at confirmado por la API).
function arrancarContador(startedAt, etiqueta) {
  if (ivContador) clearInterval(ivContador);
  const t0 = parseFechaAPI(startedAt); const base = t0 ? t0.getTime() : Date.now();
  const tick = () => { const el = $("live-estado"); if (el) { const baseTxt = etiqueta === "GRABANDO" ? "GRABANDO EN VIVO" : etiqueta; el.textContent = textoConBytes(baseTxt + " — " + fmtHMS((Date.now() - base) / 1000), el); } };
  tick(); ivContador = setInterval(tick, 1000);
}
function textoConBytes(base, el) {
  const n = Number(el && el.dataset.bytes || 0);
  return n > 0 ? base + " · " + fmtMB(n) : base;
}
function pintarEstadoReal(ses) {
  const el = $("live-estado"); if (!el) return;
  if (!ses || !ses.estado_real) { el.classList.add("oculto"); el.classList.remove("grabando", "estado-error"); el.textContent = ""; return; }
  el.classList.remove("oculto");
  el.dataset.bytes = String(ses.bytes_captura || 0);
  const vivo = ses.estado_real === "GRABANDO" || ses.estado_real === "RADAR ANALIZANDO" || ses.estado_real === "CAPTURANDO VIDEO";
  el.classList.toggle("grabando", vivo);
  el.classList.toggle("estado-error", ses.estado_real === "ERROR DE FUENTE");
  if (vivo && ses.started_at) arrancarContador(ses.started_at, ses.estado_real);
  else {
    if (ivContador) { clearInterval(ivContador); ivContador = null; }
    el.textContent = textoConBytes(ses.estado_real + (ses.error_fuente ? " — " + ses.error_fuente : ""), el);
  }
  if (ses.estado_real === "ERROR DE FUENTE") setLiveMsg(ses.estado_real + (ses.error_fuente ? ": " + ses.error_fuente : ""));
}
function mostrarGrabando(ses) {
  pintarEstadoReal(ses);
}
function ocultarGrabando() {
  const el = $("live-estado"); if (el) { el.classList.add("oculto"); el.classList.remove("grabando"); el.textContent = ""; }
  if (ivContador) { clearInterval(ivContador); ivContador = null; }
}
function pararSeguimientoGrab() { if (ivGrab) { clearInterval(ivGrab); ivGrab = null; } grabEnCurso = null; }
// Sigue el estado real de la sesión (y del job si se conoce) hasta grabando/detenida/error.
function seguirEstadoGrabacion(sid, jobId, modo) {
  pararSeguimientoGrab();
  grabEnCurso = {sid: String(sid), jobId: jobId || null, modo: modo || "inicio", desde: Date.now(), rapido: true, ocupado: false};
  const paso = async () => {
    const g = grabEnCurso; if (!g || g.ocupado) return; g.ocupado = true;
    try {
      const ss = await api("GET", "/sessions");
      const ses = (ss || []).find(x => String(x.id) === g.sid);
      if (ses) {
        if (ses.status === "grabando") {
          mostrarGrabando(ses);
          // La etiqueta del botón del radar y su cuenta de momentos se
          // repintan con el estado FRESCO en cada paso de seguimiento.
          // Sin esto, el botón conservaba el texto de antes de grabar
          // («ACTIVAR RADAR VIRAL») aunque el radar entra SOLO con la
          // grabación (start lo activa y el trabajador lo arranca): un
          // clic encima leía el estado real (activo), calculaba
          // «apagar» y APAGABA el radar creyendo activarlo, sin ningún
          // cambio visible para el dueño (la etiqueta quedaba igual).
          pintarBtnRadar(ses, await contarMomentos(ses.id));
          if (g.modo !== "detencion") { g.modo = "vigilancia"; }
          if (g.rapido) { g.rapido = false; clearInterval(ivGrab); ivGrab = setInterval(paso, 5000); }
          return;
        }
        if (ses.status === "detenida") {
          ocultarGrabando(); pararSeguimientoGrab();
          if (g.modo === "detencion") setLiveMsg("Grabación detenida. Los segmentos ya guardados no se pierden.");
          cargarSesiones(); return;
        }
        if (ses.estado_real) pintarEstadoReal(ses);
        if (ses.status === "error" || ses.estado_real === "ERROR DE FUENTE") {
          let detalle = ses.error_fuente || "";
          if (g.jobId) { try { const js = await api("GET", "/jobs?limit=50"); const j = (js || []).find(x => x.id == g.jobId); if (j && j.error) detalle = j.error; } catch (e) {} }
          pararSeguimientoGrab();
          pintarEstadoReal(ses);
          setLiveMsg("El trabajador no pudo " + (g.modo === "detencion" ? "detener" : "iniciar") + " la grabación" + (detalle ? ": " + detalle : ". Revisa «Trabajos».")); return;
        }
        // iniciando / deteniendo: si el job ya falló, se muestra su error aunque la sesión aún no pase a error.
        if (g.jobId) {
          let j = null;
          try { const js = await api("GET", "/jobs?limit=50"); j = (js || []).find(x => x.id == g.jobId); } catch (e) {}
          if (j && (j.status === "fallido" || j.status === "failed")) {
            ocultarGrabando(); pararSeguimientoGrab();
            setLiveMsg("El trabajador no pudo " + (g.modo === "detencion" ? "detener" : "iniciar") + " la grabación" + (j.error ? ": " + j.error : ".")); return;
          }
        }
        if (g.rapido && Date.now() - g.desde > 90000) {
          g.rapido = false; clearInterval(ivGrab); ivGrab = setInterval(paso, 5000);
          if (g.modo === "inicio") setLiveMsg("El trabajador aún no confirma el inicio; el trabajo sigue en cola. Revisa «Trabajos».", true);
          else if (g.modo === "detencion") setLiveMsg("El trabajador aún no confirma la detención; el trabajo sigue en cola. Revisa «Trabajos».", true);
        }
      }
    } catch (e) { /* red/API transitoria: el siguiente paso reintenta */ }
    finally { const g2 = grabEnCurso; if (g2) g2.ocupado = false; }
  };
  ivGrab = setInterval(paso, 2000); paso();
}
// Restaura distintivo/contador/poll según el estado REAL de la sesión seleccionada.
function restaurarEstadoGrabacion(ses) {
  if (!ses) { ocultarGrabando(); pararSeguimientoGrab(); return; }
  const yaSigue = grabEnCurso && grabEnCurso.sid === String(ses.id);
  if (ses.status === "grabando") { mostrarGrabando(ses); if (!yaSigue) seguirEstadoGrabacion(ses.id, null, "vigilancia"); }
  else if (ses.status === "iniciando") { pintarEstadoReal(ses); if (!yaSigue) seguirEstadoGrabacion(ses.id, null, "inicio"); }
  else if (ses.status === "deteniendo") { pintarEstadoReal(ses); if (!yaSigue) seguirEstadoGrabacion(ses.id, null, "detencion"); }
  else { pararSeguimientoGrab(); pintarEstadoReal(ses); }
}
async function cargarSesiones() {
  const previa = $("ss-lista") ? $("ss-lista").value : "";
  cargarMotor();
  try { const ss = await api("GET", "/sessions");
    $("ss-lista").innerHTML = ss.map(s => `<option value="${s.id}">#${s.id} ${esc(s.title)} — ${esc(s.status)}${s.radar_activo ? " · 📡 radar" : ""}</option>`).join("") || "<option value=''>Sin sesiones</option>";
    if (sesionElegida && ss.some(s => String(s.id) === String(sesionElegida))) $("ss-lista").value = String(sesionElegida);
    else if (previa && ss.some(s => String(s.id) === String(previa))) $("ss-lista").value = previa;
    else if (!sesionElegida) {
      const activa = ss.find(s => s.status === "grabando") || ss.find(s => s.status === "iniciando");
      if (activa) $("ss-lista").value = String(activa.id);
    }
    const tablaSes = $("ss-tabla");
    if (tablaSes) tablaSes.innerHTML = tabla(ss, [["Sesión", r => "#" + r.id + " " + esc(r.title)], ["Estado", r => esc(r.estado_real || r.status)], ["Radar", r => r.radar_sin_fuente ? "📡 sin fuente" : (r.radar_activo ? "📡 analizando" : "—")]]);
    const sel = ss.find(s => String(s.id) === String(sesionId())) || null;
    restaurarEstadoGrabacion(sel);
    await refrescarRadarUI(sel);
    cargarClipsSesion();
  } catch (e) {}
}
function sesionId() {
  const el = $("ss-lista");
  if (sesionElegida && el && [...el.options].some(o => o.value === String(sesionElegida))) return String(sesionElegida);
  return el && el.value ? el.value : "";
}
// Al cambiar la selección: restaura grabación/radar, momentos y clips de esa sesión.
async function cargarSesion() {
  const elegida = $("ss-lista") && $("ss-lista").value;
  if (elegida) recordarSesion(elegida);
  const sid = sesionId(); if (!sid) { ocultarGrabando(); pararSeguimientoGrab(); pintarBtnRadar(null, null); cargarClipsSesion(); return; }
  try { const ss = await api("GET", "/sessions");
    const sel = (ss || []).find(s => String(s.id) === String(sid)) || null;
    restaurarEstadoGrabacion(sel);
    await refrescarRadarUI(sel);
  } catch (e) {}
  cargarClipsSesion();
}
async function validarFuente() {
  const sid = sesionId(); if (!sid) { $("src-msg").textContent = "Crea y selecciona una sesión primero."; return; }
  try { const d = await api("POST", `/sessions/${sid}/sources`, {url: $("src-url").value});
    // La explicación de la API ya trae el texto completo («Compatible: tu PC
    // resuelve la señal…» en plataformas, «Fuente compatible…» en directas).
    $("src-msg").textContent = (d.allowed ? "✅ " : "⛔ No grabable así: ") + d.explanation; }
  catch (e) { $("src-msg").textContent = e.message; }
}
function extraerIdYouTube(url) {
  let m = url.match(/[?&]v=([\w-]{6,})/) || url.match(/youtu\.be\/([\w-]{6,})/) || url.match(/\/(?:live|shorts|embed)\/([\w-]{6,})/);
  return m ? m[1] : null;
}
function abrirLive() {
  const url = $("src-url").value.trim(), v = $("player"), ytBox = $("yt-box"), ext = $("live-ext");
  if (!url) { setLiveMsg("Pega primero el enlace de la fuente (HLS .m3u8, RTMP…) en el campo de la fuente."); return; }
  if (ext) ext.innerHTML = "";
  const ytId = (/youtube\.com|youtu\.be/i.test(url)) ? extraerIdYouTube(url) : null;
  if (ytId) {
    v.pause && v.pause(); v.removeAttribute("src"); v.load && v.load(); v.classList.add("oculto");
    if (ytBox) ytBox.innerHTML = `<iframe src="https://www.youtube-nocookie.com/embed/${ytId}" title="Vista previa de YouTube" allow="accelerometer; autoplay; encrypted-media; picture-in-picture" allowfullscreen referrerpolicy="strict-origin-when-cross-origin"></iframe>`;
    setLiveMsg("Vista previa oficial de YouTube dentro del panel. La grabación la hace el trabajador con FFmpeg.", true);
    return;
  }
  const esDirecta = /\.m3u8($|\?)/i.test(url) || /\.mp4($|\?)/i.test(url);
  if (esDirecta) {
    if (ytBox) ytBox.innerHTML = "";
    v.classList.remove("oculto");
    if (window.Hls && Hls.isSupported() && url.includes(".m3u8")) { const h = new Hls(); h.loadSource(url); h.attachMedia(v); }
    else { v.src = url; }
    v.play().catch(() => { setLiveMsg("El navegador no pudo reproducir esta señal directa (CORS o formato). No afecta a la grabación: la hace el trabajador con FFmpeg.", true); });
    return;
  }
  // Otra plataforma (Facebook/Twitch/TikTok/X…): sin reproductor integrado.
  if (ytBox) ytBox.innerHTML = "";
  v.pause && v.pause(); v.classList.add("oculto");
  if (ext) ext.innerHTML = `<button onclick="window.open('${esc(url)}', '_blank')">Abrir en la plataforma</button>`;
  setLiveMsg("Esta plataforma no permite reproductor integrado: la vista previa no está disponible, pero tu PC sí puede grabar la señal.", true);
}
async function iniciarGrab() {
  const sid = sesionId();
  if (!sid) { setLiveMsg("Crea y selecciona una sesión primero."); return; }
  if (motorConectado === null) await cargarMotor();
  if (!motorConectado) { setLiveMsg("Sin trabajador conectado no se puede grabar: el trabajo quedaría pendiente."); return; }
  try { const d = await api("POST", `/sessions/${sid}/start`);
    setLiveMsg("Iniciando… esperando confirmación del trabajador (trabajo #" + d.job_id + ")", true);
    seguirEstadoGrabacion(sid, d.job_id, "inicio");
  } catch (e) { setLiveMsg(e.message); }
}
async function activarRadar() {
  const sid = sesionId(); if (!sid) { setLiveMsg("Crea y selecciona una sesión primero."); return; }
  try { const ss = await api("GET", "/sessions");
    const ses = ss.find(x => x.id == sid);
    const nuevo = !(ses && ses.radar_activo);
    const d = await api("POST", `/sessions/${sid}/radar`, {activo: nuevo});
    // La etiqueta sale de la RESPUESTA de la API (d.radar_activo), no del estado local.
    const fresco = (await api("GET", "/sessions")).find(x => x.id == sid) || {radar_activo: d.radar_activo, radar_sin_fuente: !((ses && ses.status === "grabando"))};
    pintarBtnRadar(fresco, await contarMomentos(sid));
    setLiveMsg(fresco.radar_sin_fuente
      ? "📡 RADAR SIN FUENTE en la sesión #" + sid + ". No hay captura de video, así que no se analizará nada."
      : (d.radar_activo
        ? "📡 RADAR ANALIZANDO la captura de la sesión #" + sid + "."
        : "Radar en vivo DESACTIVADO en la sesión #" + sid + "."), true);
    cargarSesiones();
  } catch (e) { setLiveMsg(e.message); }
}
async function detenerGrab() {
  const sid = sesionId(); if (!sid) { setLiveMsg("Crea y selecciona una sesión primero."); return; }
  try { const d = await api("POST", `/sessions/${sid}/stop`);
    setLiveMsg("Detención solicitada (trabajo #" + d.job_id + ")…", true);
    seguirEstadoGrabacion(sid, d.job_id, "detencion");
  } catch (e) { setLiveMsg(e.message); }
}
// Clips de la sesión seleccionada, dentro del Centro LIVE.
async function cargarClipsSesion() {
  const box = $("live-clips"); if (!box) return;
  const sid = sesionId();
  if (!sid) { box.innerHTML = `<div class="fila-clips"><h4>Clips de esta sesión</h4><span class="nota">Selecciona una sesión para ver sus clips.</span></div>`; return; }
  try { const cs = await api("GET", `/clips?session_id=${sid}`);
    box.innerHTML = `<div class="fila-clips"><h4>Clips de esta sesión</h4>` +
      `<button onclick="generarTodosClips()">GENERAR TODOS LOS CLIPS</button></div>` +
      tabla(cs, [["Clip", r => "#" + r.id + " " + esc(r.title || "")], ["Duración", r => (r.duration_s ?? "—") + "s"], ["Score", r => `<span class='score'>${r.score}/100</span>`], ["Archivo/ruta", r => esc(r.file_path || "—")], ["Acciones", r => accionesClip(r, "liveclip")]]);
  } catch (e) { box.innerHTML = `<div class="fila-clips"><h4>Clips de esta sesión</h4><span class="nota">${esc(e.message)}</span></div>`; }
}
function largoClip() { const el = $("clip-largo"); return el && el.value ? Number(el.value) : null; }
async function generarTodosClips() {
  const sid = sesionId(); if (!sid) { setLiveMsg("Crea y selecciona una sesión primero."); return; }
  try { const largo = largoClip(); const q = largo ? `?largo_s=${largo}` : "";
    const d = await api("POST", `/sessions/${sid}/generar-clips` + q);
    setLiveMsg(`${d.encolados ?? 0} trabajos encolados, ${d.omitidos ?? 0} omitidos (ya tenían clip o trabajo en curso)`);
    [5000, 15000, 30000].forEach(ms => setTimeout(cargarClipsSesion, ms));
  } catch (e) { setLiveMsg(e.message); }
}
// Radar / eventos (la API ya excluye los descartados)
async function cargarEventos() {
  try { const sel = $("ss-lista"); const sid = sel && sel.value ? sel.value : "";
    const ev = await api("GET", sid ? `/events?session_id=${sid}` : "/events");
    if (!ev.length) { $("eventos").innerHTML = "<p class='nota'>Sin datos todavía.</p>"; }
    else $("eventos").innerHTML = "<table><tr><th>Hora</th><th>Tipo</th><th>Inicio–Fin</th><th>Duración</th><th>Motivo</th><th>Puntuación</th><th>Acciones</th></tr>" +
      ev.map(r => {
        const motivo = r.motivo || r.status || "";
        const hora = r.hora || r.created_at || "";
        const clipTxt = r.tiene_clip
          ? `<span class="nota">${esc(r.clip_file_path || ("Clip #" + (r.clip_id ?? "")))}</span> ` + (r.clip_descargable ? `<button onclick="reproducirEvento(${r.clip_id}, ${r.id})">REPRODUCIR</button>` : "")
          : `<button onclick="generarClipEvento(${r.id})">GENERAR CLIP</button>`;
        return `<tr><td>${esc(String(hora).replace("T", " ").slice(0, 19))}</td><td>${esc(r.type)}</td><td>${fmtMS(r.start_s)}–${fmtMS(r.end_s)}</td><td>${r.duracion_s ?? "—"}s</td><td>${esc(motivo)}</td><td><span class='score'>${r.score}/100</span></td>` +
          `<td>${clipTxt} <button class="sec" onclick="descartarEvento(${r.id})">DESCARTAR</button><div id="ev-video-${r.id}"></div></td></tr>`;
      }).join("") + "</table>";
    if (sid) { const c = $("radar-count"); if (c) c.textContent = ` — ${ev.length} momentos`; }
  } catch (e) { $("eventos").innerHTML = esc(e.message); }
}
async function generarClipEvento(id) {
  try { const largo = largoClip(); const q = largo ? `?largo_s=${largo}` : "";
    const d = await api("POST", `/events/${id}/clip` + q);
    setEvMsg(d.duplicado ? "Ese momento ya tenía un trabajo de clip en curso (trabajo #" + d.job_id + ")." : "Clip encolado (trabajo #" + d.job_id + "). Aparecerá aquí al terminar el trabajador.", true);
  } catch (e) { setEvMsg(e.message); } // incluye el 409 si ya tiene clip
}
async function descartarEvento(id) {
  try { await api("POST", `/events/${id}/descartar`); setEvMsg("Momento descartado.", true); cargarEventos(); }
  catch (e) { setEvMsg(e.message); }
}
function reproducirEvento(clipId, evId) {
  const box = $("ev-video-" + evId); if (!box) return;
  fetch(`/clips/${clipId}/download`, {headers: {"Authorization": "Bearer " + token}}).then(async r => {
    if (!r.ok) { const d = await r.json().catch(() => ({})); setEvMsg(d.detail || "Este clip no es descargable desde el servidor."); return; }
    const b = await r.blob();
    box.innerHTML = "";
    const vid = document.createElement("video");
    vid.controls = true; vid.autoplay = true; vid.src = URL.createObjectURL(b);
    box.appendChild(vid);
  }).catch(e => setEvMsg("No se pudo reproducir el clip: " + e.message));
}
// Proyectos / analizar
async function importar() {
  const f = $("imp-file").files[0]; if (!f) { $("imp-msg").textContent = "Elige primero un archivo de video (MP4, MOV o MKV)."; return; }
  const fd = new FormData(); fd.append("file", f);
  try { const r = await fetch("/projects/import", {method: "POST", headers: {"Authorization": "Bearer " + token}, body: fd});
    const d = await r.json(); if (!r.ok) throw new Error(d.detail || "Error");
    $("imp-msg").textContent = `Importado: ${d.name} (${d.duration_s ? Math.round(d.duration_s) : "?"} s)`; cargarProyectos();
  } catch (e) { $("imp-msg").textContent = e.message; }
}
async function cargarProyectos() {
  try { const ps = await api("GET", "/projects");
    $("proyectos").innerHTML = tabla(ps, [["Proyecto", r => esc(r.name)], ["Duración", r => r.duration_s ? Math.round(r.duration_s) + "s" : "?"], ["Estado", r => esc(r.status)]]);
  } catch (e) {}
}
async function cargarAnalizar() {
  try { const ps = await api("GET", "/projects");
    $("proyectos2").innerHTML = tabla(ps, [["Proyecto", r => esc(r.name)], ["Acción", r => `<button onclick="analizar(${r.id})">ANALIZAR CON IA</button>`]]);
    const js = await api("GET", "/jobs");
    $("trabajos").innerHTML = tabla(js, [["#", r => r.id], ["Tipo", r => esc(r.type)], ["Estado", r => esc(r.status)], ["Intentos", r => r.attempts], ["Error", r => esc(r.error || "")]]);
  } catch (e) {}
}
async function analizar(pid) {
  try { const d = await api("POST", `/projects/${pid}/analyze`); alert("Trabajo #" + d.job_id + " encolado. " + d.nota); cargarAnalizar(); }
  catch (e) { alert(e.message); }
}
// Clips / editor / publicar
let clipsCache = [];
let subidasEnCurso = {}; // clipId -> seguimiento del poll de «subir_clip»
let subsOn = {};
try { subsOn = JSON.parse(localStorage.getItem("jdm_subs_on") || "{}") || {}; } catch (e) { subsOn = {}; }
function subsActivos(c) {
  if (!c) return false;
  const id = String(c.id);
  if (Object.prototype.hasOwnProperty.call(subsOn, id)) return !!subsOn[id];
  return !!c.tiene_subtitulado;
}
function alternarSubtitulos(id) {
  const c = clipsCache.find(x => x.id == id);
  const on = !subsActivos(c || {id: id, tiene_subtitulado: false});
  subsOn[String(id)] = on;
  localStorage.setItem("jdm_subs_on", JSON.stringify(subsOn));
  const btn = document.querySelector('button.subs-toggle[onclick="alternarSubtitulos(' + id + ')"]');
  if (btn) {
    btn.textContent = "SUBTÍTULOS " + (on ? "ON" : "OFF");
    btn.classList.toggle("subs-on", on);
    btn.classList.toggle("sec", !on);
  }
  cargarClips();
}
function notaSubtitulos(c) {
  const estado = c.subtitulo_estado || "";
  if (estado === "error" || estado === "sin_habla") {
    return c.subtitulo_error || "No se pudieron generar los subtítulos. El clip original sigue disponible.";
  }
  if (estado === "procesando" || estado === "corrigiendo" || estado === "corregir" || estado === "pendiente") {
    return "Preparando subtítulos… el clip original sigue disponible.";
  }
  if (c.tiene_subtitulado) return "Subtítulos listos. ON los muestra sobre el video; OFF reproduce el original.";
  return "Subtítulos en espera. El original sigue disponible.";
}
function descargarSubtitulado(id) {
  fetch(`/clips/${id}/subtitulado?descargar=1`, {headers: {"Authorization": "Bearer " + token}}).then(async r => {
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      if ($("clips-msg")) $("clips-msg").textContent = d.detail || "El video con subtítulos todavía no está listo.";
      return;
    }
    const b = await r.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(b);
    a.download = "clip-" + id + "-subtitulado.mp4";
    a.click();
  }).catch(e => { if ($("clips-msg")) $("clips-msg").textContent = "No se pudo descargar el video con subtítulos: " + e.message; });
}
async function guardarTextoSubtitulos(id) {
  const ta = document.getElementById("subs-texto-" + id);
  if (!ta) return;
  try {
    const d = await api("PUT", `/clips/${id}/subtitulos`, {texto: ta.value});
    if ($("clips-msg")) $("clips-msg").textContent = d.nota || "Texto guardado.";
    await cargarClips();
  } catch (e) { if ($("clips-msg")) $("clips-msg").textContent = e.message; }
}
// Acciones de un clip (VER / DESCARGAR) compartidas por «Clips generados»
// (prefijo "clip") y los clips de sesión del Centro LIVE (prefijo
// "liveclip"): los contenedores de estado/video llevan el prefijo para no
// chocar entre secciones. VER y DESCARGAR usan la COPIA que el trabajador
// sube al servidor; sin copia, piden la subida y esperan.
function accionesClip(r, prefijo) {
  const ver = r.tiene_copia_servidor
    ? `<button onclick="verClip(${r.id}, '${prefijo}')">VER</button>`
    : `<button onclick="pedirCopia(${r.id}, 'ver', '${prefijo}')">VER</button>`;
  const bajar = r.tiene_copia_servidor
    ? `<button onclick="descargarCopia(${r.id})">DESCARGAR</button>`
    : `<button onclick="pedirCopia(${r.id}, 'descargar', '${prefijo}')">DESCARGAR</button>`;
  return ver + " " + bajar +
    `<span id="${prefijo}-estado-${r.id}" class="nota"></span><div id="${prefijo}-video-${r.id}"></div>`;
}
function verClip(id, prefijo) {
  const c = clipsCache.find(x => x.id == id);
  abrirReproductor(id, prefijo, subsActivos(c || {id: id}));
}
function nombreLocalClip(c) {
  const p = String((c && c.file_path) || "").replace(/\\/g, "/");
  return p.split("/").pop() || ("clip-" + (c ? c.id : "x") + ".mp4");
}
function descargarCopia(id) {
  const c = clipsCache.find(x => x.id == id);
  fetch(`/clips/${id}/download`, {headers: {"Authorization": "Bearer " + token}}).then(async r => {
    if (r.status === 404) { descargarArchivoServidor(id); return; }
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      if ($("clips-msg")) $("clips-msg").textContent = d.detail || "Este clip no está disponible para descargar.";
      return;
    }
    const b = await r.blob();
    const disp = r.headers.get("Content-Disposition") || "";
    const m = /filename="?([^";]+)"?/.exec(disp);
    const a = document.createElement("a");
    a.href = URL.createObjectURL(b);
    a.download = (m && m[1]) || nombreLocalClip(c);
    a.click();
  }).catch(e => { if ($("clips-msg")) $("clips-msg").textContent = "No se pudo descargar el clip: " + e.message; });
}
function descargarArchivoServidor(id) {
  fetch(`/clips/${id}/archivo?descargar=1`, {headers: {"Authorization": "Bearer " + token}}).then(async r => {
    if (!r.ok) return;
    const b = await r.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(b);
    a.download = nombreLocalClip(clipsCache.find(x => x.id == id));
    a.click();
  }).catch(() => {});
}
async function pedirCopia(id, accion, prefijo) {
  const est = document.getElementById(prefijo + "-estado-" + id);
  try { const d = await api("POST", `/clips/${id}/solicitar-subida`);
    if (d.tiene_copia_servidor || d.estado === "con_copia") {
      if (accion === "ver") verClip(id, prefijo); else descargarCopia(id);
      return;
    }
    if (est) est.textContent = d.hay_trabajador === false
      ? "Tu PC está apagada o sin motor: enciéndelo para subir el clip."
      : "Subiendo desde tu PC…";
    seguirSubida(id, accion, d.job_id, prefijo);
  } catch (e) { if (est) est.textContent = e.message; $("clips-msg").textContent = e.message; }
}
function seguirSubida(id, accion, jobId, prefijo) {
  if (subidasEnCurso[id]) { subidasEnCurso[id].accion = accion; return; }
  const reg = {accion, jobId: jobId || null, prefijo, desde: Date.now(), ocupado: false, iv: null};
  subidasEnCurso[id] = reg;
  const paso = async () => {
    if (reg.ocupado) return; reg.ocupado = true;
    try {
      const cs = await api("GET", "/clips");
      clipsCache = cs;
      const c = cs.find(x => x.id == id);
      const est = document.getElementById(reg.prefijo + "-estado-" + id);
      if (c && c.tiene_copia_servidor) {
        clearInterval(reg.iv); delete subidasEnCurso[id];
        if (reg.prefijo === "liveclip") await cargarClipsSesion(); else await cargarClips();
        if (reg.accion === "ver") verClip(id, reg.prefijo); else descargarCopia(id);
        return;
      }
      if (reg.jobId) {
        try { const js = await api("GET", "/jobs?limit=50"); const j = (js || []).find(x => x.id == reg.jobId);
          if (j && (j.status === "fallido" || j.status === "failed")) {
            clearInterval(reg.iv); delete subidasEnCurso[id];
            if (est) est.textContent = "La subida falló" + (j.error ? ": " + j.error : ".");
            return;
          }
        } catch (e) {}
      }
      if (Date.now() - reg.desde > 180000) {
        clearInterval(reg.iv); delete subidasEnCurso[id];
        if (est) est.textContent = "La subida sigue en curso en tu PC; vuelve a intentarlo en un momento.";
      }
    } catch (e) { /* red/API transitoria: el siguiente paso reintenta */ }
    finally { reg.ocupado = false; }
  };
  reg.iv = setInterval(paso, 3000); paso();
}
async function cargarClips() {
  try {
    const ses = await api("GET", "/sessions");
    const filtro = $("bib-sesion");
    if (filtro) {
      const previa = filtro.value;
      filtro.innerHTML = `<option value="">Todas las sesiones</option>` + ses.map(s => `<option value="${s.id}">#${s.id} ${esc(s.title)}</option>`).join("");
      if (previa) filtro.value = previa;
    }
    const sid = filtro && filtro.value ? filtro.value : "";
    const fecha = ($("bib-fecha") && $("bib-fecha").value) || "";
    const orden = ($("bib-orden") && $("bib-orden").value) || "reciente";
    clipsCache = await api("GET", "/clips?limit=200" + (sid ? `&session_id=${sid}` : ""));
    let lista = clipsCache.slice();
    if (fecha) lista = lista.filter(c => String(c.created_at || "").slice(0, 10) === fecha);
    lista.sort((a, b) => orden === "antiguo" ? a.id - b.id : b.id - a.id);
    const box = $("clips");
    if (!box) return;
    if (!lista.length) { box.innerHTML = "<p class='nota'>Sin clips en esta vista.</p>"; return; }
    box.innerHTML = lista.map(tarjetaClip).join("");
  } catch (e) { if ($("clips-msg")) $("clips-msg").textContent = e.message; }
}
function tarjetaClip(c) {
  const cuando = String(c.created_at || "").replace("T", " ").slice(0, 16);
  const sesion = c.session_id ? ("#" + c.session_id + " " + (c.sesion_titulo || "")) : "Sin sesión";
  const estado = c.archivo_existe || c.descargable ? (c.status || "listo") : "archivo no encontrado";
  const thumb = c.tiene_miniatura ? `<img src="/clips/${c.id}/miniatura" alt="" class="bib-thumb" loading="lazy">` : `<div class="bib-thumb bib-sin">Sin miniatura</div>`;
  return `<article class="bib-card">
    ${thumb}
    <div class="bib-cuerpo">
      <b>${esc(c.title || ("Clip #" + c.id))}</b>
      <span class="nota">${esc(sesion)}</span>
      <span class="nota">${esc(cuando)} · ${c.duration_s ?? "—"} s · <span class="score">${c.score}/100</span></span>
      <span class="nota">${esc(estado)}</span>
      <span class="nota">${esc(notaSubtitulos(c))}</span>
      <div class="bib-acciones">
        <button type="button" class="${subsActivos(c) ? "subs-on" : "sec"} subs-toggle" onclick="alternarSubtitulos(${c.id})">SUBTÍTULOS ${subsActivos(c) ? "ON" : "OFF"}</button>
        <button type="button" onclick="verClip(${c.id}, 'clip')">▶ VER CLIP</button>
        <button type="button" class="sec" onclick="descargarCopia(${c.id})">DESCARGAR CLIP</button>
        <button type="button" class="sec" onclick="descargarSubtitulado(${c.id})">DESCARGAR CON SUBTÍTULOS</button>
        <button type="button" class="sec" onclick="pedirEliminarClip(${c.id})">ELIMINAR CLIP</button>
      </div>
      <label class="subs-correccion">Corregir texto
        <textarea id="subs-texto-${c.id}" maxlength="4000">${esc(c.subtitulo_texto || "")}</textarea>
      </label>
      <button type="button" class="sec" onclick="guardarTextoSubtitulos(${c.id})">GUARDAR TEXTO</button>
    </div>
  </article>`;
}
function abrirReproductor(id, prefijo, conSubtitulos) {
  const c = clipsCache.find(x => x.id == id);
  const caja = $("reproductor"); const vid = $("reproductor-video");
  if (!caja || !vid) return;
  const usar = !!conSubtitulos && c && c.tiene_subtitulado;
  if ($("reproductor-titulo")) $("reproductor-titulo").textContent = (c && c.title) || ("Clip #" + id);
  if ($("reproductor-nota")) $("reproductor-nota").textContent = usar
    ? "Reproduciendo con subtítulos. El original siempre queda en tu PC."
    : "El original siempre queda en tu PC.";
  vid.src = usar ? ("/clips/" + id + "/subtitulado") : ("/clips/" + id + "/media");
  caja.classList.remove("oculto");
  vid.play().catch(() => {});
}
function cerrarReproductor() {
  const vid = $("reproductor-video"); const caja = $("reproductor");
  if (vid) { vid.pause(); vid.removeAttribute("src"); vid.load(); }
  if (caja) caja.classList.add("oculto");
}
function pedirEliminarClip(id) {
  clipAEliminar = id;
  const c = clipsCache.find(x => x.id == id);
  if ($("confirmar-nombre")) $("confirmar-nombre").textContent = (c && c.title) || ("Clip #" + id);
  const caja = $("confirmar-borrar"); if (caja) caja.classList.remove("oculto");
}
function cancelarBorrarClip() {
  clipAEliminar = null;
  const caja = $("confirmar-borrar"); if (caja) caja.classList.add("oculto");
}
async function confirmarBorrarClip() {
  const id = clipAEliminar;
  if (!id) return;
  try {
    await api("DELETE", `/clips/${id}`);
    cancelarBorrarClip();
    if ($("clips-msg")) $("clips-msg").textContent = "Clip #" + id + " eliminado.";
    await cargarClips();
    cargarClipsSesion();
  } catch (e) { if ($("clips-msg")) $("clips-msg").textContent = e.message; }
}
async function cargarClipSelects() {
  await cargarClips();
  const opts = clipsCache.map(c => `<option value="${c.id}">#${c.id} ${esc(c.title || "")} (${c.score}/100)</option>`).join("");
  $("ed-clip").innerHTML = opts; $("pub-clip").innerHTML = opts;
}
function descargar(id) {
  const c = clipsCache.find(x => x.id == id);
  if (c && c.descargable === false) { alert("Este clip vive en tu PC (lo generó el trabajador), no en el servidor. Ábrelo desde su carpeta:\n" + (c.file_path || "")); return; }
  fetch(`/clips/${id}/download`, {headers: {"Authorization": "Bearer " + token}}).then(async r => {
    if (!r.ok) { const d = await r.json().catch(() => ({})); alert(d.detail || "No descargable desde el servidor"); return; }
    const b = await r.blob(); const a = document.createElement("a");
    a.href = URL.createObjectURL(b); a.download = "clip-" + id + ".mp4"; a.click();
  });
}
function descargarClip() { descargar($("ed-clip").value); }
// Editor profesional (Etapa 3): la API valida y encola; exporta el trabajador.
let logoPath = "";
async function cargarEditor() {
  await cargarClipSelects();
  $("ed-clips").innerHTML = tabla(clipsCache, [["Clip", r => "#" + r.id + " " + esc(r.title || "")], ["Formato", r => esc(r.format)], ["Duración", r => r.duration_s + "s"], ["Archivo", r => r.descargable ? "En el servidor" : esc(r.file_path || "—") + " (en tu PC)"], ["Acciones", r => r.descargable ? `<button onclick="descargar(${r.id})">DESCARGAR</button>` : "—"]]);
}
async function subirLogo() {
  const f = $("ed-logo-file").files[0], cid = $("ed-clip").value;
  if (!f || !cid) { $("ed-logo-msg").textContent = "Elige un clip y un archivo de logo."; return; }
  const fd = new FormData(); fd.append("file", f);
  try { const r = await fetch(`/clips/${cid}/logo`, {method: "POST", headers: {"Authorization": "Bearer " + token}, body: fd});
    const d = await r.json(); if (!r.ok) throw new Error(d.detail || "Error");
    logoPath = d.logo_path; $("ed-logo-msg").textContent = "Logo guardado: " + d.logo_path;
  } catch (e) { $("ed-logo-msg").textContent = e.message; }
}
async function encolarEdicion() {
  const cid = $("ed-clip").value; if (!cid) { $("ed-msg").textContent = "No hay clip seleccionado."; return; }
  const body = {trim_start_s: Number($("ed-ini").value || 0), formato: $("ed-formato").value,
    offset_x: Number($("ed-ox").value || 0), offset_y: Number($("ed-oy").value || 0),
    zoom: Number($("ed-zoom").value || 1), volumen: Number($("ed-vol").value || 1),
    texto: $("ed-texto").value || null, texto_activo: $("ed-texto-activo").checked,
    logo_path: logoPath || null, subtitulos: $("ed-subs").checked,
    transcript: $("ed-transcript").value || null};
  const fin = $("ed-fin").value; if (fin !== "") body.trim_end_s = Number(fin);
  try { const d = await api("POST", `/clips/${cid}/edit`, body);
    $("ed-msg").textContent = "Trabajo #" + d.job_id + " encolado. " + (d.nota || "Lo exporta el trabajador; revisa Trabajos y Clips generados.");
  } catch (e) { $("ed-msg").textContent = e.message; }
}
async function pedirMiniatura() {
  const cid = $("ed-clip").value; if (!cid) return;
  try { const d = await api("POST", `/clips/${cid}/thumbnail`, {segundo: 1});
    $("ed-msg").textContent = "Miniatura encolada (trabajo #" + (d.job_id || "?") + "). La genera el trabajador.";
  } catch (e) { $("ed-msg").textContent = e.message; }
}
async function sugerirTextos() {
  const c = clipsCache.find(x => x.id == $("pub-clip").value); if (!c) return;
  try { const t = await api("POST", "/titles", {type: "general", mode: "general", score: c.score});
    $("pub-titulo").value = t.titulos[0]; $("pub-desc").value = t.descripciones.facebook; $("pub-tags").value = t.hashtags.join(" ");
  } catch (e) {}
}
async function publicarAhora(programar = false) {
  const plats = [...document.querySelectorAll(".plat:checked")].map(x => x.value);
  if (!plats.length) { $("pub-msg").textContent = "Marca al menos una plataforma."; return; }
  const res = [];
  for (const p of plats) {
    try { const d = await api("POST", "/publications", {clip_id: Number($("pub-clip").value), platform: p,
        title: $("pub-titulo").value, description: $("pub-desc").value, hashtags: $("pub-tags").value,
        scheduled_at: (programar && $("pub-fecha").value) ? $("pub-fecha").value : null});
      res.push(`${p}: ${d.status}. ${d.nota}`);
    } catch (e) { res.push(`${p}: ERROR ${e.message}`); }
  }
  $("pub-msg").textContent = res.join("\n"); cargarPubs();
}
async function cargarPubs() {
  try { const ps = await api("GET", "/publications");
    $("publicaciones").innerHTML = tabla(ps, [["Plataforma", r => esc(r.platform)], ["Título", r => esc(r.title || "")], ["Estado", r => esc(r.status)], ["Programada", r => esc(r.scheduled_at || "—")], ["Acción", r => `<button onclick="aprobarPub(${r.id})">APROBAR</button> <button onclick="publicarReal(${r.id})">PUBLICAR</button>`]]);
  } catch (e) {}
}
async function aprobarPub(id) {
  try { const d = await api("POST", `/publications/${id}/approve`); alert(d.nota || d.status); cargarPubs(); }
  catch (e) { alert(e.message); }
}
async function cargarRedes() {
  try { const rs = await api("GET", "/social");
    $("redes").innerHTML = tabla(rs, [["Plataforma", r => esc(r.platform)], ["Estado", r => esc(r.status)], ["Cuenta", r => esc(r.account || "—")], ["App OAuth", r => r.oauth_configurado ? "Configurada" : "Requiere configuración"], ["Nota", r => esc(r.nota || "")], ["Acción", r => `<button onclick="conectarRed('${r.platform}')">CONECTAR</button>`]]);
  } catch (e) {}
}
async function conectarRed(plat) {
  try { const r = await fetch(`/auth/${plat}/conectar?formato=json`, {headers: {"Authorization": "Bearer " + token}});
    const d = await r.json().catch(() => ({}));
    if (r.ok && d.url) { window.open(d.url, "_blank"); alert("Se abrió la página oficial de " + plat + " en una pestaña nueva: autoriza la cuenta allí y luego vuelve a esta pestaña y recarga «Mis redes sociales»."); return; }
    alert(d.detail || "No se pudo iniciar la conexión con " + plat + ".");
  } catch (e) { alert("No se pudo iniciar la conexión: " + e.message); }
}
async function publicarReal(id) {
  try { const d = await api("POST", `/publications/${id}/publish`);
    alert(d.nota || (d.ok ? "Publicado con confirmación de la plataforma." : "No se publicó: " + (d.status || ""))); cargarPubs();
  } catch (e) { alert(e.message); }
}
async function cargarCalendario() {
  try { const cs = await api("GET", "/calendar");
    $("calendario").innerHTML = tabla(cs, [["Fecha/hora", r => esc(r.when_at)], ["Publicación", r => "#" + r.publication_id], ["Nota", r => esc(r.note)], ["Estado", r => esc(r.status || "programada")], ["Acciones", r => `<button onclick="reprogramarCal(${r.id})">REPROGRAMAR</button> <button onclick="cancelarCal(${r.id})">CANCELAR</button>`]]);
    const ps = await api("GET", "/publications");
    $("cal-pub").innerHTML = ps.map(p => `<option value="${p.id}">#${p.id} ${esc(p.platform)} — ${esc(p.title || "")} (${esc(p.status)})</option>`).join("") || "<option value=''>Sin publicaciones</option>";
  } catch (e) {}
}
async function programarCal() {
  if (!$("cal-pub").value || !$("cal-fecha").value) { $("cal-msg").textContent = "Elige publicación y fecha/hora."; return; }
  try { await api("POST", "/calendar", {publication_id: Number($("cal-pub").value), when_at: $("cal-fecha").value, note: $("cal-nota").value});
    $("cal-msg").textContent = "Programada."; cargarCalendario();
  } catch (e) { $("cal-msg").textContent = e.message; }
}
async function reprogramarCal(id) {
  const nueva = prompt("Nueva fecha y hora (ej.: 2026-11-20T19:00)"); if (!nueva) return;
  try { await api("PUT", `/calendar/${id}`, {when_at: nueva}); cargarCalendario(); }
  catch (e) { alert(e.message); }
}
async function cancelarCal(id) {
  try { await api("DELETE", `/calendar/${id}`); cargarCalendario(); }
  catch (e) { alert(e.message); }
}
async function cargarHistorial() {
  try { const hs = await api("GET", "/history");
    $("historial").innerHTML = tabla(hs, [["Fecha", r => esc(r.created_at)], ["Usuario", r => esc(r.user_email || "")], ["Acción", r => esc(r.action)], ["Detalle", r => esc(r.detail)]]);
  } catch (e) {}
}
async function cargarConfig() {
  try { const c = await api("GET", "/config");
    $("cf-buffer").value = c.buffer_min; $("cf-pre").value = c.pre_s; $("cf-post").value = c.post_s;
    $("cf-score").value = c.min_score_alert; $("cf-tz").value = c.timezone;
    const ap = await api("GET", "/autopilot");
    $("ap-activo").checked = !!ap.activo; $("ap-score").value = ap.min_score;
    $("ap-hi").value = ap.hora_inicio || ""; $("ap-hf").value = ap.hora_fin || "";
    document.querySelectorAll(".ap-plat").forEach(x => x.checked = (ap.plataformas || []).includes(x.value));
  } catch (e) {}
}
async function guardarPiloto() {
  try { const ap = await api("PUT", "/autopilot", {activo: $("ap-activo").checked, min_score: Number($("ap-score").value || 80),
      hora_inicio: $("ap-hi").value || null, hora_fin: $("ap-hf").value || null, timezone: $("cf-tz").value || "America/Santo_Domingo",
      plataformas: [...document.querySelectorAll(".ap-plat:checked")].map(x => x.value)});
    $("ap-msg").textContent = "Piloto guardado: " + (ap.activo ? "ACTIVADO" : "DESACTIVADO") + ", mínimo " + ap.min_score + "/100. Sigue sin publicar solo: deja borradores para tu aprobación.";
  } catch (e) { $("ap-msg").textContent = e.message + " (solo el administrador)"; }
}
async function guardarConfig() {
  try { await api("PUT", "/config", {values: {buffer_min: Number($("cf-buffer").value), pre_s: Number($("cf-pre").value), post_s: Number($("cf-post").value), min_score_alert: Number($("cf-score").value), timezone: $("cf-tz").value}});
    $("cf-msg").textContent = "Configuración guardada."; }
  catch (e) { $("cf-msg").textContent = e.message + " (solo el administrador)"; }
}
async function registrarWorker() {
  try { const d = await api("POST", "/workers/register", {name: $("wk-nombre").value || "PC", info: {}});
    $("wk-msg").textContent = "Token del trabajador (guárdalo, no se repite): " + d.worker_token; cargarWorkers(); }
  catch (e) { $("wk-msg").textContent = e.message; }
}
async function cargarWorkers() {
  try { const ws = await api("GET", "/workers");
    $("workers").innerHTML = tabla(ws, [["Nombre", r => esc(r.name)], ["Estado", r => esc(r.status)], ["Motor", r => r.version ? "v" + esc(r.version) : "—"], ["Última vez", r => esc(r.last_seen || "—")]]);
  } catch (e) {}
}
if (token) { api("GET", "/auth/me").then(d => mostrarApp(d.email)).catch(() => {}); }
