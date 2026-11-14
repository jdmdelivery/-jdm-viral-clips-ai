"""JDM VIRAL CLIPS AI — API FastAPI (Etapa 1).
Sirve también el panel web estático. DATABASE_URL configurable (SQLite local / PostgreSQL en Render)."""
import hashlib, hmac, ipaddress, json, os, secrets, socket, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from fastapi import Body, Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
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
    # Versión del motor que reporta el trabajador en el heartbeat. En BD
    # existentes la añade la migración 004; create_all la crea en BD nuevas.
    version = Column(String, default="")
class LiveSession(Base):
    __tablename__ = "live_sessions"
    id = Column(Integer, primary_key=True); title = Column(String, nullable=False)
    mode = Column(String, default="general")  # gta | alofoke | podcast | general
    status = Column(String, default="creada")  # creada|iniciando|grabando|deteniendo|detenida|error
    # Momento en que el trabajador CONFIRMÓ el inicio de la grabación
    # (complete de «iniciar_grabacion»). En BD existentes lo añade la
    # auto-migración; create_all lo crea solo en BD nuevas.
    started_at = Column(DateTime, nullable=True)
    autopilot = Column(Boolean, default=False); min_score = Column(Integer, default=80)
    # Radar en vivo (lote 2026-11-14): el trabajador detecta momentos y genera
    # clips DURANTE la grabación. En BD existentes lo añade la migración 003;
    # create_all lo crea solo en BD nuevas.
    radar_activo = Column(Boolean, default=False)
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
    # Momento descartado por el dueño desde el panel: GET /events lo oculta
    # salvo incluir_descartados=true. En BD existentes lo añade la
    # auto-migración; create_all lo crea solo en BD nuevas.
    descartado = Column(Boolean, default=False)
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

# ---------- Auto-migración de columnas (BD existentes) ----------
# create_all NO altera tablas ya creadas: una BD creada en la Etapa 1 no tiene
# las columnas que las migraciones 002/003/004 añaden a tablas preexistentes, y
# cualquier consulta ORM sobre esos modelos fallaría (p. ej. el heartbeat del
# trabajador devolvía 500 en Render por faltar workers.version). Al arrancar se
# añaden SOLO las columnas que falten: idempotente (segundo arranque = no-op) y
# no-op en una BD nueva (create_all ya lo creó todo).
# Los índices de la migración 002 NO se crean aquí a propósito: un índice
# ausente solo afecta al rendimiento, nunca hace fallar un endpoint.
_COLUMNAS_POST_001 = [
    # (tabla, columna, tipo DDL por dialecto) — migración 002
    ("calendar_entries", "status",
     {"postgresql": "VARCHAR DEFAULT 'programada'", "sqlite": "VARCHAR DEFAULT 'programada'"}),
    # migración 003
    ("live_sessions", "radar_activo",
     {"postgresql": "BOOLEAN DEFAULT FALSE", "sqlite": "BOOLEAN DEFAULT 0"}),
    # migración 004
    ("workers", "version",
     {"postgresql": "VARCHAR DEFAULT ''", "sqlite": "VARCHAR DEFAULT ''"}),
    # lote auditoría 2026-11-14: confirmación real de grabación
    ("live_sessions", "started_at",
     {"postgresql": "TIMESTAMP", "sqlite": "TIMESTAMP"}),
    ("events", "descartado",
     {"postgresql": "BOOLEAN DEFAULT FALSE", "sqlite": "BOOLEAN DEFAULT 0"}),
]
def _auto_migrar_columnas():
    from sqlalchemy import inspect as _sa_inspect, text as _sa_text
    dialecto = engine.dialect.name
    try:
        inspector = _sa_inspect(engine)
        tablas = set(inspector.get_table_names())
    except Exception as e:  # si no se puede inspeccionar, la app arranca igual
        print(f"[auto-migración] no se pudo inspeccionar la BD: {e}", file=sys.stderr)
        return
    for tabla, columna, ddl in _COLUMNAS_POST_001:
        if tabla not in tablas:
            continue  # sin tabla no hay nada que migrar (create_all la crea)
        try:
            existentes = {c["name"] for c in inspector.get_columns(tabla)}
        except Exception as e:
            print(f"[auto-migración] no se pudieron leer las columnas de {tabla}: {e}", file=sys.stderr)
            continue
        if columna in existentes:
            continue
        tipo = ddl.get(dialecto, ddl["sqlite"])
        try:
            # Cada ALTER en su propia transacción: un fallo no tumba el arranque.
            with engine.begin() as conn:
                conn.execute(_sa_text(f"ALTER TABLE {tabla} ADD COLUMN {columna} {tipo}"))
            print(f"[auto-migración] columna añadida: {tabla}.{columna}")
        except Exception as e:
            print(f"[auto-migración] ERROR al añadir {tabla}.{columna}: {e}", file=sys.stderr)
