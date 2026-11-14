"""Adaptadores sociales por plataforma (Etapa 4) — OAuth y publicación REALES.

URLs oficiales:
- TikTok:    authorize https://www.tiktok.com/v2/auth/authorize/
             token     https://open.tiktokapis.com/v2/oauth/token/
- Meta (Facebook/Instagram):
             authorize https://www.facebook.com/v19.0/dialog/oauth
             token     https://graph.facebook.com/v19.0/oauth/access_token
- YouTube/Google:
             authorize https://accounts.google.com/o/oauth2/v2/auth
             token     https://oauth2.googleapis.com/token

Credenciales SOLO desde variables de entorno (nunca en código ni en BD):
  TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET
  META_APP_ID / META_APP_SECRET
  YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET
  JDM_PUBLIC_URL (base para redirect_uri; defecto http://127.0.0.1:8077)
  JDM_ENCRYPTION_KEY (clave Fernet; sin ella NO se almacena ningún token)

Honestidad: los métodos publish hacen la llamada real a la API oficial y solo
devuelven ok=True con external_id cuando la plataforma CONFIRMA el éxito.
Sin credenciales, sin token o sin confirmación, devuelven ok=False + motivo.
"""
import math
import os
from urllib.parse import urlencode

import requests

try:
    from cryptography.fernet import Fernet
except Exception:  # pragma: no cover - cryptography se instala vía requirements
    Fernet = None

PLATAFORMAS = ("tiktok", "facebook", "instagram", "youtube")

_CONFIG = {
    "tiktok": {
        "authorize_url": "https://www.tiktok.com/v2/auth/authorize/",
        "token_url": "https://open.tiktokapis.com/v2/oauth/token/",
        "id_env": "TIKTOK_CLIENT_KEY",
        "secret_env": "TIKTOK_CLIENT_SECRET",
        "scope": "user.info.basic,video.publish,video.upload",
    },
    "facebook": {
        "authorize_url": "https://www.facebook.com/v19.0/dialog/oauth",
        "token_url": "https://graph.facebook.com/v19.0/oauth/access_token",
        "id_env": "META_APP_ID",
        "secret_env": "META_APP_SECRET",
        "scope": "pages_show_list,pages_read_engagement,pages_manage_posts,publish_video",
    },
    "instagram": {
        "authorize_url": "https://www.facebook.com/v19.0/dialog/oauth",
        "token_url": "https://graph.facebook.com/v19.0/oauth/access_token",
        "id_env": "META_APP_ID",
        "secret_env": "META_APP_SECRET",
        "scope": "instagram_basic,instagram_content_publish,pages_show_list",
    },
    "youtube": {
        "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "id_env": "YOUTUBE_CLIENT_ID",
        "secret_env": "YOUTUBE_CLIENT_SECRET",
        "scope": "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly",
    },
}


def redirect_base() -> str:
    return os.environ.get("JDM_PUBLIC_URL", "http://127.0.0.1:8077").rstrip("/")


def redirect_uri(plataforma: str) -> str:
    return f"{redirect_base()}/auth/{plataforma}/callback"


def credenciales(plataforma: str):
    """(client_id, client_secret) desde el entorno; (None, None) si faltan."""
    cfg = _CONFIG.get(plataforma)
    if not cfg:
        return None, None
    cid = os.environ.get(cfg["id_env"]) or None
    secret = os.environ.get(cfg["secret_env"]) or None
    return cid, secret


def oauth_configurado(plataforma: str) -> bool:
    cid, secret = credenciales(plataforma)
    return bool(cid and secret)


def authorize_url(plataforma: str, state: str) -> str:
    cfg = _CONFIG[plataforma]
    cid, _ = credenciales(plataforma)
    uri = redirect_uri(plataforma)
    if plataforma == "tiktok":
        params = {"client_key": cid, "scope": cfg["scope"], "response_type": "code",
                  "redirect_uri": uri, "state": state}
    elif plataforma in ("facebook", "instagram"):
        params = {"client_id": cid, "redirect_uri": uri, "state": state,
                  "scope": cfg["scope"], "response_type": "code"}
    else:  # youtube / google
        params = {"client_id": cid, "redirect_uri": uri, "response_type": "code",
                  "scope": cfg["scope"], "access_type": "offline", "prompt": "consent",
                  "state": state}
    return f"{cfg['authorize_url']}?{urlencode(params)}"


