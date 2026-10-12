"""Fixed raster templates and registered workspace images; never execute model HTML."""

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import hashlib
import io
from PIL import Image, ImageDraw, ImageFont
from psycopg.types.json import Jsonb
from core.config import get_settings
from services.file_executor import FileExecutor
from services.file_resources import content_digest, FileTargetResolver
from services.agent.agent_reach.contracts import ReachError
from .contracts import Card, VisualTemplate
from .repository import digest
from .validation import check_draft

FONT_CANDIDATES = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/wqy-microhei/wqy-microhei.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
)


def font_path():
    for name in FONT_CANDIDATES:
        if Path(name).is_file():
            return name
    raise ReachError("FONT_MISSING", "缺少受控中文字体，请安装Noto Sans CJK后重试")


def raster(card, visual, index, count):
    card = Card.model_validate(card)
    visual = VisualTemplate.model_validate(visual)
    if visual.background.lower() == visual.foreground.lower():
        raise ReachError("LAYOUT_CONTRAST", "正文色与背景色相同，请调整品牌模板")
    image = Image.new("RGB", (1080, 1440), visual.background)
    draw = ImageDraw.Draw(image)
    face = font_path()
    title_font = ImageFont.truetype(face, 96 if card.role == "cover" else 54)
    body_font = ImageFont.truetype(face, 36)
    small = ImageFont.truetype(face, 26)
    if draw.textlength(visual.watermark, font=small) > 800:
        raise ReachError("LAYOUT_OVERFLOW", "品牌水印过长，请缩短")
    draw.rectangle((72, 90, 152, 102), fill=visual.accent)
    draw.text(
        (72, 132),
        {"cover": "封面", "body": "内容", "closing": "回顾"}[card.role],
        font=small,
        fill=visual.accent,
    )
    y = 220

    def paragraph(text, font, line_height):
        nonlocal y
        for paragraph in text.splitlines():
            line = ""
            for char in paragraph:
                if draw.textlength(line + char, font=font) > 936:
                    draw.text((72, y), line, font=font, fill=visual.foreground)
                    y += line_height
                    line = ""
                line += char
            draw.text((72, y), line, font=font, fill=visual.foreground)
            y += line_height

    paragraph(card.headline, title_font, 120 if card.role == "cover" else 88)
    y += 48
    paragraph(card.body, body_font, 58)
    if y > 1280:
        raise ReachError(
            "LAYOUT_OVERFLOW",
            f"第{index+1}张卡片内容溢出，请拆页或精简，不自动缩成小字",
        )
    draw.line((72, 1320, 1008, 1320), fill=visual.accent, width=2)
    draw.text((72, 1350), visual.watermark, font=small, fill=visual.foreground)
    draw.text((900, 1350), f"{index+1}/{count}", font=small, fill=visual.foreground)
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue(), {
        "content_bottom": y,
        "overflow": False,
        "warnings": ["留白较多，请人工预览"] if y < 700 else [],
    }


def workspace(owner, org):
    if not get_settings().file_workspace_enabled:
        raise ReachError("CONFIG_REQUIRED", "文件工作区尚未启用")
    return FileExecutor(get_settings().file_workspace_root, str(owner), str(org))


def content(repo, id, revision=None, cursor=None):
    def read(c):
        c.execute(
            "SELECT c.*,r.data,r.manifest,r.checks FROM social_contents c JOIN social_content_revisions r ON r.content_id=c.id AND r.revision=c.current_revision WHERE c.id=%s AND c.org_id=%s",
            (id, repo.scope.org_id),
        )
        row = c.fetchone()
        if not row:
            raise ReachError("NOT_FOUND", "内容不存在")
        if revision is not None and row["current_revision"] != revision:
            raise ReachError("VERSION_CONFLICT", "内容版本已变化")
        return row

    if cursor is not None:
        return read(cursor)
    with repo.cursor() as c:
        return read(c)


def asset(repo, id):
    with repo.cursor() as c:
        c.execute(
            "SELECT * FROM social_media_assets WHERE id=%s AND org_id=%s",
            (id, repo.scope.org_id),
        )
        row = c.fetchone()
    if not row:
        raise ReachError("NOT_FOUND", "图片不存在或无权访问")
    try:
        path = workspace(row["owner_id"], row["org_id"]).resolve_safe_path(row["path"])
        if content_digest(path) != row["sha256"]:
            raise ReachError("RESOURCE_CHANGED", "图片已变化，需要重新检查与确认")
    except (OSError, ValueError, PermissionError):
        raise ReachError(
            "RESOURCE_CHANGED", "图片缺失或已变化，需要重新检查与确认"
        ) from None
    return row, path


