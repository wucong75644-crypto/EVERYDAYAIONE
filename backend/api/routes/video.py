"""
视频生成路由

提供视频生成、任务查询接口。
"""

from fastapi import APIRouter, Depends

from api.deps import CurrentUser, Database
from schemas.video import (
    GenerateTextToVideoRequest,
    GenerateImageToVideoRequest,
    GenerateStoryboardVideoRequest,
    GenerateVideoResponse,
    TaskStatusResponse,
    VideoModelsResponse,
    VideoModelInfo,
    TaskStatus,
)
from services.video_service import VideoService

router = APIRouter(prefix="/videos", tags=["视频生成"])


def get_video_service(db: Database) -> VideoService:
    """获取视频服务实例"""
    return VideoService(db)


@router.post("/generate/text-to-video", response_model=GenerateVideoResponse, summary="文本生成视频")
async def generate_text_to_video(
    request: GenerateTextToVideoRequest,
    current_user: CurrentUser,
    service: VideoService = Depends(get_video_service),
):
    """
    文生视频接口

    支持模型：
    - sora-2-text-to-video: 文本生成视频（40积分/10秒，60积分/15秒）
    - sora-2-pro-storyboard: 专业故事板（100积分/10秒，150积分/15秒，250积分/25秒）

    如果 wait_for_result=False，将立即返回 task_id，需要轮询 /tasks/{id} 获取结果。
    """
    result = await service.generate_text_to_video(
        user_id=current_user["id"],
        prompt=request.prompt,
        model=request.model.value,
        n_frames=request.n_frames.value,
        aspect_ratio=request.aspect_ratio.value,
        remove_watermark=request.remove_watermark,
        wait_for_result=request.wait_for_result,
    )

    return GenerateVideoResponse(
        task_id=result["task_id"],
        status=TaskStatus(result["status"]),
        video_url=result.get("video_url"),
        duration_seconds=result.get("duration_seconds", 0),
        credits_consumed=result.get("credits_consumed", 0),
        cost_usd=result.get("cost_usd", 0.0),
        cost_time_ms=result.get("cost_time_ms"),
    )


@router.post("/generate/image-to-video", response_model=GenerateVideoResponse, summary="图片生成视频")
async def generate_image_to_video(
    request: GenerateImageToVideoRequest,
    current_user: CurrentUser,
    service: VideoService = Depends(get_video_service),
):
    """
    图生视频接口

    使用 sora-2-image-to-video 模型，需要提供首帧图片。
    积分消耗：40积分/10秒，60积分/15秒
    """
    result = await service.generate_image_to_video(
        user_id=current_user["id"],
        prompt=request.prompt,
        image_url=request.image_url,
        model=request.model.value,
        n_frames=request.n_frames.value,
        aspect_ratio=request.aspect_ratio.value,
        remove_watermark=request.remove_watermark,
        wait_for_result=request.wait_for_result,
    )

    return GenerateVideoResponse(
        task_id=result["task_id"],
        status=TaskStatus(result["status"]),
        video_url=result.get("video_url"),
        duration_seconds=result.get("duration_seconds", 0),
        credits_consumed=result.get("credits_consumed", 0),
        cost_usd=result.get("cost_usd", 0.0),
        cost_time_ms=result.get("cost_time_ms"),
    )


@router.post("/generate/storyboard", response_model=GenerateVideoResponse, summary="故事板视频生成")
async def generate_storyboard_video(
    request: GenerateStoryboardVideoRequest,
    current_user: CurrentUser,
    service: VideoService = Depends(get_video_service),
):
    """
    故事板视频生成接口

    使用 sora-2-pro-storyboard 模型，支持最长25秒视频。
    积分消耗：100积分/10秒，150积分/15秒，250积分/25秒
    """
    result = await service.generate_storyboard_video(
        user_id=current_user["id"],
        model="sora-2-pro-storyboard",
        n_frames=request.n_frames.value,
        storyboard_images=request.storyboard_images,
        aspect_ratio=request.aspect_ratio.value,
        wait_for_result=request.wait_for_result,
    )

    return GenerateVideoResponse(
        task_id=result["task_id"],
        status=TaskStatus(result["status"]),
        video_url=result.get("video_url"),
        duration_seconds=result.get("duration_seconds", 0),
        credits_consumed=result.get("credits_consumed", 0),
        cost_usd=result.get("cost_usd", 0.0),
        cost_time_ms=result.get("cost_time_ms"),
    )


@router.get("/tasks/{task_id}", response_model=TaskStatusResponse, summary="查询任务状态")
async def query_task(
    task_id: str,
    current_user: CurrentUser,
    service: VideoService = Depends(get_video_service),
):
    """
    查询视频生成任务状态

    用于轮询异步任务的完成状态。
    """
    result = await service.query_task(task_id)

    return TaskStatusResponse(
        task_id=result["task_id"],
        status=TaskStatus(result["status"]),
        video_url=result.get("video_url"),
        fail_code=result.get("fail_code"),
        fail_msg=result.get("fail_msg"),
    )


@router.get("/models", response_model=VideoModelsResponse, summary="获取可用模型")
async def get_models(
    current_user: CurrentUser,
    service: VideoService = Depends(get_video_service),
):
    """
    获取可用的视频生成模型列表
    """
    models = service.get_available_models()

    return VideoModelsResponse(
        models=[VideoModelInfo(**m) for m in models]
    )
