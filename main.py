from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import os
import uuid
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from PIL import Image

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "https://dein-shop.myshopify.com",
        "https://app-yljfykaq.fly.dev",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "/tmp/druckkonfigurator_uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "info@textil-koenig.de")
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", "noreply@textil-koenig.de")
MAX_FILE_SIZE = 50 * 1024 * 1024

configurations: dict = {}

@app.get("/healthz")
async def healthz():
    return {"status": "ok"}

@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="Datei zu groß. Max 50 MB")
    file_ext = Path(file.filename or "unknown").suffix.lower()
    allowed_extensions = {".jpg", ".jpeg", ".png", ".svg", ".pdf", ".ai", ".eps", ".tif", ".tiff"}
    if file_ext not in allowed_extensions:
        raise HTTPException(status_code=400, detail=f"Erlaubte Formate: {', '.join(allowed_extensions)}")
    file_id = str(uuid.uuid4())
    file_name = f"{file_id}{file_ext}"
    file_path = UPLOAD_DIR / file_name
    with open(file_path, "wb") as f:
        f.write(content)
    dpi_info = None
    dpi_warning = False
    if file_ext in {".jpg", ".jpeg", ".png", ".tif", ".tiff"}:
        try:
            img = Image.open(file_path)
            dpi = img.info.get("dpi", (72, 72))
            width_px, height_px = img.size
            dpi_info = {
                "dpi_x": round(dpi[0]),
                "dpi_y": round(dpi[1]),
                "width_px": width_px,
                "height_px": height_px,
            }
            if dpi[0] < 300 or dpi[1] < 300:
                dpi_warning = True
            img.close()
        except Exception:
            pass
    return {
        "file_id": file_id,
        "file_name": file.filename,
        "file_ext": file_ext,
        "file_size": len(content),
        "dpi_info": dpi_info,
        "dpi_warning": dpi_warning,
    }

@app.post("/api/configure")
async def save_configuration(config: dict):
    config_id = str(uuid.uuid4())
    config["id"] = config_id
    config["created_at"] = datetime.utcnow().isoformat()
    configurations[config_id] = config
    try:
        send_notification_email(config)
    except Exception as e:
        print(f"Email notification failed: {e}")
    return {"config_id": config_id, "status": "saved"}

@app.get("/api/configure/{config_id}")
async def get_configuration(config_id: str):
    if config_id not in configurations:
        raise HTTPException(status_code=404, detail="Konfiguration nicht gefunden")
    return configurations[config_id]

def send_notification_email(config: dict):
    if not SMTP_HOST or not SMTP_USER:
        print("SMTP not configured, skipping email")
        return
    msg = MIMEMultipart()
    msg["From"] = SMTP_FROM
    msg["To"] = ADMIN_EMAIL
    msg["Subject"] = f"Neue Druckkonfiguration: {config.get('motif_name', 'Unbenannt')}"
    body = []
    body.append("Neue Druckkonfiguration eingegangen\n")
    body.append(f"Erstellt am: {config.get('created_at', '-')}\n")
    body.append(f"Motiv-Name: {config.get('motif_name', '-')}")
    body.append(f"Anzahl Farben: {config.get('num_colors', '-')}")
    body.append(f"Farbangabe: {config.get('color_mode', '-')}")
    if config.get("colors"):
        for i, color in enumerate(config["colors"], 1):
            body.append(f"  Farbe {i}: {color}")
    body.append(f"Druckposition: {config.get('print_position', '-')}")
    body.append(f"Motivgröße: {config.get('motif_width', '-')} cm x {config.get('motif_height', '-')} cm")
    if config.get("textile_assignments"):
        body.append("\nTextil-Zuweisungen:")
        for item in config["textile_assignments"]:
            status = "Mit Druck" if item.get("print") else "Ohne Druck"
            body.append(f"  - {item.get('name', '-')}: {status}")
    msg.attach(MIMEText("\n".join(body), "plain", "utf-8"))
    file_id = config.get("file_id")
    if file_id:
        for f in UPLOAD_DIR.iterdir():
            if f.stem == file_id:
                with open(f, "rb") as attachment:
                    part = MIMEBase("application", "octet-stream")
                    part.set_payload(attachment.read())
                    encoders.encode_base64(part)
                    original_name = config.get("original_filename", f.name)
                    part.add_header("Content-Disposition", f"attachment; filename={original_name}")
                    msg.attach(part)
                break
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)