_auto_migrar_columnas()

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
def _payload_generar_clip(s, evento):
    """Payload EXACTO de un job «generar_clip» para un evento: lo usan el
    piloto automático y los endpoints manuales (POST /events/{id}/clip y
    POST /sessions/{id}/generar-clips), para que todos encolen lo mismo.
    file_path se resuelve desde el Project del evento cuando lo tiene."""
    file_path = None
    if evento.project_id:
        proj = s.get(Project, evento.project_id)
        if proj: file_path = proj.file_path
    return {
        "event_id": evento.id, "project_id": evento.project_id, "session_id": evento.session_id,
        "start_s": evento.start_s, "end_s": evento.end_s, "mode": evento.mode, "file_path": file_path,
        "type": evento.type, "score": evento.score}
def _job_generar_clip_activo(s, event_id):
    """Job «generar_clip» de ese evento en estado pendiente/procesando, o None.
    Es el criterio anti-duplicados de los endpoints manuales (el piloto usa el
    suyo propio, más estricto: cualquier job ya existente del evento)."""
    for j in (s.query(Job).filter_by(type="generar_clip")
              .filter(Job.status.in_(["pendiente", "procesando"])).order_by(Job.id).all()):
        try: p = json.loads(j.payload or "{}")
        except Exception: continue
        if p.get("event_id") == event_id: return j
    return None
def _primer_clip_del_evento(s, event_id):
    """Primer Clip (por id) asociado a un evento, o None."""
    return s.query(Clip).filter_by(event_id=event_id).order_by(Clip.id).first()
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
    job = Job(type="generar_clip", payload=json.dumps(_payload_generar_clip(s, evento)))
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
# Regla de Jose (2026-11-14): «quiero que grabe todos los en vivo de todas
# las plataformas». Los enlaces de PÁGINAS de plataformas conocidas se
# aceptan como fuente tipo «plataforma»: la API no resuelve nada — es el
# trabajador, en la PC de Jose, quien obtiene la señal real al momento de
# grabar (worker-windows/resolver.py, con yt-dlp). Las señales directas
# (.m3u8, rtmp://, archivos) siguen como siempre, y la protección anti-SSRF
# se mantiene para todo lo demás. El emparejado de dominios es exacto o por
# subdominio (nada de «youtube.com.evil.com» ni «notyoutube.com»).
DOMINIOS_PLATAFORMA = ("youtube.com", "youtu.be", "facebook.com", "fb.watch",
                       "twitch.tv", "tiktok.com", "instagram.com", "x.com", "twitter.com")
def _es_dominio(host, dominio):
    return host == dominio or host.endswith("." + dominio)
def _es_plataforma(host):
    return any(_es_dominio(host, d) for d in DOMINIOS_PLATAFORMA)
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
    # Páginas de plataformas conocidas (regla de Jose): se aceptan como
    # «plataforma» ANTES del chequeo DNS — son dominios públicos conocidos y
    # la señal la resuelve el trabajador en su PC, no este servidor. Una
    # señal directa (.m3u8/rtmp/srt) nunca pasa por esta rama.
    es_directa = ".m3u8" in p.path or p.scheme in ("rtmp", "rtmps", "srt")
    if not es_directa and p.scheme in ("http", "https") and _es_plataforma(host):
        return True, "plataforma", ("Compatible: tu PC resuelve la señal al momento de grabar. "
                                    "Al iniciar la grabación, el trabajador obtiene la señal real "
                                    "del en vivo desde el enlace de la plataforma.")
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
class RadarIn(BaseModel): activo: bool
class LiveEventIn(BaseModel):
    start_s: float = 0; end_s: float = 0; type: str = "general"; mode: str = "general"
    score: int = 0; breakdown: dict = {}
class LiveClipIn(BaseModel):
    event_index: int | None = None; start_s: float | None = None
    file_path: str | None = None; format: str = "9:16"; duration_s: float | None = None
    score: int = 0; title: str | None = None; titles: dict = {}; subtitles: bool = False
class LiveIngestIn(BaseModel):
    events: list[LiveEventIn] = []; clips: list[LiveClipIn] = []
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
def heartbeat(data: dict | None = Body(None), w=Depends(worker_auth), s=Depends(db)):
    row = s.get(Worker, w.id); row.status = "conectado"; row.last_seen = now()
    # Body opcional {"version": "1.1.0"}: los trabajadores viejos no envían
    # body y siguen funcionando igual; solo se guarda una versión no vacía.
    if data:
        version = data.get("version")
        if version:
            row.version = str(version)
    s.commit()
    return {"ok": True, "trabajos_pendientes": s.query(Job).filter_by(status="pendiente").count()}
