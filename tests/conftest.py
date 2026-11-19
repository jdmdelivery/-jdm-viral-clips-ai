import os, sys, tempfile
from pathlib import Path
import pytest
_tmp = tempfile.mkdtemp(prefix="jdm-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))
sys.path.insert(0, str(ROOT / "worker-windows"))

@pytest.fixture(autouse=True)
def _aislar_copias_de_prueba(tmp_path_factory, monkeypatch):
    """Las pruebas no escriben en data/clips_servidor: ahí están copias reales."""
    import main as apimain
    carpeta = tmp_path_factory.mktemp("clips-servidor")
    monkeypatch.setattr(apimain, "CLIPS_DIR", carpeta)


@pytest.fixture(autouse=True)
def _cola_limpia_entre_pruebas():
    """Un trabajo pendiente de otra prueba no puede ser reclamado por esta."""
    import main as apimain
    s = apimain.SessionLocal()
    try:
        vivos = ("pendiente", "procesando")
        for j in s.query(apimain.Job).filter(apimain.Job.status.in_(vivos)).all():
            j.status = "cancelado"
            j.error = "cancelado entre pruebas"
        s.commit()
    finally:
        s.close()
    yield


@pytest.fixture(autouse=True)
def _cerrar_capturas_entre_pruebas():
    """Cada prueba empieza sin una captura a medias. Durante la prueba, una
    sesión en «deteniendo» sigue bloqueando a las demás."""
    yield
    import main as apimain
    s = apimain.SessionLocal()
    try:
        activos = ("grabando", "iniciando", "deteniendo")
        for row in s.query(apimain.LiveSession).filter(apimain.LiveSession.status.in_(activos)).all():
            row.status = "detenida"
        s.commit()
    finally:
        s.close()
