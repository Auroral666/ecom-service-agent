"""知识库检索工具：通过向量检索回答 FAQ、政策类问题。

与查订单/查物流这类「结构化数据查询」工具不同，
search_knowledge 面向非结构化文本（退换货政策、配送说明、FAQ 等），
返回 Top-K 命中片段及其来源，由 LLM 引用回答。

向量后端由 settings.rag_backend 切换：
- numpy ：手写余弦相似度，零外部依赖，JSON 持久化
- chroma：嵌入式向量数据库，HNSW 索引，工程代表性

为避免每次进程启动都重建索引，单例缓存 Retriever。
"""

from pathlib import Path
from typing import Optional

from app.config.settings import settings
from app.rag.backends import create_backend
from app.rag.configuration import create_embedder
from app.rag.retriever import KnowledgeRetriever

_retriever: Optional[KnowledgeRetriever] = None


def _project_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else Path(__file__).resolve().parents[2] / path


def _create_backend_from_settings():
    """根据 settings.rag_backend 创建对应后端实例。"""
    name = settings.rag_backend.lower()
    if name == "numpy":
        return create_backend("numpy", index_path=_project_path(settings.kb_index_path))
    if name == "chroma":
        return create_backend(
            "chroma",
            persist_dir=_project_path(settings.chroma_persist_dir),
            collection_name=settings.chroma_collection,
        )
    raise ValueError(
        f"未知的 RAG 后端: {settings.rag_backend}（可选: numpy / chroma）"
    )


def _get_retriever() -> KnowledgeRetriever:
    global _retriever
    if _retriever is None:
        embedder = create_embedder()
        backend = _create_backend_from_settings()
        candidate = KnowledgeRetriever(embedder=embedder, backend=backend)
        candidate.load()
        _retriever = candidate
    return _retriever


def reset_retriever() -> None:
    """清空单例缓存（测试或切换后端时使用）。"""
    global _retriever
    _retriever = None


def search_knowledge(query: str, top_k: int = 3) -> dict:
    """检索退换货政策、配送说明、会员权益、FAQ 等知识库内容。

    Returns:
        {
          "success": bool,
          "backend": "numpy" | "chroma",
          "query": str,
          "results": [
            {"doc": "...", "section": "...", "score": 0.83, "text": "..."},
            ...
          ],
          "error": "..."  # 仅失败时存在
        }
    """
    if not query or not query.strip():
        return {"success": False, "error": "query 不能为空", "query": query, "results": []}

    try:
        retriever = _get_retriever()
    except FileNotFoundError as e:
        return {
            "success": False,
            "error": str(e),
            "backend": settings.rag_backend,
            "query": query,
            "results": [],
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"知识库初始化失败: {e}",
            "backend": settings.rag_backend,
            "query": query,
            "results": [],
        }

    top_k = max(1, min(int(top_k or 3), 5))
    try:
        hits = retriever.search(query, top_k=top_k)
    except Exception as exc:
        status = getattr(exc, 'status_code', None)
        return {"success": False, "backend": settings.rag_backend, "query": query,
                "results": [], "error_code": "EMBEDDING_QUERY_FAILED", "http_status": status,
                "error": "向量检索失败，请检查本地向量服务或独立的 Embedding 配置。"}

    return {
        "success": True,
        "backend": settings.rag_backend,
        "query": query,
        "results": [
            {
                "doc": h.chunk.doc,
                "section": h.chunk.section,
                "score": round(h.score, 4),
                "text": h.chunk.text,
            }
            for h in hits
        ],
    }
