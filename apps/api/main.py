"""JDM VIRAL CLIPS AI — API FastAPI (Etapa 1).
Sirve también el panel web estático. DATABASE_URL configurable (SQLite local / PostgreSQL en Render)."""
import hashlib, hmac, ipaddress, json, os, secrets, socket, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import (Boolean, Column, DateTime, Float, ForeignKey, Integer,
                        String, Text, create_engine)
from sqlalchemy.orm import declarative_base, sessionmaker

sys.path.insert(0, str(Path(__file__).parent))
from titles import generar as generar_titulos  # noqa: E402
import social as social_lib  # noqa: E402  (Etapa 4: OAuth/publicación real)

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data"; DATA_DIR.mkdir(exist_ok=True)
UPLOAD_DIR = DATA_DIR / "uploads"; UPLOAD_DIR.mkdir(exist_ok=True)
LOGO_DIR = DATA_DIR / "logos"; LOGO_DIR.mkdir(exist_ok=True)
LOGO_MAX_BYTES = 5 * 1024 * 1024  # límite de subida de logos (Etapa 3): 5 MB
DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{DATA_DIR}/jdm.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {})
SessionLocal = sessionmaker(bind=engine, autoflush=False)
Base = declarative_base()
now = lambda: datetime.now(timezone.utc)  # noqa: E731

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True); email = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False); role = Column(String, default="user")
    created_at = Column(DateTime, default=now)
class SessionToken(Base):
    __tablename__ = "session_tokens"
    id = Column(Integer, primary_key=True); user_id = Column(Integer, ForeignKey("users.id"))
    token = Column(String, unique=True, nullable=False); created_at = Column(DateTime, default=now)
class Worker(Base):
    __tablename__ = "workers"
    id = Column(Integer, primary_key=True); name = Column(String, nullable=False)
    token_hash = Column(String, nullable=False); status = Column(String, default="desconectado")
    last_seen = Column(DateTime); info = Column(Text, default="{}")
class LiveSession(Base):
    __tablename__ = "live_sessions"
    id = Column(Integer, primary_key=True); title = Column(String, nullable=False)
    mode = Column(String, default="general")  # gta | alofoke | podcast | general
    status = Column(String, default="creada")  # creada|grabando|detenida|error
    autopilot = Column(Boolean, default=False); min_score = Column(Integer, default=80)
    created_at = Column(DateTime, default=now); user_id = Column(Integer, ForeignKey("users.id"))
class Source(Base):
    __tablename__ = "sources"
    id = Column(Integer, primary_key=True); session_id = Column(Integer, ForeignKey("live_sessions.id"))
    url = Column(Text, nullable=False); kind = Column(String, default="hls")
    allowed = Column(Boolean, default=False); explanation = Column(Text, default="")
class Segment(Base):
    __tablename__ = "segments"
    id = Column(Integer, primary_key=True); session_id = Column(Integer, ForeignKey("live_sessions.id"))
    path = Column(Text, nullable=False); index_no = Column(Integer, default=0)
    start_s = Column(Float, default=0); duration_s = Column(Float, default=10)
    protected = Column(Boolean, default=False)
class Event(Base):
    __tablename__ = "events"
    id = Column(Integer, primary_key=True); session_id = Column(Integer, ForeignKey("live_sessions.id"), index=True)
    project_id = Column(Integer, ForeignKey("projects.id")); type = Column(String, default="general")
    mode = Column(String, default="general"); start_s = Column(Float, default=0); end_s = Column(Float, default=0)
    score = Column(Integer, default=0, index=True); breakdown = Column(Text, default="{}")
    transcript = Column(Text); status = Column(String, default="detectado")
    created_at = Column(DateTime, default=now)
class Project(Base):
    __tablename__ = "projects"
    id = Column(Integer, primary_key=True); name = Column(String, nullable=False)
    file_path = Column(Text); duration_s = Column(Float); status = Column(String, default="importado")
    created_at = Column(DateTime, default=now)
class Clip(Base):
    __tablename__ = "clips"
    id = Column(Integer, primary_key=True); event_id = Column(Integer, ForeignKey("events.id"), index=True)
    project_id = Column(Integer, ForeignKey("projects.id")); session_id = Column(Integer, ForeignKey("live_sessions.id"))
    file_path = Column(Text); thumb_path = Column(Text); format = Column(String, default="9:16")
    duration_s = Column(Float); score = Column(Integer, default=0)
    title = Column(String); titles_json = Column(Text, default="{}")
    subtitles = Column(Boolean, default=False); status = Column(String, default="listo")
    created_at = Column(DateTime, default=now)
class Job(Base):
    __tablename__ = "jobs"
    id = Column(Integer, primary_key=True); type = Column(String, nullable=False)
    payload = Column(Text, default="{}"); status = Column(String, default="pendiente", index=True)
    attempts = Column(Integer, default=0); error = Column(Text); result = Column(Text)
    worker_id = Column(Integer, ForeignKey("workers.id")); created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)
class SocialAccount(Base):
    __tablename__ = "social_accounts"
    id = Column(Integer, primary_key=True); platform = Column(String, nullable=False)
    account_name = Column(String); status = Column(String, default="sin_conectar")
    token_encrypted = Column(Text)  # nunca tokens en claro en respuestas de la API
class Publication(Base):
    __tablename__ = "publications"
    id = Column(Integer, primary_key=True); clip_id = Column(Integer, ForeignKey("clips.id"), index=True)
    platform = Column(String, nullable=False); title = Column(String); description = Column(Text)
    hashtags = Column(Text); status = Column(String, default="borrador", index=True)
    scheduled_at = Column(DateTime); external_id = Column(String); error = Column(Text)
    created_at = Column(DateTime, default=now)
class CalendarEntry(Base):
    __tablename__ = "calendar_entries"
    id = Column(Integer, primary_key=True); publication_id = Column(Integer, ForeignKey("publications.id"))
    when_at = Column(DateTime, nullable=False, index=True); note = Column(String, default="")
    # Etapa 5: programada|cancelada (cancelar NO borra la fila). En BD existentes
    # lo añade la migración 002; create_all lo crea solo en BD nuevas.
    status = Column(String, default="programada")
class Audit(Base):
    __tablename__ = "audit_log"
    id = Column(Integer, primary_key=True); user_email = Column(String)
    action = Column(String, nullable=False); detail = Column(Text, default="")
    created_at = Column(DateTime, default=now, index=True)
class Config(Base):
    __tablename__ = "config"
    id = Column(Integer, primary_key=True); key = Column(String, unique=True, nullable=False)
    value = Column(Text, default="")
Base.metadata.create_all(engine)

# ---------- Auth ----------
def hash_pw(pw: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 200_000)
    return f"pbkdf2${salt}${dk.hex()}"
