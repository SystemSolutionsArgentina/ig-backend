"""
Backend para descargar videos de Instagram, YouTube, Facebook, TikTok y
cientos de sitios más, usando yt-dlp. Pensado para la app de Android
(y cualquier otro cliente que le quiera pegar).

Endpoints:
- GET /info?url=...            -> metadata liviana (título) sin descargar
- GET /download?url=...&formato=MP4|MP3&calidad=...  -> descarga el archivo

Por qué esto es "robusto":
- yt-dlp es mantenido activamente por una gran comunidad y soporta
  cientos de sitios. Cuando alguno cambia su formato interno, alcanza con
  actualizar la librería (`pip install -U yt-dlp`), sin tocar la app.
"""

import uuid
import shutil
import tempfile
import urllib.parse
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import yt_dlp

app = FastAPI(title="Video Downloader Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DOWNLOAD_DIR = Path(tempfile.gettempdir()) / "video_downloads"
DOWNLOAD_DIR.mkdir(exist_ok=True)

CALIDADES_MP3 = {
    "La mejor calidad": "0",
    "320 kbps": "320",
    "192 kbps": "192",
    "128 kbps": "128",
}


def _es_url_valida(url):
    return url.startswith("http://") or url.startswith("https://")


def _ruta_cookies():
    """
    Busca cookies.txt en dos ubicaciones posibles:
    - Junto al código (funciona en despliegues sin Docker).
    - /etc/secrets/cookies.txt (donde Render coloca los "Secret Files" en
      despliegues Docker, que es el caso de este backend).

    Si lo encuentra en /etc/secrets (de solo lectura), lo copia a una
    carpeta temporal con permisos de escritura: yt-dlp a veces intenta
    re-guardar el archivo de cookies actualizado después de usarlo, y eso
    falla si el archivo original es de solo lectura.
    """
    local = Path(__file__).parent / "cookies.txt"
    if local.exists():
        return local

    secreto = Path("/etc/secrets/cookies.txt")
    if secreto.exists():
        copia = Path(tempfile.gettempdir()) / "cookies_copia.txt"
        try:
            if not copia.exists() or copia.stat().st_mtime < secreto.stat().st_mtime:
                shutil.copy(str(secreto), str(copia))
            return copia
        except Exception:
            return secreto  # si algo falla al copiar, al menos intentamos con el original

    return None


def _opciones_base(cookies_path):
    opts = {
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 30,
        "noplaylist": True,
        "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
    }
    if cookies_path is not None:
        opts["cookiefile"] = str(cookies_path)
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        opts["ffmpeg_location"] = ffmpeg
    return opts, (ffmpeg is not None)


def _obtener_titulo(info):
    titulo = (info.get("title") or "").strip()
    if not titulo or titulo.lower().startswith("video by"):
        descripcion = (info.get("description") or "").strip()
        if descripcion:
            titulo = descripcion
    if not titulo:
        titulo = "video"
    return titulo.split("\n")[0].strip()[:150]


@app.get("/")
def health_check():
    return {"status": "ok", "service": "video-downloader-backend"}


@app.get("/info")
def obtener_info(url: str = Query(...)):
    """Devuelve solo el título, sin descargar nada (para sugerir el nombre
    de archivo antes de arrancar la descarga real)."""
    if not _es_url_valida(url):
        raise HTTPException(status_code=400, detail="URL inválida")

    cookies_path = _ruta_cookies()
    opts, _ = _opciones_base(cookies_path)
    opts["skip_download"] = True

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        return {"titulo": _obtener_titulo(info)}
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"No se pudo obtener información: {str(e)}")


@app.get("/download")
def download_video(
    url: str = Query(...),
    formato: str = Query("MP4", description="MP4 o MP3"),
    calidad: str = Query("La mejor (recomendado)"),
):
    if not _es_url_valida(url):
        raise HTTPException(status_code=400, detail="URL inválida")

    cookies_path = _ruta_cookies()
    opts, hay_ffmpeg = _opciones_base(cookies_path)

    if formato == "MP3" and not hay_ffmpeg:
        raise HTTPException(
            status_code=500,
            detail="El servidor no tiene ffmpeg disponible, no se puede convertir a MP3",
        )

    job_id = str(uuid.uuid4())
    output_template = str(DOWNLOAD_DIR / f"{job_id}.%(ext)s")
    opts["outtmpl"] = output_template

    if formato == "MP3":
        opts["format"] = "bestaudio/best"
        opts["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": CALIDADES_MP3.get(calidad, "192"),
            }
        ]
        extension_esperada = ".mp3"
    else:
        if calidad == "La mejor (recomendado)":
            if hay_ffmpeg:
                opts["format"] = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
                opts["merge_output_format"] = "mp4"
            else:
                opts["format"] = "best[ext=mp4]/best"
        else:
            altura = calidad.replace("p", "")
            if hay_ffmpeg:
                opts["format"] = (
                    f"bestvideo[height<={altura}][ext=mp4]+bestaudio[ext=m4a]/"
                    f"best[height<={altura}][ext=mp4]/best[height<={altura}]/"
                    f"best[ext=mp4]/best"
                )
                opts["merge_output_format"] = "mp4"
            else:
                opts["format"] = (
                    f"best[height<={altura}][ext=mp4]/best[height<={altura}]/"
                    f"best[ext=mp4]/best"
                )
        extension_esperada = ".mp4"

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)

        candidatos = [
            f for f in DOWNLOAD_DIR.glob(f"{job_id}.*") if f.suffix not in (".part", ".ytdl")
        ]
        if not candidatos:
            raise HTTPException(status_code=500, detail="No se generó el archivo descargado")
        archivo_final = candidatos[0]

        # Calculamos la etiqueta de calidad real (igual que en la versión
        # de Windows), para que la app pueda armar el nombre del archivo.
        if formato == "MP3":
            if calidad == "La mejor calidad":
                abr = (info or {}).get("abr")
                etiqueta_calidad = f"{int(round(abr))}kbps" if abr else "mejorcalidad"
            else:
                etiqueta_calidad = calidad.replace(" ", "")
        else:
            if calidad == "La mejor (recomendado)":
                altura = (info or {}).get("height")
                etiqueta_calidad = f"{altura}p" if altura else "mejorcalidad"
            else:
                etiqueta_calidad = calidad

        titulo = _obtener_titulo(info)
        media_type = "audio/mpeg" if archivo_final.suffix == ".mp3" else "video/mp4"

        response = FileResponse(
            path=str(archivo_final),
            filename=f"{job_id}{archivo_final.suffix}",
            media_type=media_type,
        )
        # Headers custom para que la app arme el nombre final del archivo.
        # Los HTTP headers no soportan caracteres no-ASCII directo, por eso
        # van codificados con quote() y la app los decodifica al leerlos.
        response.headers["X-Titulo"] = urllib.parse.quote(titulo)
        response.headers["X-Calidad"] = urllib.parse.quote(etiqueta_calidad)
        response.headers["X-Extension-Real"] = archivo_final.suffix
        return response

    except HTTPException:
        raise
    except yt_dlp.utils.DownloadError as e:
        raise HTTPException(status_code=422, detail=f"No se pudo descargar el video: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error inesperado: {str(e)}")


@app.on_event("startup")
def cleanup_old_files():
    for f in DOWNLOAD_DIR.glob("*"):
        try:
            f.unlink()
        except Exception:
            pass
