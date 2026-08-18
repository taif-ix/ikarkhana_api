import base64
import io
import mimetypes
import os
import zipfile

from fastapi import APIRouter, File, HTTPException, UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError


router = APIRouter(tags=["uploads"])

ALLOWED_UPLOAD_EXTENSIONS = {".tif", ".tiff", ".png", ".jpg", ".jpeg", ".pdf", ".dwg"}
BITMAP_EXTENSIONS = {".tif", ".tiff", ".png", ".jpg", ".jpeg"}
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(25 * 1024 * 1024)))
MAX_EXPANDED_FILES = int(os.getenv("MAX_EXPANDED_FILES", "100"))


def _data_url(content: bytes, mime_type: str) -> str:
    encoded = base64.b64encode(content).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _preview_bytes(content: bytes, filename: str, mime_type: str) -> tuple[bytes, str]:
    if os.path.splitext(filename)[1].lower() not in BITMAP_EXTENSIONS:
        return content, mime_type

    try:
        with Image.open(io.BytesIO(content)) as image:
            image = ImageOps.exif_transpose(image).convert("L")
            image = ImageOps.autocontrast(image).convert("RGB")
            image.thumbnail((3200, 3200), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.save(output, format="PNG", optimize=True)
            return output.getvalue(), "image/png"
    except (UnidentifiedImageError, OSError) as exc:
        raise HTTPException(status_code=422, detail=f"{filename} is not a valid drawing image.") from exc


def _file_payload(content: bytes, filename: str, mime_type: str, source: str) -> dict[str, object]:
    preview, preview_mime = _preview_bytes(content, filename, mime_type)
    return {
        "name": filename,
        "size_kb": round(len(content) / 1024, 2),
        "size_mb": f"{len(content) / (1024 * 1024):.2f} MB",
        "mime_type": preview_mime,
        "original_mime_type": mime_type,
        "image": _data_url(preview, preview_mime),
        "source": source,
    }


@router.post("/expand-upload")
async def expand_upload(uploads: list[UploadFile] = File(...)) -> dict[str, object]:
    """Expand drawing ZIPs and return browser-ready previews for the upload screen."""
    expanded: list[dict[str, object]] = []
    skipped: list[str] = []

    for upload in uploads:
        filename = os.path.basename(upload.filename or "uploaded-file")
        content = await upload.read(MAX_UPLOAD_BYTES + 1)
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail=f"{filename} exceeds the upload size limit.")

        suffix = os.path.splitext(filename)[1].lower()
        if suffix != ".zip":
            if suffix not in ALLOWED_UPLOAD_EXTENSIONS:
                skipped.append(filename)
                continue
            mime_type = upload.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
            expanded.append(_file_payload(content, filename, mime_type, "direct-upload"))
            continue

        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                for member in archive.infolist():
                    if member.is_dir():
                        continue
                    if len(expanded) >= MAX_EXPANDED_FILES:
                        raise HTTPException(status_code=413, detail="The upload contains too many drawing files.")
                    member_name = os.path.basename(member.filename)
                    member_suffix = os.path.splitext(member_name)[1].lower()
                    if not member_name or member_suffix not in ALLOWED_UPLOAD_EXTENSIONS:
                        skipped.append(member.filename)
                        continue
                    if member.file_size > MAX_UPLOAD_BYTES:
                        skipped.append(f"{member.filename} exceeds the upload size limit")
                        continue
                    member_content = archive.read(member)
                    mime_type = mimetypes.guess_type(member_name)[0] or "application/octet-stream"
                    expanded.append(_file_payload(member_content, member_name, mime_type, filename))
        except zipfile.BadZipFile:
            skipped.append(f"{filename} is not a valid ZIP file")

    return {"files": expanded, "skipped": skipped, "count": len(expanded)}
