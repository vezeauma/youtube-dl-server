import sys
import os
import subprocess
import logging
import shutil
import unicodedata
import re
from datetime import datetime

from starlette.status import HTTP_303_SEE_OTHER
from starlette.applications import Starlette
from starlette.config import Config
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import Route
from starlette.templating import Jinja2Templates
from starlette.background import BackgroundTask
from starlette.middleware import Middleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import HTMLResponse
from starlette.responses import FileResponse

processed = set()
app = Bottle()

# Logging configuration
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

templates = Jinja2Templates(directory="templates")
config = Config(".env")

TMP_DIR="/tmp/youtube-dl"
FINAL_DIR="/youtube-dl"
SECRET_KEY = config("SECRET_KEY", cast=str, default="ThisIsASecret")
LOGIN_USERNAME = config("LOGIN_USERNAME", cast=str, default="admin")
LOGIN_PASSWORD = config("LOGIN_PASSWORD", cast=str, default="password")


SUPPORTED_FORMATS = {
    "mp3": {
        "extension": ".mp3",
        "format": "bestaudio/best",
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "0",
            },
            {"key": "FFmpegMetadata"},
        ],
        "merge_output_format": None,
    },
    "mp4_720": {
        "extension": ".mp4",
        "format": "bestvideo[ext=mp4][height<=720][fps<=30]+bestaudio[ext=m4a]/best[ext=mp4][height<=720][fps<=30]",
        "postprocessors": [
            {
                "key": "FFmpegVideoConvertor",
                "preferedformat": "mp4",
            }
        ],
        "merge_output_format": "mp4",
        "audio_multistreams": True,
        "video_multistreams": True,
    },
    "mp4_full": {
        "extension": ".mp4",
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]",
        "postprocessors": [
            {
                "key": "FFmpegVideoConvertor",
                "preferedformat": "mp4",
            }
        ],
        "merge_output_format": "mp4",
    }
}


if os.path.exists(TMP_DIR):
    for filename in os.listdir(TMP_DIR):
        file_path = os.path.join(TMP_DIR, filename)
        try:
            if os.path.isfile(file_path) or os.path.islink(file_path):
                os.unlink(file_path)  # Delete file or link
            elif os.path.isdir(file_path):
                shutil.rmtree(file_path)  # delete folders recursives
        except Exception as e:
            logger.error(f"Erreur en supprimant {file_path} : {e}")
else:
    os.makedirs(TMP_DIR, exist_ok=True)

app_defaults = {
    "YDL_FORMAT": config("YDL_FORMAT", cast=str, default="bestvideo+bestaudio/best"),
    "YDL_EXTRACT_AUDIO_FORMAT": config("YDL_EXTRACT_AUDIO_FORMAT", default=None),
    "YDL_EXTRACT_AUDIO_QUALITY": config("YDL_EXTRACT_AUDIO_QUALITY", cast=str, default="0"),
    "YDL_RECODE_VIDEO_FORMAT": config("YDL_RECODE_VIDEO_FORMAT", default=None),
    "YDL_OUTPUT_TEMPLATE": config(
        "YDL_OUTPUT_TEMPLATE",
        cast=str,
        default=os.path.join(TMP_DIR, "%(title).200s [%(id)s].%(ext)s"),
    ),
    "YDL_ARCHIVE_FILE": config("YDL_ARCHIVE_FILE", default=None),
    "YDL_UPDATE_TIME": config("YDL_UPDATE_TIME", cast=bool, default=True),
}

async def serve_file(request):
    if not request.session.get("logged_in"):
        return RedirectResponse(url="/youtube-dl/login")

    filename = request.path_params["filename"]
    filepath = os.path.join(FINAL_DIR, filename)

    if os.path.exists(filepath):
        return FileResponse(filepath, filename=filename)
    else:
        return HTMLResponse(content="Fichier non trouvé", status_code=404)

async def file_list(request):
    if not request.session.get("logged_in"):
        return RedirectResponse(url="/youtube-dl/login")

    files = []
    for filename in os.listdir(FINAL_DIR):
        filepath = os.path.join(FINAL_DIR, filename)
        if os.path.isfile(filepath):
            mod_time = os.path.getmtime(filepath)
            files.append({
                "name": filename,
                "modified": datetime.fromtimestamp(mod_time).strftime("%Y-%m-%d %H:%M:%S")
            })
    

    return templates.TemplateResponse("file_list.html", {"request": request, "files": files})