def register(repo, c, content_id, revision, data, source, rights, metadata):
    asset_id = str(uuid4())
    files = workspace(repo.scope.actor_user_id, repo.scope.org_id)
    relative = f"social-ops/{content_id}/{asset_id}.png"
    path = files.resolve_safe_path(relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    path.chmod(0o600)
    with Image.open(io.BytesIO(data)) as im:
        width, height = im.size
    sha = hashlib.sha256(data).hexdigest()
    c.execute(
        """INSERT INTO social_media_assets(id,org_id,content_id,owner_id,content_revision,path,sha256,width,height,source,rights,metadata)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
        (
            asset_id,
            repo.scope.org_id,
            content_id,
            repo.scope.actor_user_id,
            revision,
            relative,
            sha,
            width,
            height,
            source,
            rights,
            Jsonb(metadata),
        ),
    )
    return {
        "id": asset_id,
        "sha256": sha,
        "width": width,
        "height": height,
        "source": source,
        "rights": rights,
        "metadata": metadata,
    }


def render_revision(repo, c, id, expected, cards, visual, manifest):
    row = content(repo, id, expected, cursor=c)
    if cards[0]["role"] != "cover" or cards[-1]["role"] != "closing":
        raise ReachError("INVALID_CARDS", "首张需封面、最后一张需收尾卡片")
    assets = []
    for i, card in enumerate(cards):
        png, audit = raster(card, visual, i, len(cards))
        assets.append(
            register(
                repo,
                c,
                id,
                expected + 1,
                png,
                "品牌模板排版；文字来自当前内容",
                "自主排版；引用文字及品牌素材权属须由操作者核对",
                {
                    "renderer": "pillow-card-v1",
                    "layout": audit,
                    "card": card,
                    "visual": visual,
                },
            )
        )
    payload = {
        **row["data"],
        "cards": cards,
        "visual": visual,
        "assets": assets,
        "images_text_hash": digest(
            {k: row["data"].get(k) for k in ("title", "body", "tags")}
        ),
    }
    return repo.save_content(
        c,
        row["job_id"],
        payload,
        manifest,
        {"status": "needs_check", "ready_to_publish": False},
        id,
        expected,
    )


def validate_package(repo, row):
    data = row["data"]
    sources = repo.sources(
        str(row["job_id"]),
        evidence_ids=data.get("evidence_ids")
        or [e["snapshot_id"] for e in data.get("evidence", [])],
        limit=100,
    )
    basic = check_draft(data, sources)
    errors = list(basic["hard_errors"])
    if len(data.get("body", "")) > 1000:
        errors.append("正文超过当前图文制作1000字边界")
    assets = data.get("assets", [])
    if not 1 <= len(assets) <= 9:
        errors.append("需1至9张完整图片")
    if data.get("images_text_hash") != digest(
        {k: data.get(k) for k in ("title", "body", "tags")}
    ):
        errors.append("文案已变化，需要重新制作或确认图片版本")
    warnings = []
    structured_cards = data.get("cards", [])
    if structured_cards and all(c.get("role") for c in structured_cards):
        if (
            structured_cards[0]["role"] != "cover"
            or structured_cards[-1]["role"] != "closing"
        ):
            errors.append("当前卡片顺序需以封面开始、收尾结束")
    for selected in assets:
        item, path = asset(repo, selected["id"])
        if (
            str(item["content_id"]) != str(row["id"])
            or item["sha256"] != selected["sha256"]
        ):
            errors.append("素材与内容不匹配")
        with Image.open(path) as im:
            if im.size != (1080, 1440):
                errors.append("图片需1080×1440，请先调整尺寸")
            im.verify()
        if not item["source"] or not item["rights"]:
            errors.append("缺少素材来源或权属说明")
        warnings.extend(item["metadata"].get("layout", {}).get("warnings", []))
        if item["metadata"].get("replacement"):
            warnings.append("替换图片未执行OCR或视觉语义核验，请人工检查图中文字与布局")
    return {
        "hard_errors": list(dict.fromkeys(errors)),
        "warnings": warnings,
        "status": "blocked" if errors else "ready_for_confirmation",
        "unknowns": [
            "结构通过不等于语义或素材版权核验通过",
            "发布仍需操作者确认及平台核验",
        ],
        "ready_to_publish": False,
        "package_hash": digest(data),
    }


def read_image_bytes(path):
    with path.open("rb") as source:
        data = source.read(20 * 1024 * 1024 + 1)
    if len(data) > 20 * 1024 * 1024:
        raise ReachError("MEDIA_LIMIT", "图片超过20MB")
    return data
