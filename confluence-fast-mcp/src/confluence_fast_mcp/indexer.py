"""WHOOSH-based indexing for fast full-text search."""

import os
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional
from whoosh import index
from whoosh.fields import Schema, ID, TEXT, DATETIME, NUMERIC
from whoosh.qparser import MultifieldParser
from whoosh.writing import AsyncWriter

from .converters import html_to_text

logger = logging.getLogger(__name__)


# WHOOSH schema definition
SCHEMA = Schema(
    page_id=ID(stored=True, unique=True),
    space_key=ID(stored=True),
    space_name=TEXT(stored=True),
    title=TEXT(stored=True, field_boost=2.0),
    body_text=TEXT(stored=False),  # Not stored to save space
    updated=DATETIME(stored=True),
    parent_id=ID(stored=True),
    level=NUMERIC(stored=True)
)


class ConfluenceIndexer:
    """Manages WHOOSH index for Confluence pages."""

    def __init__(self, index_dir: str):
        """Initialize indexer.

        Args:
            index_dir: Directory to store WHOOSH index
        """
        self.index_dir = index_dir
        self.ix = None

        # Ensure index directory exists
        os.makedirs(index_dir, exist_ok=True)

        # Create or open index
        if index.exists_in(index_dir):
            logger.info(f"Opening existing index at {index_dir}")
            self.ix = index.open_dir(index_dir)
        else:
            logger.info(f"Creating new index at {index_dir}")
            self.ix = index.create_in(index_dir, SCHEMA)

    def needs_rebuild(self, pickle_files: List[str]) -> bool:
        """Check if index needs rebuilding based on pickle file timestamps.

        Args:
            pickle_files: List of pickle file paths

        Returns:
            True if index should be rebuilt
        """
        # Simple heuristic: if index is empty, rebuild
        with self.ix.searcher() as searcher:
            if searcher.doc_count_all() == 0:
                return True

        # Could add more sophisticated timestamp checking here
        return False

    def index_all_pages(self, pages: List[tuple], clear_first: bool = False) -> int:
        """Index all pages from pickle data.

        Args:
            pages: List of (space_key, page_data) tuples
            clear_first: Whether to clear existing index first

        Returns:
            Number of pages indexed
        """
        if clear_first:
            logger.info("Clearing existing index")
            # Create new empty index
            self.ix = index.create_in(self.index_dir, SCHEMA)

        total_pages = len(pages)
        logger.info(f"Indexing {total_pages} pages...")
        indexed_count = 0

        # Use AsyncWriter for better performance
        writer = AsyncWriter(self.ix)

        try:
            for space_key, page in pages:
                try:
                    self._index_page(writer, space_key, page)
                    indexed_count += 1

                    if indexed_count % 50 == 0:
                        logger.info(f"Progress: {indexed_count}/{total_pages} pages indexed ({100*indexed_count//total_pages}%)")

                except Exception as e:
                    logger.error(f"Error indexing page {page.get('id')}: {e}")

            writer.commit()
            logger.info(f"Successfully indexed {indexed_count}/{total_pages} pages")

        except Exception as e:
            logger.error(f"Error during indexing: {e}")
            writer.cancel()
            raise

        return indexed_count

    def _index_page(self, writer, space_key: str, page: Dict[str, Any]) -> None:
        """Index a single page.

        Args:
            writer: WHOOSH writer
            space_key: Space key
            page: Page data dictionary
        """
        page_id = str(page.get('id', ''))
        title = page.get('title', '')

        # Extract body text from HTML storage
        body_html = ''
        body_data = page.get('body', {})
        if isinstance(body_data, dict):
            storage = body_data.get('storage', {})
            if isinstance(storage, dict):
                body_html = storage.get('value', '')
            elif isinstance(storage, str):
                body_html = storage
        elif isinstance(body_data, str):
            body_html = body_data

        body_text = html_to_text(body_html) if body_html else ''

        # Parse version/history for updated date
        updated = None
        version = page.get('version', {})
        if isinstance(version, dict):
            when = version.get('when')
            if when:
                try:
                    updated = datetime.fromisoformat(when.replace('Z', '+00:00'))
                except (ValueError, AttributeError):
                    pass

        if not updated:
            # Fallback to history
            history = page.get('history', {})
            if isinstance(history, dict):
                last_updated = history.get('lastUpdated', {})
                if isinstance(last_updated, dict):
                    when = last_updated.get('when')
                    if when:
                        try:
                            updated = datetime.fromisoformat(when.replace('Z', '+00:00'))
                        except (ValueError, AttributeError):
                            pass

        if not updated:
            updated = datetime.now()

        # Get parent and level info
        ancestors = page.get('ancestors', [])
        parent_id = ''
        level = 0
        if ancestors:
            level = len(ancestors)
            if isinstance(ancestors[-1], dict):
                parent_id = str(ancestors[-1].get('id', ''))

        # Add document to index
        writer.update_document(
            page_id=page_id,
            space_key=space_key,
            space_name=page.get('space', {}).get('name', space_key) if isinstance(page.get('space'), dict) else space_key,
            title=title,
            body_text=body_text,
            updated=updated,
            parent_id=parent_id,
            level=level
        )

    def search(self, query_str: str, space_key: Optional[str] = None,
               limit: int = 25, offset: int = 0) -> List[Dict[str, Any]]:
        """Search the index.

        Args:
            query_str: Search query string
            space_key: Optional space key filter
            limit: Maximum results to return
            offset: Starting offset for pagination

        Returns:
            List of search result dictionaries
        """
        with self.ix.searcher() as searcher:
            # Parse query across title and body
            parser = MultifieldParser(['title', 'body_text'], schema=self.ix.schema)
            query = parser.parse(query_str)

            # Apply space filter if provided
            if space_key:
                from whoosh.query import And, Term
                query = And([query, Term('space_key', space_key)])

            results = searcher.search_page(query, pagenum=(offset // limit) + 1, pagelen=limit)

            return [
                {
                    'page_id': hit['page_id'],
                    'space_key': hit['space_key'],
                    'title': hit['title'],
                    'space_name': hit.get('space_name', ''),
                    'updated': hit.get('updated'),
                    'score': hit.score
                }
                for hit in results
            ]

    def search_by_title(self, title: str, space_key: Optional[str] = None) -> List[Dict[str, Any]]:
        """Search by title only.

        Args:
            title: Title search string
            space_key: Optional space key filter

        Returns:
            List of matching pages
        """
        with self.ix.searcher() as searcher:
            from whoosh.qparser import QueryParser
            from whoosh.query import And, Term

            parser = QueryParser('title', schema=self.ix.schema)
            query = parser.parse(title)

            if space_key:
                query = And([query, Term('space_key', space_key)])

            results = searcher.search(query, limit=50)

            return [
                {
                    'page_id': hit['page_id'],
                    'space_key': hit['space_key'],
                    'title': hit['title'],
                }
                for hit in results
            ]

    def get_stats(self) -> Dict[str, Any]:
        """Get index statistics.

        Returns:
            Dictionary with index stats
        """
        with self.ix.searcher() as searcher:
            return {
                'total_docs': searcher.doc_count_all(),
                'index_dir': self.index_dir,
                'schema_fields': list(self.ix.schema.names())
            }
