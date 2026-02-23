#!/usr/bin/env python3
"""Build WHOOSH search index from pickled Confluence data."""

import sys
import os
import logging

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from confluence_fast_mcp.config import get_config
from confluence_fast_mcp.pickle_loader import PickleLoader
from confluence_fast_mcp.indexer import ConfluenceIndexer

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def main():
    """Build the WHOOSH index."""
    logger.info("Starting index build...")

    # Load configuration
    config = get_config()
    logger.info(f"Pickle directory: {config.pickle_dir}")
    logger.info(f"Index directory: {config.index_dir}")

    # Initialize pickle loader
    logger.info("Loading pickle files...")
    pickle_loader = PickleLoader(config.pickle_dir)
    pickle_loader.load_all_pickles()

    spaces = pickle_loader.get_all_spaces()
    logger.info(f"Loaded {len(spaces)} spaces")

    # Initialize indexer
    logger.info("Initializing indexer...")
    indexer = ConfluenceIndexer(config.index_dir)

    # Build index
    logger.info("Building search index (this may take 10-30 seconds)...")
    all_pages = pickle_loader.get_all_pages()
    indexed_count = indexer.index_all_pages(all_pages, clear_first=True)

    logger.info(f"Successfully indexed {indexed_count} pages")

    # Show stats
    stats = indexer.get_stats()
    logger.info(f"Index statistics: {stats}")

    logger.info("Index build complete!")
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Error building index: {e}", exc_info=True)
        sys.exit(1)
