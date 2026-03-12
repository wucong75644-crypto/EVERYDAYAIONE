"""
IntentRouter 路由准确率评测

运行模式:
  Mock 模式（CI）: pytest backend/tests/test_routing_eval.py -v
  Real API 模式: ROUTING_EVAL_REAL=1 python -m pytest backend/tests/test_routing_eval.py -v -s
"""

import json
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from schemas.message import GenerationType, TextPart
from services.intent_router import IntentRouter, RoutingDecision
from config.smart_model_config import TOOL_TO_TYPE


# ============================================================
# 数据加载
# ============================================================

FIXTURES_DIR = Path(__file__).parent / "fixtures"
EVAL_CASES_PATH = FIXTURES_DIR / "routing_eval_cases.json"
EVAL_REPORT_PATH = FIXTURES_DIR / "routing_eval_report.json"

USE_REAL_API = os.environ.get("ROUTING_EVAL_REAL", "0") == "1"


@dataclass
class EvalCase:
    """单个评测用例"""
    id: str
    input: str
    expected_tool: str
    expected_type: str
    tags: List[str] = field(default_factory=list)
    difficulty: str = "easy"
    notes: Optional[str] = None


@dataclass
class EvalResult:
    """单个评测结果"""
    case_id: str
    expected_tool: str
    actual_tool: str
    expected_type: str
    actual_type: str
    tool_correct: bool
    type_correct: bool
    latency_ms: float
    routed_by: str
    tags: List[str]
    difficulty: str