def check_pw(pw: str, stored: str) -> bool:
    try:
        _, salt, hexdk = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 200_000)
        return hmac.compare_digest(dk.hex(), hexdk)
    except Exception:
        return False
def db():
    s = SessionLocal()
    try: yield s
    finally: s.close()
def current_user(authorization: str = Header(default=""), s=Depends(db)):
    tok = authorization.replace("Bearer ", "").strip()
    row = s.query(SessionToken).filter_by(token=tok).first() if tok else None
    if not row: raise HTTPException(401, "No autenticado")
    user = s.get(User, row.user_id)
    if not user: raise HTTPException(401, "No autenticado")
    return user
def audit(s, email, action, detail=""):
    s.add(Audit(user_email=email, action=action, detail=detail)); s.commit()

# ---------- Helpers Etapa 5/6 ----------
def _cfg_get(s, key, default=None):
    row = s.query(Config).filter_by(key=key).first()
    if not row: return default
    try: return json.loads(row.value)
    except Exception: return row.value
def _cfg_set(s, key, value):
    row = s.query(Config).filter_by(key=key).first()
    if row: row.value = json.dumps(value)
    else: s.add(Config(key=key, value=json.dumps(value)))
def _clamp_limit(limit):
    """Paginación Etapa 6: limit defecto 200, máximo 500."""
    try: limit = int(limit)
    except Exception: return 200
    if limit < 1: return 200
    return min(limit, 500)
def _clamp_offset(offset):
    try: return max(0, int(offset))
    except Exception: return 0

AUTOPILOT_PLATAFORMAS_DEFECTO = ["tiktok", "facebook", "instagram", "youtube"]
def _autopilot_config(s):
    activo = _cfg_get(s, "autopilot_activo", False)
    min_score = _cfg_get(s, "autopilot_min_score", 80)
    plataformas = _cfg_get(s, "autopilot_plataformas", None)
    tz = _cfg_get(s, "autopilot_timezone", None)
    return {
        "activo": bool(activo),
        "min_score": 80 if min_score is None else int(min_score),
        "plataformas": plataformas if isinstance(plataformas, list) and plataformas else list(AUTOPILOT_PLATAFORMAS_DEFECTO),
        "hora_inicio": _cfg_get(s, "autopilot_hora_inicio", None),
        "hora_fin": _cfg_get(s, "autopilot_hora_fin", None),
        "timezone": tz or "America/Santo_Domingo",
    }
def _parse_hhmm(v):
    if not v: return None
    try: return datetime.strptime(str(v), "%H:%M").time()
    except Exception: return None
def _en_ventana_horaria(cfg):
    """True si la hora actual (en la timezone configurada) cae en [hora_inicio, hora_fin].
    Sin ventana configurada siempre es True. Ventana nocturna (inicio > fin) cruza medianoche."""
    ini, fin = _parse_hhmm(cfg.get("hora_inicio")), _parse_hhmm(cfg.get("hora_fin"))
    if ini is None and fin is None: return True
    try: tz = ZoneInfo(cfg.get("timezone") or "America/Santo_Domingo")
    except Exception: tz = timezone.utc
    t = datetime.now(tz).time()
    if ini is not None and fin is not None:
        return ini <= t <= fin if ini <= fin else (t >= ini or t <= fin)
    if ini is not None: return t >= ini
    return t <= fin
def evaluar_piloto(s, evento):
    """Etapa 5 — Piloto Automático: si está activo, el score alcanza el mínimo y la hora
    cae en la ventana, encola UN Job «generar_clip» para el evento (anti-duplicados por
    event_id buscando en los payloads de jobs ya existentes). Devuelve el Job o None."""
    cfg = _autopilot_config(s)
    if not cfg["activo"]: return None
    if (evento.score or 0) < cfg["min_score"]: return None
    if not _en_ventana_horaria(cfg): return None
    for j in s.query(Job).filter_by(type="generar_clip").all():
        try: p = json.loads(j.payload or "{}")
        except Exception: continue
        if p.get("event_id") == evento.id: return None  # ya existe: anti-duplicados
    file_path = None
    if evento.project_id:
        proj = s.get(Project, evento.project_id)
        if proj: file_path = proj.file_path
    job = Job(type="generar_clip", payload=json.dumps({
        "event_id": evento.id, "project_id": evento.project_id, "session_id": evento.session_id,
        "start_s": evento.start_s, "end_s": evento.end_s, "mode": evento.mode, "file_path": file_path}))
    s.add(job); s.commit()
    audit(s, "sistema", "piloto_automatico",
          f"Job generar_clip #{job.id} creado para evento {evento.id} (score {evento.score})")
    return job

# Rate-limit de login (Etapa 6): en memoria, por (email, ip): 5 fallos en 60 s
# bloquean cualquier intento posterior (fallido o correcto) mientras dure la ventana.
# Un login correcto fuera del límite limpia el contador de esa clave.
_LOGIN_FALLOS = {}
LOGIN_MAX_FALLOS = 5
LOGIN_VENTANA_S = 60
def _login_bloqueado(clave):
    ahora = time.time()
    recientes = [t for t in _LOGIN_FALLOS.get(clave, []) if ahora - t < LOGIN_VENTANA_S]
    _LOGIN_FALLOS[clave] = recientes
    return len(recientes) >= LOGIN_MAX_FALLOS
def _login_registrar_fallo(clave):
    _LOGIN_FALLOS.setdefault(clave, []).append(time.time())

# ---------- Validación de fuentes (anti-SSRF) ----------
PLATAFORMAS_DIRECTAS = ("youtube.com", "youtu.be", "tiktok.com", "facebook.com", "instagram.com", "twitch.tv")
def _ip_privada(ip_str):
    try: ip = ipaddress.ip_address(ip_str)
    except ValueError: return True
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast
def validate_source(url: str):
    """Devuelve (allowed: bool, kind: str, explanation: str). Bloquea SSRF."""
    p = urlparse(url.strip())
    if p.scheme == "file":
        return False, "file", "Las rutas file:// no se aceptan por la API pública; importa el archivo por «Importar videos» o usa el trabajador local."
    if p.scheme not in ("http", "https", "rtmp", "rtmps", "srt"):
        return False, "desconocido", f"Esquema «{p.scheme or 'vacío'}» no compatible. Usa http(s), rtmp(s), srt o HLS (.m3u8)."
    host = (p.hostname or "").lower()
    if not host: return False, "desconocido", "La URL no tiene host válido."
    if host in ("localhost",) or host.endswith(".local") or host.endswith(".internal"):
        return False, "bloqueado", "Host local/interno bloqueado por seguridad (anti-SSRF)."
    kind = "hls" if ".m3u8" in p.path else ("rtmp" if p.scheme.startswith("rtmp") else ("srt" if p.scheme == "srt" else "http"))
    # Plataformas conocidas: explicación específica ANTES del chequeo DNS (son dominios públicos conocidos).
    if any(d in host for d in PLATAFORMAS_DIRECTAS):
        return False, kind, ("Esta plataforma no permite grabación directa desde enlaces públicos sin su API oficial "
                             "o sin ser el dueño del LIVE. Alternativas autorizadas: transmite TÚ el LIVE y envíalo por "
                             "RTMP/HLS propio, abre tu propio LIVE en OBS y conecta OBS como fuente, o importa un video "
                             "del que tengas permiso. No evadimos DRM ni controles de acceso.")
    try:
        ipaddress.ip_address(host)
        if _ip_privada(host):
            return False, "bloqueado", "IP privada, de loopback o reservada bloqueada por seguridad (anti-SSRF)."
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, None)
            for info in infos:
                if _ip_privada(info[4][0]):
                    return False, "bloqueado", f"El host «{host}» resuelve a una IP privada/interna; bloqueado (anti-SSRF)."
        except socket.gaierror:
            return False, "desconocido", f"No se pudo resolver el host «{host}»; no se puede verificar la fuente."
    return True, kind, "Fuente compatible: se puede intentar reproducir y grabar con FFmpeg si tienes permiso sobre el contenido."

