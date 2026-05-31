"""
批量生成 img2img 画廊预览图

用法：
  cd /Users/wucong/EVERYDAYAIONE/backend
  source venv/bin/activate

  # 本地文件 → 自动上传 OSS → 批量生成
  python scripts/gen_gallery_previews.py --product-file /path/to/product.jpg

  # 已有 CDN URL → 直接生成
  python scripts/gen_gallery_previews.py --product-url "https://cdn.xxx/product.jpg"

  # 预览模式（不调用 API）
  python scripts/gen_gallery_previews.py --product-file /path/to/product.jpg --dry-run

流程：
  1. 上传产品图到 OSS（如果传的是本地文件）
  2. 读取 prompt_gallery.json 中 preview_url 为空的 img2img 提示词
  3. 通过 KieImageAdapter 调用 GPT-Image-2 图生图逐条生成
  4. 下载生成图片到 frontend/public/data/previews/
  5. 更新 prompt_gallery.json 的 preview_url
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND_ROOT = PROJECT_ROOT / "backend"
GALLERY_JSON = PROJECT_ROOT / "frontend" / "public" / "data" / "prompt_gallery.json"
PREVIEW_DIR = PROJECT_ROOT / "frontend" / "public" / "data" / "previews"

sys.path.insert(0, str(BACKEND_ROOT))


def upload_to_oss(file_path: Path) -> str:
    """上传本地文件到阿里云 OSS，返回 CDN URL"""
    from dotenv import load_dotenv
    load_dotenv(BACKEND_ROOT / ".env")

    from services.oss_service import get_oss_service

    suffix = file_path.suffix.lower().lstrip(".")
    mime_map = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}
    content_type = mime_map.get(suffix, "image/jpeg")
    ext = "jpg" if suffix in ("jpg", "jpeg") else suffix

    content = file_path.read_bytes()
    print(f"上传产品图到 OSS: {file_path.name} ({len(content) / 1024:.0f}KB)")

    oss = get_oss_service()
    result = oss.upload_bytes(
        content=content,
        user_id="gallery-preview-gen",
        ext=ext,
        category="gallery",
        content_type=content_type,
    )
    url = result["url"]
    print(f"上传成功: {url}")
    return url


async def download_image(url: str, dest: Path) -> None:
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, timeout=60.0, follow_redirects=True)
        resp.raise_for_status()
        dest.write_bytes(resp.content)


async def generate_one(adapter, prompt_item: dict, product_url: str, index: int, total: int) -> str | None:
    prompt_id = prompt_item["id"]
    title = prompt_item["title"]
    prompt_text = prompt_item["prompt"].replace("[product]", "the product shown in the reference image")

    print(f"\n[{index}/{total}] {prompt_id} — {title}")
    print(f"  Prompt: {prompt_text[:100]}...")

    try:
        result = await adapter.generate(
            prompt=prompt_text,
            image_urls=[product_url],
            size="1:1",
            resolution="1K",
            wait_for_result=True,
            max_wait_time=180.0,
            poll_interval=3.0,
        )

        if result.image_urls:
            dest = PREVIEW_DIR / f"{prompt_id}.png"
            await download_image(result.image_urls[0], dest)
            print(f"  OK → {dest.name}")
            return f"/data/previews/{prompt_id}.png"

        print("  WARNING: 生成成功但无图片 URL")
        return None

    except Exception as e:
        print(f"  ERROR: {e}")
        return None


async def main(product_url: str, dry_run: bool = False, skip_existing: bool = True) -> None:
    with open(GALLERY_JSON) as f:
        gallery = json.load(f)

    img2img_prompts = [p for p in gallery["prompts"] if p.get("category") == "img2img"]
    if skip_existing:
        img2img_prompts = [p for p in img2img_prompts if not p.get("preview_url")]

    print(f"待生成：{len(img2img_prompts)} 条 img2img 提示词")

    if dry_run:
        for p in img2img_prompts:
            print(f"  {p['id']}: {p['title']}")
        print("\n(dry run — 不调用 API)")
        return

    if not img2img_prompts:
        print("全部已有预览图，无需生成。")
        return

    from services.adapters.factory import create_image_adapter
    adapter = create_image_adapter("gpt-image-2-image-to-image")

    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)

    results: dict[str, str] = {}
    try:
        for i, item in enumerate(img2img_prompts, 1):
            path = await generate_one(adapter, item, product_url, i, len(img2img_prompts))
            if path:
                results[item["id"]] = path
            if i < len(img2img_prompts):
                await asyncio.sleep(2.0)
    finally:
        await adapter.close()

    if results:
        for p in gallery["prompts"]:
            if p["id"] in results:
                p["preview_url"] = results[p["id"]]
        with open(GALLERY_JSON, "w") as f:
            json.dump(gallery, f, ensure_ascii=False, indent=2)
        print(f"\n完成！更新了 {len(results)}/{len(img2img_prompts)} 张预览图")
    else:
        print("\n未生成任何预览图。")


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(BACKEND_ROOT / ".env")

    parser = argparse.ArgumentParser(description="批量生成画廊 img2img 预览图")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--product-file", type=Path, help="本地产品图路径（自动上传 OSS）")
    group.add_argument("--product-url", help="产品图的 CDN URL（已上传）")
    parser.add_argument("--dry-run", action="store_true", help="只列出待生成项")
    parser.add_argument("--force", action="store_true", help="强制重新生成已有预览图")
    args = parser.parse_args()

    if args.product_file:
        if not args.product_file.exists():
            print(f"ERROR: 文件不存在 {args.product_file}")
            sys.exit(1)
        url = upload_to_oss(args.product_file)
    else:
        url = args.product_url

    asyncio.run(main(url, args.dry_run, skip_existing=not args.force))