@app.get("/workers")
def workers(user=Depends(current_user), s=Depends(db)):
    return [ser(w, ["id", "name", "status", "last_seen", "version"]) for w in s.query(Worker).all()]

# Sesiones LIVE y fuentes
@app.post("/sessions")
def create_session(data: SessionIn, user=Depends(current_user), s=Depends(db)):
    row = LiveSession(title=data.title, mode=data.mode, autopilot=data.autopilot, min_score=data.min_score, user_id=user.id)
    s.add(row); s.commit(); audit(s, user.email, "crear_sesion", data.title)
    return ser(row, ["id", "title", "mode", "status", "autopilot", "min_score", "radar_activo", "created_at", "started_at"])
@app.get("/sessions")
def list_sessions(limit: int = 200, offset: int = 0, user=Depends(current_user), s=Depends(db)):
    q = s.query(LiveSession).offset(_clamp_offset(offset)).limit(_clamp_limit(limit))
    return [ser(r, ["id", "title", "mode", "status", "autopilot", "min_score", "radar_activo", "created_at", "started_at"]) for r in q]
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
    # Etapa 6 + confirmación real: una sola grabación activa a la vez. Bloquea
    # si otra sesión está grabando O todavía iniciando (el trabajador aún no
    # confirmó el inicio); «deteniendo» ya no bloquea.
    otra = s.query(LiveSession).filter(LiveSession.status.in_(["grabando", "iniciando"]), LiveSession.id != sid).first()
    if otra: raise HTTPException(409, f"Ya hay otra sesión grabando («{otra.title}», id {otra.id}). Detenla antes de iniciar esta.")
    src = s.query(Source).filter_by(session_id=sid, allowed=True).first()
    if not src: raise HTTPException(400, "La sesión no tiene una fuente compatible validada. Valida primero una fuente: el enlace del en vivo de una plataforma (YouTube, Facebook, Twitch, TikTok…), un HLS (.m3u8) o un RTMP.")
    # La sesión NO pasa a «grabando» aquí: queda «iniciando» hasta que el
    # trabajador confirme completando el job «iniciar_grabacion».
    row.status = "iniciando"; s.commit()
    job = Job(type="iniciar_grabacion", payload=json.dumps({"session_id": sid, "url": src.url, "mode": row.mode,
                                                            "radar_activo": bool(row.radar_activo)}))
    s.add(job); s.commit(); audit(s, user.email, "iniciar_grabacion", row.title)
    return {"ok": True, "status": row.status, "job_id": job.id}
@app.post("/sessions/{sid}/radar")
def session_radar(sid: int, data: RadarIn, user=Depends(current_user), s=Depends(db)):
    """Radar en vivo ON/OFF. Elección de consistencia: FLAG INMEDIATO + job.
    El flag radar_activo se actualiza en el acto (el panel muestra el estado
    real sin esperar) y el job «radar_sesion» es lo que enciende/apaga el
    radar en el trabajador; si no hay grabación en curso, el trabajador lo
    reporta en el resultado del job y el radar se activará al iniciar la
    grabación (el payload de iniciar_grabacion lleva el flag)."""
    row = s.get(LiveSession, sid)
    if not row: raise HTTPException(404, "Sesión no encontrada")
    row.radar_activo = bool(data.activo); s.commit()
    job = Job(type="radar_sesion", payload=json.dumps({"session_id": sid, "activo": bool(data.activo), "mode": row.mode}))
    s.add(job); s.commit(); audit(s, user.email, "radar_sesion", f"sesión {sid} -> {'ACTIVO' if data.activo else 'INACTIVO'}")
    return {"ok": True, "radar_activo": row.radar_activo, "job_id": job.id}
@app.post("/sessions/{sid}/stop")
def stop_session(sid: int, user=Depends(current_user), s=Depends(db)):
    row = s.get(LiveSession, sid)
    if not row: raise HTTPException(404, "Sesión no encontrada")
    # La sesión NO pasa a «detenida» aquí: queda «deteniendo» hasta que el
    # trabajador confirme completando el job «detener_grabacion».
    row.status = "deteniendo"; s.commit()
    job = Job(type="detener_grabacion", payload=json.dumps({"session_id": sid})); s.add(job); s.commit()
    return {"ok": True, "status": row.status, "job_id": job.id}