async def delete_files(request):
    if not request.session.get("logged_in"):
        return RedirectResponse(url="/youtube-dl/login")

    form = await request.form()
    files_to_delete = form.getlist("files")

    for filename in files_to_delete:
        filepath = os.path.join(FINAL_DIR, filename)
        if os.path.exists(filepath):
            os.remove(filepath)

    return RedirectResponse(url="/youtube-dl/list", status_code=HTTP_303_SEE_OTHER)


async def login_page(request):
    return templates.TemplateResponse("login.html", {"request": request})

async def login_action(request):
    form = await request.form()
    username = form.get("username")
    password = form.get("password")

    if username == LOGIN_USERNAME and password == LOGIN_PASSWORD:  # change ces valeurs bien sûr
        request.session["logged_in"] = True
        return RedirectResponse(url="/youtube-dl", status_code=HTTP_303_SEE_OTHER)
    else:
        return HTMLResponse(content="Identifiants incorrects", status_code=401)


async def dl_queue_list(request):
    if not request.session.get("logged_in"):
        return RedirectResponse(url="/youtube-dl/login")
    return templates.TemplateResponse(
        "index.html", {"request": request, "ytdlp_version": version.__version__}
    )

async def redirect(request):
    return RedirectResponse(url="/youtube-dl")

async def q_put(request):
    if not request.session.get("logged_in"):
        return RedirectResponse(url="/youtube-dl/login")
    form = await request.form()
    url = form.get("url").strip()
    ui = form.get("ui")
    options = {"format": form.get("format")}

@app.route('/youtube-dl/q', method='POST')
def q_put():
    url = request.forms.get("url")
    audio_only = request.forms.get("audio-only") == 'on'
    if not url:
        return { "success" : False, "error" : "/q called without a 'url' query param" }
    parsed_url = urlparse(url)
    qparams = dict(parse_qsl(parsed_url.query))
    if 'list' in qparams:
        # This means that the video is part of a playlist:
        # we need to remove the 'list' query param,
        # otherwise youtube-dl will download the entire playlist.
        del qparams['list']
        url = urlunparse(parsed_url._replace(query=urlencode(qparams)))
    dl_q.put((url, audio_only))
    print("Added url " + url + " to the download queue")
    if audio_only and DEST_DIR.endswith('/static') and 'v' in qparams:
        return template('processing', url=quote(url, safe=''), generated_file='{}.mp3'.format(qparams['v']))
    return { "success" : True, "url" : url }

def dl_worker():
    while not done:
        url, audio_only = dl_q.get()
        download(url, audio_only)
        dl_q.task_done()

async def update_route(request):
    if not request.session.get("logged_in"):
        return RedirectResponse(url="/youtube-dl/login")
    task = BackgroundTask(update)

async def delete_files(request):
    if not request.session.get("logged_in"):
        return RedirectResponse(url="/youtube-dl/login")

# 🔤 Remove unwanted accents and characters
def remove_accents(texte):
    return ''.join(
        c for c in unicodedata.normalize('NFD', texte)
        if unicodedata.category(c) != 'Mn'
    )

def cleanup_filename(nom):
    base, ext = os.path.splitext(nom)
    base = remove_accents(base)
    # Allow: letters, numbers, space, -, _, &, (), [], comma, and . (uppercase too)
    base_cleans = re.sub(r'[^a-zA-Z0-9 \-\_\&\[\]\(\)\,\.]', '_', base)
    return f"{base_cleans}{ext}"

