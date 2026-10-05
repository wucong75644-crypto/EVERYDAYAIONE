"""Regression: trustworthy canvas, user overrides, exact pixels and output contract."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock

from PIL import Image
import pytest

from services.handlers.image_dimensions import read_image_dimensions, output_size_check
from services.handlers.image_size_requirements import resolve_size_requirement, user_size_intent
from services.handlers.chat_image_request import freeze_image_request, verify_frozen_request


def reference(width=1080, height=1080):
    from services.handlers.image_dimensions import ratio_for
    return {"width":width,"height":height,"aspect_ratio":ratio_for(width,height),
            "content_sha256":"a"*64,"workspace_path":"original.jpg","role":"产品"}


def resolve(text="", *, refs=None, previous=None, **args):
    return resolve_size_requirement({"mode":"image_to_image","prompt":"竖向笔记本摄影",**args},
        refs if refs is not None else [reference()], intent=user_size_intent(text), previous=previous)


def test_original_square_overrides_model_product_shape_estimate():
    args, target = resolve(aspect_ratio="3:4")
    assert args["aspect_ratio"] == "1:1"
    assert target["reference_sha256"] == "a"*64
    assert target["original_width"] == target["original_height"] == 1080


@pytest.mark.parametrize("text,ratio,resolution", [
    ("这六张改成3:4、2K","3:4","2K"),
    ("保持比例，提高到4K","1:1","4K"),
    ("改成方图","1:1","2K"),
    ("改为3:4","3:4","2K"),
])
def test_user_change_is_independent_of_other_dimension(text,ratio,resolution):
    args, _ = resolve(text, previous={"mode":"explicit","aspect_ratio":"1:1","resolution":"2K"},
                      aspect_ratio="9:16",resolution="1K")
    assert args["aspect_ratio"] == ratio and args["resolution"] == resolution


def test_resolution_only_retains_prior_portrait_canvas():
    args, _ = resolve("提高到4K", previous={"mode":"explicit","aspect_ratio":"3:4","resolution":"2K"})
    assert args["aspect_ratio"] == "3:4" and args["resolution"] == "4K"


def test_exif_rotated_canvas_and_digest_are_bound_to_same_original(tmp_path):
    path = tmp_path / "portrait.jpg"
    exif = Image.Exif(); exif[274] = 6
    Image.new("RGB", (800, 600)).save(path, exif=exif)
    facts = read_image_dimensions(path)
    assert (facts["width"],facts["height"],facts["aspect_ratio"]) == (600,800,"3:4")
    assert facts["content_sha256"]
    args, target = resolve(refs=[{**reference(),**facts}],aspect_ratio="1:1")
    assert args["aspect_ratio"] == "3:4" and target["reference_sha256"] == facts["content_sha256"]


@pytest.mark.parametrize("text,code", [
    ("生成1920×1080","EXACT_SIZE_UNSUPPORTED"),
    ("生成1920×1080，比例1:1","SIZE_CONFLICT"),
    ("做1080尺寸","SIZE_AMBIGUOUS"),
    ("生成7:5图片","ASPECT_RATIO_UNSUPPORTED"),
    ("生成3K图片","RESOLUTION_UNSUPPORTED"),
    ("生成0:1图片","SIZE_INVALID"),
    ("比例1:1或3:4","SIZE_AMBIGUOUS"),
])
def test_invalid_or_unsupported_does_not_become_approximate(text,code):
    with pytest.raises(ValueError,match=code): resolve(text)


def test_unsupported_precise_pixels_cannot_hide_in_structured_mode():
    with pytest.raises(ValueError,match="EXACT_SIZE_UNSUPPORTED"):
        resolve(size_requirement={"mode":"explicit","width":1920,"height":1080})


def test_inherit_requires_explicit_canvas_choice_when_ratios_differ():
    refs = [reference(), reference(600,800)]
    with pytest.raises(ValueError,match="CANVAS_REFERENCE_REQUIRED"): resolve(refs=refs)
    args, target = resolve(refs=refs,size_requirement={"mode":"inherit_reference","reference_index":1})
    assert args["aspect_ratio"] == "3:4" and target["reference_index"] == 1


def test_auto_requires_user_permission_with_reference_and_records_source():
    with pytest.raises(ValueError,match="INTENT_REQUIRED"): resolve(size_requirement={"mode":"auto"})
    args,target = resolve("比例你决定",previous={"aspect_ratio":"1:1","resolution":"2K"})
    assert args["aspect_ratio"] == "auto" and target["source"] == "user"


def test_unknown_original_dimensions_cannot_be_inferred_visually():
    with pytest.raises(ValueError,match="DIMENSIONS_UNAVAILABLE"):
        resolve(refs=[{"workspace_path":"x","content_sha256":"a"*64,"role":"竖向笔记本"}])


def test_canvas_prompt_conflict_blocked_before_freeze():
    with pytest.raises(ValueError,match="PROMPT_CONFLICT"):
        resolve(prompt="产品摄影，画布比例3:4")


def test_frozen_target_does_not_change_with_user_next_turn():
    refs=[reference()]
    args,target=resolve("统一1:1、2K",refs=refs)
    snapshot=freeze_image_request(args,refs,origin={},max_requests=15,max_credits=300,size_requirement=target)
    target["aspect_ratio"]="3:4"
    verify_frozen_request(snapshot)
    assert snapshot["size_requirement"]["aspect_ratio"] == "1:1"
    assert snapshot["size_requirement"]["resolution"] == "2K"
    assert snapshot["estimated_credits"] <= 300


def test_output_uses_actual_canvas_and_allows_only_pixel_rounding():
    assert output_size_check({"width":1024,"height":1024,"aspect_ratio":"1:1"},{"aspect_ratio":"1:1"})["size_matches"]
    assert not output_size_check({"width":768,"height":1024,"aspect_ratio":"3:4"},{"aspect_ratio":"1:1"})["size_matches"]
    assert output_size_check({"width":1365,"height":768,"aspect_ratio":"455:256"},{"aspect_ratio":"16:9"})["size_matches"]


def test_discovery_reads_legacy_original_ignoring_forged_client_dimensions(tmp_path,monkeypatch):
    from services.file_executor import FileExecutor
    from services.handlers.chat_context import image_sources
    user="00000000-0000-4000-8000-000000000001"
    files=FileExecutor(str(tmp_path),user,None)
    Image.new("RGB",(1080,1080)).save(files.resolve_safe_path("original.png"))
    monkeypatch.setattr("core.config.get_settings",lambda:SimpleNamespace(file_workspace_root=str(tmp_path)))
    row={"id":"message","role":"user","content":[{"type":"image","workspace_path":"original.png","width":600,"height":800}]}
    sources=image_sources.discovered_image_sources(row,MagicMock(),org_id=None,owner_id=user)
    assert sources[0]["canvas"]["aspect_ratio"]=="1:1"
    assert row["content"][0]["width"]==600  # no rewriting old persisted content



def test_keep_ratio_retains_prior_target_instead_of_resetting_to_reference():
    args, _ = resolve("保持比例，提高到4K",previous={"mode":"explicit","aspect_ratio":"3:4","resolution":"2K"})
    assert args["aspect_ratio"]=="3:4" and args["resolution"]=="4K"


def test_product_proportions_do_not_change_square_canvas():
    args, _ = resolve("产品比例3:4，保持原图比例",prompt="产品比例3:4，保持原图画布")
    assert args["aspect_ratio"]=="1:1"


def test_long_edge_is_not_approximated_by_resolution_tier():
    with pytest.raises(ValueError,match="EXACT_SIZE_UNSUPPORTED"):
        resolve("长边1280")


def test_negative_ratio_is_invalid_instead_of_becoming_positive():
    with pytest.raises(ValueError,match="SIZE_INVALID"):
        resolve("生成-1:1")



def test_pixel_width_height_words_are_not_approximated():
    with pytest.raises(ValueError,match="EXACT_SIZE_UNSUPPORTED"):
        resolve("宽1920高1080")


def test_output_resolution_declaration_must_match_the_user_target():
    with pytest.raises(ValueError,match="PROMPT_CONFLICT"):
        resolve("提高到4K",prompt="分辨率2K")


def test_historic_questions_do_not_become_task_size_requirements():
    from services.handlers.image_size_requirements import is_size_instruction
    assert not is_size_instruction("为什么生成3:4，1:1是什么意思")
    assert not is_size_instruction("例如生成1920×1080")
    assert is_size_instruction("这六张改成3:4、2K")
    assert is_size_instruction("3:4、2K")



@pytest.mark.parametrize("text,ratio", [("统一三比四","3:4"),("改成16比9","16:9"),("做竖向海报","3:4")])
def test_natural_language_size_requests_can_override_source(text,ratio):
    args, target = resolve(text,aspect_ratio=ratio)
    assert args["aspect_ratio"]==ratio and target["source"].startswith("user")


def test_pixel_height_and_negative_pixels_are_rejected():
    with pytest.raises(ValueError,match="EXACT_SIZE_UNSUPPORTED"): resolve("生成1080P")
    with pytest.raises(ValueError,match="SIZE_INVALID"): resolve("生成-1920×1080")


def test_ratio_and_resolution_preserve_separate_provenance():
    _, target = resolve("提高到4K",previous={"mode":"explicit","aspect_ratio":"3:4","resolution":"2K","source_task_id":"old-task"})
    assert target["source"]=="task" and target["resolution_source"]=="user"
    assert target["source_task_id"]=="old-task"



def test_current_unpersisted_image_also_gets_server_canvas_facts(tmp_path,monkeypatch):
    from services.file_executor import FileExecutor
    from services.handlers.chat_context.image_sources import discovered_image_sources
    user="00000000-0000-4000-8000-000000000001"
    files=FileExecutor(str(tmp_path),user,None)
    Image.new("RGB",(1080,1080)).save(files.resolve_safe_path("original.png"))
    monkeypatch.setattr("core.config.get_settings",lambda:SimpleNamespace(file_workspace_root=str(tmp_path)))
    sources=discovered_image_sources({"role":"user","content":[{"type":"image","workspace_path":"original.png"}]},MagicMock(),org_id=None,owner_id=user)
    assert sources[0]["canvas"]["aspect_ratio"]=="1:1"
    assert "message_id" not in sources[0]["reference"]
