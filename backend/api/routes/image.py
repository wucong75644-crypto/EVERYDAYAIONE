"""
图像生成路由

提供图像生成、编辑、任务查询接口。
"""

from fastapi import APIRouter, Depends

from api.deps import CurrentUser, Database
from schemas.image import (
    GenerateImageRequest,
    GenerateImageResponse,
    EditImageRequest,
    TaskStatusResponse,
    ImageModelsResponse,
    ImageModelInfo,
    TaskStatus,
    UploadImageRequest,
    UploadImageResponse,
)
from services.image_service import ImageService
from services.storage_service import StorageService

router = APIRouter(prefix="/images", tags=["图像生成"])


def get_image_service(db: Database) -> ImageService:
    """获取图像服务实例"""
    return ImageService(db)


@router.post("/generate", response_model=GenerateImageResponse, summary="生成图像")
async def generate_image(
    request: GenerateImageRequest,
    current_user: CurrentUser,
    service: ImageService = Depends(get_image_service),
):
    """
    文生图接口

    支持模型：
    - google/nano-banana: 基础文生图（5积分/张）
    - nano-banana-pro: 高级文生图，支持1K/2K/4K（25-48积分/张）

    如果 wait_for_result=False，将立即返回 task_id，需要轮询 /tasks/{id} 获取结果。
    """
    result = await service.generate_image(
        user_id=current_user["id"],
        prompt=request.prompt,
        model=request.model.value,
        size=request.size.value,
        output_format=request.output_format.value,
        resolution=request.resolution.value if request.resolution else None,
        wait_for_result=request.wait_for_result,
    )

    return GenerateImageResponse(
        task_id=result["task_id"],
        status=TaskStatus(result["status"]),
        image_urls=result.get("image_urls", []),
        credits_consumed=result.get("credits_consumed", 0),
        cost_usd=result.get("cost_usd", 0.0),
        cost_time_ms=result.get("cost_time_ms"),
    )


@router.post("/edit", response_model=GenerateImageResponse, summary="编辑图像")
async def edit_image(
    request: EditImageRequest,
    current_user: CurrentUser,
    service: ImageService = Depends(get_image_service),
):
    """
    图像编辑接口

    使用 google/nano-banana-edit 模型，需要提供输入图片。
    """
    result = await service.edit_image(
        user_id=current_user["id"],
        prompt=request.prompt,
        image_urls=request.image_urls,
        size=request.size.value,
        output_format=request.output_format.value,
        wait_for_result=request.wait_for_result,
    )

    return GenerateImageResponse(
        task_id=result["task_id"],
        status=TaskStatus(result["status"]),
        image_urls=result.get("image_urls", []),
        credits_consumed=result.get("credits_consumed", 0),
        cost_usd=result.get("cost_usd", 0.0),
        cost_time_ms=result.get("cost_time_ms"),
    )


@router.get("/tasks/{task_id}", response_model=TaskStatusResponse, summary="查询任务状态")
async def query_task(
    task_id: str,
    current_user: CurrentUser,
    service: ImageService = Depends(get_image_service),
):
    """
    查询图像生成任务状态

    用于轮询异步任务的完成状态。
    """
    result = await service.query_task(task_id)

    return TaskStatusResponse(
        task_id=result["task_id"],
        status=TaskStatus(result["status"]),
        image_urls=result.get("image_urls", []),
        fail_code=result.get("fail_code"),
        fail_msg=result.get("fail_msg"),
    )


@router.get("/models", response_model=ImageModelsResponse, summary="获取可用模型")
async def get_models(
    current_user: CurrentUser,
    service: ImageService = Depends(get_image_service),
):
    """
    获取可用的图像生成模型列表
    """
    models = service.get_available_models()

    return ImageModelsResponse(
        models=[ImageModelInfo(**m) for m in models]
    )


@router.post("/upload", response_model=UploadImageResponse, summary="上传图片")
async def upload_image(
    request: UploadImageRequest,
    current_user: CurrentUser,
    db: Database,
):
    """
    上传图片到存储服务

    用于图像编辑功能，先上传本地图片获取 URL。
    """
    storage = StorageService(db)
    url = await storage.upload_base64_image(
        user_id=current_user["id"],
        base64_data=request.image_data,
    )

    return UploadImageResponse(url=url)