@app.post("/sessions/{sid}/capcut")
def session_capcut(sid: int, user=Depends(current_user), s=Depends(db)):
    """Encola «enviar_capcut_sesion»: el trabajador crea en la PC un proyecto
    borrador de CapCut con todos los clips de la sesión (patrón de encolado
    de /clips/{cid}/edit y de /sessions/{sid}/radar). La API no crea nada."""
    row = s.get(LiveSession, sid)
    if not row: raise HTTPException(404, "Sesión no encontrada")
    clips = s.query(Clip).filter_by(session_id=sid).order_by(Clip.id).all()
    if not clips: raise HTTPException(400, "La sesión no tiene clips generados todavía")
    def _clip_capcut(c):
        # start_s/end_s salen del Event del clip (clip.event_id) cuando existe.
        ev = s.get(Event, c.event_id) if c.event_id else None
        return {"file_path": c.file_path,
                "title": _primer_titulo_sugerido(c) or c.title or "",
                "duration_s": c.duration_s,
                "clip_id": c.id,
                "start_s": ev.start_s if ev else None,
                "end_s": ev.end_s if ev else None}
    job = Job(type="enviar_capcut_sesion", payload=json.dumps({
        "session_id": sid, "titulo_sesion": row.title,
        "clips": [_clip_capcut(c) for c in clips]}))
    s.add(job); s.commit()
    audit(s, user.email, "enviar_capcut_sesion", f"sesión {sid} ({len(clips)} clips) -> job {job.id}")
    return {"job_id": job.id, "status": job.status, "clips": len(clips),
            "nota": "Proyecto CapCut encolado: lo crea el trabajador en la carpeta de borradores "
                    "de CapCut de tu PC, con los clips de la sesión montados en orden. "
                    "Sin trabajador conectado, el trabajo queda pendiente."}

# Trabajos (cola persistente)
@app.post("/jobs")
def create_job(data: JobIn, user=Depends(current_user), s=Depends(db)):
    j = Job(type=data.type, payload=json.dumps(data.payload)); s.add(j); s.commit()
    return ser(j, ["id", "type", "status", "attempts", "created_at"])
@app.get("/jobs")
def list_jobs(limit: int = 200, offset: int = 0, user=Depends(current_user), s=Depends(db)):
    q = s.query(Job).order_by(Job.id.desc()).offset(_clamp_offset(offset)).limit(_clamp_limit(limit))
    out = []
    for j in q:
        d = ser(j, ["id", "type", "status", "attempts", "error", "created_at"])
        # «result» como JSON ya parseado; None si está vacío o no es JSON válido.
        try: d["result"] = json.loads(j.result) if j.result else None
        except Exception: d["result"] = None
        out.append(d)
    return out
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
    # Confirmación real de grabación: solo AHORA la sesión cambia de estado
    # («iniciando» -> «grabando» con started_at, «deteniendo» -> «detenida»).
    # Una sesión inexistente (jobs creados a mano) no rompe la compleción.
    if j.type in ("iniciar_grabacion", "detener_grabacion"):
        try: _sid_conf = json.loads(j.payload or "{}").get("session_id")
        except Exception: _sid_conf = None
        _ses_conf = s.get(LiveSession, _sid_conf) if _sid_conf else None
        if _ses_conf:
            if j.type == "iniciar_grabacion":
                _ses_conf.status = "grabando"; _ses_conf.started_at = now()
            else:
                _ses_conf.status = "detenida"
            s.commit()
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
    # Al detener una grabación, la grabación completa vive solo como ruta local
    # del trabajador: se registra como Project (una sola vez por ruta) para que
    # aparezca en «Analizar con IA» y se le puedan generar clips.
    if j.type == "detener_grabacion":
        ruta = res.get("grabacion_completa")
        if ruta and not s.query(Project).filter_by(file_path=ruta).first():
            try: sid_grab = json.loads(j.payload or "{}").get("session_id")
            except Exception: sid_grab = None
            s.add(Project(name=f"Grabación sesión {sid_grab}" if sid_grab else "Grabación",
                          file_path=ruta, duration_s=res.get("duration_s"),
                          status="grabado"))
            s.commit()
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
    # Confirmación real de grabación: SOLO cuando el trabajo queda «fallido»
    # (intentos agotados) la sesión de iniciar/detener pasa a «error»; mientras
    # se reintenta (vuelve a «pendiente») la sesión no se toca.
    if j.status == "fallido" and j.type in ("iniciar_grabacion", "detener_grabacion"):
        try: _sid_err = json.loads(j.payload or "{}").get("session_id")
        except Exception: _sid_err = None
        _ses_err = s.get(LiveSession, _sid_err) if _sid_err else None
        if _ses_err:
            _ses_err.status = "error"; s.commit()
    return {"ok": True, "status": j.status}

# Ingesta del radar EN VIVO: el trabajador reporta momentos y clips DURANTE
# la grabación (sin esperar a detenerla). Dedup de eventos por solape >70%
# del intervalo menor contra los ya guardados de la sesión — mismo criterio
# que worker-windows/dedup.py (función espejo _solape_ratio).
def _solape_ratio(a_ini, a_fin, b_ini, b_fin):
    inter = min(a_fin, b_fin) - max(a_ini, b_ini)
    if inter <= 0: return 0.0
    menor = min(a_fin - a_ini, b_fin - b_ini)
    return inter / menor if menor > 0 else 0.0
