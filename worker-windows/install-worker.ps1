# Instalador del trabajador JDM Viral Clips AI para Windows
# Uso: clic derecho -> Ejecutar con PowerShell (o: powershell -ExecutionPolicy Bypass -File install-worker.ps1)
$ErrorActionPreference = "Stop"
Write-Host "=== JDM VIRAL CLIPS AI - Instalador del trabajador ===" -ForegroundColor Magenta
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { Write-Host "FALTA Python 3.10+ : instalalo desde https://www.python.org/downloads/ y vuelve a ejecutar." -ForegroundColor Red; exit 1 }
python --version
$ff = Get-Command ffmpeg -ErrorAction SilentlyContinue
if (-not $ff) { Write-Host "FALTA FFmpeg: instalalo con 'winget install ffmpeg' o desde https://ffmpeg.org/download.html y vuelve a ejecutar." -ForegroundColor Red; exit 1 }
ffmpeg -version | Select-Object -First 1
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $dir
python -m venv .venv
& .\.venv\Scripts\pip install --upgrade pip
& .\.venv\Scripts\pip install -r requirements.txt
Write-Host "Opcional (transcripcion local): .venv\Scripts\pip install faster-whisper"
# Modelo YuNet (deteccion de rostros, OpenCV Zoo oficial): es un artefacto de
# ejecucion que NO se versiona en Git; se descarga aqui si falta.
$yunet = Join-Path $dir "yunet.onnx"
if ((-not (Test-Path $yunet)) -or ((Get-Item $yunet).Length -lt 100000)) {
  Write-Host "Descargando modelo YuNet desde el OpenCV Zoo oficial..."
  try { Invoke-WebRequest -Uri "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx" -OutFile $yunet }
  catch { Write-Host "AVISO: no se pudo descargar el modelo YuNet (conexion?). La deteccion de rostros quedara no disponible; el resto del trabajador funciona igual." -ForegroundColor Yellow }
}
$api = Read-Host "URL de la API (ej.: https://jdm-viral-clips-ai.onrender.com o http://localhost:8077)"
$tok = Read-Host "Token del trabajador (se obtiene en el panel: Administracion -> Registrar trabajador)"
$base = Join-Path $env:USERPROFILE "JDMViralClips"
New-Item -ItemType Directory -Force -Path "$base\grabaciones", "$base\clips" | Out-Null
$config = @{ api_url = $api; worker_token = $tok; carpeta_grabaciones = "$base\grabaciones"; carpeta_clips = "$base\clips"; buffer_min = 5; pre_s = 20; post_s = 15; cola_offline = "$base\cola-offline.json" } | ConvertTo-Json
$config | Out-File -Encoding utf8 "worker-config.json"
Write-Host "Configuracion guardada. Probando conexion (heartbeat)..."
& .\.venv\Scripts\python -c "import json,sys; sys.path.insert(0,'.'); from sync import ApiClient; c=json.load(open('worker-config.json')); print('Heartbeat OK' if ApiClient(c['api_url'], c['worker_token']).heartbeat() else 'SIN CONEXION: revisa URL/token o que la API este en linea')"
$auto = Read-Host "Iniciar el trabajador automaticamente con Windows? (s/n)"
if ($auto -eq "s") {
  $lnk = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\JDMViralWorker.bat"
  "@echo off`r`ncd /d `"$dir`"`r`n.venv\Scripts\python worker.py --config worker-config.json" | Out-File -Encoding ascii $lnk
  Write-Host "Inicio automatico creado: $lnk"
}
Write-Host "Listo. Para arrancar ahora: .venv\Scripts\python worker.py --config worker-config.json" -ForegroundColor Green
