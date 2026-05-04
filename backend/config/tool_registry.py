"""
工具注册表 — 统一工具元数据 + 同义词表

为 tool_selector 提供结构化的工具元信息（tags / priority / domain），
以及业务同义词扩展表。

设计文档: docs/document/TECH_工具系统统一架构方案.md §四、§十一、§十二
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set


@dataclass(frozen=True)
class ToolEntry:
    """工具注册条目"""

    name: str
    domain: str  # "erp" | "crawler" | "code" | "common"
    description: str
    tags: List[str] = field(default_factory=list)
    priority: int = 2  # 1=本地优先, 2=远程
    always_include: bool = False  # True=常驻工具，不参与筛选
    has_actions: bool = False  # True=有 action enum（ERP 远程工具）


# ============================================================
# 全局注册表（按 domain 分组）
# ============================================================

TOOL_REGISTRY: Dict[str, ToolEntry] = {}


def register(entry: ToolEntry) -> ToolEntry:
    """注册工具到全局表"""
    TOOL_REGISTRY[entry.name] = entry
    return entry


def get_domain_tools(domain: str) -> List[ToolEntry]:
    """获取指定 domain 的所有工具（含 always_include）"""
    return [
        t for t in TOOL_REGISTRY.values()
        if t.domain == domain or t.always_include
    ]


def get_all_tools() -> List[ToolEntry]:
    """获取所有已注册工具"""
    return list(TOOL_REGISTRY.values())


# ============================================================
# ERP 本地工具（priority=1, 毫秒级响应）
# ============================================================

register(ToolEntry(
    name="local_product_identify",
    domain="erp",
    description="本地编码识别（商品编码/SKU/条码/名称模糊搜索）",
    tags=["商品", "编码", "SKU", "条码", "名称", "识别"],
    priority=1,
))
register(ToolEntry(
    name="local_stock_query",
    domain="erp",
    description="查询库存状态（可售/总库存/锁定/在途）",
    tags=["库存", "可售", "锁定", "预占", "仓库"],
    priority=1,
))
register(ToolEntry(
    name="local_data",
    domain="erp",
    description="统一单据查询（订单/采购/售后/收货/上架/采退），支持 summary/detail/export 三种模式",
    tags=[
        # 合并自 7 个旧工具的 tags
        "订单", "下单", "买家", "发货",
        "采购", "采购单", "供应商", "进货",
        "售后", "退货", "退款", "换货", "补发",
        "单据", "单号", "快递号", "流水",
        "全链路", "流转", "采购到销售", "进货到卖出",
        "今天多少单", "排名", "平台对比", "全平台",
        "退货率", "退款率", "毛利", "利润", "客单价",
        "成交额", "销售额", "业绩", "总量", "金额", "营收",
        "多少单", "总数", "占比", "导出", "销量",
        "导出", "下载", "Excel", "报表", "全量数据", "明细数据",
    ],
    priority=1,
))
register(ToolEntry(
    name="local_product_stats",
    domain="erp",
    description="按商品编码查统计数据（销售/采购/售后报表）",
    tags=["统计", "趋势", "销量", "对比", "报表"],
    priority=1,
))
register(ToolEntry(
    name="local_compare_stats",
    domain="erp",
    description="时间维度对比统计（同比/环比，由后端确定计算 weekday）",
    tags=[
        # 对比/同比/环比关键词集中在此工具，避免与 local_data 抢路由
        "对比", "同比", "环比", "比上周", "比上月", "比去年",
        "今天vs昨天", "本周vs上周", "本月vs上月",
        "WoW", "MoM", "YoY", "上周同期", "上月同期", "去年同期",
        "周环比", "月环比", "年同比", "去年同比", "增长率",
    ],
    priority=1,
))
register(ToolEntry(
    name="local_platform_map_query",
    domain="erp",
    description="查ERP编码与平台商品映射",
    tags=["平台映射", "淘宝链接", "店铺商品", "上架", "哪些平台"],
    priority=1,
))
register(ToolEntry(
    name="local_shop_list",
    domain="erp",
    description="查询店铺列表（按平台分组）",
    tags=[
        "店铺", "店铺列表", "哪些店铺", "拼多多店铺", "淘宝店铺",
        "京东店铺", "抖音店铺", "小红书店铺", "1688店铺",
        "所有店铺", "各店铺", "几个店", "开了哪些店",
    ],
    priority=1,
))
register(ToolEntry(
    name="local_warehouse_list",
    domain="erp",
    description="查询仓库列表（实体仓+虚拟仓）",
    tags=[
        "仓库", "仓库列表", "哪些仓库", "仓库地址", "仓库编码",
        "实体仓", "虚拟仓", "几个仓库", "发货仓",
    ],
    priority=1,
))
register(ToolEntry(
    name="local_supplier_list",
    domain="erp",
    description="查询供应商列表（按分类分组）",
    tags=[
        "供应商", "供应商列表", "哪些供应商", "供应商联系方式",
        "供应商编码", "几个供应商", "采购员", "供应商分类",
    ],
    priority=1,
))
register(ToolEntry(
    name="trigger_erp_sync",
    domain="erp",
    description="手动触发ERP数据同步",
    tags=["同步", "刷新", "更新数据"],
    priority=1,
))

# ============================================================
# ERP 远程工具（priority=2, 有 action enum）
# ============================================================

register(ToolEntry(
    name="erp_product_query",
    domain="erp",
    description="查询ERP商品/SKU/库存/品牌/分类信息",
    tags=["商品", "SKU", "库存", "品牌", "分类"],
    priority=2,
    has_actions=True,
))
register(ToolEntry(
    name="erp_trade_query",
    domain="erp",
    description="查询ERP订单/出库/物流/波次信息",
    tags=["订单", "出库", "物流", "快递", "波次"],
    priority=2,
    has_actions=True,
))
register(ToolEntry(
    name="erp_purchase_query",
    domain="erp",
    description="查询ERP供应商/采购单/收货单/上架单",
    tags=["采购", "收货", "上架", "供应商"],
    priority=2,
    has_actions=True,
))
register(ToolEntry(
    name="erp_aftersales_query",
    domain="erp",
    description="查询ERP售后工单/退货/维修单",
    tags=["售后", "退货", "维修", "工单"],
    priority=2,
    has_actions=True,
))
register(ToolEntry(
    name="erp_warehouse_query",
    domain="erp",
    description="查询ERP调拨/入出库/盘点/货位信息",
    tags=[
        "仓库", "调拨", "盘点", "货位",
        "入库", "出库", "移库", "报废", "拣货", "库龄", "复核",
        "盈亏", "利用率", "人效", "温湿度", "批次", "在途",
        "库存位置", "库存调整",
    ],
    priority=2,
    has_actions=True,
))
register(ToolEntry(
    name="erp_info_query",
    domain="erp",
    description="查询ERP基础信息（仓库/店铺/标签/客户）",
    tags=["店铺", "仓库列表", "标签", "客户"],
    priority=2,
    has_actions=True,
))
register(ToolEntry(
    name="erp_taobao_query",
    domain="erp",
    description="查询淘宝/天猫平台订单和售后",
    tags=["淘宝", "天猫", "奇门"],
    priority=2,
    has_actions=True,
))
register(ToolEntry(
    name="erp_execute",
    domain="erp",
    description="执行ERP写操作（新增/修改/删除）",
    tags=["修改", "更新", "创建", "标记"],
    priority=2,
    has_actions=True,
))

# ============================================================
# 文件操作工具（computer domain）
# ============================================================

register(ToolEntry(
    name="file_read",
    domain="computer",
    description="读取workspace内的文件内容",
    tags=["读取", "文件", "查看", "打开", "内容"],
    priority=1,
))
register(ToolEntry(
    name="file_write",
    domain="computer",
    description="在workspace内创建或写入文件",
    tags=["写入", "创建", "保存", "文件"],
    priority=1,
))
register(ToolEntry(
    name="file_list",
    domain="computer",
    description="列出workspace内目录内容",
    tags=["目录", "文件夹", "列表", "ls"],
    priority=1,
))
register(ToolEntry(
    name="file_search",
    domain="computer",
    description="在workspace内搜索文件（按名称或内容）",
    tags=["搜索", "查找", "文件"],
    priority=1,
))

# ============================================================
# 爬虫工具
# ============================================================

register(ToolEntry(
    name="social_crawler",
    domain="crawler",
    description="爬取社交媒体平台搜索结果",
    tags=["小红书", "抖音", "B站", "微博", "知乎", "口碑", "评测"],
    priority=2,
))

# ============================================================
# 常驻工具（always_include=True，不参与筛选）
# ============================================================

register(ToolEntry(
    name="code_execute",
    domain="common",
    description="在安全沙盒中执行Python代码",
    always_include=True,
))
register(ToolEntry(
    name="erp_api_search",
    domain="common",
    description="搜索ERP可用的API操作和参数文档",
    always_include=True,
))
register(ToolEntry(
    name="search_knowledge",
    domain="common",
    description="查询AI知识库获取历史经验",
    always_include=True,
))
register(ToolEntry(
    name="get_conversation_context",
    domain="common",
    description="获取当前对话的最近消息记录",
    always_include=True,
))
register(ToolEntry(
    name="route_to_chat",
    domain="common",
    description="汇总数据回复用户",
    always_include=True,
))
register(ToolEntry(
    name="ask_user",
    domain="common",
    description="信息不足时追问用户",
    always_include=True,
))

# ============================================================
# 同义词表（Level 1 扩展，~50 条覆盖 80% 场景）
# ============================================================

BUSINESS_SYNONYMS: Dict[str, List[str]] = {
    # === 单字动词（jieba 可能拆出单字，需要精确匹配分词结果）===
    "卖": ["销量", "订单", "出库"],
    "退": ["售后", "退货", "退款"],
    "买": ["采购", "进货"],
    "发": ["发货", "物流", "快递"],
    "赚": ["利润", "毛利", "成本"],
    "亏": ["利润", "成本", "亏损"],

    # === 2 字动词（jieba 能正确分出）===
    "卖了": ["销量", "订单", "出库"],
    "卖出": ["销量", "订单", "出库"],
    "退了": ["售后", "退货", "退款"],
    "退回": ["售后", "退货", "退款"],
    "买了": ["采购", "进货"],
    "发了": ["发货", "物流", "快递"],
    "发出": ["发货", "物流", "快递"],
    "到了": ["物流", "快递", "签收"],
    "到货": ["物流", "快递", "签收", "采购"],
    "赚了": ["利润", "毛利", "成本"],
    "亏了": ["利润", "成本", "亏损"],
    "发货": ["物流", "快递", "出库"],
    "退货": ["售后", "退款", "换货"],
    "退款": ["售后", "退货"],
    "采购": ["供应商", "进货", "采购单"],
    "进货": ["采购", "供应商"],
    "签收": ["物流", "快递"],
    "盘点": ["仓库", "库存"],
    "调拨": ["仓库", "库存"],
    "多少": ["统计", "数量"],

    # === 3+ 字口语短语（用子串匹配）===
    "跟得上": ["库存", "销量"],
    "缺货": ["库存", "预警", "可售"],
    "爆单": ["订单", "销量", "统计"],
    "爆了": ["订单", "销量", "统计"],
    "断货": ["库存", "预警", "可售"],
    "到哪了": ["物流", "快递"],
    "到哪": ["物流", "快递"],
    "多少钱": ["价格", "成本", "金额"],
    "卖得好": ["销量", "排名", "统计"],
    "多少单": ["订单", "统计"],
    "多少件": ["销量", "统计", "数量"],

    # === 简称 → 全称 ===
    "淘宝": ["天猫", "淘宝", "奇门"],
    "拼多多": ["拼多多", "PDD"],
    "抖音": ["抖店", "抖音"],
}


def _segment_with_merge(text: str) -> List[str]:
    """jieba 分词 + 相邻单字合并

    解决 jieba 不一致问题：有时 "卖了" → ["卖", "了"]，有时 → ["卖了"]。
    合并相邻的（单字 + "了"/"的"/"过"/"着"）为完整动词。
    """
    import jieba
    raw = list(jieba.cut(text))
    merged: List[str] = []
    i = 0
    while i < len(raw):
        word = raw[i]
        # 单字动词 + 助词 → 合并（"卖" + "了" → "卖了"）
        if (
            len(word) == 1
            and i + 1 < len(raw)
            and raw[i + 1] in ("了", "的", "过", "着")
        ):
            merged.append(word + raw[i + 1])
            i += 2
        else:
            merged.append(word)
            i += 1
    return merged


def expand_synonyms(user_input: str) -> Set[str]:
    """同义词扩展（jieba 分词 + 精确词匹配，消除单字误匹配）

    策略：
    1. jieba 分词 + 相邻单字合并（"卖"+"了"→"卖了"）
    2. 分词结果与同义词表精确匹配（非子串）— 消除"发票"误匹配"发"
    3. 3+ 字口语短语用子串匹配（"到哪了"、"卖得好"）

    Returns:
        扩展后的关键词集合
    """
    expanded: Set[str] = set()
    words = _segment_with_merge(user_input)
    word_set = set(words)

    for keyword, synonyms in BUSINESS_SYNONYMS.items():
        if len(keyword) >= 3:
            # 3+ 字口语短语：子串匹配（"跟得上" in "库存跟得上吗"）
            if keyword in user_input:
                expanded.update(synonyms)
        else:
            # 1-2 字词：分词精确匹配（"卖" 只匹配分词出的 "卖"，不匹配 "卖点"）
            if keyword in word_set:
                expanded.update(synonyms)

    return expanded
