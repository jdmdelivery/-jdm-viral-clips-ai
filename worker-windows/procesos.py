"""Lanzamiento de procesos externos SIN ventana de consola en Windows.

El motor corre con pythonw (sin consola propia: tarea programada y
start-motor.bat lo lanzan así a propósito). En Windows, cuando un
proceso sin consola lanza un ejecutable de consola (ffmpeg.exe,
ffprobe.exe) sin el flag CREATE_NO_WINDOW, el sistema le crea una
ventana de consola VISIBLE al hijo. Durante una grabación, el recorder
y el radar llaman a ffmpeg/ffprobe cada pocos segundos, así que esas
ventanas negras aparecían y desaparecían sin parar sobre el escritorio
(reporte de Jose, 2026-11-19, ya con el motor v1.3.1: el arreglo del
resolver funcionaba, pero ningún lanzamiento suprimía la ventana).

Regla del trabajador: TODO proceso externo (ffmpeg, ffprobe, yt-dlp,
el propio motor al relanzarse) se lanza con creationflags=SIN_VENTANA
(o flags() si además lleva otros flags, p. ej. baja prioridad).
Fuera de Windows el valor es 0 y no cambia nada.

Solo biblioteca estándar.
"""
import os

# CREATE_NO_WINDOW (Windows): el proceso hijo no recibe consola.
SIN_VENTANA = 0x08000000 if os.name == "nt" else 0


def flags(extra=0):
    """creationflags para subprocess: sin ventana, combinada con los
    flags extra que pida el llamador. Fuera de Windows devuelve 0
    (creationflags distinto de 0 no es válido en POSIX)."""
    if os.name == "nt":
        return SIN_VENTANA | extra
    return 0