@app.post("/workers/sessions/{sid}/live-events")
def live_events_ingest(sid: int, data: LiveIngestIn, w=Depends(worker_auth), s=Depends(db)):
    ses = s.get(LiveSession, sid)
    if not ses: raise HTTPException(404, "Sesión no encontrada")
    existentes = s.query(Event).filter_by(session_id=sid).order_by(Event.id).all()
    ids_por_indice = {}  # índice del payload -> Event.id (nuevo o el duplicado ya guardado)
    guardados = duplicados = 0
    for i, ev in enumerate(data.events):
        dup = None
        for e in existentes:
            if _solape_ratio(ev.start_s, ev.end_s, e.start_s, e.end_s) > 0.70:
                dup = e; break
        if dup is not None:
            ids_por_indice[i] = dup.id; duplicados += 1; continue
        e = Event(session_id=sid, type=ev.type, mode=ev.mode, start_s=ev.start_s, end_s=ev.end_s,
                  score=int(ev.score), breakdown=json.dumps(ev.breakdown))
        s.add(e); s.commit(); existentes.append(e); ids_por_indice[i] = e.id; guardados += 1
        try: evaluar_piloto(s, e)  # el piloto también aplica a momentos en vivo
        except Exception: pass
    clips_guardados = 0
    for cl in data.clips:
        ev_id = None
        if cl.event_index is not None: ev_id = ids_por_indice.get(cl.event_index)
        if ev_id is None and cl.start_s is not None:
            for idx, ev in enumerate(data.events):
                if abs(ev.start_s - cl.start_s) < 0.01:
                    ev_id = ids_por_indice.get(idx); break
        c = Clip(event_id=ev_id, session_id=sid, file_path=cl.file_path, format=cl.format,
                 duration_s=cl.duration_s, score=int(cl.score), title=cl.title,
                 titles_json=json.dumps(cl.titles or {}), subtitles=bool(cl.subtitles))
        s.add(c); s.commit(); clips_guardados += 1
    return {"ok": True, "eventos_guardados": guardados, "eventos_duplicados": duplicados,
            "clips_guardados": clips_guardados}

# Eventos y clips
@app.post("/events")
def create_event(data: EventIn, user=Depends(current_user), s=Depends(db)):
    e = Event(**data.model_dump(exclude={"breakdown"}), breakdown=json.dumps(data.breakdown))
    s.add(e); s.commit()
    evaluar_piloto(s, e)  # Etapa 5: el piloto decide si encola «generar_clip»
    return ser(e, ["id", "type", "mode", "start_s", "end_s", "score", "status"])
@app.get("/events")
def list_events(session_id: int | None = None, incluir_descartados: bool = False, limit: int = 200, offset: int = 0, user=Depends(current_user), s=Depends(db)):
    q = s.query(Event)
    if session_id is not None: q = q.filter_by(session_id=session_id)
    if not incluir_descartados: q = q.filter((Event.descartado == False) | (Event.descartado.is_(None)))  # noqa: E712
    out = []
    for e in q.order_by(Event.score.desc()).offset(_clamp_offset(offset)).limit(_clamp_limit(limit)):
        d = ser(e, ["id", "session_id", "project_id", "type", "mode", "start_s", "end_s", "score", "status", "created_at"])
        d["breakdown"] = json.loads(e.breakdown or "{}")
        clip = _primer_clip_del_evento(s, e.id)
        d["descartado"] = bool(e.descartado)
        d["duracion_s"] = round((e.end_s or 0) - (e.start_s or 0), 2)
        d["tiene_clip"] = clip is not None
        d["clip_id"] = clip.id if clip else None
        d["clip_file_path"] = clip.file_path if clip else None
        # Mismo criterio que GET /clips: el archivo existe en ESTE servidor.
        d["clip_descargable"] = bool(clip and clip.file_path and Path(clip.file_path).exists())
        out.append(d)
    return out
@app.post("/events/{eid}/descartar")
def descartar_evento(eid: int, user=Depends(current_user), s=Depends(db)):
    e = s.get(Event, eid)
    if not e: raise HTTPException(404, "Evento no encontrado")
    e.descartado = True; s.commit()
    audit(s, user.email, "descartar_evento", f"evento {eid}")
    return {"ok": True, "id": eid, "descartado": True}