# ---------- Esquemas ----------
class AuthIn(BaseModel): email: str; password: str
class SessionIn(BaseModel): title: str; mode: str = "general"; autopilot: bool = False; min_score: int = 80
class SourceIn(BaseModel): url: str
class JobIn(BaseModel): type: str; payload: dict = {}
class EventIn(BaseModel):
    session_id: int | None = None; project_id: int | None = None; type: str = "general"
    mode: str = "general"; start_s: float = 0; end_s: float = 0; score: int = 0
    breakdown: dict = {}; transcript: str | None = None
class ClipIn(BaseModel):
    event_id: int | None = None; project_id: int | None = None; session_id: int | None = None
    file_path: str | None = None; format: str = "9:16"; duration_s: float | None = None
    score: int = 0; title: str | None = None; subtitles: bool = False
class PubIn(BaseModel):
    clip_id: int; platform: str; title: str | None = None; description: str | None = None
    hashtags: str | None = None; scheduled_at: str | None = None
class TitlesIn(BaseModel): type: str = "general"; mode: str = "general"; score: int = 0; transcript: str | None = None
class WorkerIn(BaseModel): name: str; info: dict = {}
class ConfigIn(BaseModel): values: dict
class EditIn(BaseModel):
    trim_start_s: float = 0; trim_end_s: float | None = None
    formato: str = "9:16"; offset_x: float = 0; offset_y: float = 0
    zoom: float = 1.0; volumen: float = 1.0
    texto: str | None = None; texto_activo: bool = False
    logo_path: str | None = None; subtitulos: bool = False
    transcript: str | None = None
class ThumbIn(BaseModel): segundo: float = 1.0
class AutopilotIn(BaseModel):
    activo: bool | None = None; min_score: int | None = None
    plataformas: list | None = None; hora_inicio: str | None = None
    hora_fin: str | None = None; timezone: str | None = None
class CalendarIn(BaseModel): publication_id: int; when_at: str; note: str = ""
class CalendarUpdateIn(BaseModel): when_at: str | None = None; note: str | None = None

app = FastAPI(title="JDM Viral Clips AI", version="1.0-etapa1")
def ser(obj, fields):
    out = {}
    for f in fields:
        v = getattr(obj, f)
        out[f] = v.isoformat() if isinstance(v, datetime) else v
    return out

@app.get("/health")
def health(): return {"ok": True, "servicio": "jdm-viral-clips-ai", "version": "1.0-etapa1", "hora": now().isoformat()}

@app.post("/auth/register")
def register(data: AuthIn, s=Depends(db)):
    if len(data.password) < 8: raise HTTPException(400, "La contraseña debe tener al menos 8 caracteres")
    if s.query(User).filter_by(email=data.email.lower()).first(): raise HTTPException(400, "Ese correo ya está registrado")
    role = "admin" if s.query(User).count() == 0 else "user"
    u = User(email=data.email.lower(), password_hash=hash_pw(data.password), role=role)
    s.add(u); s.commit(); audit(s, u.email, "registro", f"rol={role}")
    return {"id": u.id, "email": u.email, "role": role}
@app.post("/auth/login")
def login(data: AuthIn, request: Request, s=Depends(db)):
    email = data.email.lower()
    ip = request.client.host if request.client else "desconocida"
    clave = (email, ip)
    if _login_bloqueado(clave):
        raise HTTPException(429, "Demasiados intentos de inicio de sesión fallidos. Espera 60 segundos e inténtalo de nuevo.")
    u = s.query(User).filter_by(email=email).first()
    if not u or not check_pw(data.password, u.password_hash):
        _login_registrar_fallo(clave)
        raise HTTPException(401, "Correo o contraseña incorrectos")
    _LOGIN_FALLOS.pop(clave, None)  # login correcto fuera del límite: limpia la ventana
    tok = secrets.token_urlsafe(32); s.add(SessionToken(user_id=u.id, token=tok)); s.commit()
    audit(s, u.email, "login")
    return {"token": tok, "email": u.email, "role": u.role}
@app.get("/auth/me")
def me(user=Depends(current_user)): return {"email": user.email, "role": user.role}

# Trabajadores
@app.post("/workers/register")
def worker_register(data: WorkerIn, user=Depends(current_user), s=Depends(db)):
    tok = secrets.token_urlsafe(32)
    w = Worker(name=data.name, token_hash=hashlib.sha256(tok.encode()).hexdigest(), status="conectado",
               last_seen=now(), info=json.dumps(data.info))
    s.add(w); s.commit()
    return {"id": w.id, "worker_token": tok, "nota": "Guarda este token en el trabajador; no se vuelve a mostrar."}
def worker_auth(x_worker_token: str = Header(default=""), s=Depends(db)):
    h = hashlib.sha256(x_worker_token.encode()).hexdigest() if x_worker_token else ""
    w = s.query(Worker).filter_by(token_hash=h).first()
    if not w: raise HTTPException(401, "Token de trabajador inválido")
    return w
@app.post("/workers/heartbeat")
def heartbeat(w=Depends(worker_auth), s=Depends(db)):
    row = s.get(Worker, w.id); row.status = "conectado"; row.last_seen = now(); s.commit()
    return {"ok": True, "trabajos_pendientes": s.query(Job).filter_by(status="pendiente").count()}
@app.get("/workers")
def workers(user=Depends(current_user), s=Depends(db)):
    return [ser(w, ["id", "name", "status", "last_seen"]) for w in s.query(Worker).all()]

