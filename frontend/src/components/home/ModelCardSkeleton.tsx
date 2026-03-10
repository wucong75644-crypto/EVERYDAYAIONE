/**
 * 模型卡片骨架屏
 *
 * 订阅列表加载期间的卡片占位组件。
 */

export default function ModelCardSkeleton() {
  return (
    <div className="bg-white rounded-xl border border-gray-200 flex flex-col animate-pulse">
      <div className="p-4 flex-1">
        {/* 标签占位 */}
        <div className="h-5 w-12 bg-gray-200 rounded-full" />

        {/* 名称占位 */}
        <div className="h-5 w-3/4 bg-gray-200 rounded mt-2" />

        {/* 描述占位 */}
        <div className="h-4 w-full bg-gray-200 rounded mt-1.5" />

        {/* 能力标签占位 */}
        <div className="flex gap-1.5 mt-3">
          <div className="h-5 w-12 bg-gray-200 rounded" />
          <div className="h-5 w-12 bg-gray-200 rounded" />
          <div className="h-5 w-12 bg-gray-200 rounded" />
        </div>

        {/* 费用占位 */}
        <div className="h-4 w-16 bg-gray-200 rounded mt-2" />
      </div>

      {/* 按钮区占位 */}
      <div className="border-t border-gray-100 px-4 py-3 flex justify-center">
        <div className="h-4 w-16 bg-gray-200 rounded" />
      </div>
    </div>
  );
}