@app.post("/events/{eid}/clip")
def clip_de_evento(eid: int, user=Depends(current_user), s=Depends(db)):
    """Generación manual de UN clip: encola «generar_clip» con el mismo
    payload que arma el piloto automático para el evento."""
    e = s.get(Event, eid)
    if not e: raise HTTPException(404, "Evento no encontrado")
    if e.descartado: raise HTTPException(400, "Ese momento está descartado: no se le puede generar un clip")
    clip = _primer_clip_del_evento(s, eid)
    if clip: raise HTTPException(409, f"Ese momento ya tiene un clip (#{clip.id})")
    activo = _job_generar_clip_activo(s, eid)
    if activo: return {"job_id": activo.id, "duplicado": True}
    job = Job(type="generar_clip", payload=json.dumps(_payload_generar_clip(s, e)))
    s.add(job); s.commit()
    audit(s, user.email, "generar_clip_manual", f"evento {eid} -> job {job.id}")
    return {"job_id": job.id, "duplicado": False}
@app.post("/sessions/{sid}/generar-clips")
def generar_clips_sesion(sid: int, user=Depends(current_user), s=Depends(db)):
    """Encola «generar_clip» para cada evento NO descartado de la sesión que
    aún no tenga clip ni un job generar_clip pendiente/procesando."""
    if not s.get(LiveSession, sid): raise HTTPException(404, "Sesión no encontrada")
    eventos = (s.query(Event).filter_by(session_id=sid)
               .filter((Event.descartado == False) | (Event.descartado.is_(None)))  # noqa: E712
               .order_by(Event.id).all())
    jobs, omitidos = [], 0
    for e in eventos:
        if _primer_clip_del_evento(s, e.id) or _job_generar_clip_activo(s, e.id):
            omitidos += 1; continue
        job = Job(type="generar_clip", payload=json.dumps(_payload_generar_clip(s, e)))
        s.add(job); s.commit(); jobs.append(job.id)
    audit(s, user.email, "generar_clips_sesion", f"sesión {sid}: {len(jobs)} encolados, {omitidos} omitidos")
    return {"jobs": jobs, "encolados": len(jobs), "omitidos": omitidos, "total_eventos": len(eventos)}
@app.post("/clips")
def create_clip(data: ClipIn, user=Depends(current_user), s=Depends(db)):
    c = Clip(**data.model_dump()); s.add(c); s.commit()
    return ser(c, ["id", "file_path", "format", "duration_s", "score", "title", "subtitles", "status"])
@app.get("/clips")
def list_clips(session_id: int | None = None, limit: int = 200, offset: int = 0, user=Depends(current_user), s=Depends(db)):
    q = s.query(Clip)
    if session_id is not None: q = q.filter_by(session_id=session_id)
    q = q.order_by(Clip.id.desc()).offset(_clamp_offset(offset)).limit(_clamp_limit(limit))
    out = []
    for c in q:
        d = ser(c, ["id", "event_id", "project_id", "session_id", "file_path", "thumb_path", "format", "duration_s", "score", "title", "subtitles", "status", "created_at"])
        # «descargable» solo si el archivo está EN ESTE servidor; la mayoría de
        # los clips viven en el disco del trabajador (la ruta se muestra tal cual).
        d["descargable"] = bool(c.file_path and Path(c.file_path).exists())
        out.append(d)
    return out
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

# ---------- CapCut (lado API) ----------
# La API NO crea el borrador: valida y encola el trabajo; lo crea el
# trabajador en la PC, en la carpeta de borradores de CapCut.
def _primer_titulo_sugerido(c):
    """Primer título sugerido de titles_json ({"titulos": [...]}), o None.

    titles_json llega como texto JSON (lo guarda así el complete del
    trabajador); cualquier fallo de parseo o forma inesperada devuelve None
    y el llamante cae al título propio del clip."""
    try:
        datos = json.loads(c.titles_json or "{}")
    except Exception:
        return None
    if not isinstance(datos, dict): return None
    titulos = datos.get("titulos")
    if isinstance(titulos, list) and titulos and titulos[0]:
        return str(titulos[0])
    return None
@app.post("/clips/{cid}/capcut")
def clip_capcut(cid: int, user=Depends(current_user), s=Depends(db)):
    c = s.get(Clip, cid)
    if not c: raise HTTPException(404, "Clip no encontrado")
    if not c.file_path: raise HTTPException(400, "El clip no tiene archivo asociado")
    titulo = _primer_titulo_sugerido(c) or c.title or f"Clip {cid}"
    job = Job(type="enviar_capcut_clip", payload=json.dumps({
        "clip_id": cid, "project_id": c.project_id, "file_path": c.file_path,
        "title": titulo, "duration_s": c.duration_s}))
    s.add(job); s.commit()
    audit(s, user.email, "enviar_capcut", f"clip {cid} -> job {job.id}")
    return {"job_id": job.id, "status": job.status,
            "nota": "Envío a CapCut encolado: lo crea el trabajador en la carpeta de borradores "
                    "de CapCut de tu PC, con el clip y su título listos para editar. "
                    "Sin trabajador conectado, el trabajo queda pendiente."}

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
def user_or_worker(authorization: str = Header(default=""), x_worker_token: str = Header(default=""), s=Depends(db)):
    """Autoriza al usuario del panel (Bearer) O a un trabajador (X-Worker-Token).
    Se intenta primero el token de trabajador; si no valida, se intenta el usuario.
    Si ninguno valida, current_user/worker_auth lanzan 401."""
    if x_worker_token:
        try:
            return {"tipo": "trabajador", "worker": worker_auth(x_worker_token=x_worker_token, s=s)}
        except HTTPException:
            pass
    return {"tipo": "usuario", "user": current_user(authorization=authorization, s=s)}
