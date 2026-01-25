/**
 * 加载骨架屏组件
 *
 * 显示消息加载时的骨架屏动画
 */

export default function LoadingSkeleton() {
  return (
    <div className="flex-1 overflow-y-auto bg-white">
      <div className="max-w-3xl mx-auto py-6 px-4 space-y-8 animate-pulse">
        {/* 用户消息骨架 */}
        <div className="flex justify-end">
          <div className="max-w-[70%] space-y-2">
            <div className="h-4 bg-gray-200 rounded w-3/4"></div>
            <div className="h-4 bg-gray-200 rounded w-full"></div>
          </div>
        </div>
        {/* AI 消息骨架 */}
        <div className="flex justify-start">
          <div className="max-w-[70%] space-y-2">
            <div className="h-4 bg-gray-200 rounded w-full"></div>
            <div className="h-4 bg-gray-200 rounded w-5/6"></div>
            <div className="h-4 bg-gray-200 rounded w-4/6"></div>
          </div>
        </div>
        {/* 用户消息骨架 */}
        <div className="flex justify-end">
          <div className="max-w-[70%] space-y-2">
            <div className="h-4 bg-gray-200 rounded w-2/3"></div>
          </div>
        </div>
        {/* AI 消息骨架 */}
        <div className="flex justify-start">
          <div className="max-w-[70%] space-y-2">
            <div className="h-4 bg-gray-200 rounded w-full"></div>
            <div className="h-4 bg-gray-200 rounded w-4/5"></div>
          </div>
        </div>
      </div>
    </div>
  );
}
