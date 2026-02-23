"""FastMCP server for Confluence data."""

import logging
import os
from typing import Optional, Dict, Any, List

from fastmcp import FastMCP

from .config import get_config
from .pickle_loader import PickleLoader
from .indexer import ConfluenceIndexer
from .converters import html_to_adf
from .search import translate_cql
from .fallback import ConfluenceFallbackClient
from .models import (
    SpaceResponse,
    PageResponse,
    PageBody,
    PageVersion,
    SearchResult,
    ResourceResponse,
    UserInfoResponse
)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize FastMCP server
mcp = FastMCP("confluence-fast-mcp")

# Global instances (initialized on startup)
config = None
pickle_loader = None
indexer = None
fallback_client = None

# Fake cloud ID for local server
FAKE_CLOUD_ID = "local-confluence-fast-mcp"


@mcp.tool()
def getAccessibleAtlassianResources() -> List[Dict[str, Any]]:
    """Get accessible Atlassian resources (mock for local).

    Returns:
        List of resource dictionaries
    """
    return [{
        "id": FAKE_CLOUD_ID,
        "name": "Local Confluence (Fast MCP)",
        "url": "http://localhost",
        "scopes": ["read:confluence-content.all"],
        "avatarUrl": ""
    }]


@mcp.tool()
def atlassianUserInfo() -> Dict[str, Any]:
    """Get current user info (mock for local).

    Returns:
        User info dictionary
    """
    return {
        "accountId": "local-user",
        "accountType": "atlassian",
        "email": "local@example.com",
        "displayName": "Local User"
    }


@mcp.tool()
def getConfluenceSpaces(
    cloudId: Optional[str] = None,
    searchString: Optional[str] = None,
    maxResults: int = 50
) -> List[Dict[str, Any]]:
    """Get Confluence spaces.

    Args:
        cloudId: Cloud ID (ignored for local)
        searchString: Optional search filter
        maxResults: Maximum results to return

    Returns:
        List of space dictionaries
    """
    spaces = pickle_loader.get_all_spaces()

    # Apply search filter
    if searchString:
        search_lower = searchString.lower()
        spaces = [
            s for s in spaces
            if search_lower in s['name'].lower() or search_lower in s['key'].lower()
        ]

    # Limit results
    spaces = spaces[:maxResults]

    # Format as Confluence API response
    return [
        {
            "id": space['key'],  # Use key as ID
            "key": space['key'],
            "name": space['name'],
            "type": "global",
            "status": "current"
        }
        for space in spaces
    ]


@mcp.tool()
def getConfluencePage(
    cloudId: str,
    pageIdOrTitleAndSpaceKey: str,
    spaceKey: Optional[str] = None
) -> Dict[str, Any]:
    """Get a Confluence page by ID or title.

    Args:
        cloudId: Cloud ID (ignored for local)
        pageIdOrTitleAndSpaceKey: Page ID or page title
        spaceKey: Space key (required if using title)

    Returns:
        Page dictionary with ADF body
    """
    # Try to get by ID first
    result = pickle_loader.get_page_by_id(pageIdOrTitleAndSpaceKey)

    # If not found by ID and spaceKey provided, try title lookup
    if not result and spaceKey:
        result = pickle_loader.get_page_by_title(pageIdOrTitleAndSpaceKey, spaceKey)

    if not result:
        return {
            "isError": True,
            "message": f"Page not found: {pageIdOrTitleAndSpaceKey}"
        }

    page = result['page']
    page_space_key = result['space_key']

    # Convert to Confluence API format
    return _format_page_response(page, page_space_key)


@mcp.tool()
def getPagesInConfluenceSpace(
    cloudId: str,
    spaceIdOrKey: str,
    limit: int = 25,
    start: int = 0
) -> Dict[str, Any]:
    """Get pages in a Confluence space.

    Args:
        cloudId: Cloud ID (ignored for local)
        spaceIdOrKey: Space ID or key
        limit: Maximum results
        start: Starting index

    Returns:
        Search result with pages
    """
    pages = pickle_loader.get_pages_in_space(spaceIdOrKey, limit=limit, start=start)

    if not pages:
        return {
            "results": [],
            "start": start,
            "limit": limit,
            "size": 0
        }

    # Format pages
    formatted_pages = [
        _format_page_response(page, spaceIdOrKey, include_body=False)
        for page in pages
    ]

    return {
        "results": formatted_pages,
        "start": start,
        "limit": limit,
        "size": len(formatted_pages)
    }