# Sesiones LIVE y fuentes
@app.post("/sessions")
def create_session(data: SessionIn, user=Depends(current_user), s=Depends(db)):
    row = LiveSession(title=data.title, mode=data.mode, autopilot=data.autopilot, min_score=data.min_score, user_id=user.id)
    s.add(row); s.commit(); audit(s, user.email, "crear_sesion", data.title)
    return ser(row, ["id", "title", "mode", "status", "autopilot", "min_score", "created_at"])
@app.get("/sessions")
def list_sessions(limit: int = 200, offset: int = 0, user=Depends(current_user), s=Depends(db)):
    q = s.query(LiveSession).offset(_clamp_offset(offset)).limit(_clamp_limit(limit))
    return [ser(r, ["id", "title", "mode", "status", "autopilot", "min_score", "created_at"]) for r in q]
@app.post("/sessions/{sid}/sources")
def add_source(sid: int, data: SourceIn, user=Depends(current_user), s=Depends(db)):
    if not s.get(LiveSession, sid): raise HTTPException(404, "Sesión no encontrada")
    allowed, kind, explanation = validate_source(data.url)
    row = Source(session_id=sid, url=data.url, kind=kind, allowed=allowed, explanation=explanation)
    s.add(row); s.commit()
    return {"id": row.id, "allowed": allowed, "kind": kind, "explanation": explanation}
@app.post("/sessions/{sid}/validate")
def validate_only(sid: int, data: SourceIn, user=Depends(current_user)):
    allowed, kind, explanation = validate_source(data.url)
    return {"allowed": allowed, "kind": kind, "explanation": explanation}
@app.post("/sessions/{sid}/start")
def start_session(sid: int, user=Depends(current_user), s=Depends(db)):
    row = s.get(LiveSession, sid)
    if not row: raise HTTPException(404, "Sesión no encontrada")
    # Etapa 6: una sola grabación activa a la vez (hasta demostrar hardware para más).
    otra = s.query(LiveSession).filter(LiveSession.status == "grabando", LiveSession.id != sid).first()
    if otra: raise HTTPException(409, f"Ya hay otra sesión grabando («{otra.title}», id {otra.id}). Detenla antes de iniciar esta.")
    src = s.query(Source).filter_by(session_id=sid, allowed=True).first()
    if not src: raise HTTPException(400, "La sesión no tiene una fuente AUTORIZADA y compatible. Valida primero una fuente RTMP/HLS propia.")
    row.status = "grabando"; s.commit()
    job = Job(type="iniciar_grabacion", payload=json.dumps({"session_id": sid, "url": src.url, "mode": row.mode}))
    s.add(job); s.commit(); audit(s, user.email, "iniciar_grabacion", row.title)
    return {"ok": True, "status": row.status, "job_id": job.id}
@app.post("/sessions/{sid}/stop")
def stop_session(sid: int, user=Depends(current_user), s=Depends(db)):
    row = s.get(LiveSession, sid)
    if not row: raise HTTPException(404, "Sesión no encontrada")
    row.status = "detenida"; s.commit()
    job = Job(type="detener_grabacion", payload=json.dumps({"session_id": sid})); s.add(job); s.commit()
    return {"ok": True, "status": row.status, "job_id": job.id}

# Trabajos (cola persistente)
@app.post("/jobs")
def create_job(data: JobIn, user=Depends(current_user), s=Depends(db)):
    j = Job(type=data.type, payload=json.dumps(data.payload)); s.add(j); s.commit()
    return ser(j, ["id", "type", "status", "attempts", "created_at"])
@app.get("/jobs")
def list_jobs(limit: int = 200, offset: int = 0, user=Depends(current_user), s=Depends(db)):
    q = s.query(Job).order_by(Job.id.desc()).offset(_clamp_offset(offset)).limit(_clamp_limit(limit))
    return [ser(j, ["id", "type", "status", "attempts", "error", "created_at"]) for j in q]
@app.post("/jobs/claim")
def claim_job(w=Depends(worker_auth), s=Depends(db)):
    j = s.query(Job).filter_by(status="pendiente").order_by(Job.id).first()
    if not j: return {"job": None}
    j.status = "procesando"; j.attempts += 1; j.worker_id = w.id; s.commit()
    return {"job": {"id": j.id, "type": j.type, "payload": json.loads(j.payload or "{}")}}
@app.post("/jobs/{jid}/complete")
def complete_job(jid: int, body: dict, w=Depends(worker_auth), s=Depends(db)):
    j = s.get(Job, jid)
    if not j: raise HTTPException(404, "Trabajo no encontrado")
    res = body.get("result", {})
    j.status = "completado"; j.result = json.dumps(res); j.error = None; s.commit()
    # El trabajador puede reportar eventos y clips reales: se persisten tal cual.
    eventos_persistidos = []
    for ev in res.get("events", []):
        e = Event(session_id=ev.get("session_id"), project_id=ev.get("project_id") or _payload_project(j),
                  type=ev.get("type", "general"), mode=ev.get("mode", "general"),
                  start_s=ev.get("start_s", 0), end_s=ev.get("end_s", 0), score=int(ev.get("score", 0)),
                  breakdown=json.dumps(ev.get("breakdown", {})), transcript=ev.get("transcript"))
        s.add(e); s.commit(); ev["_id"] = e.id; eventos_persistidos.append(e)
    # Etapa 5: cada evento persistido por un trabajador también pasa por el piloto.
    for e in eventos_persistidos:
        try: evaluar_piloto(s, e)
        except Exception: pass  # el piloto nunca debe tumbar la compleción del trabajo
    # Etapa 3: si el job es «editar_clip», el clip resultante hereda
    # event_id/session_id/project_id del clip origen cuando el resultado no los trae.
    origen = None
    if j.type == "editar_clip":
        try: _ocid = json.loads(j.payload or "{}").get("clip_id")
        except Exception: _ocid = None
        if _ocid: origen = s.get(Clip, _ocid)
    clips_persistidos = []
    for cl in res.get("clips", []):
        c = Clip(event_id=cl.get("event_id") or (origen.event_id if origen else None),
                 project_id=cl.get("project_id") or _payload_project(j) or (origen.project_id if origen else None),
                 session_id=cl.get("session_id") or (origen.session_id if origen else None),
                 file_path=cl.get("file_path"), thumb_path=cl.get("thumb_path"),
                 format=cl.get("format", "9:16"),
                 duration_s=cl.get("duration_s"), score=int(cl.get("score", 0)), title=cl.get("title"),
                 titles_json=json.dumps(cl.get("titles", {})), subtitles=bool(cl.get("subtitles", False)))
        s.add(c); s.commit(); clips_persistidos.append(c)
    # Etapa 5: clip generado por el piloto → publicaciones en borrador/requiere_conexion.
    if j.type == "generar_clip" and clips_persistidos:
        _piloto_publicaciones(s, clips_persistidos)
    return {"ok": True}
def _payload_project(job):
    try: return json.loads(job.payload or "{}").get("project_id")
    except Exception: return None
