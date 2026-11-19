"""Copia de clips en Cloudflare R2 (API compatible con S3).

Sin R2_ACCOUNT_ID, R2_BUCKET, R2_ACCESS_KEY_ID y R2_SECRET_ACCESS_KEY
no hace nada: la API sigue guardando la copia en disco. El original del
clip permanece en la PC. Este módulo no borra grabaciones completas.
"""
import hashlib
import hmac
import os
from datetime import datetime, timezone
from urllib.parse import quote

import requests

_VACIO = hashlib.sha256(b"").hexdigest()


def desde_entorno():
    account = os.environ.get("R2_ACCOUNT_ID", "").strip()
    bucket = os.environ.get("R2_BUCKET", "").strip()
    access = os.environ.get("R2_ACCESS_KEY_ID", "").strip()
    secret = os.environ.get("R2_SECRET_ACCESS_KEY", "").strip()
    if not (account and bucket and access and secret):
        return None
    return AlmacenR2(account, bucket, access, secret)


def es_uri(valor):
    return isinstance(valor, str) and valor.startswith("r2://") and "/" in valor[5:]


def clave_de_uri(valor):
    resto = valor[5:]
    bucket, _, clave = resto.partition("/")
    return bucket, clave


def clave_copia(user_id, session_id, clip_id, clase):
    """Ruta interna del bucket: un objeto por usuario, sesión, clip y tipo.

    clase es «original» o «subtitulado». No incluye grabaciones completas.
    """
    if clase not in {"original", "subtitulado"}:
        raise ValueError("Tipo de copia no permitido")
    return f"usuarios/{int(user_id or 0)}/sesiones/{int(session_id or 0)}/clips/{int(clip_id)}/{clase}.mp4"


class AlmacenR2:
    def __init__(self, account_id, bucket, access_key, secret_key):
        self.bucket = bucket
        self.access = access_key
        self.secret = secret_key
        self.host = f"{account_id}.r2.cloudflarestorage.com"

    def uri(self, clave):
        return f"r2://{self.bucket}/{clave}"

    def put(self, clave, data, content_type="application/octet-stream"):
        return self._enviar("PUT", clave, body=data, content_type=content_type)

    def abrir(self, clave, rango=None):
        extra = {"Range": rango} if rango else None
        return self._enviar("GET", clave, extra=extra, stream=True)

    def borrar(self, clave):
        return self._enviar("DELETE", clave)

    def existe(self, clave):
        resp = self._enviar("HEAD", clave)
        try:
            return resp.status_code == 200
        finally:
            resp.close()

    def _enviar(self, method, clave, body=None, content_type=None, extra=None, stream=False):
        extra = {k: v for k, v in (extra or {}).items() if v}
        payload = body if isinstance(body, (bytes, bytearray)) else b""
        payload_hash = hashlib.sha256(payload).hexdigest() if method == "PUT" else _VACIO
        ahora = datetime.now(timezone.utc)
        amz_date = ahora.strftime("%Y%m%dT%H%M%SZ")
        fecha = ahora.strftime("%Y%m%d")
        canonical_uri = "/" + quote(self.bucket, safe="") + "/" + quote(clave, safe="/")
        firmados = {
            "host": self.host,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
        }
        if content_type:
            firmados["content-type"] = content_type
        for k, v in extra.items():
            firmados[k.lower()] = v
        nombres = ";".join(sorted(firmados))
        canonical_headers = "".join(f"{k}:{firmados[k]}\n" for k in sorted(firmados))
        canonical = "\n".join([method, canonical_uri, "", canonical_headers, nombres, payload_hash])
        alcance = f"{fecha}/auto/s3/aws4_request"
        a_firmar = "\n".join([
            "AWS4-HMAC-SHA256", amz_date, alcance,
            hashlib.sha256(canonical.encode()).hexdigest(),
        ])

        def _mac(key, msg):
            return hmac.new(key, msg.encode(), hashlib.sha256).digest()

        k = _mac(("AWS4" + self.secret).encode(), fecha)
        k = _mac(k, "auto")
        k = _mac(k, "s3")
        k = _mac(k, "aws4_request")
        firma = hmac.new(k, a_firmar.encode(), hashlib.sha256).hexdigest()
        headers = {
            "Host": self.host,
            "x-amz-date": amz_date,
            "x-amz-content-sha256": payload_hash,
            "Authorization": (
                f"AWS4-HMAC-SHA256 Credential={self.access}/{alcance}, "
                f"SignedHeaders={nombres}, Signature={firma}"
            ),
        }
        if content_type:
            headers["Content-Type"] = content_type
        headers.update(extra)
        return requests.request(
            method, f"https://{self.host}{canonical_uri}",
            data=body if method == "PUT" else None,
            headers=headers, stream=stream, timeout=120,
        )