@app.get("/projects")
def list_projects(limit: int = 200, offset: int = 0, user=Depends(current_user), s=Depends(db)):
    q = s.query(Project).offset(_clamp_offset(offset)).limit(_clamp_limit(limit))
    return [ser(p, ["id", "name", "duration_s", "status", "created_at"]) for p in q]
@app.get("/projects/{pid}/file")
def project_file(pid: int, auth=Depends(user_or_worker), s=Depends(db)):
    """Descarga del archivo del proyecto: la usa el trabajador remoto, cuyo disco
    no contiene la ruta del servidor (arquitectura híbrida Render + Windows)."""
    p = s.get(Project, pid)
    if not p: raise HTTPException(404, "Proyecto no encontrado")
    if not Path(p.file_path).is_file():
        raise HTTPException(404, "El archivo del proyecto no existe en el disco del servidor")
    return FileResponse(p.file_path, filename=Path(p.file_path).name)
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
def social_conectar(plataforma: str, formato: str = "", user=Depends(current_user), s=Depends(db)):
    if plataforma not in social_lib.PLATAFORMAS: raise HTTPException(404, "Plataforma no válida")
    if not social_lib.oauth_configurado(plataforma):
        raise HTTPException(400, f"Falta crear la app OAuth oficial de {plataforma}: requiere configuración — "
                                 "no hay credenciales cargadas en el servidor (variables de entorno). "
                                 "Crea la app en la plataforma y carga su client id/secret en el servidor "
                                 "para poder conectar; sin eso la conexión no puede iniciarse.")
    state = secrets.token_urlsafe(24)
    _cfg_set(s, f"oauth_state_{plataforma}", state); s.commit()
    url = social_lib.authorize_url(plataforma, state)
    # El panel pide la URL en JSON para abrirla en una pestaña nueva (antes el
    # fetch con redirect:"manual" nunca llevaba al usuario a autorizar).
    if formato == "json":
        return {"url": url}
    return RedirectResponse(url, status_code=307)
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

# ---------- Páginas legales públicas (Términos y Privacidad) ----------
_LEGAL_CSS = """
body{margin:0;background:#0f0f0f;color:#f4f4f5;font-family:system-ui,'Segoe UI',Arial,sans-serif;line-height:1.6}
.wrap{max-width:780px;margin:0 auto;padding:28px 18px 48px}
.card{background:#1a1a1d;border:1px solid #2e2e33;border-radius:16px;padding:28px 30px}
h1{font-size:27px;margin:0 0 4px}
.bar{height:4px;border-radius:4px;background:linear-gradient(92deg,#ff0033,#ff7a18,#ff2ea6,#a335d6);margin:12px 0 20px}
h2{font-size:17.5px;margin:22px 0 6px;color:#fff}
p,li{color:#d6d6dc;font-size:15px}
a{color:#ff5c7a}
.meta{color:#a7a7b3;font-size:13.5px}
.back{display:inline-block;margin-top:22px;font-weight:700}
"""
_LEGAL_CONTACTO = "joseluisdiazmartinez90@gmail.com"

def _pagina_legal(titulo: str, cuerpo: str) -> str:
    return (f"<!DOCTYPE html><html lang=\"es\"><head><meta charset=\"UTF-8\">"
            f"<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f"<title>{titulo} — JDM Viral Clips AI</title><style>{_LEGAL_CSS}</style></head>"
            f"<body><div class=\"wrap\"><div class=\"card\"><h1>{titulo}</h1>"
            f"<p class=\"meta\">JDM Viral Clips AI · Última actualización: 14 de noviembre de 2026</p>"
            f"<div class=\"bar\"></div>{cuerpo}"
            f"<a class=\"back\" href=\"/\">← Volver al panel</a>"
            f"</div></div></body></html>")