def exchange_code(plataforma: str, code: str):
    """Intercambia el código por un access_token contra el endpoint OFICIAL.
    Devuelve (token, None) o (None, motivo_sin_secretos)."""
    cid, secret = credenciales(plataforma)
    if not (cid and secret):
        return None, "faltan las credenciales OAuth de la plataforma en el entorno"
    cfg = _CONFIG[plataforma]
    uri = redirect_uri(plataforma)
    try:
        if plataforma == "tiktok":
            r = requests.post(cfg["token_url"], json={
                "client_key": cid, "client_secret": secret, "code": code,
                "grant_type": "authorization_code", "redirect_uri": uri}, timeout=30)
        elif plataforma in ("facebook", "instagram"):
            r = requests.get(cfg["token_url"], params={
                "client_id": cid, "client_secret": secret, "redirect_uri": uri,
                "code": code}, timeout=30)
        else:
            r = requests.post(cfg["token_url"], data={
                "code": code, "client_id": cid, "client_secret": secret,
                "redirect_uri": uri, "grant_type": "authorization_code"}, timeout=30)
    except requests.RequestException:
        return None, "error de red al contactar el endpoint oficial de tokens"
    if r.status_code != 200:
        return None, f"la plataforma rechazó el intercambio (HTTP {r.status_code})"
    try:
        token = r.json().get("access_token")
    except ValueError:
        return None, "la plataforma devolvió una respuesta no válida"
    if not token:
        return None, "la plataforma no devolvió access_token"
    return token, None


# ---------- Cifrado de tokens (Fernet) ----------
def _fernet():
    key = os.environ.get("JDM_ENCRYPTION_KEY")
    if not key or Fernet is None:
        return None
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except Exception:
        return None


def cifrado_disponible() -> bool:
    return _fernet() is not None


def cifrar_token(token: str):
    f = _fernet()
    return f.encrypt(token.encode()).decode() if f else None


def descifrar_token(cifrado: str):
    f = _fernet()
    if not f or not cifrado:
        return None
    try:
        return f.decrypt(cifrado.encode()).decode()
    except Exception:
        return None


# ---------- Publicación real por plataforma ----------
class AdaptadorBase:
    plataforma = ""

    def publish(self, access_token, file_path, title, descripcion):
        """Solo ok=True si la plataforma confirma. Nunca finge éxito."""
        if not access_token:
            return {"ok": False, "motivo": "La cuenta no tiene un token de acceso válido."}
        if not oauth_configurado(self.plataforma):
            return {"ok": False, "motivo": "Faltan las credenciales OAuth de la app de la plataforma."}
        if not file_path or not os.path.exists(file_path):
            return {"ok": False, "motivo": "El archivo del clip no está accesible en este servidor; "
                                           "la subida directa a la plataforma requiere el archivo."}
        try:
            return self._publish(access_token, file_path, title or "", descripcion or "")
        except requests.RequestException:
            return {"ok": False, "motivo": "Error de red al contactar la API oficial de la plataforma."}

    def _publish(self, access_token, file_path, title, descripcion):  # pragma: no cover
        return {"ok": False, "motivo": "Adaptador sin implementar."}


class AdaptadorYouTube(AdaptadorBase):
    plataforma = "youtube"

    def _publish(self, access_token, file_path, title, descripcion):
        init = requests.post(
            "https://www.googleapis.com/upload/youtube/v3/videos",
            params={"uploadType": "resumable", "part": "snippet,status"},
            headers={"Authorization": f"Bearer {access_token}",
                     "Content-Type": "application/json; charset=UTF-8",
                     "X-Upload-Content-Type": "video/mp4"},
            json={"snippet": {"title": title[:100], "description": descripcion},
                  "status": {"privacyStatus": "public"}},
            timeout=60)
        if init.status_code not in (200, 201):
            return {"ok": False, "motivo": f"YouTube rechazó el inicio de la subida (HTTP {init.status_code})."}
        loc = init.headers.get("Location")
        if not loc:
            return {"ok": False, "motivo": "YouTube no devolvió URL de subida."}
        with open(file_path, "rb") as fh:
            data = fh.read()
        up = requests.put(loc, headers={"Content-Type": "video/mp4"}, data=data, timeout=600)
        if up.status_code in (200, 201):
            vid = up.json().get("id")
            if vid:
                return {"ok": True, "external_id": str(vid)}
        return {"ok": False, "motivo": f"YouTube no confirmó la subida (HTTP {up.status_code})."}