def _piloto_publicaciones(s, clips):
    """Etapa 5: con el piloto activo, por cada clip de un job «generar_clip» crea una
    Publication por plataforma configurada, con títulos generados. NUNCA «publicado»:
    «borrador» si hay cuenta conectada, «requiere_conexion» si no la hay."""
    cfg = _autopilot_config(s)
    if not cfg["activo"]: return
    for clip in clips:
        ev = s.get(Event, clip.event_id) if clip.event_id else None
        tipo = ev.type if ev else "general"
        modo = ev.mode if ev else "general"
        tit = generar_titulos(tipo, modo, clip.score or 0, ev.transcript if ev else None)
        for plat in cfg["plataformas"]:
            if plat not in social_lib.PLATAFORMAS: continue
            if s.query(Publication).filter_by(clip_id=clip.id, platform=plat).first(): continue
            acc = s.query(SocialAccount).filter_by(platform=plat, status="conectado").first()
            estado = "borrador" if acc else "requiere_conexion"
            pub = Publication(clip_id=clip.id, platform=plat, title=tit["titulos"][0],
                              description=tit["descripciones"].get(plat),
                              hashtags=" ".join(tit["hashtags"]), status=estado)
            s.add(pub); s.commit()
            audit(s, "sistema", "piloto_automatico",
                  f"Publicación {estado} #{pub.id} para clip {clip.id} en {plat} (sin publicar: falta aprobación/conexión real)")
@app.post("/jobs/{jid}/fail")
def fail_job(jid: int, body: dict, w=Depends(worker_auth), s=Depends(db)):
    j = s.get(Job, jid)
    if not j: raise HTTPException(404, "Trabajo no encontrado")
    j.error = str(body.get("error", "error"))[:2000]
    j.status = "pendiente" if j.attempts < 3 else "fallido"; s.commit()
    return {"ok": True, "status": j.status}

# Eventos y clips
@app.post("/events")
def create_event(data: EventIn, user=Depends(current_user), s=Depends(db)):
    e = Event(**data.model_dump(exclude={"breakdown"}), breakdown=json.dumps(data.breakdown))
    s.add(e); s.commit()
    evaluar_piloto(s, e)  # Etapa 5: el piloto decide si encola «generar_clip»
    return ser(e, ["id", "type", "mode", "start_s", "end_s", "score", "status"])
@app.get("/events")
def list_events(limit: int = 200, offset: int = 0, user=Depends(current_user), s=Depends(db)):
    out = []
    for e in s.query(Event).order_by(Event.score.desc()).offset(_clamp_offset(offset)).limit(_clamp_limit(limit)):
        d = ser(e, ["id", "session_id", "project_id", "type", "mode", "start_s", "end_s", "score", "status", "created_at"])
        d["breakdown"] = json.loads(e.breakdown or "{}"); out.append(d)
    return out
@app.post("/clips")
def create_clip(data: ClipIn, user=Depends(current_user), s=Depends(db)):
    c = Clip(**data.model_dump()); s.add(c); s.commit()
    return ser(c, ["id", "file_path", "format", "duration_s", "score", "title", "subtitles", "status"])
@app.get("/clips")
def list_clips(limit: int = 200, offset: int = 0, user=Depends(current_user), s=Depends(db)):
    q = s.query(Clip).order_by(Clip.id.desc()).offset(_clamp_offset(offset)).limit(_clamp_limit(limit))
    return [ser(c, ["id", "event_id", "project_id", "session_id", "file_path", "thumb_path", "format", "duration_s", "score", "title", "subtitles", "status", "created_at"]) for c in q]
@app.get("/clips/{cid}/download")
def download_clip(cid: int, user=Depends(current_user), s=Depends(db)):
    c = s.get(Clip, cid)
    if not c or not c.file_path or not Path(c.file_path).exists(): raise HTTPException(404, "Archivo del clip no disponible en este servidor (vive en el trabajador Windows)")
    return FileResponse(c.file_path, media_type="video/mp4", filename=Path(c.file_path).name)

# ---------- Editor (Etapa 3, lado API) ----------
# La API NO edita video: valida y encola el trabajo; lo exporta el trabajador
# (Windows/Linux) y el clip resultante entra por el complete existente (result.clips).
@app.post("/clips/{cid}/edit")
def edit_clip(cid: int, data: EditIn, user=Depends(current_user), s=Depends(db)):
    c = s.get(Clip, cid)
    if not c: raise HTTPException(404, "Clip no encontrado")
    if data.formato not in ("9:16", "16:9", "1:1"): raise HTTPException(400, "Formato no válido: usa 9:16, 16:9 o 1:1")
    if data.trim_start_s < 0: raise HTTPException(400, "trim_start_s no puede ser negativo")
    if data.trim_end_s is not None:
        if data.trim_end_s <= data.trim_start_s: raise HTTPException(400, "trim_end_s debe ser mayor que trim_start_s")
        if c.duration_s and data.trim_end_s > c.duration_s: raise HTTPException(400, "trim_end_s supera la duración del clip")
    if not (-1 <= data.offset_x <= 1): raise HTTPException(400, "offset_x debe estar entre -1 y 1")
    if not (-1 <= data.offset_y <= 1): raise HTTPException(400, "offset_y debe estar entre -1 y 1")
    if data.zoom < 1: raise HTTPException(400, "zoom debe ser >= 1")
    if data.volumen <= 0: raise HTTPException(400, "volumen debe ser un factor > 0")
    params = data.model_dump()
    job = Job(type="editar_clip", payload=json.dumps({
        "clip_id": cid, "file_path": c.file_path, "params": params, "title": c.title}))
    s.add(job); s.commit()
    audit(s, user.email, "editar_clip", f"clip {cid} -> job {job.id} formato {data.formato}")
    return {"job_id": job.id, "status": job.status,
            "nota": "Edición encolada: la exporta el trabajador (Windows/Linux) al reclamar el trabajo; "
                    "el clip editado aparecerá en /clips cuando el trabajador lo complete. La API no edita video."}
@app.post("/clips/{cid}/logo")
def upload_logo(cid: int, file: UploadFile = File(...), user=Depends(current_user), s=Depends(db)):
    c = s.get(Clip, cid)
    if not c: raise HTTPException(404, "Clip no encontrado")
    ext = Path(file.filename or "").suffix.lower()
    if ext not in (".png", ".jpg", ".jpeg"): raise HTTPException(400, "Logo no admitido: sube PNG o JPG")
    data = file.file.read(LOGO_MAX_BYTES + 1)
    if len(data) > LOGO_MAX_BYTES: raise HTTPException(413, "Logo mayor de 5 MB")
    dest = LOGO_DIR / f"clip_{cid}_{int(time.time())}{ext}"
    dest.write_bytes(data)
    audit(s, user.email, "subir_logo", f"clip {cid}: {dest.name}")
    return {"ok": True, "logo_path": str(dest),
            "nota": "Logo guardado en el servidor. Úsalo como logo_path al encolar la edición del clip."}