def load_eval_cases() -> List[EvalCase]:
    """加载评测数据集"""
    with open(EVAL_CASES_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return [EvalCase(**c) for c in data["cases"]]


# ============================================================
# 评测执行器
# ============================================================


class EvalRunner:
    """路由评测执行器"""

    def __init__(self, router: IntentRouter):
        self._router = router
        self._results: List[EvalResult] = []

    async def run_case(self, case: EvalCase) -> EvalResult:
        """执行单个评测用例"""
        content = [TextPart(text=case.input)]
        start = time.perf_counter()
        try:
            decision = await self._router.route(
                content=content,
                user_id="eval-user",
                conversation_id="eval-conv",
            )
        except Exception as e:
            # 路由失败时构造一个空决策
            decision = RoutingDecision(
                generation_type=GenerationType.CHAT,
                raw_tool_name="error",
                routed_by=f"error:{type(e).__name__}",
            )
        latency_ms = (time.perf_counter() - start) * 1000

        actual_tool = decision.raw_tool_name
        actual_type = decision.generation_type.value

        result = EvalResult(
            case_id=case.id,
            expected_tool=case.expected_tool,
            actual_tool=actual_tool,
            expected_type=case.expected_type,
            actual_type=actual_type,
            tool_correct=actual_tool == case.expected_tool,
            type_correct=actual_type == case.expected_type,
            latency_ms=latency_ms,
            routed_by=decision.routed_by,
            tags=case.tags,
            difficulty=case.difficulty,
        )
        self._results.append(result)
        return result

    async def run_all(self, cases: List[EvalCase]) -> List[EvalResult]:
        """执行所有评测用例"""
        for case in cases:
            await self.run_case(case)
        return self._results

    def generate_report(self) -> Dict[str, Any]:
        """生成评测报告"""
        total = len(self._results)
        if total == 0:
            return {"error": "No results"}

        tool_correct = sum(1 for r in self._results if r.tool_correct)
        type_correct = sum(1 for r in self._results if r.type_correct)

        # 按类型分组
        by_type: Dict[str, Dict] = defaultdict(lambda: {"total": 0, "correct": 0})
        for r in self._results:
            by_type[r.expected_type]["total"] += 1
            if r.type_correct:
                by_type[r.expected_type]["correct"] += 1

        # 按难度分组
        by_difficulty: Dict[str, Dict] = defaultdict(lambda: {"total": 0, "correct": 0})
        for r in self._results:
            by_difficulty[r.difficulty]["total"] += 1
            if r.tool_correct:
                by_difficulty[r.difficulty]["correct"] += 1

        # 混淆矩阵
        confusion: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for r in self._results:
            confusion[r.expected_tool][r.actual_tool] += 1

        # 按标签分组
        by_tag: Dict[str, Dict] = defaultdict(lambda: {"total": 0, "correct": 0})
        for r in self._results:
            for tag in r.tags:
                by_tag[tag]["total"] += 1
                if r.tool_correct:
                    by_tag[tag]["correct"] += 1

        # 失败列表
        failures = [
            {
                "case_id": r.case_id,
                "expected": r.expected_tool,
                "actual": r.actual_tool,
                "routed_by": r.routed_by,
            }
            for r in self._results if not r.tool_correct
        ]

        latencies = [r.latency_ms for r in self._results]

        return {
            "summary": {
                "total_cases": total,
                "tool_accuracy": round(tool_correct / total, 4),
                "type_accuracy": round(type_correct / total, 4),
                "tool_correct": tool_correct,
                "tool_wrong": total - tool_correct,
                "avg_latency_ms": round(sum(latencies) / len(latencies), 1),
            },
            "by_type": {
                k: {**v, "accuracy": round(v["correct"] / v["total"], 4)}
                for k, v in by_type.items()
            },
            "by_difficulty": {
                k: {**v, "accuracy": round(v["correct"] / v["total"], 4)}
                for k, v in by_difficulty.items()
            },
            "by_tag": {
                k: {**v, "accuracy": round(v["correct"] / v["total"], 4)}
                for k, v in sorted(by_tag.items())
            },
            "confusion_matrix": {k: dict(v) for k, v in confusion.items()},
            "failures": failures,
            "mode": "real_api" if USE_REAL_API else "mock",
        }


# ============================================================
# Mock 模式测试（CI 用，验证框架 + 数据集）
# ============================================================


class TestEvalDataset:
    """评测数据集 Schema 验证"""

    def test_dataset_loads(self):
        """数据集能正常加载"""
        cases = load_eval_cases()
        assert len(cases) >= 100, f"数据集太小：{len(cases)} 条（需 ≥100）"

    def test_schema_valid(self):
        """所有用例 schema 正确"""
        cases = load_eval_cases()
        valid_tools = {"text_chat", "generate_image", "generate_video", "web_search"}
        valid_types = {"chat", "image", "video"}
        valid_difficulties = {"easy", "medium", "hard"}

        for case in cases:
            assert case.expected_tool in valid_tools, f"{case.id}: 无效 tool={case.expected_tool}"
            assert case.expected_type in valid_types, f"{case.id}: 无效 type={case.expected_type}"
            assert case.difficulty in valid_difficulties, f"{case.id}: 无效 difficulty"
            assert len(case.input) > 0, f"{case.id}: input 为空"
            assert len(case.tags) > 0, f"{case.id}: 缺少 tags"

    def test_ids_unique(self):
        """用例 ID 唯一"""
        cases = load_eval_cases()
        ids = [c.id for c in cases]
        assert len(ids) == len(set(ids)), "存在重复 ID"

    def test_type_distribution(self):
        """各类型覆盖度检查"""
        cases = load_eval_cases()
        type_counts = defaultdict(int)
        for c in cases:
            type_counts[c.expected_type] += 1

        assert type_counts["chat"] >= 40, f"chat 用例太少：{type_counts['chat']}"
        assert type_counts["image"] >= 15, f"image 用例太少：{type_counts['image']}"
        assert type_counts["video"] >= 8, f"video 用例太少：{type_counts['video']}"

    def test_difficulty_distribution(self):
        """难度等级覆盖"""
        cases = load_eval_cases()
        diffs = {c.difficulty for c in cases}
        assert "easy" in diffs
        assert "hard" in diffs

    def test_report_generation_logic(self):
        """报告生成逻辑（用构造数据验证）"""
        runner = EvalRunner(MagicMock())
        runner._results = [
            EvalResult(
                case_id="test_001", expected_tool="text_chat", actual_tool="text_chat",
                expected_type="chat", actual_type="chat", tool_correct=True,
                type_correct=True, latency_ms=100.0, routed_by="model",
                tags=["chat"], difficulty="easy",
            ),
            EvalResult(
                case_id="test_002", expected_tool="generate_image", actual_tool="text_chat",
                expected_type="image", actual_type="chat", tool_correct=False,
                type_correct=False, latency_ms=200.0, routed_by="model",
                tags=["image"], difficulty="hard",
            ),
        ]
        report = runner.generate_report()
        assert report["summary"]["total_cases"] == 2
        assert report["summary"]["tool_accuracy"] == 0.5
        assert report["summary"]["type_accuracy"] == 0.5
        assert len(report["failures"]) == 1
        assert report["failures"][0]["case_id"] == "test_002"
        assert "text_chat" in report["confusion_matrix"]
        assert "generate_image" in report["confusion_matrix"]


# ============================================================
# Real API 模式测试（需要 DASHSCOPE_API_KEY）
# ============================================================


@pytest.mark.skipif(
    not USE_REAL_API,
    reason="Real API eval: set ROUTING_EVAL_REAL=1 to run",
)
class TestRoutingEvalReal:
    """真实 API 评测"""

    @pytest.mark.asyncio
    async def test_full_eval(self):
        """完整路由准确率评测"""
        cases = load_eval_cases()
        router = IntentRouter()

        try:
            runner = EvalRunner(router)
            await runner.run_all(cases)
            report = runner.generate_report()

            # 保存报告
            EVAL_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(EVAL_REPORT_PATH, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)

            # 打印摘要
            s = report["summary"]
            print(f"\n{'='*60}")
            print(f"  Routing Eval Report")
            print(f"{'='*60}")
            print(f"  Total: {s['total_cases']}")
            print(f"  Tool Accuracy: {s['tool_accuracy']:.2%}")
            print(f"  Type Accuracy: {s['type_accuracy']:.2%}")
            print(f"  Avg Latency:  {s['avg_latency_ms']:.0f}ms")
            print(f"  Failures:     {len(report['failures'])}")
            for f_item in report["failures"][:10]:
                print(f"    - {f_item['case_id']}: expected={f_item['expected']} actual={f_item['actual']}")
            print(f"{'='*60}")

            # 准确率阈值断言
            assert s["tool_accuracy"] >= 0.85, (
                f"Tool accuracy {s['tool_accuracy']:.2%} below 85%"
            )
            assert s["type_accuracy"] >= 0.90, (
                f"Type accuracy {s['type_accuracy']:.2%} below 90%"
            )

        finally:
            await router.close()