def update():
    try:
        output = subprocess.check_output(
            [sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"]
        )
        message = output.decode("utf-8")
        logger.info(message)
        logger.warning("⚠️ yt-dlp a été mis à jour. Veuillez redémarrer le conteneur pour appliquer les changements.")
    except subprocess.CalledProcessError as e:
        logger.error(f"❌ Erreur lors de la mise à jour : {e.output}")

def get_ydl_options(request_options):
    # return options
    requested_format = request_options.get("format", "mp4_full")
    format_config = SUPPORTED_FORMATS.get(requested_format)

    if not format_config:
        raise ValueError(f"Format non supporté : {requested_format}")

    options = {
        "format": format_config["format"],
        "postprocessors": format_config.get("postprocessors", []),
        "outtmpl": app_defaults["YDL_OUTPUT_TEMPLATE"],
        "download_archive": app_defaults["YDL_ARCHIVE_FILE"],
        "updatetime": app_defaults["YDL_UPDATE_TIME"],
        "addmetadata": True,
        "merge_output_format": format_config.get("merge_output_format"),
        "verbose": True,
        "ignoreerrors": True,
    }

    if format_config.get("audio_multistreams"):
        options["audio_multistreams"] = True
    if format_config.get("video_multistreams"):
        options["video_multistreams"] = True

    if request_options.get("custom_order", False):
        options["postprocessor_args"] = {
            'ffmpeg': ['-map', '0:a', '-map', '1:v']
        }
    return options

def download(url, request_options):
    try:
        format_requested = request_options.get("format")
        with YoutubeDL(get_ydl_options(request_options)) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                logger.error(f"❌ Could not extract info for {url}")
                return

            ydl.download([url])

            # Manage videos individually (playlist or single video)
            entries = info.get("entries", [info])

            for video_info in entries:
                if not video_info:
                    continue
                extension = SUPPORTED_FORMATS.get(format_requested, {}).get("extension", "")
                filename_tmp_full = ydl.prepare_filename(video_info)
                filename_tmp_no_ext = os.path.splitext(filename_tmp_full)[0]
                temp_filename=f"{filename_tmp_no_ext}{extension}"
                logger.info(f"TEMP FILE: {temp_filename}")
                filename_only = os.path.basename(temp_filename)
                filename = os.path.join(os.path.dirname(temp_filename), cleanup_filename(filename_only))

                # We move the temporary file if necessary
                if temp_filename != filename and os.path.exists(temp_filename):
                    shutil.move(temp_filename, filename)
                logger.info(f"FILES: {filename}")

                if os.path.exists(filename) and format_requested == "mp4_720":
                    #base, ext = os.path.splitext(filename)
                    temp_filename = f"{filename_tmp_no_ext}_serato{extension}"


                    handbrake_cmd = [
                        "HandBrakeCLI",
                        "-Z", "Fast 720p30",
                        "--verbose=1",
                        "-i", filename,
                        "-o", temp_filename,
                    ]
                    headbrake_result = subprocess.run(handbrake_cmd, text=True)
                    if headbrake_result.returncode == 0:
                        logger.info("✅ Metadata added successfully")
                        os.remove(filename)
                        os.rename(temp_filename, filename)
                        logger.info(f"✅ Original file replaced by Serato-compatible version")
                    else:
                        logger.error(f"❌ Failed to add metadata : {headbrake_result.stderr}")

                # Move to final folder
                if os.path.exists(filename):
                    final_path = os.path.join(FINAL_DIR, os.path.basename(filename))
                    shutil.move(filename, final_path)
                    logger.info(f"✅ Copied to : {final_path}")
                else:
                    logger.warning(f"❌ File not found, skipping final move: {filename}")

    except Exception as e:
        logger.error(f"❌ Download or merge failed for {url}: {e}")

routes = [
    Route("/", endpoint=redirect),
    Route("/youtube-dl/login", endpoint=login_page, methods=["GET"]),
    Route("/youtube-dl/login", endpoint=login_action, methods=["POST"]),
    Route("/youtube-dl", endpoint=dl_queue_list),
    Route("/youtube-dl/q", endpoint=q_put, methods=["POST"]),
    Route("/youtube-dl/update", endpoint=update_route, methods=["PUT"]),
    Route("/youtube-dl/list", endpoint=file_list),
    Route("/youtube-dl/delete", endpoint=delete_files, methods=["POST"]),
    Route("/youtube-dl/files/{filename}", endpoint=serve_file),
]

app = Starlette(
    debug=True, 
    routes=routes,
    middleware=[
        Middleware(
            SessionMiddleware, 
            secret_key=SECRET_KEY,
            max_age=604800, # 1 semaine en secondes
            )
    ])

# print("Updating youtube-dl to the newest version")
# logger.info("Updating yt-dlp to the newest version")
# update()
