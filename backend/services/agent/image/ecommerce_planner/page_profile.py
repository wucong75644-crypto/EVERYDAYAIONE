"""Page policy over the existing model registry; no credentials or new clients."""
from types import SimpleNamespace
from core.exceptions import AppException
from services.adapters.factory import MODEL_REGISTRY

MODELS = (("kimi-k3", "Kimi K3", "dashscope", "kimi"),
          ("gemini-3.8-flash", "Gemini 3.8 Flash", "kie", "gemini"),
          ("gpt-6-luna", "GPT 6 Luna", "kie", "gpt"))


def capabilities(settings):
    rows = []
    for model, label, provider, prefix in MODELS:
        configured = bool(getattr(settings, f"{provider}_api_key", None))
        priced = all(getattr(settings, f"detail_{prefix}_{direction}_credits_per_million", None)
                     for direction in ("input", "output"))
        enabled = prefix != "gpt" or getattr(settings, "detail_gpt_enabled", False)
        reason = ("平台模型密钥未配置" if not configured else "模型积分费率未配置" if not priced
                  else "GPT策划暂未开放" if not enabled else None)
        rows.append({"id": model, "name": label, "available": configured and priced and enabled,
                     "reason": reason})
    return rows


def profile(settings, model):
    from services.agent.image.analysis_media import media_policy
    entry = next((row for row in MODELS if row[0] == model), None)
    if not entry or MODEL_REGISTRY[model].provider.value != entry[2]:
        raise AppException("DETAIL_MODEL_INVALID", "请选择 Kimi、Gemini 或 GPT", 400)
    available = next(row for row in capabilities(settings) if row["id"] == model)
    if not available["available"]:
        raise AppException("DETAIL_MODEL_UNAVAILABLE", available["reason"], 409)
    transport, json_mode = media_policy(settings, model)
    return SimpleNamespace(
        ecom_analysis_transport=transport, ecom_analysis_json_output=json_mode,
        ecom_analysis_memory_mb=getattr(settings, "ecom_analysis_memory_mb", 384),
        ecom_image_planning_model=model, ecom_image_planning_reasoning="high" if model == "kimi-k3" else "medium",
        ecom_image_planning_stage_timeout=300,
        ecom_image_planning_input_credits_per_million=getattr(settings, f"detail_{entry[3]}_input_credits_per_million"),
        ecom_image_planning_output_credits_per_million=getattr(settings, f"detail_{entry[3]}_output_credits_per_million"),
        wall_seconds=settings.detail_page_planning_seconds)
