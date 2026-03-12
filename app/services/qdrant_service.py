"""Qdrant vector database service for RAG retrieval."""

import structlog
from openai import AsyncOpenAI
from qdrant_client import QdrantClient
from qdrant_client.http.models import (
    Distance,
    VectorParams,
    PointStruct,
    Filter,
    FieldCondition,
    MatchAny,
)

from app.settings import get_settings

logger = structlog.get_logger()
settings = get_settings()

VECTOR_SIZE = 1536  # text-embedding-3-small


class QdrantService:
    def __init__(self):
        self.client = QdrantClient(
            host=settings.qdrant_host,
            port=settings.qdrant_port,
        )
        self.openai = AsyncOpenAI(api_key=settings.openai_api_key)
        self.collection = settings.qdrant_collection

    async def ensure_collection(self):
        """Create collection if it doesn't exist."""
        collections = self.client.get_collections().collections
        exists = any(c.name == self.collection for c in collections)
        if not exists:
            self.client.create_collection(
                collection_name=self.collection,
                vectors_config=VectorParams(
                    size=VECTOR_SIZE,
                    distance=Distance.COSINE,
                ),
            )
            logger.info("Created Qdrant collection", name=self.collection)
        else:
            logger.info("Qdrant collection exists", name=self.collection)

    async def _embed(self, text: str) -> list[float]:
        """Get embedding vector for text."""
        response = await self.openai.embeddings.create(
            model=settings.openai_embedding_model,
            input=text,
        )
        return response.data[0].embedding

    async def search(
        self,
        query: str,
        categories: list[str] | None = None,
        limit: int = 5,
    ) -> list[dict]:
        """Search for relevant chunks in the knowledge base."""
        vector = await self._embed(query)

        search_filter = None
        if categories:
            search_filter = Filter(
                must=[
                    FieldCondition(
                        key="category",
                        match=MatchAny(any=categories),
                    )
                ]
            )

        results = self.client.search(
            collection_name=self.collection,
            query_vector=vector,
            query_filter=search_filter,
            limit=limit,
            score_threshold=0.3,
        )

        chunks = []
        for hit in results:
            payload = hit.payload or {}
            chunks.append({
                "text": payload.get("text", ""),
                "source_name": payload.get("source_name", ""),
                "source_url": payload.get("source_url", ""),
                "category": payload.get("category", ""),
                "score": hit.score,
            })

        logger.info(
            "RAG search completed",
            query=query[:60],
            categories=categories,
            results=len(chunks),
        )
        return chunks

    def upsert_chunks(self, chunks: list[dict], vectors: list[list[float]]):
        """Insert or update chunks in the collection (used by crawler)."""
        points = []
        for i, (chunk, vector) in enumerate(zip(chunks, vectors)):
            points.append(
                PointStruct(
                    id=chunk["id"],
                    vector=vector,
                    payload={
                        "text": chunk["text"],
                        "source_id": chunk["source_id"],
                        "source_name": chunk["source_name"],
                        "source_url": chunk["source_url"],
                        "category": chunk["category"],
                        "page_url": chunk.get("page_url", ""),
                        "language": chunk.get("language", "nl"),
                    },
                )
            )

        # Batch upsert in groups of 100
        batch_size = 100
        for i in range(0, len(points), batch_size):
            batch = points[i : i + batch_size]
            self.client.upsert(
                collection_name=self.collection,
                points=batch,
            )
        logger.info("Upserted chunks to Qdrant", count=len(points))