@app.post("/clips/{cid}/thumbnail")
def clip_thumbnail(cid: int, data: ThumbIn, user=Depends(current_user), s=Depends(db)):
    # Elección documentada: job de tipo propio «miniatura» (no «editar_clip»), con
    # params {solo_miniatura: true, segundo}; lo genera el trabajador y el resultado
    # (thumb_path) entra por el complete existente.
    c = s.get(Clip, cid)
    if not c: raise HTTPException(404, "Clip no encontrado")
    if data.segundo < 0: raise HTTPException(400, "segundo no puede ser negativo")
    if c.duration_s and data.segundo > c.duration_s: raise HTTPException(400, "segundo supera la duración del clip")
    job = Job(type="miniatura", payload=json.dumps({
        "clip_id": cid, "file_path": c.file_path,
        "params": {"solo_miniatura": True, "segundo": data.segundo}, "title": c.title}))
    s.add(job); s.commit()
    audit(s, user.email, "crear_miniatura", f"clip {cid} segundo {data.segundo} -> job {job.id}")
    return {"job_id": job.id, "status": job.status,
            "nota": "Miniatura encolada como job «miniatura»: la genera el trabajador desde el segundo "
                    "indicado; sin trabajador conectado queda pendiente."}

# Proyectos / importación / análisis
@app.post("/projects/import")
def import_video(file: UploadFile = File(...), user=Depends(current_user), s=Depends(db)):
    if not file.filename.lower().endswith((".mp4", ".mov", ".mkv")): raise HTTPException(400, "Formato no admitido: sube MP4/MOV/MKV")
    dest = UPLOAD_DIR / f"{int(time.time())}_{Path(file.filename).name}"
    # Etapa 6: límite real leído del entorno EN CADA PETICIÓN (JDM_MAX_UPLOAD_MB,
    # defecto 2048 MB), escribiendo en chunks de 1 MB y abortando con 413 en cuanto
    # se supera, borrando el archivo parcial.
    try: max_bytes = int(float(os.environ.get("JDM_MAX_UPLOAD_MB", "2048")) * 1024 * 1024)
    except ValueError: max_bytes = 2048 * 1024 * 1024
    size = 0
    with open(dest, "wb") as out:
        while True:
            chunk = file.file.read(1024 * 1024)
            if not chunk: break
            size += len(chunk)
            if size > max_bytes:
                out.close(); dest.unlink(missing_ok=True)
                raise HTTPException(413, f"Archivo mayor que el límite permitido ({max_bytes // (1024 * 1024)} MB, JDM_MAX_UPLOAD_MB)")
            out.write(chunk)
    dur = None
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(dest)],
                           capture_output=True, text=True, timeout=60)
        dur = float(r.stdout.strip())
    except Exception: pass
    p = Project(name=file.filename, file_path=str(dest), duration_s=dur); s.add(p); s.commit()
    audit(s, user.email, "importar_video", file.filename)
    return {"id": p.id, "name": p.name, "duration_s": dur, "file_path": str(dest)}
@app.get("/projects")
def list_projects(limit: int = 200, offset: int = 0, user=Depends(current_user), s=Depends(db)):
    q = s.query(Project).offset(_clamp_offset(offset)).limit(_clamp_limit(limit))
    return [ser(p, ["id", "name", "duration_s", "status", "created_at"]) for p in q]
@app.post("/projects/{pid}/analyze")
def analyze(pid: int, user=Depends(current_user), s=Depends(db)):
    p = s.get(Project, pid)
    if not p: raise HTTPException(404, "Proyecto no encontrado")
    j = Job(type="analizar_video", payload=json.dumps({"project_id": pid, "file_path": p.file_path}))
    s.add(j); s.commit()
    return {"ok": True, "job_id": j.id, "nota": "Trabajo encolado: lo procesa el trabajador (Windows/Linux). Sin trabajador conectado, queda pendiente y así se muestra."}

# Títulos
@app.post("/titles")
def titles(data: TitlesIn, user=Depends(current_user)):
    return generar_titulos(data.type, data.mode, data.score, data.transcript)

# Redes sociales y publicaciones (SOLO borradores con aprobación en Etapa 1)
@app.get("/social")
def social(user=Depends(current_user), s=Depends(db)):
    out = []
    for plat in ("tiktok", "facebook", "instagram", "youtube"):
        acc = s.query(SocialAccount).filter_by(platform=plat).first()
        configurado = social_lib.oauth_configurado(plat)
        status = acc.status if acc else "sin_conectar"
        if status == "conectado":
            nota = ""
        elif not configurado:
            nota = "Requiere configuración: faltan las credenciales OAuth de la app de la plataforma"
        elif not acc:
            nota = "Credenciales OAuth configuradas: vincula la cuenta en /auth/" + plat + "/conectar."
        else:
            nota = "Conexión OAuth pendiente: requiere crear la app oficial de la plataforma y su aprobación."
        out.append({"platform": plat, "status": status,
                    "account": acc.account_name if acc else None,
                    "oauth_configurado": configurado, "nota": nota})
    return out

# OAuth real (Etapa 4): state aleatorio guardado en Config y validado en el callback.
@app.get("/auth/{plataforma}/conectar")
def social_conectar(plataforma: str, user=Depends(current_user), s=Depends(db)):
    if plataforma not in social_lib.PLATAFORMAS: raise HTTPException(404, "Plataforma no válida")
    if not social_lib.oauth_configurado(plataforma):
        raise HTTPException(400, "Esta plataforma requiere configuración: faltan las credenciales OAuth "
                                 "de la app oficial en las variables de entorno del servidor.")
    state = secrets.token_urlsafe(24)
    _cfg_set(s, f"oauth_state_{plataforma}", state); s.commit()
    return RedirectResponse(social_lib.authorize_url(plataforma, state), status_code=307)
