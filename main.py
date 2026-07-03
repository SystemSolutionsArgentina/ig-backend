"""
Backend para descargar videos de Instagram usando yt-dlp.

Por qué esto es "robusto":
- yt-dlp es una librería open source mantenida activamente por una gran
  comunidad. Cuando Instagram cambia su formato interno, yt-dlp se actualiza
  (generalmente en horas o pocos días) y vos solo necesitás actualizar la
  versión de la librería (`pip install -U yt-dlp`), sin tocar la app Android
  ni el resto del backend.
- Toda la lógica "frágil" (parsear HTML/JSON de Instagram) queda encapsulada
  acá, lejos de la app que instalás en tu celular.
"""

import os
import uuid
import shutil
import tempfile
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
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


@app.get("/")
def health_check():
    """Endpoint simple para verificar que el servidor está vivo."""
    return {"status": "ok", "service": "ig-video-downloader"}


@app.get("/download")
def download_video(url: str = Query(..., description="Link del post/reel de Instagram")):
    """
    Descarga un video de Instagram y lo devuelve como archivo.

    Uso: GET /download?url=https://www.instagram.com/reel/XXXXX/
    """
    if "instagram.com" not in url:
        raise HTTPException(status_code=400, detail="El link no parece ser de Instagram")

    # Nombre de archivo único para evitar colisiones entre pedidos simultáneos
    job_id = str(uuid.uuid4())
    output_template = str(DOWNLOAD_DIR / f"{job_id}.%(ext)s")

    ydl_opts = {
        "outtmpl": output_template,
        # Pedimos un archivo que ya venga con video+audio combinados
        # (Instagram casi siempre lo ofrece así). Esto evita depender de
        # ffmpeg para fusionar pistas separadas, lo que hace el backend
        # más simple de instalar y desplegar.
        "format": "best[ext=mp4]/best",
        "quiet": True,
        "no_warnings": True,
        # Evita que un solo pedido cuelgue el server para siempre
        "socket_timeout": 30,
    }

    # Si existe un archivo cookies.txt (exportado desde el navegador con
    # sesión iniciada en Instagram), lo usamos. Muchos posts/reels de
    # Instagram exigen estar logueado, incluso siendo contenido público.
    cookies_path = Path(__file__).parent / "cookies.txt"
    if cookies_path.exists():
        ydl_opts["cookiefile"] = str(cookies_path)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            # Si el merge cambió la extensión a mp4, ajustamos el path
            final_path = Path(filename)
            if not final_path.exists():
                mp4_path = final_path.with_suffix(".mp4")
                if mp4_path.exists():
                    final_path = mp4_path

        if not final_path.exists():
            raise HTTPException(status_code=500, detail="No se pudo generar el archivo de video")

        return FileResponse(
            path=str(final_path),
            filename=f"instagram_{job_id}.mp4",
            media_type="video/mp4",
        )

    except yt_dlp.utils.DownloadError as e:
        # Este es el error más común cuando Instagram cambia algo o el post
        # es privado / no existe. Lo devolvemos como mensaje claro.
        raise HTTPException(status_code=422, detail=f"No se pudo descargar el video: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error inesperado: {str(e)}")


@app.on_event("startup")
def cleanup_old_files():
    """Limpia archivos viejos al arrancar, por si quedaron de una ejecución anterior."""
    for f in DOWNLOAD_DIR.glob("*"):
        try:
            f.unlink()
        except Exception:
            pass