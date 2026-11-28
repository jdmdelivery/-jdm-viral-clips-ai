"""v1.3.15 — Sincronía audio/video en la grabación en vivo.

El problema (reportado por Jose el 2026-11-27): en los clips la voz se
queda detrás del video. Causa: con el par «bv*+ba» el motor abre el
video y el audio como DOS entradas de FFmpeg, cada una con su propio
reloj, y las une como si arrancaran a la vez; el desfase crece con las
horas de sesión.

La corrección, en dos piezas:
1. `_cmd_grabar` alinea las dos entradas (en la v1.3.17: SIN tocar los
   tiempos del video — el «use_wallclock_as_timestamps» de la primera
   versión congelaba la imagen en grabaciones reales y se retiró; el
   ajuste lo hace el audio con «aresample=async=1», ver test_v1317.py).
2. El bucle del Recorder rota la captura cada MAX_CAPTURA_CONTINUA_S:
   reinicia FFmpeg con señal fresca SIN gastar reintentos, para que
   ningún desfase residual se acumule durante horas.
"""
import json
from pathlib import Path

import recorder
import worker as worker_mod
from recorder import Recorder, _cmd_grabar

DOS = "https://v.example.com/video\nhttps://v.example.com/audio"
UNA = "https://v.example.com/live.m3u8"


def test_dos_entradas_en_vivo_se_alinean_por_reloj_de_pared():
    # HISTÓRICO (v1.3.17): esta prueba fijaba el «use_wallclock» de la
    # v1.3.15; esa bandera congeló la imagen en una grabación real y se
    # retiró. El comportamiento vigente lo fija test_v1317.py; aquí se
    # conserva el mapeo histórico de entradas, que no cambió.
    cmd = _cmd_grabar(DOS, "salida", en_vivo=True)
    assert cmd.count("-i") == 2
    i = cmd.index("-map")
    assert cmd[i:i + 4] == ["-map", "0:v:0?", "-map", "1:a:0?"]


def test_dos_entradas_sin_vivo_no_tocan_los_relojes():
    # Un archivo publicado con formatos separados conserva las marcas
    # propias de cada pista y tampoco lleva el ajuste de audio en vivo.
    cmd = _cmd_grabar(DOS, "salida")
    assert "-use_wallclock_as_timestamps" not in cmd
    assert "-start_at_zero" not in cmd
    assert "aresample=async=1" not in cmd


def test_una_sola_entrada_en_vivo_como_siempre():
    # La señal combinada ya trae audio y video en un solo reloj.
    cmd = _cmd_grabar(UNA, "salida", en_vivo=True)
    assert cmd.count("-i") == 1
    assert "-use_wallclock_as_timestamps" not in cmd
    assert "-start_at_zero" not in cmd
    assert "aresample=async=1" not in cmd


class _ProcFalso:
    """FFmpeg fingido: corre hasta que lo terminan; el segundo, además,
    marca la detención del Recorder en su segundo poll para cerrar el
    bucle sin hilos ni esperas largas."""

    def __init__(self, al_detener=None):
        self.pid = 4321
        self._terminado = False
        self._al_detener = al_detener
        self._polls = 0

    def poll(self):
        self._polls += 1
        if self._al_detener and self._polls >= 2:
            self._al_detener()
            return 0
        return 0 if self._terminado else None

    def terminate(self):
        self._terminado = True


def test_rotacion_reinicia_sin_gastar_reintentos(tmp_path, monkeypatch):
    r = Recorder(DOS, tmp_path, en_vivo=True)
    # Un segmento de ESTA corrida en disco (mtime fresco tras el init).
    (tmp_path / "seg_00000.mp4").write_bytes(b"x" * 2048)
    monkeypatch.setattr(recorder, "MAX_CAPTURA_CONTINUA_S", 0.2)
    cmds = []
    procs = [_ProcFalso(), _ProcFalso(al_detener=r.detener_flag.set)]

    def popen_falso(cmd, **kwargs):
        cmds.append(cmd)
        return procs[len(cmds) - 1]

    monkeypatch.setattr(recorder.subprocess, "Popen", popen_falso)
    r._bucle()

    # Dos FFmpeg: el inicial y el de la rotación. La rotación NO es una
    # caída: el contador de reintentos queda en cero.
    assert len(cmds) == 2
    assert r.reintentos == 0
    # La numeración de segmentos continúa donde iba (no pisa el seg_00000).
    i = cmds[1].index("-segment_start_number")
    assert cmds[1][i + 1] == "1"
    # Y la captura rotada usa el mismo comando seguro de sincronía
    # (v1.3.17: ajuste por audio, jamás reloj de pared).
    for cmd in cmds:
        assert "-use_wallclock_as_timestamps" not in cmd
        assert "aresample=async=1" in cmd


def test_version_1_3_15_y_recorder_en_manifiesto():
    assert worker_mod.VERSION == "1.3.17"
    manifiesto = json.loads((Path(__file__).resolve().parents[1]
                             / "worker-windows" / "manifest.json")
                            .read_text(encoding="utf-8"))
    assert manifiesto["version"] == "1.3.17"
    assert "recorder.py" in manifiesto["files"]