@app.get("/auth/{plataforma}/callback")
def social_callback(plataforma: str, code: str | None = None, state: str | None = None, s=Depends(db)):
    if plataforma not in social_lib.PLATAFORMAS: raise HTTPException(404, "Plataforma no válida")
    guardado = _cfg_get(s, f"oauth_state_{plataforma}", None)
    if not state or not guardado or not hmac.compare_digest(str(state), str(guardado)):
        raise HTTPException(400, "Parámetro state inválido o expirado: la conexión no se completó.")
    _cfg_set(s, f"oauth_state_{plataforma}", ""); s.commit()  # state de un solo uso
    if not code: raise HTTPException(400, "La plataforma no devolvió código de autorización.")
    if not social_lib.oauth_configurado(plataforma):
        raise HTTPException(400, "Esta plataforma requiere configuración: faltan las credenciales OAuth de la app oficial.")
    token, error = social_lib.exchange_code(plataforma, code)
    if not token:
        return JSONResponse({"ok": False, "platform": plataforma, "status": "sin_conectar",
                             "motivo": f"El intercambio del código con la plataforma falló: {error}. La cuenta NO queda conectada."},
                            status_code=502)
    if not social_lib.cifrado_disponible():
        # Sin clave Fernet NO se almacena el token: la cuenta no queda conectada.
        return {"ok": False, "platform": plataforma, "status": "sin_conectar",
                "motivo": "La plataforma devolvió un token, pero NO se almacenó: falta la variable "
                          "JDM_ENCRYPTION_KEY (Fernet) para guardarlo cifrado. La cuenta NO queda conectada."}
    cifrado = social_lib.cifrar_token(token)
    acc = s.query(SocialAccount).filter_by(platform=plataforma).first()
    if acc: acc.status = "conectado"; acc.token_encrypted = cifrado
    else:
        acc = SocialAccount(platform=plataforma, status="conectado", token_encrypted=cifrado); s.add(acc)
    s.commit()
    audit(s, "sistema", "conectar_red_social", f"{plataforma} conectada por OAuth (token cifrado)")
    return {"ok": True, "platform": plataforma, "status": "conectado"}
@app.post("/publications")
def create_pub(data: PubIn, user=Depends(current_user), s=Depends(db)):
    clip = s.get(Clip, data.clip_id)
    if not clip: raise HTTPException(404, "Clip no encontrado")
    if data.platform not in ("tiktok", "facebook", "instagram", "youtube"): raise HTTPException(400, "Plataforma no válida")
    acc = s.query(SocialAccount).filter_by(platform=data.platform, status="conectado").first()
    sched = None
    if data.scheduled_at:
        try: sched = datetime.fromisoformat(data.scheduled_at)
        except ValueError: raise HTTPException(400, "Fecha inválida (ISO)")
    pub = Publication(clip_id=clip.id, platform=data.platform, title=data.title, description=data.description,
                      hashtags=data.hashtags, scheduled_at=sched,
                      status="requiere_conexion" if not acc else "borrador")
    s.add(pub); s.commit()
    if sched: s.add(CalendarEntry(publication_id=pub.id, when_at=sched, note=f"{data.platform}: {data.title or ''}")); s.commit()
    audit(s, user.email, "crear_borrador_publicacion", f"{data.platform} clip {clip.id}")
    return {"id": pub.id, "status": pub.status,
            "nota": "Borrador guardado. Nada se publica sin tu aprobación y sin conexión OAuth verificada de la plataforma."}
@app.get("/publications")
def list_pubs(limit: int = 200, offset: int = 0, user=Depends(current_user), s=Depends(db)):
    q = s.query(Publication).order_by(Publication.id.desc()).offset(_clamp_offset(offset)).limit(_clamp_limit(limit))
    return [ser(p, ["id", "clip_id", "platform", "title", "status", "scheduled_at", "created_at"]) for p in q]
@app.post("/publications/{pid}/publish")
def publish_pub(pid: int, user=Depends(current_user), s=Depends(db)):
    """Etapa 4: intenta publicar SOLO con cuenta «conectado» y token descifrable.
    El status NUNCA pasa a «publicado» sin confirmación real de la API oficial.
    Cada intento queda auditado."""
    p = s.get(Publication, pid)
    if not p: raise HTTPException(404, "Publicación no encontrada")
    acc = s.query(SocialAccount).filter_by(platform=p.platform, status="conectado").first()
    if not acc:
        existe = s.query(SocialAccount).filter_by(platform=p.platform).first()
        estado = existe.status if existe else "sin_conectar"
        motivo = (f"No se publicó: la cuenta de {p.platform} no está conectada (estado actual: {estado}). "
                  "Conéctala por OAuth para publicar de verdad.")
        audit(s, user.email, "intentar_publicacion", f"publicación {pid} en {p.platform}: {motivo}")
        return {"ok": False, "status": p.status, "motivo": motivo}
    token = social_lib.descifrar_token(acc.token_encrypted)
    if not token:
        motivo = (f"No se publicó: el token guardado de {p.platform} no se pudo descifrar "
                  "(falta o cambió JDM_ENCRYPTION_KEY). Vuelve a conectar la cuenta.")
        audit(s, user.email, "intentar_publicacion", f"publicación {pid} en {p.platform}: {motivo}")
        return {"ok": False, "status": p.status, "motivo": motivo}
    clip = s.get(Clip, p.clip_id)
    adapter = social_lib.get_adapter(p.platform)
    res = adapter.publish(token, clip.file_path if clip else None, p.title, p.description) if adapter \
        else {"ok": False, "motivo": "Plataforma sin adaptador."}
    if res.get("ok"):
        p.status = "publicado"; p.external_id = res.get("external_id"); p.error = None; s.commit()
        audit(s, user.email, "publicar_red_social", f"publicación {pid} en {p.platform}: external_id {p.external_id}")
        return {"ok": True, "status": p.status, "external_id": p.external_id}
    motivo = res.get("motivo", "La plataforma no confirmó la publicación.")
    p.error = motivo; s.commit()
    audit(s, user.email, "intentar_publicacion", f"publicación {pid} en {p.platform}: {motivo}")
    return {"ok": False, "status": p.status, "motivo": motivo}
@app.post("/publications/{pid}/approve")
def approve_pub(pid: int, user=Depends(current_user), s=Depends(db)):
    p = s.get(Publication, pid)
    if not p: raise HTTPException(404, "Publicación no encontrada")
    if p.status == "requiere_conexion":
        return {"ok": False, "status": p.status, "nota": "Aprobada por ti en intención, pero NO se puede publicar: la cuenta de la plataforma no está conectada por OAuth. No se marca como publicada."}
    p.status = "aprobado"; s.commit()
    return {"ok": True, "status": p.status, "nota": "Aprobado. La publicación real requiere la integración OAuth de la Etapa 4."}

# Calendario completo (Etapa 5): crear, reprogramar y cancelar (sin borrar la fila)
@app.post("/calendar")
def calendar_create(data: CalendarIn, user=Depends(current_user), s=Depends(db)):
    pub = s.get(Publication, data.publication_id)
    if not pub: raise HTTPException(404, "Publicación no encontrada")
    try: when = datetime.fromisoformat(data.when_at)
    except ValueError: raise HTTPException(400, "Fecha inválida (usa ISO, ej. 2026-12-01T15:00:00)")
    entry = CalendarEntry(publication_id=pub.id, when_at=when, note=data.note, status="programada")
    s.add(entry); pub.scheduled_at = when; s.commit()
    audit(s, user.email, "crear_calendario", f"publicación {pub.id} programada para {data.when_at}")
    return ser(entry, ["id", "publication_id", "when_at", "note", "status"])
