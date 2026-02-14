"""
手动处理已完成的任务

用于修复：任务已完成但消息没有创建的情况
"""

import asyncio
import os
from dotenv import load_dotenv
from supabase import create_client
from services.task_completion_service import TaskCompletionService
from services.adapters.base import ImageGenerateResult, TaskStatus

load_dotenv('.env')

async def main():
    # 初始化数据库
    url = os.getenv('SUPABASE_URL')
    key = os.getenv('SUPABASE_SERVICE_ROLE_KEY')
    db = create_client(url, key)

    # 任务 ID
    external_task_id = "3e919aa94b9640772092817af69ee9f0"

    # 创建服务
    service = TaskCompletionService(db)

    # 获取任务
    task = service.get_task(external_task_id)
    if not task:
        print(f"❌ 任务不存在: {external_task_id}")
        return

    print(f"✅ 找到任务:")
    print(f"  - external_task_id: {task['external_task_id']}")
    print(f"  - status: {task['status']}")
    print(f"  - type: {task['type']}")
    print(f"  - placeholder_message_id: {task.get('placeholder_message_id')}")

    # 检查是否已有 result
    result_data = task.get('result')
    if not result_data:
        print("❌ 任务没有 result 数据")
        return

    print(f"\n✅ 任务结果:")
    print(f"  - status: {result_data.get('status')}")
    print(f"  - image_urls: {result_data.get('image_urls')}")

    # 构造 ImageGenerateResult
    task_result = ImageGenerateResult(
        status=TaskStatus.SUCCESS if result_data['status'] == 'success' else TaskStatus.FAILED,
        task_id=external_task_id,
        image_urls=result_data.get('image_urls', []),
        fail_code=result_data.get('fail_code'),
        fail_msg=result_data.get('fail_msg'),
    )

    # 处理任务
    print(f"\n🔄 开始处理任务...")
    try:
        await service.process_result(external_task_id, task_result)
        print(f"✅ 任务处理完成！")
        print(f"\n请刷新前端查看图片")
    except Exception as e:
        print(f"❌ 处理失败: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
