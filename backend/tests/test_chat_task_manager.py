"""聊天内定时任务管理器单测

覆盖：
- NL解析 → 表单构建（create）
- 任务列表（list）
- 任务查找（_find_task）
- 表单提交处理（_submit_create / _submit_update）
- 暂停/恢复/删除
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.scheduler.chat_task_manager import (
    ChatTaskManager,
    FormBlockResult,
    handle_form_submit,
    _build_create_form,
    _build_form_field,
    _build_update_form,
    _calc_once_run_at,
    _load_push_targets,
)


# ════════════════════════════════════════════════════════
# Fixtures
# ════════════════════════════════════════════════════════

def _mock_db():
    """构造 mock DB"""
    db = MagicMock()
    db.table.return_value = db
    db.select.return_value = db
    db.eq.return_value = db
    db.ilike.return_value = db
    db.order.return_value = db
    db.limit.return_value = db
    db.insert.return_value = db
    db.update.return_value = db
    db.delete.return_value = db
    db.in_.return_value = db
    db.execute.return_value = MagicMock(data=[])
    return db


# ════════════════════════════════════════════════════════
# _build_create_form
# ════════════════════════════════════════════════════════

class TestBuildCreateForm:
    def test_form_structure(self):
        parsed = {"name": "日报", "prompt": "汇总日报", "schedule_type": "daily", "time_str": "09:00"}
        targets = [{"label": "推送给我（网页）", "value": '{"type":"web","user_id":"u1"}'}]
        form = _build_create_form(parsed, targets)

        assert form["type"] == "form"
        assert form["form_type"] == "scheduled_task_create"
        assert "创建定时任务" in form["title"]
        assert len(form["fields"]) == 7  # name, prompt, schedule_type, time_str, weekdays, day_of_month, push_target

    def test_default_values_from_parsed(self):
        parsed = {"name": "周报", "prompt": "推周报", "schedule_type": "weekly", "time_str": "10:00"}
        targets = [{"label": "web", "value": "{}"}]
        form = _build_create_form(parsed, targets)

        field_map = {f["name"]: f for f in form["fields"]}
        assert field_map["name"]["default_value"] == "周报"
        assert field_map["prompt"]["default_value"] == "推周报"
        assert field_map["schedule_type"]["default_value"] == "weekly"
        assert field_map["time_str"]["default_value"] == "10:00"

    def test_weekdays_visible_when(self):
        parsed = {"name": "t", "prompt": "p", "schedule_type": "daily"}
        form = _build_create_form(parsed, [{"label": "w", "value": "{}"}])

        field_map = {f["name"]: f for f in form["fields"]}
        assert field_map["weekdays"]["visible_when"] == {"field": "schedule_type", "value": "weekly"}
        assert field_map["day_of_month"]["visible_when"] == {"field": "schedule_type", "value": "monthly"}


# ════════════════════════════════════════════════════════
# _build_update_form
# ════════════════════════════════════════════════════════

class TestBuildUpdateForm:
    def test_update_form_prefills_current(self):
        task = {
            "id": "abc-123",
            "name": "旧任务",
            "prompt": "旧指令",
            "schedule_type": "daily",
            "cron_expr": "0 9 * * *",
            "push_target": {"type": "web", "user_id": "u1"},
        }
        form = _build_update_form(task, {}, [{"label": "web", "value": "{}"}])

        assert form["form_type"] == "scheduled_task_update"
        field_map = {f["name"]: f for f in form["fields"]}
        assert field_map["task_id"]["default_value"] == "abc-123"
        assert field_map["name"]["default_value"] == "旧任务"
        assert field_map["time_str"]["default_value"] == "09:00"

    def test_update_form_applies_changes(self):
        task = {"id": "x", "name": "old", "prompt": "old", "schedule_type": "daily",
                "cron_expr": "0 9 * * *", "push_target": {}}
        changes = {"name": "new", "time_str": "10:30"}
        form = _build_update_form(task, changes, [{"label": "w", "value": "{}"}])

        field_map = {f["name"]: f for f in form["fields"]}
        assert field_map["name"]["default_value"] == "new"
        assert field_map["time_str"]["default_value"] == "10:30"


# ════════════════════════════════════════════════════════
# _load_push_targets
# ════════════════════════════════════════════════════════

class TestLoadPushTargets:
    @pytest.mark.asyncio
    async def test_always_includes_web_self(self):
        db = _mock_db()
        targets = await _load_push_targets(db, "user1", "org1")
        assert len(targets) >= 1
        assert "推送给我（网页）" in targets[0]["label"]
        parsed = json.loads(targets[0]["value"])
        assert parsed["type"] == "web"
        assert parsed["user_id"] == "user1"

    @pytest.mark.asyncio
    async def test_includes_wecom_groups(self):
        db = _mock_db()
        # wecom_user_mappings 无匹配
        db.execute.side_effect = [
            MagicMock(data=[]),  # wecom_user_mappings
            MagicMock(data=[{"chatid": "g1", "chat_name": "测试群"}]),  # wecom_chat_targets
        ]
        targets = await _load_push_targets(db, "u1", "org1")
        group_targets = [t for t in targets if "企微群" in t["label"]]
        assert len(group_targets) == 1
        assert "测试群" in group_targets[0]["label"]


# ════════════════════════════════════════════════════════
# ChatTaskManager.handle
# ════════════════════════════════════════════════════════

class TestChatTaskManagerHandle:
    @pytest.mark.asyncio
    async def test_create_returns_form(self):
        db = _mock_db()
        mgr = ChatTaskManager(db, "user1", "org1")

        with patch("services.scheduler.chat_task_manager.parse_task_nl", new_callable=AsyncMock) as mock_parse:
            mock_parse.return_value = {
                "name": "日报", "prompt": "汇总日报", "schedule_type": "daily", "time_str": "09:00",
            }
            result = await mgr.handle("create", {"description": "每天早上9点推日报"})

        assert result["type"] == "form"
        assert result["form_type"] == "scheduled_task_create"

    @pytest.mark.asyncio
    async def test_create_without_description(self):
        db = _mock_db()
        mgr = ChatTaskManager(db, "user1", "org1")
        result = await mgr.handle("create", {"description": ""})
        assert result["type"] == "text"
        assert "描述" in result["text"]

    @pytest.mark.asyncio
    async def test_list_empty(self):
        db = _mock_db()
        mgr = ChatTaskManager(db, "user1", "org1")
        result = await mgr.handle("list", {})
        assert result["type"] == "text"
        assert "还没有" in result["text"]

    @pytest.mark.asyncio
    async def test_list_with_tasks(self):
        db = _mock_db()
        db.execute.return_value = MagicMock(data=[
            {"id": "t1", "name": "日报", "status": "active",
             "schedule_type": "daily", "cron_expr": "0 9 * * *",
             "next_run_at": "2026-04-27T01:00:00Z", "run_count": 5},
        ])
        mgr = ChatTaskManager(db, "user1", "org1")
        result = await mgr.handle("list", {})
        assert result["type"] == "text"
        assert "日报" in result["text"]
        assert "每天 09:00" in result["text"]

    @pytest.mark.asyncio
    async def test_pause(self):
        db = _mock_db()
        db.execute.return_value = MagicMock(data=[
            {"id": "t1", "name": "日报", "status": "active"},
        ])
        mgr = ChatTaskManager(db, "user1", "org1")
        result = await mgr.handle("pause", {"task_name": "日报"})
        assert "暂停" in result["text"]

    @pytest.mark.asyncio
    async def test_pause_already_paused(self):
        db = _mock_db()
        db.execute.return_value = MagicMock(data=[
            {"id": "t1", "name": "日报", "status": "paused"},
        ])
        mgr = ChatTaskManager(db, "user1", "org1")
        result = await mgr.handle("pause", {"task_name": "日报"})
        assert "已经是暂停" in result["text"]

    @pytest.mark.asyncio
    async def test_resume(self):
        db = _mock_db()
        db.execute.return_value = MagicMock(data=[
            {"id": "t1", "name": "日报", "status": "paused",
             "cron_expr": "0 9 * * *", "timezone": "Asia/Shanghai"},
        ])
        mgr = ChatTaskManager(db, "user1", "org1")
        result = await mgr.handle("resume", {"task_name": "日报"})
        assert "恢复" in result["text"]

    @pytest.mark.asyncio
    async def test_delete(self):
        db = _mock_db()
        db.execute.return_value = MagicMock(data=[
            {"id": "t1", "name": "日报", "status": "active"},
        ])
        mgr = ChatTaskManager(db, "user1", "org1")
        result = await mgr.handle("delete", {"task_name": "日报"})
        assert "删除" in result["text"]

    @pytest.mark.asyncio
    async def test_unknown_action(self):
        db = _mock_db()
        mgr = ChatTaskManager(db, "user1", "org1")
        result = await mgr.handle("fly", {})
        assert "不支持" in result["text"]


# ════════════════════════════════════════════════════════
# handle_form_submit
# ════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════
# _calc_once_run_at（月末溢出修复验证）
# ════════════════════════════════════════════════════════

class TestCalcOnceRunAt:
    def test_future_time_today(self):
        """今天还没到的时间 → 返回今天"""
        import time_machine
        from datetime import datetime as dt
        from zoneinfo import ZoneInfo
        with time_machine.travel(dt(2026, 4, 26, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai"))):
            result = _calc_once_run_at("22:00", "Asia/Shanghai")
            assert result.day == 26
            assert result.hour == 22

    def test_past_time_today_goes_tomorrow(self):
        """今天已过的时间 → 返回明天"""
        import time_machine
        from datetime import datetime as dt
        from zoneinfo import ZoneInfo
        with time_machine.travel(dt(2026, 4, 26, 23, 0, tzinfo=ZoneInfo("Asia/Shanghai"))):
            result = _calc_once_run_at("09:00", "Asia/Shanghai")
            assert result.day == 27

    def test_month_end_no_overflow(self):
        """月末最后一天 → timedelta 正确跨月，不崩溃"""
        import time_machine
        from datetime import datetime as dt
        from zoneinfo import ZoneInfo
        # 4月30日 23:30，设置 09:00 → 应该是5月1日
        with time_machine.travel(dt(2026, 4, 30, 23, 30, tzinfo=ZoneInfo("Asia/Shanghai"))):
            result = _calc_once_run_at("09:00", "Asia/Shanghai")
            assert result.month == 5
            assert result.day == 1

    def test_year_end_no_overflow(self):
        """12月31日 → 跨年"""
        import time_machine
        from datetime import datetime as dt
        from zoneinfo import ZoneInfo
        with time_machine.travel(dt(2026, 12, 31, 23, 0, tzinfo=ZoneInfo("Asia/Shanghai"))):
            result = _calc_once_run_at("09:00", "Asia/Shanghai")
            assert result.year == 2027
            assert result.month == 1
            assert result.day == 1


# ════════════════════════════════════════════════════════
# handle_form_submit（含权限校验）
# ════════════════════════════════════════════════════════

class TestHandleFormSubmit:
    @pytest.mark.asyncio
    @patch("services.permissions.checker.check_permission", new_callable=AsyncMock, return_value=True)
    async def test_create_success(self, _mock_perm):
        db = _mock_db()
        data = {
            "name": "日报",
            "prompt": "汇总日报",
            "schedule_type": "daily",
            "time_str": "09:00",
            "push_target": '{"type":"web","user_id":"u1"}',
        }
        result = await handle_form_submit(db, "u1", "org1", "scheduled_task_create", data)
        assert result["success"] is True
        assert "日报" in result["message"]

    @pytest.mark.asyncio
    @patch("services.permissions.checker.check_permission", new_callable=AsyncMock, return_value=False)
    async def test_create_denied(self, _mock_perm):
        """权限拒绝 → 不创建任务"""
        db = _mock_db()
        data = {"name": "日报", "prompt": "汇总", "schedule_type": "daily",
                "time_str": "09:00", "push_target": "{}"}
        result = await handle_form_submit(db, "u1", "org1", "scheduled_task_create", data)
        assert result["success"] is False
        assert "无权" in result["message"]

    @pytest.mark.asyncio
    @patch("services.permissions.checker.check_permission", new_callable=AsyncMock, return_value=True)
    async def test_create_missing_name(self, _mock_perm):
        db = _mock_db()
        data = {"name": "", "prompt": "test", "schedule_type": "daily", "time_str": "09:00",
                "push_target": "{}"}
        result = await handle_form_submit(db, "u1", "org1", "scheduled_task_create", data)
        assert result["success"] is False
        assert "名称" in result["message"]

    @pytest.mark.asyncio
    @patch("services.permissions.checker.check_permission", new_callable=AsyncMock, return_value=True)
    async def test_create_weekly(self, _mock_perm):
        db = _mock_db()
        data = {
            "name": "周报",
            "prompt": "推周报",
            "schedule_type": "weekly",
            "time_str": "10:00",
            "weekdays": [1, 3, 5],
            "push_target": '{"type":"web","user_id":"u1"}',
        }
        result = await handle_form_submit(db, "u1", "org1", "scheduled_task_create", data)
        assert result["success"] is True

    @pytest.mark.asyncio
    @patch("services.permissions.checker.check_permission", new_callable=AsyncMock, return_value=True)
    async def test_update_missing_task_id(self, _mock_perm):
        db = _mock_db()
        result = await handle_form_submit(db, "u1", "org1", "scheduled_task_update", {})
        assert result["success"] is False

    @pytest.mark.asyncio
    @patch("services.permissions.checker.check_permission", new_callable=AsyncMock, return_value=True)
    async def test_update_task_not_found(self, _mock_perm):
        db = _mock_db()
        result = await handle_form_submit(db, "u1", "org1", "scheduled_task_update",
                                          {"task_id": "nonexistent"})
        assert result["success"] is False
        assert "不存在" in result["message"]

    @pytest.mark.asyncio
    @patch("services.permissions.checker.check_permission", new_callable=AsyncMock, return_value=False)
    async def test_update_denied(self, _mock_perm):
        """权限拒绝 → 不修改任务"""
        db = _mock_db()
        result = await handle_form_submit(db, "u1", "org1", "scheduled_task_update",
                                          {"task_id": "some-id"})
        assert result["success"] is False
        assert "无权" in result["message"]

    @pytest.mark.asyncio
    async def test_unknown_form_type(self):
        db = _mock_db()
        result = await handle_form_submit(db, "u1", "org1", "alien_form", {})
        assert result["success"] is False

    @pytest.mark.asyncio
    @patch("services.permissions.checker.check_permission", new_callable=AsyncMock, return_value=True)
    async def test_create_once_type(self, _mock_perm):
        """once 类型→生成 run_at 而非 cron_expr"""
        db = _mock_db()
        data = {
            "name": "一次性推送",
            "prompt": "推一次",
            "schedule_type": "once",
            "time_str": "22:00",
            "push_target": '{"type":"web","user_id":"u1"}',
        }
        result = await handle_form_submit(db, "u1", "org1", "scheduled_task_create", data)
        assert result["success"] is True
        # 验证 insert 被调用且 cron_expr=None
        insert_call = db.insert.call_args
        assert insert_call is not None
        row = insert_call[0][0]
        assert row["cron_expr"] is None
        assert row["schedule_type"] == "once"
        assert row["run_at"] is not None

    @pytest.mark.asyncio
    @patch("services.permissions.checker.check_permission", new_callable=AsyncMock, return_value=True)
    async def test_create_monthly(self, _mock_perm):
        """monthly 类型→正确设置 day_of_month"""
        db = _mock_db()
        data = {
            "name": "月报",
            "prompt": "推月报",
            "schedule_type": "monthly",
            "time_str": "14:00",
            "day_of_month": 15,
            "push_target": '{"type":"web","user_id":"u1"}',
        }
        result = await handle_form_submit(db, "u1", "org1", "scheduled_task_create", data)
        assert result["success"] is True
        row = db.insert.call_args[0][0]
        assert row["day_of_month"] == 15
        assert "15" in row["cron_expr"]

    @pytest.mark.asyncio
    @patch("services.permissions.checker.check_permission", new_callable=AsyncMock, return_value=True)
    async def test_create_missing_prompt(self, _mock_perm):
        """缺执行内容→拒绝"""
        db = _mock_db()
        data = {"name": "test", "prompt": "", "schedule_type": "daily",
                "time_str": "09:00", "push_target": "{}"}
        result = await handle_form_submit(db, "u1", "org1", "scheduled_task_create", data)
        assert result["success"] is False
        assert "执行内容" in result["message"]

    @pytest.mark.asyncio
    @patch("services.permissions.checker.check_permission", new_callable=AsyncMock, return_value=True)
    async def test_update_success(self, _mock_perm):
        """修改任务成功路径"""
        db = _mock_db()
        db.execute.return_value = MagicMock(data=[
            {"id": "task-123", "name": "旧", "prompt": "旧指令",
             "schedule_type": "daily", "user_id": "u1", "org_id": "org1"},
        ])
        data = {
            "task_id": "task-123",
            "name": "新名称",
            "prompt": "新指令",
            "schedule_type": "daily",
            "time_str": "10:00",
            "push_target": '{"type":"web","user_id":"u1"}',
        }
        result = await handle_form_submit(db, "u1", "org1", "scheduled_task_update", data)
        assert result["success"] is True
        assert "新名称" in result["message"]


# ════════════════════════════════════════════════════════
# _build_form_field
# ════════════════════════════════════════════════════════

class TestBuildFormField:
    def test_minimal_field(self):
        f = _build_form_field("name", "text", "名称")
        assert f["type"] == "text"
        assert f["name"] == "name"
        assert f["label"] == "名称"
        assert f["required"] is False
        assert "default_value" not in f
        assert "placeholder" not in f

    def test_full_field(self):
        f = _build_form_field(
            "freq", "select", "频率",
            required=True,
            default_value="daily",
            placeholder="选择",
            options=[{"label": "每天", "value": "daily"}],
            visible_when={"field": "x", "value": "y"},
        )
        assert f["required"] is True
        assert f["default_value"] == "daily"
        assert f["placeholder"] == "选择"
        assert len(f["options"]) == 1
        assert f["visible_when"]["field"] == "x"

    def test_hidden_field(self):
        f = _build_form_field("task_id", "hidden", "", default_value="abc")
        assert f["type"] == "hidden"
        assert f["default_value"] == "abc"


# ════════════════════════════════════════════════════════
# FormBlockResult
# ════════════════════════════════════════════════════════

class TestFormBlockResult:
    def test_construction(self):
        r = FormBlockResult(form={"type": "form", "title": "test"}, llm_hint="hint")
        assert r.form["title"] == "test"
        assert r.llm_hint == "hint"

    def test_default_hint(self):
        r = FormBlockResult(form={})
        assert r.llm_hint == ""


# ════════════════════════════════════════════════════════
# ChatTaskManager._find_task
# ════════════════════════════════════════════════════════

class TestFindTask:
    @pytest.mark.asyncio
    async def test_find_by_short_id(self):
        db = _mock_db()
        db.execute.return_value = MagicMock(data=[
            {"id": "abcd1234-full-uuid", "name": "日报"},
        ])
        mgr = ChatTaskManager(db, "u1", "org1")
        task = await mgr._find_task(task_id="abcd1234")
        assert task is not None
        assert task["name"] == "日报"

    @pytest.mark.asyncio
    async def test_find_by_name(self):
        db = _mock_db()
        # task_id="" 是 falsy，跳过短ID路径，直接走名称路径
        db.execute.return_value = MagicMock(data=[{"id": "t1", "name": "销售日报"}])
        mgr = ChatTaskManager(db, "u1", "org1")
        task = await mgr._find_task(task_name="日报")
        assert task is not None
        assert task["name"] == "销售日报"

    @pytest.mark.asyncio
    async def test_find_nothing(self):
        db = _mock_db()
        mgr = ChatTaskManager(db, "u1", "org1")
        task = await mgr._find_task(task_id="", task_name="")
        assert task is None

    @pytest.mark.asyncio
    async def test_find_short_id_no_match(self):
        db = _mock_db()
        db.execute.return_value = MagicMock(data=[
            {"id": "xxxx-yyyy", "name": "不匹配"},
        ])
        mgr = ChatTaskManager(db, "u1", "org1")
        task = await mgr._find_task(task_id="abcd")
        assert task is None


# ════════════════════════════════════════════════════════
# ChatTaskManager._handle_update
# ════════════════════════════════════════════════════════

class TestHandleUpdate:
    @pytest.mark.asyncio
    async def test_update_returns_form(self):
        db = _mock_db()
        db.execute.return_value = MagicMock(data=[
            {"id": "t1", "name": "日报", "prompt": "推日报", "status": "active",
             "schedule_type": "daily", "cron_expr": "0 9 * * *",
             "push_target": {"type": "web", "user_id": "u1"}},
        ])
        mgr = ChatTaskManager(db, "u1", "org1")
        with patch("services.scheduler.chat_task_manager.parse_task_nl", new_callable=AsyncMock) as mock_parse:
            mock_parse.return_value = {"schedule_type": "weekly", "time_str": "10:00", "weekdays": [1, 3, 5]}
            result = await mgr.handle("update", {"task_name": "日报", "description": "改成每周一三五10点"})
        assert result["type"] == "form"
        assert result["form_type"] == "scheduled_task_update"
        field_map = {f["name"]: f for f in result["fields"]}
        assert field_map["schedule_type"]["default_value"] == "weekly"

    @pytest.mark.asyncio
    async def test_update_task_not_found(self):
        db = _mock_db()
        mgr = ChatTaskManager(db, "u1", "org1")
        result = await mgr.handle("update", {"task_name": "不存在的任务"})
        assert result["type"] == "text"
        assert "未找到" in result["text"]

    @pytest.mark.asyncio
    async def test_update_without_description(self):
        """不传 description → 返回当前值的表单（无变更覆盖）"""
        db = _mock_db()
        db.execute.return_value = MagicMock(data=[
            {"id": "t1", "name": "日报", "prompt": "p", "status": "active",
             "schedule_type": "daily", "cron_expr": "0 9 * * *",
             "push_target": {}},
        ])
        mgr = ChatTaskManager(db, "u1", "org1")
        result = await mgr.handle("update", {"task_name": "日报"})
        assert result["type"] == "form"
        field_map = {f["name"]: f for f in result["fields"]}
        assert field_map["name"]["default_value"] == "日报"


# ════════════════════════════════════════════════════════
# _load_push_targets 补充
# ════════════════════════════════════════════════════════

class TestLoadPushTargetsExtended:
    @pytest.mark.asyncio
    async def test_includes_wecom_user(self):
        db = _mock_db()
        db.execute.side_effect = [
            MagicMock(data=[{"wecom_userid": "wc1", "wecom_nickname": "张三"}]),
            MagicMock(data=[]),  # 无群
        ]
        targets = await _load_push_targets(db, "u1", "org1")
        wecom_targets = [t for t in targets if "企微" in t["label"] and "张三" in t["label"]]
        assert len(wecom_targets) == 1

    @pytest.mark.asyncio
    async def test_db_error_graceful(self):
        """DB 查询异常 → 降级只返回 web 目标"""
        db = _mock_db()
        db.execute.side_effect = Exception("DB down")
        targets = await _load_push_targets(db, "u1", "org1")
        # web 目标不依赖 DB，始终存在
        assert len(targets) == 1
        assert "网页" in targets[0]["label"]
