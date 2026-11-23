"""Instancia única del motor: un solo pythonw.exe por PC.

La auditoría del trabajo #71 (2026-11-20) lo dejó diagnosticado: NADA
impedía que dos motores corrieran a la vez en la misma PC (la tarea
programada de arranque + un doble clic en start-motor.bat, o la
relanzada del actualizador cruzándose con un arranque manual). Dos
motores se pisan: reclaman los mismos trabajos de la API, escriben en
las mismas carpetas de sesión y cada uno cree ser el dueño de la
grabación.

La solución del sector NO es un archivo con el PID (el PID queda
viejo si el proceso muere de golpe y los PID se reutilizan): es un
BLOQUEO DE ARCHIVO DEL SISTEMA OPERATIVO sobre `motor.lock` en la
carpeta de datos. El bloqueo vive solo mientras el proceso vive: si
el motor se cierra o se cae, el SO lo libera solo y el siguiente
arranque entra sin limpiar nada. En Windows se usa msvcrt.locking y
en POSIX fcntl.flock; solo biblioteca estándar.

adquirir() devuelve el handle ABIERTO (hay que guardarlo toda la
vida del proceso: si se cierra o lo recoge el GC, el bloqueo se
pierde) o None si ya hay otro motor con el bloqueo.
"""
import os, time
from pathlib import Path

NOMBRE_BLOQUEO = "motor.lock"


def _intentar_bloqueo(f):
    """Un intento de bloqueo exclusivo sin esperar. True si se obtuvo."""
    try:
        if os.name == "nt":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def adquirir(carpeta, espera_s=0.0, pausa_s=0.25):
    """Toma el bloqueo de instancia única en `carpeta` (la de datos).

    Con espera_s > 0 reintenta hasta ese límite: la relanzada del
    actualizador arranca el motor nuevo mientras el viejo aún está
    saliendo, y el nuevo debe esperar a que el viejo libere en vez de
    rendirse (o peor, de convivir con él). Devuelve el handle abierto
    o None si otro motor conserva el bloqueo. Nunca lanza: un fallo
    del bloqueo no debe impedir diagnosticar desde el log."""
    ruta = Path(carpeta) / NOMBRE_BLOQUEO
    try:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        f = open(ruta, "a+b")
    except OSError:
        return None
    limite = time.time() + max(0.0, float(espera_s))
    while True:
        if _intentar_bloqueo(f):
            try:
                f.seek(0)
                f.truncate()
                f.write(f"pid={os.getpid()}\n".encode("utf-8"))
                f.flush()
            except OSError:
                pass
            return f
        if time.time() >= limite:
            try:
                f.close()
            except OSError:
                pass
            return None
        time.sleep(pausa_s)
