// JDM VIRAL CLIPS AI — panel. Todo sale de la API; estados vacíos honestos.
const SECCIONES = [["inicio","🏠 Inicio"],["centro","🔴 Centro LIVE"],["radar","📡 LIVE VIRAL RADAR"],["proyecto","🆕 Nuevo proyecto"],["importar","📥 Importar videos"],["analizar","🤖 Analizar con IA"],["clips","🎬 Clips generados"],["editor","✂️ Editor"],["redes","🌐 Mis redes sociales"],["publicar","📤 Publicar clips"],["calendario","📅 Calendario"],["historial","🕘 Historial"],["stats","📊 Estadísticas"],["config","⚙️ Configuración"],["admin","🛠 Administración"]];
let token = localStorage.getItem("jdm_token") || "";
let motorConectado = null, ultimoEstadoMotor = null; // null = aún sin comprobar
let ivMotor = null, ivEventos = null;                 // intervalos de auto-refresco por sección
let ivGrab = null, ivContador = null, grabEnCurso = null, capcutIv = null; // seguimiento de grabación / CapCut
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
  if (sec === "centro" && !ivMotor) ivMotor = setInterval(cargarMotor, 15000);
  if (sec === "radar" && !ivEventos) ivEventos = setInterval(cargarEventos, 15000);
  ({inicio: cargarStats, stats: cargarStats, radar: cargarEventos, analizar: cargarAnalizar, clips: cargarClips,
    editor: cargarEditor, redes: cargarRedes, publicar: () => {cargarClipSelects(); cargarPubs();},
    calendario: cargarCalendario, historial: cargarHistorial, config: cargarConfig, admin: cargarWorkers,
    centro: cargarSesiones, importar: cargarProyectos}[sec] || (() => {}))();
}
async function entrar() {
  try { const d = await api("POST", "/auth/login", {email: $("li-email").value, password: $("li-pass").value});
    token = d.token; localStorage.setItem("jdm_token", token); mostrarApp(d.email);
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
function salir() { localStorage.removeItem("jdm_token"); location.reload(); }
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
  const activo = !!(ses && ses.radar_activo);
  const cuenta = (nMomentos === null || nMomentos === undefined) ? "" : ` — ${nMomentos} momentos`;
  btn.innerHTML = (activo ? "📡 RADAR ACTIVADO — DESACTIVAR" : "📡 ACTIVAR RADAR VIRAL") + ` <span id="radar-count">${esc(cuenta)}</span>`;
}
async function refrescarRadarUI(ses) {
  const sid = ses ? ses.id : sesionId();
  pintarBtnRadar(ses, await contarMomentos(sid));
}
// Distintivo + contador de grabación en vivo (desde started_at confirmado por la API).
function arrancarContador(startedAt) {
  if (ivContador) clearInterval(ivContador);
  const t0 = parseFechaAPI(startedAt); const base = t0 ? t0.getTime() : Date.now();
  const tick = () => { const el = $("live-estado"); if (el) el.textContent = "🔴 GRABANDO EN VIVO — " + fmtHMS((Date.now() - base) / 1000); };
  tick(); ivContador = setInterval(tick, 1000);
}
function mostrarGrabando(ses) {
  const el = $("live-estado"); if (!el) return;
  el.classList.remove("oculto"); el.classList.add("grabando"); arrancarContador(ses && ses.started_at);
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
          if (g.modo !== "detencion") { g.modo = "vigilancia"; }
          if (g.rapido) { g.rapido = false; clearInterval(ivGrab); ivGrab = setInterval(paso, 5000); }
          return;
        }
        if (ses.status === "detenida") {
          ocultarGrabando(); pararSeguimientoGrab();
          if (g.modo === "detencion") setLiveMsg("Grabación detenida. Los segmentos ya guardados no se pierden.");
          cargarSesiones(); return;
        }
        if (ses.status === "error") {
          let detalle = "";
          if (g.jobId) { try { const js = await api("GET", "/jobs?limit=50"); const j = (js || []).find(x => x.id == g.jobId); if (j && j.error) detalle = j.error; } catch (e) {} }
          ocultarGrabando(); pararSeguimientoGrab();
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
  else if (ses.status === "iniciando") { ocultarGrabando(); if (!yaSigue) seguirEstadoGrabacion(ses.id, null, "inicio"); }
  else if (ses.status === "deteniendo") { if (!yaSigue) seguirEstadoGrabacion(ses.id, null, "detencion"); }
  else { ocultarGrabando(); pararSeguimientoGrab(); }
}
async function cargarSesiones() {
  const previa = $("ss-lista") ? $("ss-lista").value : "";
  cargarMotor();
  try { const ss = await api("GET", "/sessions");
    $("ss-lista").innerHTML = ss.map(s => `<option value="${s.id}">#${s.id} ${esc(s.title)} — ${esc(s.status)}${s.radar_activo ? " · 📡 radar" : ""}</option>`).join("") || "<option value=''>Sin sesiones</option>";
    if (previa && ss.some(s => String(s.id) === String(previa))) $("ss-lista").value = previa;
    const tablaSes = $("ss-tabla");
    if (tablaSes) tablaSes.innerHTML = tabla(ss, [["Sesión", r => "#" + r.id + " " + esc(r.title)], ["Estado", r => esc(r.status)], ["Radar", r => r.radar_activo ? "📡 activo" : "—"], ["Acciones", r => `<button onclick="proyectoCapCutSesion(${r.id})">Proyecto CapCut</button>`]]);
    const sel = ss.find(s => String(s.id) === String(sesionId())) || null;
    restaurarEstadoGrabacion(sel);
    await refrescarRadarUI(sel);
    cargarClipsSesion();
  } catch (e) {}
}
async function proyectoCapCutSesion(id) {
  if (capcutIv) { clearInterval(capcutIv); capcutIv = null; }
  let jobId = null;
  try { const d = await api("POST", `/sessions/${id}/capcut`); jobId = d.job_id;
    setLiveMsg("Proyecto CapCut encolado (trabajo #" + jobId + ", " + (d.clips ?? "?") + " clips). Esperando confirmación del trabajador… " + (d.nota || ""), true);
  } catch (e) { setLiveMsg(e.message); return; }
  const desde = Date.now(); let ocupado = false;
  const paso = async () => {
    if (ocupado) return; ocupado = true;
    try { const js = await api("GET", "/jobs?limit=50"); const j = (js || []).find(x => x.id == jobId);
      if (j && j.status === "completado") {
        clearInterval(capcutIv); capcutIv = null;
        const cap = (j.result && j.result.capcut) || null;
        let donde = "", paquete = "";
        if (typeof cap === "string") donde = cap;
        else if (cap && typeof cap === "object") { donde = cap.borrador || cap.carpeta || cap.ruta || cap.path || ""; paquete = cap.paquete || cap.paquete_importacion || ""; }
        if (!paquete && j.result && j.result.paquete) paquete = j.result.paquete;
        setLiveMsg("Proyecto CapCut creado" + (donde ? ": " + donde : " (trabajo #" + jobId + " completado)") + (paquete ? ". Paquete de importación: " + paquete : ""));
      } else if (j && (j.status === "fallido" || j.status === "failed")) {
        clearInterval(capcutIv); capcutIv = null;
        setLiveMsg("El Proyecto CapCut falló" + (j.error ? ": " + j.error : " (trabajo #" + jobId + ")."));
      } else if (Date.now() - desde > 120000) {
        clearInterval(capcutIv); capcutIv = null;
        setLiveMsg("El trabajo #" + jobId + " de CapCut sigue en curso; revisa «Trabajos».", true);
      }
    } catch (e) { /* reintenta en el siguiente paso */ }
    finally { ocupado = false; }
  };
  capcutIv = setInterval(paso, 3000); paso();
}
function sesionId() { const el = $("ss-lista"); return el ? el.value : ""; }
// Al cambiar la selección: restaura grabación/radar, momentos y clips de esa sesión.
async function cargarSesion() {
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
    pintarBtnRadar({radar_activo: d.radar_activo}, await contarMomentos(sid));
    setLiveMsg(d.radar_activo
      ? "📡 Radar en vivo ACTIVADO en la sesión #" + sid + (d.job_id ? " (trabajo #" + d.job_id + ")" : "") + ". Mientras grabes, los momentos irán apareciendo en «LIVE VIRAL RADAR» con sus clips."
      : "Radar en vivo DESACTIVADO en la sesión #" + sid + ".");
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
      `<button onclick="generarTodosClips()">GENERAR TODOS LOS CLIPS</button>` +
      `<button onclick="proyectoCapCutSesion(${sid})">PROYECTO CAPCUT</button></div>` +
      tabla(cs, [["Clip", r => "#" + r.id + " " + esc(r.title || "")], ["Duración", r => (r.duration_s ?? "—") + "s"], ["Score", r => `<span class='score'>${r.score}/100</span>`], ["Archivo/ruta", r => esc(r.file_path || "—")], ["Acciones", r => `<button onclick="enviarCapCutLive(${r.id})">Enviar a CapCut</button>`]]);
  } catch (e) { box.innerHTML = `<div class="fila-clips"><h4>Clips de esta sesión</h4><span class="nota">${esc(e.message)}</span></div>`; }
}
async function enviarCapCutLive(id) { await enviarCapCut(id); const m = $("clips-msg"); if (m && m.textContent) setLiveMsg(m.textContent); }
async function generarTodosClips() {
  const sid = sesionId(); if (!sid) { setLiveMsg("Crea y selecciona una sesión primero."); return; }
  try { const d = await api("POST", `/sessions/${sid}/generar-clips`);
    setLiveMsg(`${d.encolados ?? 0} trabajos encolados, ${d.omitidos ?? 0} omitidos (ya tenían clip o trabajo en curso)`);
    [5000, 15000, 30000].forEach(ms => setTimeout(cargarClipsSesion, ms));
  } catch (e) { setLiveMsg(e.message); }
}
// Radar / eventos (la API ya excluye los descartados)
async function cargarEventos() {
  try { const sel = $("ss-lista"); const sid = sel && sel.value ? sel.value : "";
    const ev = await api("GET", sid ? `/events?session_id=${sid}` : "/events");
    if (!ev.length) { $("eventos").innerHTML = "<p class='nota'>Sin datos todavía.</p>"; }
    else $("eventos").innerHTML = "<table><tr><th>Tipo</th><th>Inicio–Fin</th><th>Duración</th><th>Motivo</th><th>Puntuación</th><th>Acciones</th></tr>" +
      ev.map(r => {
        const bd = r.breakdown && typeof r.breakdown === "object" ? Object.entries(r.breakdown) : [];
        const motivo = bd.length ? bd.map(([k, v]) => `${esc(k)}: ${esc(typeof v === "object" ? JSON.stringify(v) : v)}`).join(" · ") : esc(r.status || "");
        const clipTxt = r.tiene_clip
          ? `<span class="nota">${esc(r.clip_file_path || ("Clip #" + (r.clip_id ?? "")))}</span> ` + (r.clip_descargable ? `<button onclick="reproducirEvento(${r.clip_id}, ${r.id})">REPRODUCIR</button>` : "")
          : `<button onclick="generarClipEvento(${r.id})">GENERAR CLIP</button>`;
        return `<tr><td>${esc(r.type)}</td><td>${fmtMS(r.start_s)}–${fmtMS(r.end_s)}</td><td>${r.duracion_s ?? "—"}s</td><td>${motivo}</td><td><span class='score'>${r.score}/100</span></td>` +
          `<td>${clipTxt} <button class="sec" onclick="descartarEvento(${r.id})">DESCARTAR</button><div id="ev-video-${r.id}"></div></td></tr>`;
      }).join("") + "</table>";
    if (sid) { const c = $("radar-count"); if (c) c.textContent = ` — ${ev.length} momentos`; }
  } catch (e) { $("eventos").innerHTML = esc(e.message); }
}
async function generarClipEvento(id) {
  try { const d = await api("POST", `/events/${id}/clip`);
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
async function cargarClips() {
  try { clipsCache = await api("GET", "/clips");
    $("clips").innerHTML = tabla(clipsCache, [["Clip", r => "#" + r.id + " " + esc(r.title || "")], ["Formato", r => esc(r.format)], ["Duración", r => r.duration_s + "s"], ["Score", r => `<span class='score'>${r.score}/100</span>`], ["Subtítulos", r => r.subtitles ? "Sí" : "No"], ["Archivo", r => r.descargable ? "En el servidor" : esc(r.file_path || "—") + " (en tu PC)"], ["Acciones", r => (r.descargable ? `<button onclick="descargar(${r.id})">DESCARGAR</button> ` : "") + `<button onclick="enviarCapCut(${r.id})">Enviar a CapCut</button>`]]);
  } catch (e) {}
}
async function enviarCapCut(id) {
  try { const d = await api("POST", `/clips/${id}/capcut`);
    $("clips-msg").textContent = "Trabajo #" + d.job_id + " encolado. " + (d.nota || "");
  } catch (e) { $("clips-msg").textContent = e.message; }
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
