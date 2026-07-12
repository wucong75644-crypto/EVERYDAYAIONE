"""主图详情页草稿读取与图片关联。"""

from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError
from loguru import logger

from core.config import get_settings
from core.exceptions import AppException
from services.file_executor import FileExecutor


_ALLOWED_FORMATS = frozenset({"JPEG", "PNG", "WEBP"})
_MAX_IMAGE_SIZE = 10 * 1024 * 1024


class DetailProjectService:
    def __init__(self, db: Any, user_id: str, org_id: str | None) -> None:
        self.db = db
        self.user_id = user_id
        self.org_id = org_id
        settings = get_settings()
        self.executor = FileExecutor(
            workspace_root=settings.file_workspace_root,
            user_id=user_id,
            org_id=org_id,
        )

    def get_current(self) -> dict | None:
        result = (
            self.db.table("detail_projects").select("*")
            .eq("user_id", self.user_id).eq("status", "draft")
            .order("updated_at", desc=True).limit(1).execute()
        )
        if not result.data:
            return None
        project = dict(result.data[0])
        images = (
            self.db.table("detail_project_images").select("*")
            .eq("project_id", str(project["id"])).eq("user_id", self.user_id)
            .order("sort_order").execute().data or []
        )
        project["images"] = [self._serialize_image(dict(item)) for item in images]
        return project

    def attach_image(self, workspace_path: str, category: str) -> dict:
        self._validate_workspace_image(workspace_path)
        try:
            with self.db.pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT * FROM attach_detail_project_image(%s, %s, %s, %s)",
                        (self.user_id, self.org_id, workspace_path, category),
                    )
                conn.commit()
        except Exception as exc:
            message = str(exc)
            mapping = {
                "DETAIL_IMAGE_DUPLICATE": ("DETAIL_IMAGE_DUPLICATE", "图片已添加", 409),
                "DETAIL_IMAGE_LIMIT_EXCEEDED": ("DETAIL_IMAGE_LIMIT_EXCEEDED", "最多添加9张图片", 409),
                "DETAIL_PROJECT_ORG_ACCESS_DENIED": ("DETAIL_IMAGE_FORBIDDEN", "无权访问该企业", 403),
            }
            for marker, error in mapping.items():
                if marker in message:
                    raise AppException(code=error[0], message=error[1], status_code=error[2]) from exc
            logger.error(
                f"Attach detail image failed | user_id={self.user_id} | "
                f"org_id={self.org_id} | path={workspace_path} | error={exc}"
            )
            raise AppException("DETAIL_IMAGE_ATTACH_FAILED", "图片关联失败", 500) from exc
        return self.get_current() or {}

    def update_settings(self, project_id: str, version: int, settings: dict) -> dict:
        allowed = {
            "content_type", "platform", "requirement", "language",
            "aspect_ratio", "quality", "image_count",
        }
        updates = {key: value for key, value in settings.items() if key in allowed and value is not None}
        if not updates:
            return self._require_project(project_id)
        assignments = ", ".join(f'"{key}" = %s' for key in updates)
        params = [*updates.values(), project_id, self.user_id, self.org_id, version]
        sql = f"""
            UPDATE detail_projects SET {assignments}, version = version + 1, updated_at = NOW()
            WHERE id = %s AND user_id = %s AND org_id IS NOT DISTINCT FROM %s
              AND version = %s AND status = 'draft'
            RETURNING id
        """
        self._execute_versioned(sql, params)
        return self.get_current() or {}

    def remove_image(self, project_id: str, image_id: str, version: int) -> dict:
        with self.db.pool.connection() as conn:
            with conn.cursor() as cur:
                self._lock_project(cur, project_id, version)
                cur.execute(
                    "DELETE FROM detail_project_images WHERE id=%s AND project_id=%s AND user_id=%s RETURNING sort_order",
                    (image_id, project_id, self.user_id),
                )
                deleted = cur.fetchone()
                if not deleted:
                    raise AppException("DETAIL_IMAGE_NOT_FOUND", "项目图片不存在", 404)
                deleted_order = deleted["sort_order"] if isinstance(deleted, dict) else deleted[0]
                cur.execute(
                    "UPDATE detail_project_images SET sort_order=sort_order-1 "
                    "WHERE project_id=%s AND sort_order>%s",
                    (project_id, deleted_order),
                )
                self._bump_version(cur, project_id)
            conn.commit()
        return self.get_current() or {}

    def update_category(self, project_id: str, image_id: str, version: int, category: str) -> dict:
        with self.db.pool.connection() as conn:
            with conn.cursor() as cur:
                self._lock_project(cur, project_id, version)
                cur.execute(
                    "UPDATE detail_project_images SET category=%s, updated_at=NOW() "
                    "WHERE id=%s AND project_id=%s AND user_id=%s RETURNING id",
                    (category, image_id, project_id, self.user_id),
                )
                if not cur.fetchone():
                    raise AppException("DETAIL_IMAGE_NOT_FOUND", "项目图片不存在", 404)
                self._bump_version(cur, project_id)
            conn.commit()
        return self.get_current() or {}

    def reorder_images(self, project_id: str, version: int, image_ids: list[str]) -> dict:
        if len(image_ids) != len(set(image_ids)):
            raise AppException("DETAIL_IMAGE_ORDER_INVALID", "图片顺序包含重复项", 400)
        with self.db.pool.connection() as conn:
            with conn.cursor() as cur:
                self._lock_project(cur, project_id, version)
                cur.execute(
                    "SELECT id::text FROM detail_project_images WHERE project_id=%s AND user_id=%s",
                    (project_id, self.user_id),
                )
                current_ids = {
                    (row["id"] if isinstance(row, dict) else row[0])
                    for row in cur.fetchall()
                }
                if current_ids != set(image_ids):
                    raise AppException("DETAIL_IMAGE_ORDER_INVALID", "图片顺序与当前草稿不一致", 400)
                cur.execute(
                    "SELECT id::text, workspace_path, category, created_at FROM detail_project_images "
                    "WHERE project_id=%s AND user_id=%s",
                    (project_id, self.user_id),
                )
                rows = cur.fetchall()
                by_id = {
                    (row["id"] if isinstance(row, dict) else row[0]): row
                    for row in rows
                }
                cur.execute("DELETE FROM detail_project_images WHERE project_id=%s", (project_id,))
                for order, image_id in enumerate(image_ids):
                    row = by_id[image_id]
                    values = list(row.values()) if isinstance(row, dict) else list(row)
                    cur.execute(
                        "INSERT INTO detail_project_images(id, project_id, user_id, org_id, workspace_path, category, sort_order, created_at) "
                        "VALUES (%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s,%s,%s,%s)",
                        (values[0], project_id, self.user_id, self.org_id, values[1], values[2], order, values[3]),
                    )
                self._bump_version(cur, project_id)
            conn.commit()
        return self.get_current() or {}

    def _require_project(self, project_id: str) -> dict:
        project = self.get_current()
        if not project or str(project["id"]) != project_id:
            raise AppException("DETAIL_PROJECT_NOT_FOUND", "草稿项目不存在", 404)
        return project

    def _execute_versioned(self, sql: str, params: list) -> None:
        with self.db.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                if not cur.fetchone():
                    raise AppException("DETAIL_PROJECT_VERSION_CONFLICT", "草稿已在其他页面更新", 409)
            conn.commit()

    def _lock_project(self, cur: Any, project_id: str, version: int) -> None:
        cur.execute(
            "SELECT id FROM detail_projects WHERE id=%s AND user_id=%s "
            "AND org_id IS NOT DISTINCT FROM %s AND version=%s AND status='draft' FOR UPDATE",
            (project_id, self.user_id, self.org_id, version),
        )
        if not cur.fetchone():
            raise AppException("DETAIL_PROJECT_VERSION_CONFLICT", "草稿已在其他页面更新", 409)

    @staticmethod
    def _bump_version(cur: Any, project_id: str) -> None:
        cur.execute(
            "UPDATE detail_projects SET version=version+1, updated_at=NOW() WHERE id=%s",
            (project_id,),
        )

    def _validate_workspace_image(self, workspace_path: str) -> None:
        try:
            target = self.executor.resolve_safe_path(workspace_path)
        except PermissionError as exc:
            raise AppException("DETAIL_IMAGE_FORBIDDEN", "图片路径无权访问", 403) from exc
        if not target.exists() or not target.is_file():
            raise AppException("DETAIL_IMAGE_NOT_FOUND", "工作区图片不存在", 404)
        if target.is_symlink():
            raise AppException("DETAIL_IMAGE_FORBIDDEN", "不允许使用符号链接", 403)
        if target.stat().st_size > _MAX_IMAGE_SIZE:
            raise AppException("DETAIL_IMAGE_TOO_LARGE", "图片不能超过10MB", 413)
        try:
            with Image.open(target) as image:
                image.verify()
                if image.format not in _ALLOWED_FORMATS:
                    raise AppException("DETAIL_IMAGE_INVALID_TYPE", "仅支持JPG、PNG和WebP", 400)
        except AppException:
            raise
        except (UnidentifiedImageError, OSError) as exc:
            raise AppException("DETAIL_IMAGE_INVALID_CONTENT", "图片内容无效", 400) from exc

    def _serialize_image(self, image: dict) -> dict:
        path = image["workspace_path"]
        try:
            target: Path = self.executor.resolve_safe_path(path)
            ready = target.is_file() and not target.is_symlink()
        except PermissionError:
            ready = False
        image["status"] = "ready" if ready else "missing"
        image["original_url"] = self.executor.get_cdn_url(path) if ready else None
        image["thumbnail_url"] = image["original_url"]
        return image
