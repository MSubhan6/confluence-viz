"""HTML storage format to ADF (Atlassian Document Format) conversion."""

import logging
import sys
import os
from typing import Dict, Any, List, Optional
from bs4 import BeautifulSoup, NavigableString, Tag

# Add parent directory to path to import html_cleaner from confluence-viz
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', 'confluence-viz'))

try:
    from utils.html_cleaner import clean_confluence_html
    HTML_CLEANER_AVAILABLE = True
except ImportError:
    HTML_CLEANER_AVAILABLE = False
    logging.warning("html_cleaner not available, using basic text extraction")

logger = logging.getLogger(__name__)


def html_to_text(html_content: str) -> str:
    """Convert HTML to plain text.

    Args:
        html_content: HTML content string

    Returns:
        Plain text representation
    """
    if HTML_CLEANER_AVAILABLE:
        try:
            return clean_confluence_html(html_content)
        except Exception as e:
            logger.warning(f"Error using html_cleaner, falling back to basic: {e}")

    # Fallback to basic BeautifulSoup extraction
    if not html_content:
        return ""

    soup = BeautifulSoup(html_content, 'html.parser')
    return soup.get_text(separator=' ', strip=True)


def html_to_adf(html_content: str) -> Dict[str, Any]:
    """Convert HTML storage format to Atlassian Document Format (ADF).

    Args:
        html_content: HTML content string

    Returns:
        ADF document structure
    """
    if not html_content:
        return {
            "version": 1,
            "type": "doc",
            "content": []
        }

    try:
        soup = BeautifulSoup(html_content, 'html.parser')
        content = _convert_nodes(soup)

        # Ensure we have at least some content
        if not content:
            content = [{
                "type": "paragraph",
                "content": []
            }]

        return {
            "version": 1,
            "type": "doc",
            "content": content
        }
    except Exception as e:
        logger.error(f"Error converting HTML to ADF: {e}")
        # Return fallback with plain text
        text = html_to_text(html_content)
        return {
            "version": 1,
            "type": "doc",
            "content": [{
                "type": "paragraph",
                "content": [{
                    "type": "text",
                    "text": text[:5000]  # Limit to prevent huge payloads
                }]
            }]
        }


def _convert_nodes(element, parent_tag: Optional[str] = None) -> List[Dict[str, Any]]:
    """Recursively convert HTML nodes to ADF nodes.

    Args:
        element: BeautifulSoup element
        parent_tag: Name of parent tag (for context)

    Returns:
        List of ADF content nodes
    """
    content = []

    for child in element.children:
        if isinstance(child, NavigableString):
            text = str(child).strip()
            if text:
                content.append({
                    "type": "text",
                    "text": text
                })
        elif isinstance(child, Tag):
            node = _convert_tag(child, parent_tag)
            if node:
                if isinstance(node, list):
                    content.extend(node)
                else:
                    content.append(node)

    return content


def _convert_tag(tag: Tag, parent_tag: Optional[str] = None) -> Optional[Any]:
    """Convert a single HTML tag to ADF node(s).

    Args:
        tag: BeautifulSoup tag
        parent_tag: Name of parent tag

    Returns:
        ADF node or list of nodes, or None
    """
    tag_name = tag.name.lower()

    # Paragraphs
    if tag_name == 'p':
        return {
            "type": "paragraph",
            "content": _convert_inline_content(tag)
        }

    # Headings
    if tag_name in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
        level = int(tag_name[1])
        return {
            "type": "heading",
            "attrs": {"level": level},
            "content": _convert_inline_content(tag)
        }

    # Lists
    if tag_name == 'ul':
        return {
            "type": "bulletList",
            "content": _convert_list_items(tag)
        }

    if tag_name == 'ol':
        return {
            "type": "orderedList",
            "content": _convert_list_items(tag)
        }

    # List items (when called directly)
    if tag_name == 'li':
        return {
            "type": "listItem",
            "content": _convert_nodes(tag, parent_tag='li')
        }

    # Code blocks
    if tag_name == 'pre':
        code_tag = tag.find('code')
        code_text = code_tag.get_text() if code_tag else tag.get_text()
        return {
            "type": "codeBlock",
            "content": [{
                "type": "text",
                "text": code_text
            }]
        }

    # Tables
    if tag_name == 'table':
        return _convert_table(tag)

    # Block quotes
    if tag_name == 'blockquote':
        return {
            "type": "blockquote",
            "content": _convert_nodes(tag, parent_tag='blockquote')
        }

    # Horizontal rule
    if tag_name == 'hr':
        return {"type": "rule"}

    # Confluence macros - replace with text
    if tag_name == 'ac:structured-macro':
        try:
            macro_name = tag.get('ac:name', 'unknown') if tag else 'unknown'
            logger.debug(f"Encountered Confluence macro: {macro_name}")
            return {
                "type": "paragraph",
                "content": [{
                    "type": "text",
                    "text": f"[Macro: {macro_name}]"
                }]
            }
        except AttributeError as e:
            logger.warning(f"Error processing macro: {e}")
            return {
                "type": "paragraph",
                "content": [{
                    "type": "text",
                    "text": "[Macro: error]"
                }]
            }

    # Divs and other containers - recurse into children
    if tag_name in ['div', 'section', 'article', 'span']:
        nodes = _convert_nodes(tag, parent_tag)
        # If we get block-level content, return as-is
        # Otherwise wrap in paragraph
        if nodes:
            return nodes
        return None

    # Break tags
    if tag_name == 'br':
        return {
            "type": "hardBreak"
        }

    # Unknown tags - extract text
    text = tag.get_text(strip=True)
    if text:
        logger.debug(f"Unsupported tag: {tag_name}, extracting text")
        return {
            "type": "paragraph",
            "content": [{
                "type": "text",
                "text": text
            }]
        }

    return None


