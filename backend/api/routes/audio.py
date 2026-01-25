"""
音频路由

提供音频文件上传、删除等功能。
"""

from fastapi import APIRouter, Depends, File, UploadFile, HTTPException, Query

from api.deps import CurrentUser, Database
from schemas.audio import (
    AudioUploadResponse,
    AudioInfoResponse,
    AudioDeleteRequest,
)
from services.audio_service import AudioService

router = APIRouter(prefix="/audio", tags=["音频"])


def get_audio_service(db: Database) -> AudioService:
    """获取音频服务实例"""
    return AudioService(db)


@router.post("/upload", response_model=AudioUploadResponse, summary="上传音频")
async def upload_audio(
    current_user: CurrentUser,
    service: AudioService = Depends(get_audio_service),
    file: UploadFile = File(...),
):
    """
    上传音频文件

    - **file**: 音频文件（支持 webm/mp4/mp3/wav/ogg，最大 25MB）

    返回：
    - **audio_url**: 音频的公开 URL
    - **duration**: 音频时长（秒）
    - **size**: 文件大小（字节）
    """
    # 读取文件内容
    file_data = await file.read()
    content_type = file.content_type or "audio/webm"

    try:
        result = await service.upload_audio(
            user_id=current_user["id"],
            file_data=file_data,
            content_type=content_type,
            filename=file.filename,
        )

        return AudioUploadResponse(
            audio_url=result["audio_url"],
            duration=result["duration"],
            size=result["size"],
        )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"上传失败: {e}")


@router.get("/info", response_model=AudioInfoResponse, summary="获取音频信息")
async def get_audio_info(
    current_user: CurrentUser,
    service: AudioService = Depends(get_audio_service),
    url: str = Query(..., description="音频文件 URL"),
):
    """
    获取音频文件信息

    - **url**: 音频文件的公开 URL

    返回：
    - **duration**: 音频时长（秒）
    - **size**: 文件大小（字节）
    """
    try:
        result = await service.get_audio_info(url)
        return AudioInfoResponse(
            duration=result["duration"],
            size=result["size"],
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取信息失败: {e}")


@router.delete("/delete", summary="删除音频")
async def delete_audio(
    request: AudioDeleteRequest,
    current_user: CurrentUser,
    service: AudioService = Depends(get_audio_service),
):
    """
    删除音频文件

    - **audio_url**: 音频文件的公开 URL
    """
    try:
        await service.delete_audio(request.audio_url)
        return {"message": "删除成功"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除失败: {e}")
