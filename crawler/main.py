"""Knowledge base crawler — crawls configured sources and indexes into Qdrant."""

import asyncio
import hashlib
import json
import os
import time
import structlog
from datetime import datetime

import httpx
from bs4 import BeautifulSoup
from openai import AsyncOpenAI
from qdrant_client import QdrantClient
from qdrant_client.http.models import (
    Distance, VectorParams, PointStruct, Filter, FieldCondition, MatchValue,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from dotenv import load_dotenv
load_dotenv()

logger = structlog.get_logger()

# Settings
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
EMBEDDING_MODEL = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
QDRANT_HOST = os.getenv("QDRANT_HOST", "qdrant")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
COLLECTION = os.getenv("QDRANT_COLLECTION", "taxnav_knowledge")
CRAWLER_SCHEDULE = os.getenv("CRAWLER_SCHEDULE", "0 3 * * *")
VECTOR_SIZE = 1536

# Chunk settings
CHUNK_SIZE = 800   # tokens approx (chars / 4)
CHUNK_OVERLAP = 100


class KnowledgeCrawler:
    def __init__(self):
        self.openai = AsyncOpenAI(api_key=OPENAI_API_KEY)
        self.qdrant = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
        self.http = httpx.AsyncClient(
            timeout=30,
            follow_redirects=True,
            headers={
                "User-Agent": "TaxNavigator-KnowledgeBot/1.0 (+https://taxnavigator-advice.nl)",
                "Accept-Language": "nl,en;q=0.9,uk;q=0.8",
            },
        )
        self._ensure_collection()

    def _ensure_collection(self):
        collections = self.qdrant.get_collections().collections
        if not any(c.name == COLLECTION for c in collections):
            self.qdrant.create_collection(
                collection_name=COLLECTION,
                vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
            )
            logger.info("Created collection", name=COLLECTION)

    # ------------------------------------------------------------------
    # Crawling
    # ------------------------------------------------------------------
    async def crawl_source(self, source: dict) -> list[dict]:
        """Crawl a single source and return text chunks."""
        base_url = source["base_url"]
        source_id = source["id"]
        max_pages = source.get("max_pages", 50)
        allowed_paths = source.get("allowed_paths", [])

        logger.info("Crawling source", source_id=source_id, base_url=base_url)

        visited = set()
        to_visit = [base_url]
        all_chunks = []

        while to_visit and len(visited) < max_pages:
            url = to_visit.pop(0)
            if url in visited:
                continue

            # Check allowed paths
            if allowed_paths:
                from urllib.parse import urlparse
                path = urlparse(url).path
                if not any(path.startswith(ap) for ap in allowed_paths):
                    if url != base_url:
                        continue

            visited.add(url)

            try:
                resp = await self.http.get(url)
                accept_status = source.get("accept_status", [200])
                if resp.status_code not in accept_status:
                    continue

                # Re-check allowed paths after redirect
                if allowed_paths and str(resp.url) != url:
                    from urllib.parse import urlparse
                    final_path = urlparse(str(resp.url)).path
                    if not any(final_path.startswith(ap) for ap in allowed_paths):
                        # Follow redirect but still extract links from redirected page
                        pass
                if "text/html" not in resp.headers.get("content-type", ""):
                    continue

                soup = BeautifulSoup(resp.text, "lxml")

                # Extract text from main content
                text = self._extract_text(soup)
                if len(text) < 100:  # skip near-empty pages
                    continue

                title = soup.title.string if soup.title else url

                # Chunk the text
                chunks = self._chunk_text(text, source, url, title)
                all_chunks.extend(chunks)

                # Find links for further crawling (use final URL after redirects)
                link_base = str(resp.url) if str(resp.url).startswith(base_url) else base_url
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    full_url = self._resolve_url(link_base, href)
                    if full_url and full_url.startswith(base_url) and full_url not in visited:
                        to_visit.append(full_url)

                # Be polite — small delay between requests
                await asyncio.sleep(0.5)

            except Exception as e:
                logger.warning("Failed to crawl", url=url, error=str(e))
                continue

        logger.info("Source crawled", source_id=source_id, pages=len(visited), chunks=len(all_chunks))
        return all_chunks

    def _extract_text(self, soup: BeautifulSoup) -> str:
        """Extract meaningful text from HTML, skipping nav/footer/scripts."""
        # Remove non-content elements
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form", "noscript"]):
            tag.decompose()

        # Try to find main content
        main = soup.find("main") or soup.find("article") or soup.find(class_="content") or soup.find("body")
        if not main:
            return ""

        text = main.get_text(separator="\n", strip=True)
        # Clean up excessive whitespace
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        return "\n".join(lines)

    def _chunk_text(self, text: str, source: dict, page_url: str, title: str) -> list[dict]:
        """Split text into overlapping chunks."""
        chunks = []
        # Simple character-based chunking (approx CHUNK_SIZE * 4 chars)
        max_chars = CHUNK_SIZE * 4
        overlap_chars = CHUNK_OVERLAP * 4

        i = 0
        while i < len(text):
            end = min(i + max_chars, len(text))
            chunk_text = text[i:end]

            # Try to break at sentence boundary
            if end < len(text):
                last_period = chunk_text.rfind(".")
                last_newline = chunk_text.rfind("\n")
                break_at = max(last_period, last_newline)
                if break_at > max_chars * 0.5:
                    chunk_text = chunk_text[: break_at + 1]
                    end = i + break_at + 1

            chunk_id = hashlib.md5(f"{source['id']}:{page_url}:{i}".encode()).hexdigest()

            chunks.append({
                "id": chunk_id,
                "text": f"[{title}]\n{chunk_text}",
                "source_id": source["id"],
                "source_name": source["name"],
                "source_url": source["base_url"],
                "category": source["category"],
                "page_url": page_url,
                "language": source.get("language", "nl"),
            })

            i = end - overlap_chars if end < len(text) else end

        return chunks

    def _resolve_url(self, base: str, href: str) -> str | None:
        """Resolve relative URLs."""
        from urllib.parse import urljoin, urlparse
        try:
            full = urljoin(base, href)
            parsed = urlparse(full)
            # Only http(s), no fragments, no query heavy pages
            if parsed.scheme not in ("http", "https"):
                return None
            clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
            # Skip files
            skip_ext = (".pdf", ".jpg", ".png", ".gif", ".zip", ".xlsx", ".docx")
            if any(clean.lower().endswith(ext) for ext in skip_ext):
                return None
            return clean
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Embedding & Indexing
    # ------------------------------------------------------------------
    async def embed_and_index(self, chunks: list[dict]):
        """Generate embeddings and upsert to Qdrant."""
        if not chunks:
            return

        # Batch embeddings (max 100 per request)
        batch_size = 100
        all_points = []

        for i in range(0, len(chunks), batch_size):
            batch = chunks[i : i + batch_size]
            texts = [c["text"] for c in batch]

            resp = await self.openai.embeddings.create(
                model=EMBEDDING_MODEL,
                input=texts,
            )

            for chunk, emb_data in zip(batch, resp.data):
                all_points.append(
                    PointStruct(
                        id=chunk["id"],
                        vector=emb_data.embedding,
                        payload={
                            "text": chunk["text"],
                            "source_id": chunk["source_id"],
                            "source_name": chunk["source_name"],
                            "source_url": chunk["source_url"],
                            "category": chunk["category"],
                            "page_url": chunk["page_url"],
                            "language": chunk["language"],
                            "indexed_at": datetime.utcnow().isoformat(),
                        },
                    )
                )

            # Small delay to respect rate limits
            await asyncio.sleep(0.2)

        # Upsert to Qdrant
        for i in range(0, len(all_points), 100):
            batch = all_points[i : i + 100]
            self.qdrant.upsert(collection_name=COLLECTION, points=batch)

        logger.info("Indexed chunks", total=len(all_points))

    # ------------------------------------------------------------------
    # Full crawl run
    # ------------------------------------------------------------------
    async def run_full_crawl(self):
        """Crawl all sources and re-index."""
        start = time.time()
        logger.info("=== Starting full crawl ===")

        with open("config/sources.json", "r") as f:
            config = json.load(f)

        all_chunks = []
        for source in config["sources"]:
            try:
                chunks = await self.crawl_source(source)
                all_chunks.extend(chunks)
            except Exception as e:
                logger.error("Source crawl failed", source=source["id"], error=str(e))

        logger.info("Total chunks collected", count=len(all_chunks))

        # Embed and index
        await self.embed_and_index(all_chunks)

        elapsed = time.time() - start
        logger.info("=== Full crawl completed ===", elapsed_seconds=round(elapsed, 1), total_chunks=len(all_chunks))


# ------------------------------------------------------------------
# Entry point with scheduler
# ------------------------------------------------------------------
async def main():
    crawler = KnowledgeCrawler()

    # Run initial crawl
    logger.info("Running initial crawl...")
    await crawler.run_full_crawl()

    # Schedule recurring crawls
    scheduler = AsyncIOScheduler()
    parts = CRAWLER_SCHEDULE.split()
    trigger = CronTrigger(
        minute=parts[0], hour=parts[1], day=parts[2],
        month=parts[3], day_of_week=parts[4],
    )
    scheduler.add_job(crawler.run_full_crawl, trigger)
    scheduler.start()
    logger.info("Crawler scheduled", cron=CRAWLER_SCHEDULE)

    # Keep running
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())