def _convert_inline_content(element) -> List[Dict[str, Any]]:
    """Convert inline HTML elements to ADF text with marks.

    Args:
        element: BeautifulSoup element

    Returns:
        List of ADF text nodes with marks
    """
    content = []

    for child in element.children:
        if isinstance(child, NavigableString):
            text = str(child)
            if text and text.strip():
                content.append({
                    "type": "text",
                    "text": text
                })
        elif isinstance(child, Tag):
            text_node = _convert_inline_tag(child)
            if text_node:
                if isinstance(text_node, list):
                    content.extend(text_node)
                else:
                    content.append(text_node)

    return content if content else [{"type": "text", "text": ""}]


def _convert_inline_tag(tag: Tag) -> Optional[Any]:
    """Convert inline HTML tags to ADF text with marks.

    Args:
        tag: BeautifulSoup tag

    Returns:
        ADF text node(s) with marks
    """
    tag_name = tag.name.lower()
    text = tag.get_text()

    # Build marks list
    marks = []

    if tag_name in ['strong', 'b']:
        marks.append({"type": "strong"})
    elif tag_name in ['em', 'i']:
        marks.append({"type": "em"})
    elif tag_name in ['u']:
        marks.append({"type": "underline"})
    elif tag_name in ['s', 'strike', 'del']:
        marks.append({"type": "strike"})
    elif tag_name in ['code']:
        marks.append({"type": "code"})
    elif tag_name == 'a':
        href = tag.get('href')
        if href:
            marks.append({
                "type": "link",
                "attrs": {"href": href}
            })

    # Recursively process children if there are nested tags
    if tag.find():
        # Has nested tags, recurse
        nested_content = _convert_inline_content(tag)
        # Apply marks to all nested content
        for node in nested_content:
            if marks:
                node['marks'] = marks + node.get('marks', [])
        return nested_content

    # No nested tags, create text node
    if text:
        node = {
            "type": "text",
            "text": text
        }
        if marks:
            node["marks"] = marks
        return node

    return None


def _convert_list_items(list_tag: Tag) -> List[Dict[str, Any]]:
    """Convert list items to ADF format.

    Args:
        list_tag: BeautifulSoup ul or ol tag

    Returns:
        List of ADF listItem nodes
    """
    items = []
    for li in list_tag.find_all('li', recursive=False):
        # Check if list item contains nested lists
        nested_list = li.find(['ul', 'ol'], recursive=False)

        if nested_list:
            # Get content before nested list
            content = []
            for child in li.children:
                if child == nested_list:
                    break
                if isinstance(child, NavigableString):
                    text = str(child).strip()
                    if text:
                        content.append({
                            "type": "paragraph",
                            "content": [{
                                "type": "text",
                                "text": text
                            }]
                        })
                elif isinstance(child, Tag) and child.name not in ['ul', 'ol']:
                    node = _convert_tag(child)
                    if node:
                        content.append(node if not isinstance(node, list) else node[0])

            # Add nested list
            nested_node = _convert_tag(nested_list)
            if nested_node:
                content.append(nested_node)

            items.append({
                "type": "listItem",
                "content": content if content else [{
                    "type": "paragraph",
                    "content": []
                }]
            })
        else:
            # Simple list item
            item_content = _convert_inline_content(li)
            items.append({
                "type": "listItem",
                "content": [{
                    "type": "paragraph",
                    "content": item_content
                }]
            })

    return items


def _convert_table(table_tag: Tag) -> Dict[str, Any]:
    """Convert HTML table to ADF format.

    Args:
        table_tag: BeautifulSoup table tag

    Returns:
        ADF table node
    """
    rows = []

    for tr in table_tag.find_all('tr'):
        cells = []
        for cell in tr.find_all(['td', 'th']):
            cell_type = "tableHeader" if cell.name == 'th' else "tableCell"
            cells.append({
                "type": cell_type,
                "content": _convert_nodes(cell, parent_tag='table')
            })

        if cells:
            rows.append({
                "type": "tableRow",
                "content": cells
            })

    return {
        "type": "table",
        "content": rows
    }
