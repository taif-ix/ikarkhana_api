from __future__ import annotations

import io
import mimetypes
import os
import zipfile

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import StreamingResponse

from app.api.routes.common import ALLOWED_UPLOAD_EXTENSIONS, data_url
from app.services.vision import image_bytes_for_preview


router = APIRouter()


@router.post("/expand-upload")
async def expand_upload(uploads: list[UploadFile] = File(...)) -> dict[str, object]:
    expanded: list[dict[str, object]] = []
    skipped: list[str] = []

    for upload in uploads:
        filename = upload.filename or "uploaded-file"
        content = await upload.read()
        suffix = os.path.splitext(filename)[1].lower()

        if suffix == ".zip":
            try:
                with zipfile.ZipFile(io.BytesIO(content)) as archive:
                    for member in archive.infolist():
                        if member.is_dir():
                            continue
                        member_name = os.path.basename(member.filename)
                        member_suffix = os.path.splitext(member_name)[1].lower()
                        if member_suffix not in ALLOWED_UPLOAD_EXTENSIONS:
                            skipped.append(member.filename)
                            continue
                        member_content = archive.read(member)
                        mime_type = mimetypes.guess_type(member_name)[0] or "application/octet-stream"
                        preview_content, preview_mime = image_bytes_for_preview(member_content, mime_type, member_name)
                        expanded.append(
                            {
                                "name": member_name,
                                "size_kb": round(len(member_content) / 1024, 2),
                                "size_mb": f"{len(member_content) / (1024 * 1024):.2f} MB",
                                "mime_type": preview_mime,
                                "original_mime_type": mime_type,
                                "image": data_url(preview_content, preview_mime),
                                "source": filename,
                            }
                        )
            except zipfile.BadZipFile:
                skipped.append(f"{filename} is not a valid zip file")
            continue

        if suffix not in ALLOWED_UPLOAD_EXTENSIONS:
            skipped.append(filename)
            continue

        mime_type = upload.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        preview_content, preview_mime = image_bytes_for_preview(content, mime_type, filename)
        expanded.append(
            {
                "name": filename,
                "size_kb": round(len(content) / 1024, 2),
                "size_mb": f"{len(content) / (1024 * 1024):.2f} MB",
                "mime_type": preview_mime,
                "original_mime_type": mime_type,
                "image": data_url(preview_content, preview_mime),
                "source": "direct-upload",
            }
        )

    return {"files": expanded, "skipped": skipped, "count": len(expanded)}


@router.post("/diagram-preview")
async def diagram_preview(diagram: UploadFile = File(...)) -> StreamingResponse:
    content = await diagram.read()
    image_content, mime_type = image_bytes_for_preview(content, diagram.content_type, diagram.filename)
    return StreamingResponse(io.BytesIO(image_content), media_type=mime_type)
