"""
Backend para descargar videos de Instagram, TikTok y Facebook
usando yt-dlp.

Por qué esto es "robusto":
- yt-dlp es una librería open source mantenida activamente por una gran
  comunidad, con soporte para cientos de sitios (incluidos los 4 de acá).
  Cuando alguno de estos sitios cambia su formato interno, yt-dlp se
  actualiza (generalmente en horas o pocos días) y vos solo necesitás
  actualizar la versión de la librería (`pip install -U yt-dlp`), sin tocar
  la app Android ni el resto del backend.
- Toda la lógica "frágil" (parsear HTML/JSON de cada sitio) queda
  encapsulada acá, lejos de la app que instalás en tu celular/PC.

Novedades de esta versión:
- Fix de audio: antes se usaba format="best[ext=mp4]/best", que en muchos
  Reels de Instagram termina eligiendo el stream de SOLO VIDEO (sin audio),
  porque Instagram separa video y audio en pistas distintas. Ahora se pide
  explícitamente "mejor video + mejor audio" y se combinan con ffmpeg.
- Selección de formato y calidad: los parámetros `formato` (mp4/mp3) y
  `calidad` usan Enums de Python, lo que hace que en la documentación
  interactiva de FastAPI (/docs) aparezcan como menús desplegables.
- Soporte para TikTok y Facebook, además de Instagram.
"""

import shutil
import uuid
import tempfile
from enum import Enum
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import imageio_ffmpeg
import yt_dlp

app = FastAPI(title="IG Video Downloader")

# Permite que la app Android (y cualquier cliente) le pegue a este backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DOWNLOAD_DIR = Path(tempfile.gettempdir()) / "ig_downloads"
DOWNLOAD_DIR.mkdir(exist_ok=True)

# Render monta los "Secret Files" como SOLO LECTURA, pero yt-dlp necesita
# poder re-escribir el archivo de cookies (actualiza cookies de sesión
# mientras descarga). Por eso copiamos el secreto a una ubicación temporal
# donde sí se puede escribir, una sola vez al arrancar el servidor.
RENDER_SECRET_COOKIES = Path("/etc/secrets/cookies.txt")
LOCAL_COOKIES = Path(__file__).parent / "cookies.txt"
RUNTIME_COOKIES = Path(tempfile.gettempdir()) / "cookies_runtime.txt"

FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()

# Plataformas soportadas. yt-dlp funciona con cientos de sitios, pero acá
# limitamos explícitamente a las 4 que pediste, para dar mensajes de error
# más claros si alguien manda un link de otro sitio no soportado/probado.
DOMINIOS_SOPORTADOS = (
    "instagram.com",
    "tiktok.com",
    "facebook.com",
    "fb.watch",
)


def es_link_soportado(url: str) -> bool:
    return any(dominio in url for dominio in DOMINIOS_SOPORTADOS)


class FormatoDescarga(str, Enum):
    mp4 = "mp4"
    mp3 = "mp3"


class CalidadDescarga(str, Enum):
    mejor = "mejor"
    p1080 = "1080p"
    p720 = "720p"
    p480 = "480p"
    kbps320 = "320kbps"
    kbps192 = "192kbps"
    kbps128 = "128kbps"


# Calidades válidas según el formato elegido
CALIDADES_VALIDAS_MP4 = {CalidadDescarga.mejor, CalidadDescarga.p1080, CalidadDescarga.p720, CalidadDescarga.p480}
CALIDADES_VALIDAS_MP3 = {CalidadDescarga.mejor, CalidadDescarga.kbps320, CalidadDescarga.kbps192, CalidadDescarga.kbps128}

# Mapeo de calidad -> selector de formato de yt-dlp (para video mp4)
FORMATO_YTDLP_POR_CALIDAD_MP4 = {
    CalidadDescarga.mejor: "bv*+ba/b",
    CalidadDescarga.p1080: "bv*[height<=1080]+ba/b[height<=1080]",
    CalidadDescarga.p720: "bv*[height<=720]+ba/b[height<=720]",
    CalidadDescarga.p480: "bv*[height<=480]+ba/b[height<=480]",
}

# Mapeo de calidad -> bitrate de audio (para extracción mp3)
BITRATE_POR_CALIDAD_MP3 = {
    CalidadDescarga.mejor: "320",
    CalidadDescarga.kbps320: "320",
    CalidadDescarga.kbps192: "192",
    CalidadDescarga.kbps128: "128",
}


@app.get("/")
def health_check():
    """Endpoint simple para verificar que el servidor está vivo."""
    cookies_info = {
        "secret_file_existe": RENDER_SECRET_COOKIES.exists(),
        "copia_escribible_existe": RUNTIME_COOKIES.exists(),
        "copia_escribible_bytes": RUNTIME_COOKIES.stat().st_size if RUNTIME_COOKIES.exists() else 0,
    }
    return {
        "status": "ok",
        "service": "media-downloader",
        "yt_dlp_version": yt_dlp.version.__version__,
        "cookies": cookies_info,
    }


