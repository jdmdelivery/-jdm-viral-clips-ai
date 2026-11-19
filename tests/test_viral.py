import viral

def test_puntuacion_determinista_y_explicable():
    senales = {"rms_medio_db": -30.0, "rms_pico_db": -5.0, "cambios_escena": 5, "duracion_s": 10}
    s1, d1 = viral.puntuar(senales, "general")
    s2, d2 = viral.puntuar(senales, "general")
    assert s1 == s2 and d1 == d2  # determinista
    assert 0 <= s1 <= 100
    for k in ("intensidad_audio", "dinamica_audio", "actividad_visual", "claridad_duracion", "senales_medidas"):
        assert k in d1  # desglose explicable guardado

def test_momento_fuerte_supera_a_tranquilo():
    fuerte = {"rms_medio_db": -25.0, "rms_pico_db": -4.0, "cambios_escena": 7, "duracion_s": 10}
    tranquilo = {"rms_medio_db": -45.0, "rms_pico_db": -40.0, "cambios_escena": 0, "duracion_s": 10}
    assert viral.puntuar(fuerte, "general")[0] > viral.puntuar(tranquilo, "general")[0]
    assert viral.puntuar(tranquilo, "general")[0] < 30

def test_modos_ponderan_distinto():
    senales = {"rms_medio_db": -28.0, "rms_pico_db": -6.0, "cambios_escena": 6, "duracion_s": 10}
    g = viral.puntuar(senales, "gta")[0]
    a = viral.puntuar(senales, "alofoke")[0]
    assert g != a  # las ponderaciones de modo cambian el resultado

def test_clasificacion():
    assert viral.clasificar({"rms_pico_db": -4, "rms_medio_db": -30, "cambios_escena": 6}, "gta") == "accion"
    assert viral.clasificar({"rms_pico_db": -4, "rms_medio_db": -30, "cambios_escena": 0}, "alofoke") == "discusion"

def test_buffer_protege_eventos(tmp_path):
    from buffer import BufferCircular
    import subprocess
    b = BufferCircular(tmp_path, minutos=2)
    for i in range(30):
        p = tmp_path / f"seg_{i:05d}.mp4"; p.write_bytes(b"x")
        b.registrar(str(p), i, i * 10, 10)
    b.proteger({28})
    # No se poda hasta que la película completa exista: así un fallo de unión
    # no borra los segmentos. El archivo de prueba no es una grabación real.
    (tmp_path / "grabacion_completa.mp4").write_bytes(b"x" * 8192)
    borrados = b.podar(ahora_s=300)
    assert borrados > 0
    assert (tmp_path / "seg_00028.mp4").exists()  # protegido sobrevive
    assert not (tmp_path / "seg_00000.mp4").exists()  # viejo sin proteger se poda
