"""Dedicated vector indexing for project knowledge revisions.

This module deliberately does not call ``get_rag_manager``.  Project knowledge
uses the configured embedding model but keeps its collection and lifecycle
separate from the legacy API knowledge base.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from threading import RLock
from typing import Any

from django.conf import settings
from django.db.models import F
from langchain_core.documents import Document
from langchain_huggingface import HuggingFaceEmbeddings

from ai_core.models import RAGConfiguration
from .models import DocumentRevision, KnowledgeChunk


logger = logging.getLogger(__name__)
PROJECT_KNOWLEDGE_COLLECTION = "project_knowledge"
_INDEX_CACHE_LOCK = RLock()
_INDEX_CACHE_KEY: tuple[Any, ...] | None = None
_INDEX_CACHE_INSTANCE: "ProjectKnowledgeIndex | None" = None


class ProjectKnowledgeSearchUnavailable(RuntimeError):
    """A filtered vector query could not be completed safely."""


@dataclass(frozen=True)
class _Result:
    chunk: KnowledgeChunk
    vector_score: float
    keyword_score: float


class ProjectKnowledgeIndex:
    """A small adapter over the configured Chroma or Milvus backend."""

    def __init__(self, config: RAGConfiguration | Any | None = None, *, embeddings: Any = None, vector_store: Any = None):
        self.config = config or self._load_config()
        self.is_chroma = self.config.vector_db_type.lower() == "chroma"
        self.embeddings = embeddings if embeddings is not None else self._create_embeddings()
        self.vector_store = vector_store if vector_store is not None else self._create_vector_store()

    @staticmethod
    def _load_config() -> RAGConfiguration:
        return _load_active_rag_config()

    def _create_embeddings(self) -> HuggingFaceEmbeddings:
        return HuggingFaceEmbeddings(
            model_name=self.config.embedding_model,
            encode_kwargs={"normalize_embeddings": True, "batch_size": 32},
        )

    def _create_vector_store(self) -> Any:
        if self.is_chroma:
            from langchain_chroma import Chroma

            directory = self.config.chroma_persist_directory or os.path.join(settings.BASE_DIR, "project_knowledge_chroma_db")
            return Chroma(
                collection_name=PROJECT_KNOWLEDGE_COLLECTION,
                persist_directory=directory,
                embedding_function=self.embeddings,
            )

        from langchain_milvus import Milvus

        return Milvus(
            embedding_function=self.embeddings,
            collection_name=f"{self.config.milvus_collection_name}_{PROJECT_KNOWLEDGE_COLLECTION}",
            connection_args={"host": self.config.milvus_host, "port": str(self.config.milvus_port)},
        )

    def index_revision(self, revision: DocumentRevision) -> int:
        chunks = list(
            revision.chunks.select_related("revision__document").order_by("ordinal", "id")
        )
        if not chunks:
            return 0

        documents = []
        ids = []
        for chunk in chunks:
            document = revision.document
            documents.append(Document(
                page_content=_embedding_content(chunk),
                metadata={
                    "chunk_id": str(chunk.id),
                    "project_id": document.project_id,
                    "document_id": document.id,
                    "revision_id": str(revision.id),
                    "file_name": document.name,
                    "heading": chunk.heading,
                    "ordinal": chunk.ordinal,
                },
            ))
            # Stable UUIDs are the vector primary keys across task retries.
            ids.append(str(chunk.id))
        if self.is_chroma:
            # LangChain Chroma maps add_documents(ids=...) to Chroma upsert.
            self.vector_store.add_documents(documents, ids=ids)
        else:
            # langchain-milvus 0.3.2 exposes upsert explicitly.  Using
            # add_documents here can insert another entity version on retry.
            self.vector_store.upsert(ids=ids, documents=documents)
        return len(ids)

    def search(self, project_id: int, revision_ids: list[str], query: str, top_k: int) -> list[dict]:
        allowed_chunks = _current_indexed_chunks(project_id, revision_ids)
        if not allowed_chunks or not query.strip() or top_k <= 0:
            return []
        allowed_by_id = {str(chunk.id): chunk for chunk in allowed_chunks}
        try:
            vector_results = self._vector_search(query, revision_ids, project_id, max(top_k * 3, top_k))
        except Exception as exc:
            # Never retry by searching the whole collection: a failed backend
            # filter must not turn into a cross-project data exposure.
            logger.warning("项目知识库向量过滤查询失败，已拒绝返回结果: %s", exc)
            raise ProjectKnowledgeSearchUnavailable("项目资料检索暂时不可用，请稍后重试。") from exc

        results: list[_Result] = []
        seen = set()
        for vector_document, raw_score in vector_results:
            chunk_id = str(vector_document.metadata.get("chunk_id", ""))
            chunk = allowed_by_id.get(chunk_id)
            if not chunk or chunk_id in seen:
                continue
            seen.add(chunk_id)
            results.append(_Result(chunk, self._normalise_score(raw_score), _keyword_score(query, chunk)))

        results.sort(key=lambda item: (item.keyword_score, item.vector_score, -item.chunk.ordinal), reverse=True)
        # Reserve a small part of the input budget for neighbouring paragraphs.
        # Filling all slots with vector hits first made contextual expansion a no-op.
        selected = results[:max(1, top_k - min(2, top_k - 1))]
        selected = _add_adjacent_chunks(selected, allowed_chunks, top_k)
        selected_ids = {str(item.chunk.id) for item in selected}
        for candidate in results:
            if len(selected) >= top_k:
                break
            if str(candidate.chunk.id) not in selected_ids:
                selected.append(candidate)
                selected_ids.add(str(candidate.chunk.id))
        return [_serialize_chunk(item.chunk) for item in selected]

    def _vector_search(self, query: str, revision_ids: list[str], project_id: int, limit: int) -> list[tuple[Document, float]]:
        if self.is_chroma:
            vector_filter = {
                "$and": [
                    {"project_id": {"$eq": project_id}},
                    {"revision_id": {"$in": [str(value) for value in revision_ids]}},
                ]
            }
            return self.vector_store.similarity_search_with_score(query, k=limit, filter=vector_filter)

        revision_values = ", ".join(f'"{str(value)}"' for value in revision_ids)
        expression = f"project_id == {int(project_id)} and revision_id in [{revision_values}]"
        return self.vector_store.similarity_search_with_score(query, k=limit, expr=expression)

    def _normalise_score(self, score: float | int | None) -> float:
        if score is None:
            return 0.0
        score = float(score)
        return 1.0 / (1.0 + max(score, 0.0)) if self.is_chroma else score

    def delete_revision(self, revision_id: str) -> None:
        revision_id = str(revision_id)
        if self.is_chroma:
            collection = getattr(self.vector_store, "_collection", None)
            if collection is None:
                raise RuntimeError("项目知识库 Chroma collection 不可用，无法安全删除版本索引")
            existing = collection.get(where={"revision_id": revision_id}, include=[])
            ids = existing.get("ids", []) if existing else []
            if ids:
                collection.delete(ids=ids)
            return
        self.vector_store.delete(expr=f'revision_id == "{revision_id}"')


def get_project_knowledge_index() -> ProjectKnowledgeIndex:
    """Return the current configured index without reloading embeddings per query."""
    global _INDEX_CACHE_KEY, _INDEX_CACHE_INSTANCE
    config = _load_active_rag_config()
    cache_key = _index_cache_key(config)
    with _INDEX_CACHE_LOCK:
        if _INDEX_CACHE_INSTANCE is not None and _INDEX_CACHE_KEY == cache_key:
            return _INDEX_CACHE_INSTANCE
        _INDEX_CACHE_INSTANCE = ProjectKnowledgeIndex(config=config)
        _INDEX_CACHE_KEY = cache_key
        return _INDEX_CACHE_INSTANCE


def clear_project_knowledge_index_cache() -> None:
    """Drop the process-local index instance, primarily for controlled tests."""
    global _INDEX_CACHE_KEY, _INDEX_CACHE_INSTANCE
    with _INDEX_CACHE_LOCK:
        _INDEX_CACHE_KEY = None
        _INDEX_CACHE_INSTANCE = None


def _load_active_rag_config() -> RAGConfiguration:
    config = (
        RAGConfiguration.objects.filter(is_default=True, is_active=True).first()
        or RAGConfiguration.objects.filter(is_active=True).first()
    )
    if not config:
        raise ValueError("没有可用的 RAG 配置，无法建立项目知识库索引")
    return config


def _index_cache_key(config: RAGConfiguration | Any) -> tuple[Any, ...]:
    """Include every backend setting that changes the index or embedding identity."""
    updated_at = getattr(config, "updated_at", None)
    return (
        getattr(config, "id", None),
        updated_at.isoformat() if hasattr(updated_at, "isoformat") else str(updated_at),
        getattr(config, "vector_db_type", ""),
        getattr(config, "embedding_model", ""),
        getattr(config, "chroma_persist_directory", ""),
        getattr(config, "milvus_host", ""),
        getattr(config, "milvus_port", ""),
        getattr(config, "milvus_collection_name", ""),
        getattr(config, "milvus_dim", ""),
    )


def index_revision(revision: DocumentRevision) -> int:
    return get_project_knowledge_index().index_revision(revision)


def search_chunks(project_id: int, revision_ids: list[str], query: str, top_k: int = 8) -> list[dict]:
    """Search only currently active, non-deleted, index-ready project revisions."""
    if not revision_ids:
        return []
    return get_project_knowledge_index().search(project_id, revision_ids, query, top_k)


def delete_revision_index(revision_id: str) -> None:
    """Delete one revision from the dedicated collection; callers own retries."""
    get_project_knowledge_index().delete_revision(revision_id)


def _current_indexed_chunks(project_id: int, revision_ids: list[str]) -> list[KnowledgeChunk]:
    return list(
        KnowledgeChunk.objects.select_related("revision__document")
        .filter(
            revision_id__in=[str(value) for value in revision_ids],
            revision__document__project_id=project_id,
            revision__document__is_active=True,
            revision__document__is_deleted=False,
            revision__index_status="ready",
            revision__document__current_revision_id=F("revision_id"),
        )
        .order_by("revision_id", "ordinal", "id")
    )


def _keyword_score(query: str, chunk: KnowledgeChunk) -> float:
    query = query.casefold().strip()
    heading = (chunk.heading or "").casefold()
    content = chunk.content.casefold()
    score = 0.0
    if query in heading:
        score += 8.0
    if query in content:
        score += 2.0
    for token in re.findall(r"[\w\u4e00-\u9fff]+", query):
        if len(token) < 2 or token == query:
            continue
        if token in heading:
            score += 2.0
        if token in content:
            score += 0.5
    return score


def _add_adjacent_chunks(selected: list[_Result], allowed_chunks: list[KnowledgeChunk], top_k: int) -> list[_Result]:
    if len(selected) >= top_k:
        return selected[:top_k]
    by_revision_ordinal = {(str(chunk.revision_id), chunk.ordinal): chunk for chunk in allowed_chunks}
    selected_ids = {str(item.chunk.id) for item in selected}
    expanded = list(selected)
    for item in selected:
        for offset in (-1, 1):
            adjacent = by_revision_ordinal.get((str(item.chunk.revision_id), item.chunk.ordinal + offset))
            if adjacent and adjacent.section_key == item.chunk.section_key and str(adjacent.id) not in selected_ids:
                expanded.append(_Result(adjacent, item.vector_score, 0.0))
                selected_ids.add(str(adjacent.id))
                if len(expanded) >= top_k:
                    return expanded
    return expanded


def _serialize_chunk(chunk: KnowledgeChunk) -> dict:
    document = chunk.revision.document
    return {
        "id": str(chunk.id),
        "content": chunk.content,
        "file_name": document.name,
        "document_id": document.id,
        "revision_id": str(chunk.revision_id),
        "heading": chunk.heading,
        "location": chunk.location,
    }


def _embedding_content(chunk: KnowledgeChunk) -> str:
    """Keep source headings in the text sent to the embedding model."""
    heading = (chunk.heading or "").strip()
    content = chunk.content
    if not heading or content == heading or content.startswith(f"{heading}\n"):
        return content
    return f"{heading}\n{content}"
