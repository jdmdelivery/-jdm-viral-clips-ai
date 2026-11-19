"""R2 solo se usa cuando el entorno lo configura. Sin claves, no hay almacén."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))
import almacen_r2  # noqa: E402


def test_sin_claves_no_hay_almacen(monkeypatch):
    for clave in ("R2_ACCOUNT_ID", "R2_BUCKET", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"):
        monkeypatch.delenv(clave, raising=False)
    assert almacen_r2.desde_entorno() is None


def test_uri_de_clip_no_es_grabacion():
    almacen = almacen_r2.AlmacenR2("cuenta", "jdm-viral-clips", "ak", "sk")
    uri = almacen.uri("clips/12.mp4")
    assert almacen_r2.es_uri(uri)
    assert almacen_r2.clave_de_uri(uri) == ("jdm-viral-clips", "clips/12.mp4")
    assert not almacen_r2.es_uri(r"C:\Users\thene\JDMViralClips\grabaciones\sesion-12\grabacion_completa.mp4")
