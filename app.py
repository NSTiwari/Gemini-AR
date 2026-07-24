import base64
import io
import logging
import os
import uuid

import fal_client
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from google import genai
from PIL import Image, UnidentifiedImageError

load_dotenv()

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_IMAGE_MODEL = os.environ.get("GEMINI_IMAGE_MODEL", "gemini-3-pro-image")
FAL_KEY = os.environ.get("FAL_KEY")
if FAL_KEY:
    os.environ["FAL_KEY"] = FAL_KEY

AR_MODEL = "fal-ai/hyper3d/rodin/v2.5"

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
MAX_IMAGE_DIMENSION = 2048  # longest side, px - keeps payloads reasonable

UPLOAD_DIR = os.path.join("static", "uploads")
OUTPUT_DIR = os.path.join("static", "outputs")

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-key")
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024  # 25 MB per request

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("gemini-ar")

_client = None


def get_client():
    global _client
    if _client is None:
        if not GEMINI_API_KEY:
            raise RuntimeError(
                "GEMINI_API_KEY is not set. Copy .env.example to .env and add your key."
            )
        _client = genai.Client(api_key=GEMINI_API_KEY)
    return _client


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def load_and_normalize_image(file_storage):
    """Validate the upload is really an image, downscale it, and re-encode as PNG."""
    raw = file_storage.read()
    try:
        img = Image.open(io.BytesIO(raw))
        img.verify()
        img = Image.open(io.BytesIO(raw))  # re-open: verify() consumes the parser
        img = img.convert("RGB")
    except (UnidentifiedImageError, OSError):
        raise ValueError("That doesn't look like a readable image file.")

    if max(img.size) > MAX_IMAGE_DIMENSION:
        img.thumbnail((MAX_IMAGE_DIMENSION, MAX_IMAGE_DIMENSION), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def save_bytes(data, directory, ext="png"):
    filename = f"{uuid.uuid4().hex}.{ext}"
    path = os.path.join(directory, filename)
    with open(path, "wb") as f:
        f.write(data)
    return path.replace("\\", "/")


def resolve_local_image(image_url, directory):
    """Map a previously-returned /static/<directory>/<file> URL back to a real
    file on disk, rejecting anything that isn't exactly one of our own saved
    images (no path traversal, no arbitrary files)."""
    filename = os.path.basename(str(image_url))
    path = os.path.join(directory, filename)
    if not filename or not os.path.isfile(path):
        raise ValueError("Unknown image. Please start over.")
    return path


def resolve_output_image(image_url):
    return resolve_local_image(image_url, OUTPUT_DIR)


def gemini_edit_image(image_png_bytes, prompt_text):
    """Send one image + a text instruction to Gemini and return the edited PNG bytes."""
    input_content = [
        {"type": "text", "text": prompt_text},
        {"type": "image", "data": base64.b64encode(image_png_bytes).decode("utf-8"), "mime_type": "image/png"},
    ]
    client = get_client()
    interaction = client.interactions.create(
        model=GEMINI_IMAGE_MODEL,
        input=input_content,
        response_modalities=["image"],
    )
    if not interaction.output_image or not interaction.output_image.data:
        raise RuntimeError("The model did not return an image.")
    return base64.b64decode(interaction.output_image.data)


EDIT_ROOM_PROMPT = (
    "You are a professional interior visualizer. The attached image is a real "
    "photo of a customer's own room.\n\n"
    "Edit this image to naturally add the following into the scene:\n"
    "\"{instruction}\"\n\n"
    "Rules:\n"
    "- Match the room's existing perspective, scale, and lighting exactly.\n"
    "- Keep everything else in the room unchanged — same walls, floor, windows, doors, camera angle.\n"
    "- The added object should look photorealistic and physically plausible in this space.\n"
    "- The output must be a single edited image, not text."
)

ISOLATE_OBJECT_PROMPT = (
    "The attached image shows a room with an object that was just added, "
    "described as: \"{instruction}\".\n\n"
    "Generate a clean, standalone product-photography image of ONLY that object:\n"
    "- Centered, plain white/neutral studio background.\n"
    "- No room, walls, floor, or any other objects visible.\n"
    "- Same design, color, and style as the object shown in the attached image.\n"
    "- Professional e-commerce product-shot lighting.\n"
    "- The output must be a single image, not text."
)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/edit-room", methods=["POST"])
def edit_room():
    room_file = request.files.get("room")
    instruction = (request.form.get("prompt") or "").strip()

    if not room_file or not room_file.filename:
        return jsonify(error="Please take a photo of the room first."), 400
    if not allowed_file(room_file.filename):
        return jsonify(error="Photo must be a PNG, JPG, or WEBP file."), 400
    if not instruction:
        return jsonify(error="Please describe what to add, e.g. “Add a blue sofa”."), 400

    try:
        room_png = load_and_normalize_image(room_file)
    except ValueError as e:
        return jsonify(error=str(e)), 400

    room_url = save_bytes(room_png, UPLOAD_DIR)

    try:
        edited_bytes = gemini_edit_image(room_png, EDIT_ROOM_PROMPT.format(instruction=instruction))
    except Exception:
        logger.exception("Gemini room edit failed")
        return jsonify(error="Could not edit the photo. Please try again."), 502

    edited_url = save_bytes(edited_bytes, OUTPUT_DIR)

    return jsonify(success=True, room_url=f"/{room_url}", edited_url=f"/{edited_url}")


@app.route("/api/isolate-object", methods=["POST"])
def isolate_object():
    body = request.get_json(silent=True) or {}
    edited_url = body.get("edited_url")
    instruction = (body.get("prompt") or "").strip()

    if not edited_url:
        return jsonify(error="Missing edited_url."), 400
    if not instruction:
        return jsonify(error="Missing prompt."), 400

    try:
        source_path = resolve_output_image(edited_url)
    except ValueError as e:
        return jsonify(error=str(e)), 400

    with open(source_path, "rb") as f:
        edited_bytes = f.read()

    try:
        product_bytes = gemini_edit_image(edited_bytes, ISOLATE_OBJECT_PROMPT.format(instruction=instruction))
    except Exception:
        logger.exception("Gemini object isolation failed")
        return jsonify(error="Could not isolate the object. Please try again."), 502

    product_url = save_bytes(product_bytes, OUTPUT_DIR)

    return jsonify(success=True, product_url=f"/{product_url}")


AR_JOBS = {}  # job_id -> dict, in-memory only (fine for this single-process demo)


@app.route("/api/generate-3d/start", methods=["POST"])
def generate_3d_start():
    """Kick off a real 3D model (GLB + USDZ) generation and return immediately
    with a job_id. Generation itself runs for minutes on fal.ai's side, so the
    client polls /status instead of holding one long HTTP request open (which
    mobile networks and tunnels/proxies will kill long before that)."""
    if not FAL_KEY:
        return jsonify(error="FAL_KEY is not set. Copy .env.example to .env and add your key."), 500

    body = request.get_json(silent=True) or {}
    image_url = body.get("image_url")
    if not image_url:
        return jsonify(error="Missing image_url."), 400

    try:
        source_path = resolve_output_image(image_url)
    except ValueError as e:
        return jsonify(error=str(e)), 400

    with open(source_path, "rb") as f:
        source_bytes = f.read()
    data_uri = f"data:image/png;base64,{base64.b64encode(source_bytes).decode('utf-8')}"

    try:
        glb_handle = fal_client.submit(AR_MODEL, arguments={"image_urls": [data_uri], "geometry_file_format": "glb"})
        usdz_handle = fal_client.submit(AR_MODEL, arguments={"image_urls": [data_uri], "geometry_file_format": "usdz"})
    except Exception:
        logger.exception("Failed to submit 3D model generation")
        return jsonify(error="Could not start 3D model generation. Please try again."), 502

    job_id = uuid.uuid4().hex
    AR_JOBS[job_id] = {
        "glb_handle": glb_handle, "usdz_handle": usdz_handle,
        "glb_url": None, "usdz_url": None, "glb_failed": False, "usdz_failed": False,
    }
    return jsonify(success=True, job_id=job_id)


def _poll_one(job, handle_key, url_key, failed_key):
    if job[url_key] is not None or job[failed_key]:
        return
    try:
        status = job[handle_key].status()
    except Exception:
        logger.exception("Status check failed for %s", handle_key)
        job[failed_key] = True
        return
    if isinstance(status, fal_client.Completed):
        try:
            result = job[handle_key].get()
            job[url_key] = (result.get("model_mesh") or {}).get("url")
        except Exception:
            logger.exception("Fetching result failed for %s", handle_key)
            job[failed_key] = True


@app.route("/api/generate-3d/status/<job_id>", methods=["GET"])
def generate_3d_status(job_id):
    job = AR_JOBS.get(job_id)
    if not job:
        return jsonify(error="Unknown or expired job."), 404

    _poll_one(job, "glb_handle", "glb_url", "glb_failed")
    _poll_one(job, "usdz_handle", "usdz_url", "usdz_failed")

    if job["glb_url"]:
        return jsonify(success=True, status="done", glb_url=job["glb_url"], usdz_url=job["usdz_url"])
    if job["glb_failed"]:
        return jsonify(error="The model did not return a usable 3D mesh. Please try again."), 502

    return jsonify(success=True, status="pending")


if __name__ == "__main__":
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    app.run(debug=True, host="0.0.0.0", port=5001)
