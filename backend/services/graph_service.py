"""
图查询抽象层

封装 PostgreSQL 递归 CTE 实现知识图谱遍历。
未来可替换为 Neo4j/Kuzu 后端，调用方零改动。
"""

from typing import Any, Dict, List, Optional

from loguru import logger
from psycopg.types.json import Json

from services.knowledge_config import get_pg_connection


class GraphService:
    """知识图谱查询服务"""

    async def find_related(
        self,
        node_id: str,
        depth: int = 2,
        relation_types: Optional[List[str]] = None,
        org_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        查找 N 跳以内的相关节点

        Args:
            node_id: 起始节点 ID
            depth: 最大跳数（默认 2）
            relation_types: 过滤关系类型（None=全部）

        Returns:
            相关节点列表，每项包含 node 信息 + 路径深度 + 关系类型
        """
        conn_ctx = await get_pg_connection()
        if conn_ctx is None:
            return []

        type_filter = ""
        edge_org_filter = (
            "AND (e.org_id = %(org_id)s OR e.org_id IS NULL)"
            if org_id else "AND e.org_id IS NULL"
        )
        params: Dict[str, Any] = {"node_id": node_id, "depth": depth}
        if relation_types:
            type_filter = "AND e.relation_type = ANY(%(relation_types)s)"
            params["relation_types"] = relation_types

        query = f"""
        WITH RECURSIVE traversal AS (
            -- 起始节点的直接邻居
            SELECT
                e.target_id AS node_id,
                e.relation_type,
                e.weight,
                1 AS depth
            FROM knowledge_edges e
            WHERE e.source_id = %(node_id)s
                {type_filter} {edge_org_filter}

            UNION

            -- 反向边
            SELECT
                e.source_id AS node_id,
                e.relation_type,
                e.weight,
                1 AS depth
            FROM knowledge_edges e
            WHERE e.target_id = %(node_id)s
                {type_filter} {edge_org_filter}

            UNION ALL

            -- 递归展开
            SELECT
                CASE WHEN e.source_id = t.node_id THEN e.target_id ELSE e.source_id END,
                e.relation_type,
                e.weight,
                t.depth + 1
            FROM traversal t
            JOIN knowledge_edges e ON (e.source_id = t.node_id OR e.target_id = t.node_id)
                {edge_org_filter}
            WHERE t.depth < %(depth)s
                AND CASE WHEN e.source_id = t.node_id THEN e.target_id ELSE e.source_id END != %(node_id)s
                {type_filter}
        )
        SELECT DISTINCT ON (n.id)
            n.id, n.category, n.subcategory, n.node_type,
            n.title, n.content, n.confidence, n.metadata,
            t.depth, t.relation_type, t.weight
        FROM traversal t
        JOIN knowledge_nodes n ON n.id = t.node_id
        WHERE n.is_deleted = FALSE
            AND (n.org_id = %(org_id)s OR n.org_id IS NULL)
        ORDER BY n.id, t.depth ASC
        LIMIT 20;
        """
        params["org_id"] = org_id

        async with conn_ctx as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, params)
                rows = await cur.fetchall()
                columns = [desc.name for desc in cur.description]
                return [dict(zip(columns, row)) for row in rows]

    async def find_path(
        self,
        from_id: str,
        to_id: str,
        max_depth: int = 3,
        org_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        查找两个节点间的最短路径（按 org 隔离 edges）

        Returns:
            路径上的节点列表（含关系），空列表表示无路径
        """
        conn_ctx = await get_pg_connection()
        if conn_ctx is None:
            return []

        org_filter = (
            "AND (e.org_id = %(org_id)s OR e.org_id IS NULL)"
            if org_id else "AND e.org_id IS NULL"
        )

        query = f"""
        WITH RECURSIVE path_search AS (
            SELECT
                e.target_id AS node_id,
                ARRAY[e.source_id, e.target_id] AS path,
                ARRAY[e.relation_type] AS relations,
                1 AS depth
            FROM knowledge_edges e
            WHERE e.source_id = %(from_id)s {org_filter}

            UNION ALL

            SELECT
                CASE WHEN e.source_id = p.node_id THEN e.target_id ELSE e.source_id END,
                p.path || CASE WHEN e.source_id = p.node_id THEN e.target_id ELSE e.source_id END,
                p.relations || e.relation_type,
                p.depth + 1
            FROM path_search p
            JOIN knowledge_edges e ON (e.source_id = p.node_id OR e.target_id = p.node_id)
                {org_filter}
            WHERE p.depth < %(max_depth)s
                AND NOT (CASE WHEN e.source_id = p.node_id THEN e.target_id ELSE e.source_id END = ANY(p.path))
        )
        SELECT path, relations, depth
        FROM path_search
        WHERE node_id = %(to_id)s
        ORDER BY depth ASC
        LIMIT 1;
        """

        async with conn_ctx as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, {
                    "from_id": from_id,
                    "to_id": to_id,
                    "max_depth": max_depth,
                    "org_id": org_id,
                })
                row = await cur.fetchone()
                if not row:
                    return []
                return [{"path": row[0], "relations": row[1], "depth": row[2]}]

    async def add_edge(
        self,
        source_id: str,
        target_id: str,
        relation_type: str,
        weight: float = 1.0,
        metadata: Optional[Dict[str, Any]] = None,
        org_id: Optional[str] = None,
    ) -> Optional[str]:
        """
        添加关系边（重复边则更新权重，按 org 隔离）

        Returns:
            边的 ID，失败返回 None
        """
        conn_ctx = await get_pg_connection()
        if conn_ctx is None:
            return None

        query = """
        INSERT INTO knowledge_edges (source_id, target_id, relation_type, weight, metadata, org_id)
        VALUES (%(source_id)s, %(target_id)s, %(relation_type)s, %(weight)s, %(metadata)s, %(org_id)s)
        ON CONFLICT (source_id, target_id, relation_type)
        DO UPDATE SET weight = EXCLUDED.weight, metadata = EXCLUDED.metadata, org_id = EXCLUDED.org_id
        RETURNING id;
        """

        try:
            async with conn_ctx as conn:
                async with conn.cursor() as cur:
                    await cur.execute(query, {
                        "source_id": source_id,
                        "target_id": target_id,
                        "relation_type": relation_type,
                        "weight": weight,
                        "metadata": Json(metadata or {}),
                        "org_id": org_id,
                    })
                    result = await cur.fetchone()
                    await conn.commit()
                    return str(result[0]) if result else None
        except Exception as e:
            logger.error(
                f"Graph add_edge failed | {source_id} -[{relation_type}]-> {target_id} | error={e}"
            )
            return None

    async def get_subgraph(
        self,
        node_ids: List[str],
        include_edges: bool = True,
        org_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        获取指定节点的子图

        Returns:
            {"nodes": [...], "edges": [...]}
        """
        conn_ctx = await get_pg_connection()
        if conn_ctx is None:
            return {"nodes": [], "edges": []}

        try:
            async with conn_ctx as conn:
                async with conn.cursor() as cur:
                    # 获取节点
                    await cur.execute(
                        """
                        SELECT id, category, subcategory, node_type, title, content,
                               confidence, metadata
                        FROM knowledge_nodes
                        WHERE id = ANY(%(ids)s) AND is_deleted = FALSE
                            AND (org_id = %(org_id)s OR org_id IS NULL);
                        """,
                        {"ids": node_ids, "org_id": org_id},
                    )
                    node_rows = await cur.fetchall()
                    node_cols = [desc.name for desc in cur.description]
                    nodes = [dict(zip(node_cols, r)) for r in node_rows]

                    edges = []
                    if include_edges and node_ids:
                        await cur.execute(
                            """
                            SELECT id, source_id, target_id, relation_type, weight, metadata
                            FROM knowledge_edges
                            WHERE source_id = ANY(%(ids)s) AND target_id = ANY(%(ids)s)
                              AND (org_id = %(org_id)s OR org_id IS NULL);
                            """,
                            {"ids": node_ids, "org_id": org_id},
                        )
                        edge_rows = await cur.fetchall()
                        edge_cols = [desc.name for desc in cur.description]
                        edges = [dict(zip(edge_cols, r)) for r in edge_rows]

                    return {"nodes": nodes, "edges": edges}
        except Exception as e:
            logger.error(f"Graph get_subgraph failed | error={e}")
            return {"nodes": [], "edges": []}


# 全局单例
graph_service = GraphService()
