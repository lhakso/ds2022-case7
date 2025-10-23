import os
import logging
from datetime import datetime
from urllib.parse import quote
from flask import Flask, request, jsonify, Response
from werkzeug.utils import secure_filename
from azure.storage.blob import BlobServiceClient, ContentSettings, PublicAccess
from azure.core.exceptions import ResourceExistsError
from dotenv import load_dotenv

# Load .env when running locally (Azure will ignore this and inject env vars automatically)
load_dotenv()

# ----------------------------------------------------
# 🔧 CONFIG (read from environment variables)
# ----------------------------------------------------
# e.g. https://myaccount.blob.core.windows.net
STORAGE_ACCOUNT_URL = os.environ.get("STORAGE_ACCOUNT_URL")
AZURE_STORAGE_CONNECTION_STRING = os.environ.get(
    "AZURE_STORAGE_CONNECTION_STRING")
IMAGES_CONTAINER = os.environ.get("IMAGES_CONTAINER", "lanternfly-images")

if not (STORAGE_ACCOUNT_URL and AZURE_STORAGE_CONNECTION_STRING):
    raise RuntimeError(
        "Missing required environment vars: STORAGE_ACCOUNT_URL or AZURE_STORAGE_CONNECTION_STRING")

# ----------------------------------------------------
# Flask setup
# ----------------------------------------------------
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB max

logging.basicConfig(level=logging.INFO)
LOG = logging.getLogger("lanternfly")

# ----------------------------------------------------
# Azure Blob Storage setup
# ----------------------------------------------------
bsc = BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)
cc = bsc.get_container_client(IMAGES_CONTAINER)
try:
    cc.create_container(public_access=PublicAccess.Blob)
except ResourceExistsError:
    pass

container_url = f"{STORAGE_ACCOUNT_URL}/{IMAGES_CONTAINER}"

# ----------------------------------------------------
# Helpers
# ----------------------------------------------------


def is_image(file):
    return (file.mimetype or "").startswith("image/")


def timestamped_name(filename: str) -> str:
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    safe = secure_filename(filename)
    return f"{ts}-{safe}"

# ----------------------------------------------------
# API Routes
# ----------------------------------------------------


@app.post("/api/v1/upload")
def upload():
    try:
        if "file" not in request.files:
            return jsonify(ok=False, error="Missing file"), 400
        f = request.files["file"]
        if f.filename == "":
            return jsonify(ok=False, error="Empty filename"), 400
        if not is_image(f):
            return jsonify(ok=False, error="Only image/* allowed"), 415

        blob_name = timestamped_name(f.filename)
        blob_client = cc.get_blob_client(blob_name)
        blob_client.upload_blob(
            f.stream,
            overwrite=True,
            content_settings=ContentSettings(content_type=f.mimetype),
        )

        url = f"{container_url}/{quote(blob_name)}"
        LOG.info("Uploaded %s", url)
        return jsonify(ok=True, url=url)
    except Exception as e:
        LOG.exception("Upload error")
        return jsonify(ok=False, error=str(e)), 500


@app.get("/api/v1/gallery")
def gallery():
    try:
        blobs = [f"{container_url}/{quote(b.name)}" for b in cc.list_blobs()]
        blobs.sort(reverse=True)
        return jsonify(ok=True, gallery=blobs)
    except Exception as e:
        LOG.exception("Gallery error")
        return jsonify(ok=False, error=str(e)), 500


@app.get("/health")
def health():
    return Response("OK", status=200)

# ----------------------------------------------------
# Frontend (simple HTML + JS)
# ----------------------------------------------------


@app.get("/")
def index():
    return """
<!doctype html>
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Lanternfly Uploader</title>
<style>
body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial;margin:0;padding:1rem;background:#f7f7f7}
.card{max-width:860px;margin:0 auto 1rem;background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:1rem}
h2{margin:.2rem 0 1rem}
.row{display:flex;gap:.5rem;align-items:center;flex-wrap:wrap}
input[type=file]{padding:.4rem;border:1px solid #e5e7eb;border-radius:8px}
button{padding:.6rem 1rem;border-radius:8px;border:1px solid #232D4B;background:#232D4B;color:#fff;cursor:pointer}
button.secondary{background:#fff;color:#232D4B}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:10px;margin-top:.75rem}
.item{border:1px solid #e5e7eb;border-radius:10px;padding:6px;background:#fff}
.thumb{width:100%;height:150px;object-fit:cover;border-radius:8px;background:#f1f5f9}
.cap{font-size:.8rem;color:#475569;margin-top:.3rem;word-break:break-all}
.muted{color:#64748b}
.note{background:#fff7ed;border:1px solid #fed7aa;color:#7c2d12;padding:.5rem .75rem;border-radius:8px;margin:.6rem 0}
.spinner{display:inline-block;width:16px;height:16px;border:2px solid #cbd5e1;border-top-color:#0ea5e9;border-radius:50%;animation:spin .9s linear infinite;vertical-align:-3px}
@keyframes spin{to{transform:rotate(360deg)}}
</style>

<div class="card">
  <h2>Upload an image</h2>
  <div class="note">Max 10 MB. Only image/* types. Publicly viewable below.</div>
  <div class="row">
    <input type="file" id="f" accept="image/*" capture="environment">
    <button id="u" onclick="send()">Upload</button>
    <button class="secondary" onclick="document.getElementById('f').value='';">Reset</button>
    <span id="s" class="muted"></span>
  </div>
</div>

<div class="card">
  <h2>Gallery</h2>
  <div id="grid" class="grid"></div>
</div>

<script>
const grid=document.getElementById('grid');
async function send(){
  const f=document.getElementById('f').files[0];
  if(!f){alert("Choose a file first");return;}
  const fd=new FormData(); fd.append("file",f);
  document.getElementById('u').disabled=true;
  document.getElementById('s').innerHTML='<span class="spinner"></span> Uploading...';
  try{
    const r=await fetch('/api/v1/upload',{method:'POST',body:fd});
    const j=await r.json();
    if(!j.ok) throw new Error(j.error);
    alert("Uploaded: "+j.url);
    await load();
  }catch(e){alert("Error: "+e.message);}
  document.getElementById('u').disabled=false;
  document.getElementById('s').innerHTML='';
}
async function load(){
  grid.innerHTML='<div class="muted"><span class="spinner"></span> Loading...</div>';
  const r=await fetch('/api/v1/gallery');
  const j=await r.json();
  if(!j.ok){grid.innerHTML='<div class="muted">Failed to load gallery</div>';return;}
  grid.innerHTML='';
  j.gallery.forEach(u=>{
    const d=document.createElement('div');d.className='item';
    const img=document.createElement('img');img.src=u;img.className='thumb';
    const cap=document.createElement('div');cap.className='cap';cap.textContent=u.split('/').pop();
    d.appendChild(img);d.appendChild(cap);grid.appendChild(d);
  });
}
document.addEventListener('DOMContentLoaded',load);
</script>
"""


# ----------------------------------------------------
# Run app
# ----------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
