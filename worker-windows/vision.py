"""Análisis visual: cambios de escena con el filtro scene de FFmpeg.
OpenCV se usa solo si está instalado (detección de movimiento simple); si no, se informa."""
import subprocess, re, tempfile
from pathlib import Path

def cambios_de_escena(path, umbral=0.35):
    """Cuenta cambios de escena reales decodificando con el filtro select/showinfo."""
    try:
        r = subprocess.run(["ffmpeg", "-i", str(path), "-filter:v",
                            f"select='gt(scene\\,{umbral})',showinfo", "-f", "null", "-"],
                           capture_output=True, text=True, timeout=600)
        return len(re.findall(r"showinfo.*n:\s*\d+", r.stderr))
    except Exception:
        return 0

def opencv_disponible():
    try:
        import cv2  # noqa: F401
        return True
    except Exception:
        return False

# El modelo YuNet es un ARTEFACTO DE EJECUCIÓN: no se versiona en Git (ver
# .gitignore). Si falta junto a vision.py, se descarga del repositorio
# oficial OpenCV Zoo (opencv/opencv_zoo en GitHub). Si la descarga falla
# (sin conexión, etc.), se devuelve None y la detección de rostros queda
# honestamente no disponible; el resto del análisis sigue funcionando.
YUNET_URL = ("https://github.com/opencv/opencv_zoo/raw/main/models/"
             "face_detection_yunet/face_detection_yunet_2023mar.onnx")
YUNET_MIN_BYTES = 100_000  # el modelo real pesa 232.589 bytes; menos = puntero LFS o descarga rota

def _asegurar_modelo_yunet():
    modelo = Path(__file__).with_name("yunet.onnx")
    try:
        if modelo.exists() and modelo.stat().st_size >= YUNET_MIN_BYTES:
            return modelo
        import urllib.request
        tmp = modelo.with_suffix(".onnx.tmp")
        urllib.request.urlretrieve(YUNET_URL, tmp)  # sigue redirecciones (Git LFS)
        if tmp.exists() and tmp.stat().st_size >= YUNET_MIN_BYTES:
            tmp.replace(modelo)
            return modelo
        tmp.unlink(missing_ok=True)
        return modelo if modelo.exists() else None
    except Exception:
        try:
            modelo.with_suffix(".onnx.tmp").unlink(missing_ok=True)
        except Exception:
            pass
        return modelo if (modelo.exists() and modelo.stat().st_size >= YUNET_MIN_BYTES) else None

# --- Etapa 2: rostros (Haar) y movimiento (FFmpeg) ---------------------------

def _duracion(path):
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "csv=p=0", str(path)], capture_output=True, text=True, timeout=60)
        return float(r.stdout.strip())
    except Exception:
        return 0.0

def detectar_rostros(path, segundo=None):
    """Rostros en UN frame (el central por defecto). Devuelve
    {disponible, rostros, nota}. Backends, en orden:
      1. Cascada Haar de cv2 (cv2 4.x: CascadeClassifier + XML incluido).
      2. YuNet (cv2.FaceDetectorYN) con el modelo oficial opencv_zoo
         `yunet.onnx` junto a este archivo — necesario porque opencv 5.x
         ELIMINÓ CascadeClassifier y los XML de Haar del paquete (verificado
         en esta VM con opencv-python-headless 5.0.0.93).
    Sin opencv o sin ningún backend: disponible False con el motivo exacto;
    conteo_movimiento() (FFmpeg) sigue disponible como alternativa."""
    try:
        import cv2
    except Exception as e:
        return {"disponible": False, "rostros": None,
                "nota": f"opencv no está instalado en este trabajador ({e}); conteo_movimiento() sigue disponible."}
    try:
        if segundo is None:
            segundo = max(0.0, _duracion(path) / 2.0)
        with tempfile.TemporaryDirectory() as td:
            frame = str(Path(td) / "frame.jpg")
            r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", str(segundo), "-i", str(path),
                                "-frames:v", "1", frame], capture_output=True, text=True, timeout=120)
            if r.returncode != 0 or not Path(frame).exists():
                # FFmpeg 8 no extrae frame de una imagen fija con -ss previo: reintento sin seek.
                r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(path),
                                    "-frames:v", "1", frame], capture_output=True, text=True, timeout=120)
            if r.returncode != 0 or not Path(frame).exists():
                return {"disponible": False, "rostros": None,
                        "nota": "No se pudo extraer el frame central con FFmpeg."}
            # Backend 1: Haar (solo cv2 4.x)
            if hasattr(cv2, "CascadeClassifier"):
                cascada_path = str(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml")
                if Path(cascada_path).exists():
                    gris = cv2.imread(frame, cv2.IMREAD_GRAYSCALE)
                    if gris is None:
                        return {"disponible": False, "rostros": None,
                                "nota": "Frame extraído ilegible para cv2."}
                    cascada = cv2.CascadeClassifier(cascada_path)
                    if not cascada.empty():
                        rostros = cascada.detectMultiScale(gris, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30))
                        return {"disponible": True, "rostros": int(len(rostros)),
                                "nota": f"Cascada Haar sobre el frame en {segundo:.1f} s."}
            # Backend 2: YuNet (cv2 5.x) — descarga el modelo si falta.
            modelo = _asegurar_modelo_yunet()
            if hasattr(cv2, "FaceDetectorYN_create") and modelo is not None:
                img = cv2.imread(frame)
                if img is None:
                    return {"disponible": False, "rostros": None,
                            "nota": "Frame extraído ilegible para cv2."}
                h, w = img.shape[:2]
                det = cv2.FaceDetectorYN_create(str(modelo), "", (w, h), 0.7, 0.3, 5000)
                _n, caras = det.detect(img)
                return {"disponible": True, "rostros": int(0 if caras is None else len(caras)),
                        "nota": f"YuNet (FaceDetectorYN, modelo opencv_zoo) sobre el frame en {segundo:.1f} s."}
            return {"disponible": False, "rostros": None,
                    "nota": "cv2 sin CascadeClassifier (opencv 5 eliminó Haar) y sin modelo yunet.onnx "
                            "junto a vision.py; conteo_movimiento() sigue disponible."}
    except Exception as e:
        return {"disponible": False, "rostros": None, "nota": f"detectar_rostros falló: {e}"}

def conteo_movimiento(path, fps=2, ancho=64, alto=36):
    """Movimiento SIN opencv: decodifica a gris crudo 64x36 con FFmpeg y promedia
    la diferencia absoluta entre frames consecutivos (0 = imagen estática)."""
    try:
        tam = ancho * alto
        r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path),
                            "-vf", f"fps={fps},scale={ancho}:{alto},format=gray",
                            "-f", "rawvideo", "-"], capture_output=True, timeout=600)
        datos = r.stdout
        n_frames = len(datos) // tam
        if r.returncode != 0 or n_frames < 2:
            return {"disponible": False, "movimiento": None,
                    "nota": "No se pudieron decodificar al menos 2 frames con FFmpeg."}
        total, pares = 0.0, 0
        for f in range(1, n_frames):
            a = datos[(f - 1) * tam: f * tam]
            b = datos[f * tam: (f + 1) * tam]
            total += sum(abs(x - y) for x, y in zip(a, b)) / tam
            pares += 1
        return {"disponible": True, "movimiento": round(total / pares, 3), "frames": n_frames,
                "nota": f"Diferencia media entre frames ({fps} fps, {ancho}x{alto} gris), sin opencv."}
    except Exception as e:
        return {"disponible": False, "movimiento": None, "nota": f"conteo_movimiento falló: {e}"}
