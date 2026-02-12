"""
修复孤儿任务：已完成但消息未创建的任务

定期运行此脚本来处理那些状态为 completed 但 version=1 的任务
"""

import asyncio
import os
import sys
from dotenv import load_dotenv
from supabase import create_client
from services.task_completion_service import TaskCompletionService
from services.adapters.base import ImageGenerateResult, VideoGenerateResult, TaskStatus

load_dotenv('.env')

async def main():
    # 初始化数据库
    url = os.getenv('SUPABASE_URL')
    key = os.getenv('SUPABASE_SERVICE_ROLE_KEY')
    db = create_client(url, key)

    # 查询所有 completed 但 version=1 且 started_at=None 的任务
    result = db.table('tasks').select('*').eq('status', 'completed').eq('version', 1).is_('started_at', 'null').execute()

    tasks = result.data
    print(f"✅ 找到 {len(tasks)} 个孤儿任务")

    if not tasks:
        print("没有需要修复的任务")
        return

    # 处理每个任务
    service = TaskCompletionService(db)
    fixed_count = 0

    for task in tasks:
        external_task_id = task['external_task_id']
        task_type = task['type']
        result_data = task.get('result')

        if not result_data or not result_data.get('image_urls') and not result_data.get('video_url'):
            print(f"⏭️  跳过无结果任务: {external_task_id}")
            continue

        try:
            # 构造结果对象
            if task_type == 'image':
                task_result = ImageGenerateResult(
                    status=TaskStatus.SUCCESS if result_data['status'] == 'success' else TaskStatus.FAILED,
                    task_id=external_task_id,
                    image_urls=result_data.get('image_urls', []),
                    fail_code=result_data.get('fail_code'),
                    fail_msg=result_data.get('fail_msg'),
                )
            elif task_type == 'video':
                task_result = VideoGenerateResult(
                    status=TaskStatus.SUCCESS if result_data['status'] == 'success' else TaskStatus.FAILED,
                    task_id=external_task_id,
                    video_url=result_data.get('video_url'),
                    thumbnail_url=result_data.get('thumbnail_url'),
                    duration_seconds=result_data.get('duration_seconds'),
                    fail_code=result_data.get('fail_code'),
                    fail_msg=result_data.get('fail_msg'),
                )
            else:
                print(f"⏭️  跳过 chat 任务: {external_task_id}")
                continue

            # 先重置任务状态为 pending，这样 process_result 才会处理
            db.table('tasks').update({
                'status': 'pending',
                'version': 1
            }).eq('id', task['id']).execute()

            print(f"🔄 处理任务: {external_task_id}")

            # 调用处理
            success = await service.process_result(external_task_id, task_result)

            if success:
                print(f"✅ 修复成功: {external_task_id}")
                fixed_count += 1
            else:
                print(f"❌ 修复失败: {external_task_id}")

        except Exception as e:
            print(f"❌ 处理任务失败 {external_task_id}: {e}")
            import traceback
            traceback.print_exc()

    print(f"\n=== 修复完成 ===")
    print(f"总计: {len(tasks)} 个任务")
    print(f"成功修复: {fixed_count} 个")
    print(f"失败: {len(tasks) - fixed_count} 个")

if __name__ == "__main__":
    asyncio.run(main())