class AdaptadorFacebook(AdaptadorBase):
    plataforma = "facebook"

    def _publish(self, access_token, file_path, title, descripcion):
        with open(file_path, "rb") as fh:
            r = requests.post(
                "https://graph.facebook.com/v19.0/me/videos",
                data={"access_token": access_token, "title": title, "description": descripcion},
                files={"source": fh}, timeout=600)
        if r.status_code == 200:
            vid = r.json().get("id")
            if vid:
                return {"ok": True, "external_id": str(vid)}
        return {"ok": False, "motivo": f"Facebook no confirmó la publicación (HTTP {r.status_code})."}


class AdaptadorInstagram(AdaptadorBase):
    plataforma = "instagram"

    def _publish(self, access_token, file_path, title, descripcion):
        # La API oficial de Instagram exige un ig_user_id y una video_url PÚBLICA
        # para crear el contenedor; un archivo local de este servidor no basta.
        return {"ok": False, "motivo": "Instagram exige crear el contenedor con una URL de video "
                                        "pública y el ID de la cuenta IG vinculada; este clip vive "
                                        "como archivo local, así que la API no puede aceptarlo todavía."}


class AdaptadorTikTok(AdaptadorBase):
    plataforma = "tiktok"

    def _publish(self, access_token, file_path, title, descripcion):
        size = os.path.getsize(file_path)
        chunk = min(size, 10_000_000) or size
        total = max(1, math.ceil(size / chunk)) if size else 1
        init = requests.post(
            "https://open.tiktokapis.com/v2/post/publish/video/init/",
            headers={"Authorization": f"Bearer {access_token}",
                     "Content-Type": "application/json; charset=UTF-8"},
            json={"post_info": {"title": (title or "")[:2200], "privacy_level": "PUBLIC_TO_EVERYONE"},
                  "source_info": {"source": "FILE_UPLOAD", "video_size": size,
                                  "chunk_size": chunk, "total_chunk_count": total}},
            timeout=60)
        if init.status_code != 200:
            return {"ok": False, "motivo": f"TikTok rechazó el inicio de la publicación (HTTP {init.status_code})."}
        data = init.json().get("data", {})
        publish_id, upload_url = data.get("publish_id"), data.get("upload_url")
        if not publish_id or not upload_url:
            return {"ok": False, "motivo": "TikTok no devolvió publish_id/upload_url."}
        with open(file_path, "rb") as fh:
            contenido = fh.read()
        up = requests.put(upload_url, headers={
            "Content-Type": "video/mp4",
            "Content-Range": f"bytes 0-{size - 1}/{size}"}, data=contenido, timeout=600)
        if up.status_code not in (200, 201, 204):
            return {"ok": False, "motivo": f"TikTok no aceptó la subida del video (HTTP {up.status_code})."}
        estado = requests.post(
            "https://open.tiktokapis.com/v2/post/publish/status/fetch/",
            headers={"Authorization": f"Bearer {access_token}",
                     "Content-Type": "application/json; charset=UTF-8"},
            json={"publish_id": publish_id}, timeout=60)
        status = estado.json().get("data", {}).get("status") if estado.status_code == 200 else None
        if status == "PUBLISH_COMPLETE":
            return {"ok": True, "external_id": publish_id}
        return {"ok": False, "motivo": f"TikTok aún no confirma la publicación (estado: {status or 'desconocido'})."}


_ADAPTADORES = {"tiktok": AdaptadorTikTok, "facebook": AdaptadorFacebook,
                "instagram": AdaptadorInstagram, "youtube": AdaptadorYouTube}


def get_adapter(plataforma: str):
    cls = _ADAPTADORES.get(plataforma)
    return cls() if cls else None
