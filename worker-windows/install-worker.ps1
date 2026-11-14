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
# Verificacion: yt-dlp es obligatorio (resolver.py graba desde enlaces de
# plataformas: YouTube, Facebook, Twitch...). Si el import falla, el
# instalador se detiene aqui en vez de dejar un trabajador a medias.
& .\.venv\Scripts\python -c "import yt_dlp; print('yt-dlp OK', yt_dlp.version.__version__)"
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
[System.IO.File]::WriteAllText((Join-Path $dir "worker-config.json"), $config, (New-Object System.Text.UTF8Encoding($false)))
Write-Host "Configuracion guardada. Probando conexion (heartbeat)..."
& .\.venv\Scripts\python -c "import json,sys; sys.path.insert(0,'.'); from sync import ApiClient; c=json.load(open('worker-config.json', encoding='utf-8-sig')); print('Heartbeat OK' if ApiClient(c['api_url'], c['worker_token']).heartbeat() else 'SIN CONEXION: revisa URL/token o que la API este en linea')"
# Motor invisible: tarea programada que arranca el trabajador con
# pythonw (sin ventana de consola) al iniciar sesion, con reintentos.
try {
  $accionMotor = New-ScheduledTaskAction -Execute "$dir\.venv\Scripts\pythonw.exe" -Argument "worker.py --config worker-config.json" -WorkingDirectory $dir
  $disparadorMotor = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
  $ajustesMotor = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable
  Register-ScheduledTask -TaskName "JDM Viral Clips AI - Motor" -Action $accionMotor -Trigger $disparadorMotor -Settings $ajustesMotor -Force | Out-Null
  Write-Host "Tarea programada creada: JDM Viral Clips AI - Motor (arranca solo al iniciar sesion)." -ForegroundColor Green
} catch {
  Write-Host "AVISO: no se pudo crear la tarea programada del motor. Podras arrancarlo a mano con start-motor.bat." -ForegroundColor Yellow
}
# Arranque manual del motor (sin consola): start-motor.bat en esta carpeta.
$lineasBat = @("@echo off", 'cd /d "%~dp0"', 'start "" .venv\Scripts\pythonw.exe worker.py --config worker-config.json')
[System.IO.File]::WriteAllText((Join-Path $dir "start-motor.bat"), ($lineasBat -join "`r`n") + "`r`n", [System.Text.Encoding]::ASCII)
Write-Host "Arranque manual creado: start-motor.bat"
Write-Host "Listo. El motor arranca solo al iniciar sesion (tarea programada JDM Viral Clips AI - Motor). Para arrancarlo a mano ahora: ejecuta start-motor.bat en esta carpeta." -ForegroundColor Green
