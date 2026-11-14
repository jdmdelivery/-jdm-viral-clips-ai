"""Auto-migración de columnas al arrancar la API (BD existentes de la Etapa 1).

create_all() no altera tablas ya creadas: una BD creada con la migración 001
no tiene workers.version (004), live_sessions.radar_activo (003) ni
calendar_entries.status (002), y el ORM fallaba con 500 (confirmado en vivo:
POST /workers/heartbeat en Render). Estos tests simulan esa BD «vieja» en un
subproceso con su propia DATABASE_URL y comprueban que al importar la app:
  1) las columnas aparecen, con los valores por defecto en las filas viejas;
  2) un heartbeat con token falso devuelve 401 (no 500) y uno real, 200;
  3) un segundo arranque no cambia nada ni da errores (idempotente).
Y en la BD nueva de la suite (conftest), re-ejecutar la auto-migración es no-op.
"""
import hashlib, json, os, sqlite3, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOKEN_VIEJO = "token-viejo"

_HIJO = r"""
import hashlib, json, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(os.environ["JDM_ROOT"]) / "apps" / "api"))
import main
from sqlalchemy import inspect as sa_inspect
insp = sa_inspect(main.engine)
cols = {t: sorted(c["name"] for c in insp.get_columns(t))
        for t in ("workers", "live_sessions", "calendar_entries")}
from fastapi.testclient import TestClient
client = TestClient(main.app, raise_server_exceptions=False)
r_falso = client.post("/workers/heartbeat", headers={"x-worker-token": "falso"}, json={})
r_real = client.post("/workers/heartbeat", headers={"x-worker-token": "TOKEN_VIEJO"}, json={})
s = main.SessionLocal()
w = s.query(main.Worker).first()
ls = s.query(main.LiveSession).first()
ce = s.query(main.CalendarEntry).first()
out = {"columnas": cols,
       "heartbeat_falso": r_falso.status_code,
       "heartbeat_real": r_real.status_code,
       "version_fila_vieja": w.version if w else None,
       "radar_fila_vieja": bool(ls.radar_activo) if ls else None,
       "status_fila_vieja": ce.status if ce else None}
s.close()
print("RESULTADO:" + json.dumps(out))
""".replace("TOKEN_VIEJO", TOKEN_VIEJO)


def _crear_bd_vieja(ruta: Path):
    """Crea una BD SQLite con el esquema EXACTO de la migración 001."""
    sql = (ROOT / "database" / "migrations" / "001_inicial.sql").read_text(encoding="utf-8")
    # SQLite no conoce SERIAL: equivale a INTEGER PRIMARY KEY a estos efectos.
    sql = sql.replace("SERIAL PRIMARY KEY", "INTEGER PRIMARY KEY")
    con = sqlite3.connect(ruta)
    con.executescript(sql)
    # Datos «viejos»: un trabajador, una sesión y una entrada de calendario.
    con.execute("INSERT INTO workers (name, token_hash, status) VALUES ('pc-vieja', ?, 'conectado')",
                (hashlib.sha256(TOKEN_VIEJO.encode()).hexdigest(),))
    con.execute("INSERT INTO live_sessions (title) VALUES ('sesion vieja')")
    con.execute("INSERT INTO calendar_entries (when_at) VALUES ('2026-01-01 00:00:00')")
    con.commit(); con.close()


def _arrancar(ruta: Path) -> dict:
    env = {**os.environ,
           "DATABASE_URL": f"sqlite:///{ruta}",
           "JDM_ROOT": str(ROOT)}
    p = subprocess.run([sys.executable, "-c", _HIJO], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, f"el arranque falló:\n{p.stdout}\n{p.stderr}"
    linea = [l for l in p.stdout.splitlines() if l.startswith("RESULTADO:")]
    assert linea, f"sin RESULTADO en la salida:\n{p.stdout}\n{p.stderr}"
    return json.loads(linea[-1][len("RESULTADO:"):])


def test_bd_vieja_se_migra_al_arrancar_y_el_heartbeat_no_da_500(tmp_path):
    ruta = tmp_path / "vieja.db"
    _crear_bd_vieja(ruta)
    r = _arrancar(ruta)
    # 1) Las tres columnas posteriores a la 001 aparecen.
    assert "version" in r["columnas"]["workers"]
    assert "radar_activo" in r["columnas"]["live_sessions"]
    assert "status" in r["columnas"]["calendar_entries"]
    # Las filas viejas leen los valores por defecto de cada migración.
    assert r["version_fila_vieja"] == ""
    assert r["radar_fila_vieja"] is False
    assert r["status_fila_vieja"] == "programada"
    # 2) Heartbeat: token falso = 401 (antes de la migración: 500); real = 200.
    assert r["heartbeat_falso"] == 401
    assert r["heartbeat_real"] == 200


def test_segundo_arranque_es_noop(tmp_path):
    ruta = tmp_path / "vieja2.db"
    _crear_bd_vieja(ruta)
    r1 = _arrancar(ruta)
    r2 = _arrancar(ruta)  # segundo arranque: sin cambios ni errores
    assert r2["columnas"] == r1["columnas"]
    assert r2["heartbeat_falso"] == 401
    assert r2["heartbeat_real"] == 200


def test_en_bd_nueva_la_automigracion_es_noop():
    # La BD de la suite la crea create_all con el esquema completo: la
    # auto-migración no debe añadir ni romper nada al re-ejecutarse.
    import main
    from sqlalchemy import inspect as sa_inspect
    antes = {t: [c["name"] for c in sa_inspect(main.engine).get_columns(t)]
             for t in ("workers", "live_sessions", "calendar_entries")}
    main._auto_migrar_columnas()
    despues = {t: [c["name"] for c in sa_inspect(main.engine).get_columns(t)]
               for t in ("workers", "live_sessions", "calendar_entries")}
    assert antes == despues