@app.get("/terminos", response_class=HTMLResponse)
def terminos():
    cuerpo = """
    <h2>1. Qué es JDM Viral Clips AI</h2>
    <p>JDM Viral Clips AI es un sistema personal, creado y operado por su propietario, para grabar
    transmisiones en vivo, detectar momentos destacados y crear clips cortos de video. Funciona con
    este panel web y con un programa trabajador que se ejecuta en la computadora del propietario,
    donde se realiza la grabación y el procesamiento de video.</p>
    <h2>2. Cuenta y acceso</h2>
    <p>El acceso al panel requiere una cuenta registrada por el propietario. El propietario es
    responsable de mantener la confidencialidad de su contraseña y del uso que haga del sistema.</p>
    <h2>3. Contenido y responsabilidad</h2>
    <p>El propietario decide qué transmisiones graba y qué videos importa al sistema, y es
    responsable de contar con los derechos o permisos necesarios sobre ese contenido. Las
    grabaciones de las transmisiones se almacenan en la computadora del propietario.</p>
    <h2>4. Publicación en redes sociales</h2>
    <p>El sistema no publica contenido por su cuenta: cada publicación requiere la aprobación del
    propietario y, cuando se realiza, usa las APIs oficiales de cada plataforma (TikTok, Facebook,
    Instagram y YouTube) mediante conexiones OAuth autorizadas por el propio propietario.</p>
    <h2>5. Disponibilidad y resultados</h2>
    <p>El sistema se ofrece «tal cual», sin garantía de disponibilidad continua. La puntuación de
    viralidad que muestra el panel es una estimación calculada a partir de señales medibles del
    video (audio, cambios de escena, duración) y no garantiza vistas ni resultados en ninguna
    plataforma.</p>
    <h2>6. Cambios en estos términos</h2>
    <p>Estos términos pueden actualizarse; la fecha de la última actualización aparece al inicio
    de esta página.</p>
    <h2>7. Contacto</h2>
    <p>Para cualquier pregunta sobre estos términos, escribe a
    <a href="mailto:joseluisdiazmartinez90@gmail.com">joseluisdiazmartinez90@gmail.com</a>.</p>
    """
    return _pagina_legal("Términos de uso", cuerpo)

@app.get("/privacidad", response_class=HTMLResponse)
def privacidad():
    cuerpo = """
    <h2>1. Datos que maneja el sistema</h2>
    <ul>
      <li><b>Cuenta del panel:</b> el correo electrónico del propietario y su contraseña, que se
      guarda con hash (PBKDF2), nunca en texto plano.</li>
      <li><b>Cuentas de redes sociales conectadas:</b> cuando el propietario conecta TikTok,
      Facebook, Instagram o YouTube, el sistema guarda los tokens de acceso OAuth cifrados
      (Fernet) para poder publicar con su autorización. Si el cifrado no está configurado en el
      servidor, el sistema no almacena esos tokens.</li>
      <li><b>Actividad y contenido:</b> metadatos de sesiones, fuentes, eventos detectados, clips,
      trabajos y publicaciones (títulos, duraciones, puntuaciones y estados). Los videos que el
      propietario importa desde el panel se guardan en el almacenamiento del servicio; las
      grabaciones de transmisiones en vivo se guardan en la computadora del propietario, donde
      corre el programa trabajador.</li>
      <li><b>Sesión en el navegador:</b> el panel guarda el token de acceso en el almacenamiento
      local del navegador para mantener la sesión iniciada.</li>
    </ul>
    <h2>2. Para qué se usan los datos</h2>
    <p>Los datos se usan únicamente para operar el sistema del propietario: grabar, analizar,
    crear clips y publicar o programar contenido cuando él lo aprueba.</p>
    <h2>3. Compartición de datos</h2>
    <p>No vendemos datos. Solo se envía información a las plataformas que el propietario conecta
    (al publicar o programar contenido con su aprobación) y a los proveedores de infraestructura
    donde corre el servicio.</p>
    <h2>4. Seguridad</h2>
    <p>Las contraseñas se almacenan con hash y los tokens de las cuentas sociales se almacenan
    cifrados, como se describe en la sección 1.</p>
    <h2>5. Derechos del propietario</h2>
    <p>El propietario puede solicitar el acceso, la corrección o la eliminación de sus datos
    escribiendo al correo de contacto.</p>
    <h2>6. Contacto</h2>
    <p>Para cualquier pregunta sobre esta política, escribe a
    <a href="mailto:joseluisdiazmartinez90@gmail.com">joseluisdiazmartinez90@gmail.com</a>.</p>
    """
    return _pagina_legal("Política de privacidad", cuerpo)

# Panel web estático
WEB_DIR = BASE_DIR / "apps" / "web"
@app.get("/")
def root(): return FileResponse(WEB_DIR / "index.html")
@app.get("/tiktokwgWIbOYQU4ZZROQ99XH8furOtNXmJ1b9.txt")
def tiktok_verificacion():
    # Verificación de sitio de TikTok for Developers: el archivo debe servirse
    # en la raíz del dominio con su contenido exacto (68 bytes, sin salto final).
    return FileResponse(WEB_DIR / "tiktokwgWIbOYQU4ZZROQ99XH8furOtNXmJ1b9.txt",
                        media_type="text/plain; charset=utf-8")
if WEB_DIR.exists(): app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
