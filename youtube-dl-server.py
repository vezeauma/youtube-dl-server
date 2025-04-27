import sys
import os
import subprocess
import logging
import shutil
import unicodedata
import re

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

from yt_dlp import YoutubeDL, version

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
            files.append(filename)

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

    if not url:
        return JSONResponse(
            {"success": False, "error": "/q called without a 'url' in form data"}
        )

    task = BackgroundTask(download, url, options)

    print("Added url " + url + " to the download queue")

    if not ui:
        return JSONResponse(
            {"success": True, "url": url, "options": options}, background=task
        )
    return RedirectResponse(
        url="/youtube-dl?added=" + url, status_code=HTTP_303_SEE_OTHER, background=task
    )

async def update_route(scope, receive, send):
    if not request.session.get("logged_in"):
        return RedirectResponse(url="/youtube-dl/login")
    task = BackgroundTask(update)

    return JSONResponse({"output": "Initiated package update"}, background=task)

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

        print(output.decode("utf-8"))
    except subprocess.CalledProcessError as e:
        print(e.output)

def get_ydl_options(request_options):
    extract_audio_format = None
    recode_format = None
    ydl_format = app_defaults["YDL_FORMAT"]
    postprocessors = []
    merge_output_format = "mp4"
    # Options to control the order of streams
    audio_multistreams = False
    video_multistreams = False

    requested_format = request_options.get("format", "bestvideo")

    if requested_format == "mp3":
        extract_audio_format = "mp3"
        postprocessors.extend([
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": extract_audio_format,
                "preferredquality": "0",
            },
            {"key": "FFmpegMetadata"},
        ])
        ydl_format = "bestaudio/best"
        merge_output_format = None  # No need to merge
    elif requested_format == "bestaudio":
        ydl_format = "bestaudio/best"
        merge_output_format = None  # No need to merge
    elif requested_format == "mp4_720":
        # we force an MP4 conversion if it is not already MP4
        recode_format = "mp4"
        ydl_format = "bestvideo[ext=mp4][height<=720][fps<=30]+bestaudio[ext=m4a]/best[ext=mp4][height<=720][fps<=30]"
        audio_multistreams = True
        video_multistreams = True

    elif requested_format == "mp4_full":
        # no re-encoding, just mux to mp4 if needed
        recode_format = "mp4"
        ydl_format = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]"

    if recode_format:
        postprocessors.append(
            {
                "key": "FFmpegVideoConvertor",
                "preferedformat": recode_format,
            }
        )

    options = {
        "format": ydl_format,
        "postprocessors": postprocessors,
        "outtmpl": app_defaults["YDL_OUTPUT_TEMPLATE"],
        "download_archive": app_defaults["YDL_ARCHIVE_FILE"],
        "updatetime": app_defaults["YDL_UPDATE_TIME"],
        "addmetadata": True,
        "merge_output_format": merge_output_format,
        "verbose": True,
    }
    # Add options for stream order control
    if audio_multistreams:
        options["audio_multistreams"] = True
    if video_multistreams:
        options["video_multistreams"] = True
    
    # If you really want to control the order, consider adding FFmpeg options for the final muxing
    if request_options.get("custom_order", False):
        options["postprocessor_args"] = {
            'ffmpeg': ['-map', '0:a', '-map', '1:v']  # To force audio first
        }
    
    return options

def download(url, request_options):
    try:
        format_requested = request_options.get("format")
        with YoutubeDL(get_ydl_options(request_options)) as ydl:
            info = ydl.extract_info(url, download=False)
            ydl.download([url])

            # Manage videos individually (playlist or single video)
            entries = info["entries"] if "entries" in info else [info]

            for video_info in entries:
                filename_tmp = ydl.prepare_filename(video_info)
                logger.info(f"TEMP FILE: {filename_tmp}")
                filename_only = os.path.basename(filename_tmp)
                filename = os.path.join(os.path.dirname(filename_tmp), cleanup_filename(filename_only))

                # We move the temporary file if necessary
                if filename_tmp != filename and os.path.exists(filename_tmp):
                    shutil.move(filename_tmp, filename)
                logger.info(f"FILES: {filename}")

                if os.path.exists(filename) and format_requested == "mp4_720":
                    base, ext = os.path.splitext(filename)
                    temp_filename = f"{base}_serato{ext}"

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
                final_path = os.path.join(FINAL_DIR, os.path.basename(filename))
                shutil.move(filename, final_path)
                logger.info(f"✅ Copied to : {final_path}")

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
            max_age=604800 # 1 semaine en secondes
            )
    ])

print("Updating youtube-dl to the newest version")
logger.info("Updating yt-dlp to the newest version")
update()