@mcp.tool()
def searchConfluenceUsingCql(
    cloudId: str,
    cql: str,
    limit: int = 25,
    cursor: Optional[str] = None,
    start: int = 0
) -> Dict[str, Any]:
    """Search Confluence using CQL.

    Args:
        cloudId: Cloud ID (ignored for local)
        cql: CQL query string
        limit: Maximum results
        cursor: Pagination cursor (ignored)
        start: Starting offset

    Returns:
        Search results
    """
    logger.info(f"CQL search: {cql}")

    # Parse CQL
    whoosh_query, space_filter = translate_cql(cql)

    # Search index
    search_results = indexer.search(
        whoosh_query,
        space_key=space_filter,
        limit=limit,
        offset=start
    )

    # Retrieve full page data for results
    formatted_results = []
    for result in search_results:
        page_result = pickle_loader.get_page_by_id(result['page_id'])
        if page_result:
            page = page_result['page']
            space_key = page_result['space_key']
            formatted_page = _format_page_response(page, space_key, include_body=False)
            formatted_results.append(formatted_page)

    return {
        "results": formatted_results,
        "start": start,
        "limit": limit,
        "size": len(formatted_results)
    }


@mcp.tool()
def search(query: str) -> List[Dict[str, Any]]:
    """Simple search across all Confluence content (Rovo-style).

    Args:
        query: Search query

    Returns:
        List of search results
    """
    # Use WHOOSH for full-text search
    search_results = indexer.search(query, limit=50)

    results = []
    for result in search_results:
        page_result = pickle_loader.get_page_by_id(result['page_id'])
        if page_result:
            page = page_result['page']
            space_key = page_result['space_key']

            # Format as ARI-style result
            results.append({
                "id": f"ari:cloud:confluence:{FAKE_CLOUD_ID}:page/{page.get('id')}",
                "title": page.get('title', ''),
                "url": f"http://localhost/spaces/{space_key}/pages/{page.get('id')}",
                "contentType": "page",
                "space": {
                    "key": space_key,
                    "name": result.get('space_name', space_key)
                },
                "excerpt": result.get('title', '')[:200]  # Simple excerpt
            })

    return results


@mcp.tool()
def fetch(id: str) -> Dict[str, Any]:
    """Fetch details of a Confluence page by ARI.

    Args:
        id: Atlassian Resource Identifier (ARI)

    Returns:
        Page details
    """
    # Parse ARI to extract page ID
    # Format: ari:cloud:confluence:cloudId:page/pageId
    if id.startswith('ari:cloud:confluence:'):
        parts = id.split('/')
        if len(parts) >= 2:
            page_id = parts[-1]
            return getConfluencePage(FAKE_CLOUD_ID, page_id)

    return {
        "isError": True,
        "message": f"Invalid ARI format: {id}"
    }


def _format_page_response(page: Dict[str, Any], space_key: str,
                         include_body: bool = True) -> Dict[str, Any]:
    """Format a page as Confluence API response.

    Args:
        page: Page data from pickle
        space_key: Space key
        include_body: Whether to include full body

    Returns:
        Formatted page dictionary
    """
    page_id = str(page.get('id', ''))
    title = page.get('title', '')

    response = {
        "id": page_id,
        "type": "page",
        "status": "current",
        "title": title,
        "space": {
            "key": space_key,
            "name": space_key
        }
    }

    # Add version info
    version = page.get('version', {})
    if isinstance(version, dict):
        response["version"] = {
            "number": version.get('number', 1),
            "when": version.get('when', ''),
        }

    # Add body if requested
    if include_body:
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

        # Convert to ADF
        adf = html_to_adf(body_html)

        response["body"] = {
            "storage": {
                "value": body_html,
                "representation": "storage"
            },
            "atlas_doc_format": {
                "value": adf,
                "representation": "atlas_doc_format"
            }
        }

    # Add links
    response["_links"] = {
        "self": f"/rest/api/content/{page_id}",
        "webui": f"/spaces/{space_key}/pages/{page_id}"
    }

    return response


def initialize_server():
    """Initialize server components."""
    global config, pickle_loader, indexer, fallback_client

    logger.info("Initializing Confluence Fast MCP Server...")

    # Load configuration
    config = get_config()
    logger.info(f"Pickle directory: {config.pickle_dir}")
    logger.info(f"Index directory: {config.index_dir}")

    # Initialize pickle loader
    pickle_loader = PickleLoader(config.pickle_dir)
    pickle_loader.load_all_pickles()

    # Initialize indexer
    indexer = ConfluenceIndexer(config.index_dir)

    # Check if index exists
    stats = indexer.get_stats()
    logger.info(f"Index stats: {stats}")

    if stats['total_docs'] == 0:
        logger.error("Index is empty. Please run 'python3 build_index.py' first.")
        raise RuntimeError("WHOOSH index not found. Run build_index.py to create it.")

    logger.info(f"Using existing index with {stats['total_docs']} documents")

    # Initialize fallback client (if configured)
    if config.confluence_url and config.confluence_username and config.confluence_api_token:
        fallback_client = ConfluenceFallbackClient(
            config.confluence_url,
            config.confluence_username,
            config.confluence_api_token
        )
        logger.info("Fallback client configured for attachment support")
    else:
        logger.info("Fallback client not configured (attachments unavailable)")

    logger.info("Server initialization complete!")


def main():
    """Main entry point."""
    # Initialize components
    initialize_server()

    # Run FastMCP server
    logger.info("Starting FastMCP server...")
    mcp.run()


if __name__ == "__main__":
    main()
