import os
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from typing import Optional

from database import get_db

router = APIRouter()
security = HTTPBearer(auto_error=False)

def verify_media_access(
    token: Optional[str] = Query(None),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)
):
    correct_password = os.getenv("ADMIN_PASSWORD")
    if not correct_password:
        return True
    
    provided_pwd = token or (credentials.credentials if credentials else None)
    if provided_pwd != correct_password:
        raise HTTPException(status_code=401, detail="Unauthorized media access")
    return True

@router.get("/media/{identifier}")
async def get_media_file(
    identifier: str,
    download: Optional[bool] = Query(False),
    auth: bool = Depends(verify_media_access),
    db: AsyncSession = Depends(get_db)
):
    if identifier.isdigit():
        sql = text("SELECT mime_type, file_data, caption, media_id FROM whatsapp_media WHERE id = :id LIMIT 1")
        params = {"id": int(identifier)}
    else:
        sql = text("SELECT mime_type, file_data, caption, media_id FROM whatsapp_media WHERE media_id = :mid ORDER BY created_at DESC LIMIT 1")
        params = {"mid": identifier}

    result = await db.execute(sql, params)
    row = result.mappings().one_or_none()

    if not row or not row["file_data"]:
        raise HTTPException(status_code=404, detail="Media file not found")

    file_bytes = bytes(row["file_data"])
    mime_type = row["mime_type"] or "image/jpeg"
    ext = "jpg"
    if "png" in mime_type:
        ext = "png"
    elif "webp" in mime_type:
        ext = "webp"
    elif "pdf" in mime_type:
        ext = "pdf"

    headers = {}
    if download:
        filename = f"media_{identifier}.{ext}"
        headers["Content-Disposition"] = f'attachment; filename="{filename}"'

    return Response(content=file_bytes, media_type=mime_type, headers=headers)