@app.put("/calendar/{eid}")
def calendar_update(eid: int, data: CalendarUpdateIn, user=Depends(current_user), s=Depends(db)):
    entry = s.get(CalendarEntry, eid)
    if not entry: raise HTTPException(404, "Entrada de calendario no encontrada")
    if data.when_at is not None:
        try: entry.when_at = datetime.fromisoformat(data.when_at)
        except ValueError: raise HTTPException(400, "Fecha inválida (usa ISO)")
        entry.status = "programada"  # reprogramar reactiva una entrada cancelada
        pub = s.get(Publication, entry.publication_id)
        if pub: pub.scheduled_at = entry.when_at
    if data.note is not None: entry.note = data.note
    s.commit()
    audit(s, user.email, "reprogramar_calendario", f"entrada {eid} -> {entry.when_at.isoformat()}")
    return ser(entry, ["id", "publication_id", "when_at", "note", "status"])
@app.delete("/calendar/{eid}")
def calendar_delete(eid: int, user=Depends(current_user), s=Depends(db)):
    entry = s.get(CalendarEntry, eid)
    if not entry: raise HTTPException(404, "Entrada de calendario no encontrada")
    entry.status = "cancelada"; s.commit()  # cancelar NO borra: queda el historial
    audit(s, user.email, "cancelar_calendario", f"entrada {eid} (publicación {entry.publication_id})")
    return {"ok": True, "id": eid, "status": entry.status}
@app.get("/calendar")
def calendar_list(limit: int = 200, offset: int = 0, user=Depends(current_user), s=Depends(db)):
    q = s.query(CalendarEntry).order_by(CalendarEntry.when_at).offset(_clamp_offset(offset)).limit(_clamp_limit(limit))
    return [ser(c, ["id", "publication_id", "when_at", "note", "status"]) for c in q]
@app.get("/history")
def history(limit: int = 200, offset: int = 0, user=Depends(current_user), s=Depends(db)):
    q = s.query(Audit).order_by(Audit.id.desc()).offset(_clamp_offset(offset)).limit(_clamp_limit(limit))
    return [ser(a, ["id", "user_email", "action", "detail", "created_at"]) for a in q]

# Piloto Automático (Etapa 5): configuración en la tabla Config (claves autopilot_*)
@app.get("/autopilot")
def get_autopilot(user=Depends(current_user), s=Depends(db)):
    return _autopilot_config(s)
@app.put("/autopilot")
def put_autopilot(data: AutopilotIn, user=Depends(current_user), s=Depends(db)):
    if user.role != "admin": raise HTTPException(403, "Solo el administrador cambia el piloto automático")
    cfg = _autopilot_config(s)
    if data.activo is not None: cfg["activo"] = bool(data.activo)
    if data.min_score is not None:
        if not (0 <= data.min_score <= 100): raise HTTPException(400, "min_score debe estar entre 0 y 100")
        cfg["min_score"] = data.min_score
    if data.plataformas is not None:
        invalidas = [p for p in data.plataformas if p not in social_lib.PLATAFORMAS]
        if invalidas: raise HTTPException(400, f"Plataformas no válidas: {', '.join(invalidas)}")
        cfg["plataformas"] = list(data.plataformas)
    if data.hora_inicio is not None:
        if data.hora_inicio and _parse_hhmm(data.hora_inicio) is None: raise HTTPException(400, "hora_inicio debe ser HH:MM")
        cfg["hora_inicio"] = data.hora_inicio or None
    if data.hora_fin is not None:
        if data.hora_fin and _parse_hhmm(data.hora_fin) is None: raise HTTPException(400, "hora_fin debe ser HH:MM")
        cfg["hora_fin"] = data.hora_fin or None
    if data.timezone is not None:
        try: ZoneInfo(data.timezone)
        except Exception: raise HTTPException(400, "timezone no válida (ej. America/Santo_Domingo)")
        cfg["timezone"] = data.timezone
    _cfg_set(s, "autopilot_activo", cfg["activo"]); _cfg_set(s, "autopilot_min_score", cfg["min_score"])
    _cfg_set(s, "autopilot_plataformas", cfg["plataformas"]); _cfg_set(s, "autopilot_hora_inicio", cfg["hora_inicio"])
    _cfg_set(s, "autopilot_hora_fin", cfg["hora_fin"]); _cfg_set(s, "autopilot_timezone", cfg["timezone"])
    s.commit()
    audit(s, user.email, "actualizar_autopilot", json.dumps(cfg))
    return cfg
@app.get("/config")
def get_config(user=Depends(current_user), s=Depends(db)):
    base = {"buffer_min": 5, "pre_s": 20, "post_s": 15, "min_score_alert": 80,
            "timezone": "America/Santo_Domingo", "subtitles_default": False}
    for row in s.query(Config).all():
        try: base[row.key] = json.loads(row.value)
        except Exception: base[row.key] = row.value
    return base
@app.put("/config")
def put_config(data: ConfigIn, user=Depends(current_user), s=Depends(db)):
    if user.role != "admin": raise HTTPException(403, "Solo el administrador cambia la configuración")
    for k, v in data.values.items():
        row = s.query(Config).filter_by(key=k).first()
        if row: row.value = json.dumps(v)
        else: s.add(Config(key=k, value=json.dumps(v)))
    s.commit(); return {"ok": True}
@app.get("/stats")
def stats(user=Depends(current_user), s=Depends(db)):
    eventos = s.query(Event).all(); clips = s.query(Clip).all(); pubs = s.query(Publication).all()
    def _conteo(items, attr):
        out = {}
        for it in items:
            k = getattr(it, attr)
            out[k] = out.get(k, 0) + 1
        return out
    umbral = _cfg_get(s, "min_score_alert", None)
    umbral = 80 if umbral is None else int(umbral)
    return {"sesiones": s.query(LiveSession).count(), "grabando": s.query(LiveSession).filter_by(status="grabando").count(),
            "eventos": s.query(Event).count(), "clips": s.query(Clip).count(), "proyectos": s.query(Project).count(),
            "trabajos_pendientes": s.query(Job).filter_by(status="pendiente").count(),
            "publicaciones": s.query(Publication).count(),
            "score_promedio": (sum(e.score for e in eventos) / max(1, len(eventos))),
            # Etapa 5: agregados reales (sin quitar ninguna clave existente)
            "eventos_por_modo": _conteo(eventos, "mode"),
            "clips_por_formato": _conteo(clips, "format"),
            "publicaciones_por_estado": _conteo(pubs, "status"),
            "mejor_score": max((e.score for e in eventos), default=0),
            "eventos_sobre_umbral": sum(1 for e in eventos if (e.score or 0) >= umbral)}

# Panel web estático
WEB_DIR = BASE_DIR / "apps" / "web"
@app.get("/")
def root(): return FileResponse(WEB_DIR / "index.html")
if WEB_DIR.exists(): app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
