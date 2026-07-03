# Backend de descarga de videos de Instagram

## 1. Probarlo en tu computadora (opcional pero recomendado)

```bash
pip install -r requirements.txt
uvicorn main:app --reload
```

Abrí en el navegador: `http://localhost:8000/download?url=https://www.instagram.com/reel/ALGUN_LINK/`
Debería empezar a descargar el video. Si funciona acá, funciona en Render.

## 2. Subir el código a GitHub

Render se conecta a un repositorio de GitHub. Pasos:

1. Creá una cuenta en https://github.com si no tenés.
2. Creá un repositorio nuevo (puede ser privado), por ejemplo `ig-backend`.
3. Subí estos 3 archivos (`main.py`, `requirements.txt`, este `README.md`) al repositorio.
   - Más fácil: desde GitHub Desktop, o arrastrando los archivos directo en la web de GitHub ("Add file" → "Upload files").

## 3. Desplegar en Render (gratis)

1. Creá una cuenta en https://render.com (podés entrar con tu cuenta de GitHub).
2. Click en "New +" → "Web Service".
3. Elegí el repositorio `ig-backend` que subiste.
4. Configurá:
   - **Runtime**: Python 3
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn main:app --host 0.0.0.0 --port $PORT`
   - **Plan**: Free
5. Click "Create Web Service". Esperá unos minutos.
6. Cuando termine, Render te da una URL tipo:
   `https://ig-backend-xxxx.onrender.com`

Esa es la URL que va a usar la app Android.

## 4. Probar que quedó online

Abrí en el navegador:
`https://ig-backend-xxxx.onrender.com/download?url=https://www.instagram.com/reel/ALGUN_LINK/`

## ⚠️ Importante sobre el plan gratis de Render

El plan free "duerme" el servidor después de 15 minutos sin uso. La primera
petición después de estar dormido tarda ~30-50 segundos en responder (se
está "despertando"). Las siguientes son rápidas. Es normal, no es un error.

## 5. Mantenerlo actualizado (la parte que lo hace "robusto")

Cuando Instagram cambie algo y el backend empiece a fallar, el arreglo casi
siempre es:

1. Editá `requirements.txt` y poné `yt-dlp` en la última versión (mirá
   https://pypi.org/project/yt-dlp/ para la versión más nueva).
2. Subí el cambio a GitHub.
3. Render redespliega automáticamente en unos minutos.

No hace falta tocar la app Android ni volver a instalarla.