@app.get("/download")
def download_video(
    url: str = Query(..., description="Link del video (Instagram, TikTok o Facebook)"),
    formato: FormatoDescarga = Query(FormatoDescarga.mp4, description="Formato de salida"),
    calidad: CalidadDescarga = Query(CalidadDescarga.mejor, description="Calidad deseada"),
):
    """
    Descarga un video de Instagram, TikTok o Facebook y lo
    devuelve como archivo mp4 o mp3.

    Ejemplos:
      GET /download?url=...&formato=mp4&calidad=1080p
      GET /download?url=...&formato=mp3&calidad=192kbps
      GET /download?url=...   (usa mp4 + mejor calidad por defecto)
    """
    if not es_link_soportado(url):
        raise HTTPException(
            status_code=400,
            detail="El link no parece ser de Instagram, TikTok o Facebook",
        )

    # Validar que la combinación formato + calidad tenga sentido
    if formato == FormatoDescarga.mp4 and calidad not in CALIDADES_VALIDAS_MP4:
        raise HTTPException(
            status_code=400,
            detail=f"Calidad '{calidad.value}' no es válida para mp4. "
                   f"Opciones válidas: {[c.value for c in CALIDADES_VALIDAS_MP4]}",
        )
    if formato == FormatoDescarga.mp3 and calidad not in CALIDADES_VALIDAS_MP3:
        raise HTTPException(
            status_code=400,
            detail=f"Calidad '{calidad.value}' no es válida para mp3. "
                   f"Opciones válidas: {[c.value for c in CALIDADES_VALIDAS_MP3]}",
        )

    # Nombre de archivo único para evitar colisiones entre pedidos simultáneos
    job_id = str(uuid.uuid4())
    output_template = str(DOWNLOAD_DIR / f"{job_id}.%(ext)s")

    ydl_opts = {
        "outtmpl": output_template,
        "ffmpeg_location": FFMPEG_PATH,
        "quiet": True,
        "no_warnings": True,
        # Evita que un solo pedido cuelgue el server para siempre
        "socket_timeout": 30,
    }

    if formato == FormatoDescarga.mp4:
        # Pedimos el mejor video + el mejor audio por separado, y que
        # yt-dlp los combine (mux) usando ffmpeg. Esto asegura que el
        # archivo final SIEMPRE tenga audio, aunque Instagram entregue
        # las pistas separadas.
        ydl_opts["format"] = FORMATO_YTDLP_POR_CALIDAD_MP4[calidad]
        ydl_opts["merge_output_format"] = "mp4"
    else:
        # mp3: bajamos solo la mejor pista de audio disponible y la
        # convertimos a mp3 con el bitrate elegido.
        ydl_opts["format"] = "bestaudio/best"
        ydl_opts["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": BITRATE_POR_CALIDAD_MP3[calidad],
            }
        ]

    # Si en el arranque del servidor se preparó un cookies.txt (ver
    # `preparar_cookies_escribibles`), lo usamos. Esto ayuda quando el
    # sitio exige estar logueado (YouTube con "confirmá que no sos un bot",
    # posts/reels privados, videos de Facebook que piden login, etc.)
    if RUNTIME_COOKIES.exists():
        ydl_opts["cookiefile"] = str(RUNTIME_COOKIES)

    extension_esperada = "mp3" if formato == FormatoDescarga.mp3 else "mp4"

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            final_path = Path(filename)
            # Con postprocesamiento (mp3) o merge (mp4), la extensión final
            # puede diferir de la que reporta prepare_filename, así que
            # verificamos y ajustamos si hace falta.
            if not final_path.exists():
                alt_path = final_path.with_suffix(f".{extension_esperada}")
                if alt_path.exists():
                    final_path = alt_path

        if not final_path.exists():
            raise HTTPException(status_code=500, detail="No se pudo generar el archivo de salida")

        media_type = "audio/mpeg" if formato == FormatoDescarga.mp3 else "video/mp4"

        return FileResponse(
            path=str(final_path),
            filename=f"descarga_{job_id}.{extension_esperada}",
            media_type=media_type,
        )

    except yt_dlp.utils.DownloadError as e:
        # Este es el error más común cuando el sitio cambia algo o el
        # contenido es privado / no existe. Lo devolvemos como mensaje claro.
        raise HTTPException(status_code=422, detail=f"No se pudo descargar el archivo: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error inesperado: {str(e)}")


@app.on_event("startup")
def preparar_cookies_escribibles():
    """
    Copia el archivo de cookies a una ubicación temporal donde yt-dlp SÍ
    puede escribir (Render monta /etc/secrets como solo lectura).

    Prioridad:
      1) Render Secret File en /etc/secrets/cookies.txt (forma segura,
         nunca pasa por GitHub).
      2) Un cookies.txt junto al código (solo para pruebas locales; no
         subir esto a un repo público, expone tu sesión iniciada).
    """
    origen = None
    if RENDER_SECRET_COOKIES.exists():
        origen = RENDER_SECRET_COOKIES
    elif LOCAL_COOKIES.exists():
        origen = LOCAL_COOKIES

    if origen is not None:
        try:
            shutil.copyfile(origen, RUNTIME_COOKIES)
        except Exception:
            # Si por algún motivo falla la copia, seguimos sin cookies en
            # vez de tirar abajo el arranque del servidor.
            pass


@app.on_event("startup")
def cleanup_old_files():
    """Limpia archivos viejos al arrancar, por si quedaron de una ejecución anterior."""
    for f in DOWNLOAD_DIR.glob("*"):
        try:
            f.unlink()
        except Exception:
            pass
