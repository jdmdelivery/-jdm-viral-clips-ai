// JDM VIRAL CLIPS AI — panel. Todo sale de la API; estados vacíos honestos.
const SECCIONES = [["inicio","🏠 Inicio"],["centro","🔴 Centro LIVE"],["radar","📡 LIVE VIRAL RADAR"],["proyecto","🆕 Nuevo proyecto"],["importar","📥 Importar videos"],["analizar","🤖 Analizar con IA"],["clips","🎬 Clips generados"],["editor","✂️ Editor"],["redes","🌐 Mis redes sociales"],["publicar","📤 Publicar clips"],["calendario","📅 Calendario"],["historial","🕘 Historial"],["stats","📊 Estadísticas"],["config","⚙️ Configuración"],["admin","🛠 Administración"]];
let token = localStorage.getItem("jdm_token") || "";
const $ = id => document.getElementById(id);
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
function salir() { localStorage.removeItem("jdm_token"); location.reload(); }
function mostrarApp(email) {
  $("login").classList.add("oculto"); $("app").classList.remove("oculto"); $("usr").textContent = email || "";
  $("menu").innerHTML = SECCIONES.map(([k, v]) => `<button data-sec="${k}" onclick="ir('${k}')">${v}</button>`).join("");
  ir("inicio");
}
async function cargarStats() {
  try { const s = await api("GET", "/stats");
    const datos = {Sesiones: s.sesiones, "Grabando ahora": s.grabando, Eventos: s.eventos, Clips: s.clips, Proyectos: s.proyectos, "Trabajos pendientes": s.trabajos_pendientes, Publicaciones: s.publicaciones, "Score promedio": Math.round(s.score_promedio)};
    if (s.mejor_score !== undefined) { datos["Mejor score"] = s.mejor_score; datos["Eventos ≥ umbral"] = s.eventos_sobre_umbral; }
    if (s.publicaciones_por_estado) datos["Publicaciones por estado"] = Object.entries(s.publicaciones_por_estado).map(([k, v]) => `${k}: ${v}`).join(", ") || "—";
    if (s.eventos_por_modo) datos["Eventos por modo"] = Object.entries(s.eventos_por_modo).map(([k, v]) => `${k}: ${v}`).join(", ") || "—";
    const html = Object.entries(datos).map(([k, v]) => `<div class="card">${k}<br><b>${v}</b></div>`).join("");
    $("stats").innerHTML = html; $("stats2").innerHTML = html;
  } catch (e) { $("stats").innerHTML = "<p class='nota'>" + esc(e.message) + "</p>"; }
}
// Centro LIVE
async function crearSesion() {
  try { await api("POST", "/sessions", {title: $("ss-titulo").value || "Sesión LIVE", mode: $("ss-modo").value});
    $("live-msg").textContent = "Sesión creada."; cargarSesiones(); }
  catch (e) { $("live-msg").textContent = e.message; }
}
async function cargarSesiones() {
  try { const ss = await api("GET", "/sessions");
    $("ss-lista").innerHTML = ss.map(s => `<option value="${s.id}">#${s.id} ${esc(s.title)} — ${esc(s.status)}</option>`).join("") || "<option value=''>Sin sesiones</option>";
  } catch (e) {}
}
function sesionId() { return $("ss-lista").value; }
async function cargarSesion() {}
async function validarFuente() {
  const sid = sesionId(); if (!sid) { $("src-msg").textContent = "Crea y selecciona una sesión primero."; return; }
  try { const d = await api("POST", `/sessions/${sid}/sources`, {url: $("src-url").value});
    $("src-msg").textContent = (d.allowed ? "✅ Compatible: " : "⛔ No grabable así: ") + d.explanation; }
  catch (e) { $("src-msg").textContent = e.message; }
}
function abrirLive() {
  const url = $("src-url").value.trim(), v = $("player");
  if (!url) { $("live-msg").textContent = "Pega primero el enlace de la fuente (HLS .m3u8, RTMP…) en el campo de la fuente."; return; }
  if (window.Hls && Hls.isSupported() && url.includes(".m3u8")) { const h = new Hls(); h.loadSource(url); h.attachMedia(v); }
  else { v.src = url; }
  v.play().catch(() => { $("live-msg").textContent = "El navegador no pudo reproducir la fuente (puede requerir CORS o no ser compatible). La grabación la hace el trabajador con FFmpeg."; });
}
async function iniciarGrab() {
  try { const d = await api("POST", `/sessions/${sesionId()}/start`);
    $("live-msg").textContent = "Grabación solicitada (trabajo #" + d.job_id + "). La ejecuta el trabajador; sin trabajador conectado queda pendiente."; }
  catch (e) { $("live-msg").textContent = e.message; }
}
function activarRadar() { $("live-msg").textContent = "El radar en vivo llega en la próxima actualización; hoy el análisis corre al detener la grabación o al importar el video. Los momentos detectados aparecen en «LIVE VIRAL RADAR»."; }
async function detenerGrab() {
  try { const d = await api("POST", `/sessions/${sesionId()}/stop`);
    $("live-msg").textContent = "Detención solicitada (trabajo #" + d.job_id + "). Los segmentos ya guardados no se pierden."; cargarSesiones(); }
  catch (e) { $("live-msg").textContent = e.message; }
}
// Radar / eventos
async function cargarEventos() {
  try { const ev = await api("GET", "/events");
    $("eventos").innerHTML = tabla(ev, [["Tipo", r => esc(r.type)], ["Modo", r => esc(r.mode)], ["Inicio", r => r.start_s + "s"], ["Fin", r => r.end_s + "s"], ["Puntuación", r => `<span class='score'>${r.score}/100</span>`], ["Estado", r => esc(r.status)], ["Acciones", r => `<button onclick="ir('publicar')">PUBLICAR</button>`]]);
  } catch (e) { $("eventos").innerHTML = esc(e.message); }
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
    $("clips").innerHTML = tabla(clipsCache, [["Clip", r => "#" + r.id + " " + esc(r.title || "")], ["Formato", r => esc(r.format)], ["Duración", r => r.duration_s + "s"], ["Score", r => `<span class='score'>${r.score}/100</span>`], ["Subtítulos", r => r.subtitles ? "Sí" : "No"], ["Archivo", r => r.descargable ? "En el servidor" : esc(r.file_path || "—") + " (en tu PC)"], ["Acciones", r => r.descargable ? `<button onclick="descargar(${r.id})">DESCARGAR</button>` : "—"]]);
  } catch (e) {}
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
    $("workers").innerHTML = tabla(ws, [["Nombre", r => esc(r.name)], ["Estado", r => esc(r.status)], ["Última vez", r => esc(r.last_seen || "—")]]);
  } catch (e) {}
}
if (token) { api("GET", "/auth/me").then(d => mostrarApp(d.email)).catch(() => {}); }
