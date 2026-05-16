"""Functions to ingest and analyze a codebase directory or single file."""

from __future__ import annotations

import os
import ssl
from dataclasses import dataclass
from typing import TYPE_CHECKING, Union

import requests.exceptions
import tiktoken

from gitingest.schemas import FileSystemNode, FileSystemNodeType
from gitingest.schemas.filesystem import SEPARATOR
from gitingest.utils.compat_func import readlink
from gitingest.utils.logging_config import get_logger

if TYPE_CHECKING:
    from gitingest.schemas import IngestionQuery

# Initialize logger for this module
logger = get_logger(__name__)

_TOKEN_THRESHOLDS: list[tuple[int, str]] = [
    (1_000_000, "M"),
    (1_000, "k"),
]

# Folder-truncation modes.
TRUNC_MIDDLE = "middle"  # keep first + last, elide the middle (1 gap)
TRUNC_END = "end"  # keep first, elide the tail (1 gap at the end)
TRUNC_ENDS_AND_MIDDLE = "ends-and-middle"  # keep first + middle + last (2 gaps)
_VALID_TRUNC_MODES = frozenset({TRUNC_MIDDLE, TRUNC_END, TRUNC_ENDS_AND_MIDDLE})


@dataclass(frozen=True)
class FolderTruncateConfig:
    """Configuration for collapsing oversized folders in the tree and contents.

    A folder with more than ``threshold`` direct children is collapsed so that at
    most ``keep`` children are shown; the rest are replaced by one or more elision
    markers depending on ``mode``.
    """

    threshold: int = 20
    keep: int | None = None  # defaults to threshold when None
    mode: str = TRUNC_MIDDLE

    def __post_init__(self) -> None:
        if self.threshold < 1:
            msg = f"threshold must be >= 1, got {self.threshold}"
            raise ValueError(msg)
        if self.keep is not None and self.keep < 0:
            msg = f"keep must be >= 0, got {self.keep}"
            raise ValueError(msg)
        if self.mode not in _VALID_TRUNC_MODES:
            msg = f"mode must be one of {sorted(_VALID_TRUNC_MODES)}, got {self.mode!r}"
            raise ValueError(msg)


@dataclass(frozen=True)
class _ElidedGroup:
    """Placeholder representing a contiguous block of children that were collapsed."""

    count: int


_ChildOrElision = Union[FileSystemNode, _ElidedGroup]


def _truncate_children(
    children: list[FileSystemNode],
    config: FolderTruncateConfig | None,
) -> list[_ChildOrElision]:
    """Apply folder truncation to a list of direct children.

    Returns the original list when ``config`` is ``None`` or the folder is at or
    below the threshold. Otherwise returns a mixed list of ``FileSystemNode`` and
    ``_ElidedGroup`` entries in display order.
    """
    if config is None or len(children) <= config.threshold:
        return list(children)

    keep = config.threshold if config.keep is None else config.keep
    n = len(children)
    keep = min(keep, n)  # never "keep" more than we have

    if config.mode == TRUNC_END:
        head = children[:keep]
        elided = n - keep
        result: list[_ChildOrElision] = list(head)
        if elided > 0:
            result.append(_ElidedGroup(count=elided))
        return result

    if config.mode == TRUNC_MIDDLE:
        head_size = (keep + 1) // 2
        tail_size = keep - head_size
        head = children[:head_size]
        tail = children[n - tail_size:] if tail_size > 0 else []
        elided = n - head_size - tail_size
        result = list(head)
        if elided > 0:
            result.append(_ElidedGroup(count=elided))
        result.extend(tail)
        return result

    # TRUNC_ENDS_AND_MIDDLE: split keep across head + middle + tail, centered.
    head_size = (keep + 2) // 3
    tail_size = (keep + 2) // 3
    # Cap so we never over-allocate when keep is small.
    head_size = min(head_size, keep)
    tail_size = min(tail_size, keep - head_size)
    mid_size = max(0, keep - head_size - tail_size)

    available_middle = n - head_size - tail_size
    if mid_size >= available_middle:
        # Nothing left to elide once we account for the blocks; degrade gracefully
        # to a single-gap middle truncation.
        head = children[:head_size]
        tail = children[n - tail_size:] if tail_size > 0 else []
        elided = n - head_size - tail_size
        result = list(head)
        if elided > 0:
            result.append(_ElidedGroup(count=elided))
        result.extend(tail)
        return result

    mid_center = n // 2
    mid_start = max(head_size, mid_center - mid_size // 2)
    mid_end = mid_start + mid_size
    if mid_end > n - tail_size:
        mid_end = n - tail_size
        mid_start = mid_end - mid_size

    head = children[:head_size]
    middle = children[mid_start:mid_end]
    tail = children[n - tail_size:] if tail_size > 0 else []
    first_gap = mid_start - head_size
    second_gap = (n - tail_size) - mid_end

    result = list(head)
    if first_gap > 0:
        result.append(_ElidedGroup(count=first_gap))
    result.extend(middle)
    if second_gap > 0:
        result.append(_ElidedGroup(count=second_gap))
    result.extend(tail)
    return result


def format_node(
    node: FileSystemNode,
    query: IngestionQuery,
    *,
    truncate: FolderTruncateConfig | None = None,
) -> tuple[str, str, str]:
    """Generate a summary, directory structure, and file contents for a given file system node.

    If the node represents a directory, the function will recursively process its contents.
    When ``truncate`` is provided, directories with more direct children than its threshold
    are collapsed in both the tree and the file-contents output.

    Parameters
    ----------
    node : FileSystemNode
        The file system node to be summarized.
    query : IngestionQuery
        The parsed query object containing information about the repository and query parameters.
    truncate : FolderTruncateConfig | None
        Optional folder-truncation configuration. When ``None``, output is unchanged.

    Returns
    -------
    tuple[str, str, str]
        A tuple containing the summary, directory structure, and file contents.

    """
    is_single_file = node.type == FileSystemNodeType.FILE
    summary = _create_summary_prefix(query, single_file=is_single_file)

    if node.type == FileSystemNodeType.DIRECTORY:
        summary += f"Files analyzed: {node.file_count}\n"
    elif node.type == FileSystemNodeType.FILE:
        summary += f"File: {node.name}\n"
        summary += f"Lines: {len(node.content.splitlines()):,}\n"

    tree = "Directory structure:\n" + _create_tree_structure(query, node=node, truncate=truncate)

    content = _gather_file_contents(node, truncate=truncate)

    token_estimate = _format_token_count(tree + content)
    if token_estimate:
        summary += f"\nEstimated tokens: {token_estimate}"

    return summary, tree, content


def _create_summary_prefix(query: IngestionQuery, *, single_file: bool = False) -> str:
    """Create a prefix string for summarizing a repository or local directory.

    Includes repository name (if provided), commit/branch details, and subpath if relevant.

    Parameters
    ----------
    query : IngestionQuery
        The parsed query object containing information about the repository and query parameters.
    single_file : bool
        A flag indicating whether the summary is for a single file (default: ``False``).

    Returns
    -------
    str
        A summary prefix string containing repository, commit, branch, and subpath details.

    """
    parts = []

    if query.user_name:
        parts.append(f"Repository: {query.user_name}/{query.repo_name}")
    else:
        # Local scenario
        parts.append(f"Directory: {query.slug}")

    if query.tag:
        parts.append(f"Tag: {query.tag}")
    elif query.branch and query.branch not in ("main", "master"):
        parts.append(f"Branch: {query.branch}")

    if query.commit:
        parts.append(f"Commit: {query.commit}")

    if query.subpath != "/" and not single_file:
        parts.append(f"Subpath: {query.subpath}")

    return "\n".join(parts) + "\n"


def _gather_file_contents(
    node: FileSystemNode,
    *,
    truncate: FolderTruncateConfig | None = None,
) -> str:
    """Recursively gather contents of all files under the given node.

    This function recursively processes a directory node and gathers the contents of all files
    under that node. It returns the concatenated content of all files as a single string.

    When ``truncate`` is provided, directories whose direct-child count exceeds its threshold
    have their content output collapsed to match the truncated tree view.

    Parameters
    ----------
    node : FileSystemNode
        The current directory or file node being processed.
    truncate : FolderTruncateConfig | None
        Optional folder-truncation configuration applied at every directory level.

    Returns
    -------
    str
        The concatenated content of all files under the given node.

    """
    if node.type != FileSystemNodeType.DIRECTORY:
        return node.content_string

    parts: list[str] = []
    for entry in _truncate_children(node.children, truncate):
        if isinstance(entry, _ElidedGroup):
            parts.append(_format_content_elision(entry, parent=node))
        else:
            parts.append(_gather_file_contents(entry, truncate=truncate))
    return "\n".join(parts)


def _format_content_elision(group: _ElidedGroup, *, parent: FileSystemNode) -> str:
    """Render a content-section marker for a collapsed block of children."""
    parent_path = str(parent.path_str).replace(os.sep, "/").rstrip("/")
    # Root nodes have path_str == "." — fall back to the slug-style name so the
    # marker reads "in fat_repo/" rather than "in ./".
    if not parent_path or parent_path == ".":
        parent_path = parent.name or "."
    plural = "s" if group.count != 1 else ""
    return (
        f"{SEPARATOR}\n"
        f"[{group.count} item{plural} collapsed in {parent_path}/]\n"
        f"{SEPARATOR}\n"
    )


def _create_tree_structure(
    query: IngestionQuery,
    *,
    node: FileSystemNode,
    prefix: str = "",
    is_last: bool = True,
    truncate: FolderTruncateConfig | None = None,
) -> str:
    """Generate a tree-like string representation of the file structure.

    This function generates a string representation of the directory structure, formatted
    as a tree with appropriate indentation for nested directories and files.

    Parameters
    ----------
    query : IngestionQuery
        The parsed query object containing information about the repository and query parameters.
    node : FileSystemNode
        The current directory or file node being processed.
    prefix : str
        A string used for indentation and formatting of the tree structure (default: ``""``).
    is_last : bool
        A flag indicating whether the current node is the last in its directory (default: ``True``).
    truncate : FolderTruncateConfig | None
        Optional folder-truncation configuration applied at every directory level.

    Returns
    -------
    str
        A string representing the directory structure formatted as a tree.

    """
    if not node.name:
        # If no name is present, use the slug as the top-level directory name
        node.name = query.slug

    tree_str = ""
    current_prefix = "└── " if is_last else "├── "

    # Indicate directories with a trailing slash
    display_name = node.name
    if node.type == FileSystemNodeType.DIRECTORY:
        display_name += "/"
    elif node.type == FileSystemNodeType.SYMLINK:
        display_name += " -> " + readlink(node.path).name

    tree_str += f"{prefix}{current_prefix}{display_name}\n"

    if node.type == FileSystemNodeType.DIRECTORY and node.children:
        child_prefix = prefix + ("    " if is_last else "│   ")
        display_entries = _truncate_children(node.children, truncate)
        for i, entry in enumerate(display_entries):
            entry_is_last = i == len(display_entries) - 1
            if isinstance(entry, _ElidedGroup):
                marker_prefix = "└── " if entry_is_last else "├── "
                plural = "s" if entry.count != 1 else ""
                tree_str += f"{child_prefix}{marker_prefix}... {entry.count} item{plural} collapsed ...\n"
            else:
                tree_str += _create_tree_structure(
                    query,
                    node=entry,
                    prefix=child_prefix,
                    is_last=entry_is_last,
                    truncate=truncate,
                )
    return tree_str


def _format_token_count(text: str) -> str | None:
    """Return a human-readable token-count string (e.g. 1.2k, 1.2 M).

    Parameters
    ----------
    text : str
        The text string for which the token count is to be estimated.

    Returns
    -------
    str | None
        The formatted number of tokens as a string (e.g., ``"1.2k"``, ``"1.2M"``), or ``None`` if an error occurs.

    """
    try:
        encoding = tiktoken.get_encoding("o200k_base")  # gpt-4o, gpt-4o-mini
        total_tokens = len(encoding.encode(text, disallowed_special=()))
    except (ValueError, UnicodeEncodeError) as exc:
        logger.warning("Failed to estimate token size", extra={"error": str(exc)})
        return None
    except (requests.exceptions.RequestException, ssl.SSLError) as exc:
        # If network errors, skip token count estimation instead of erroring out
        logger.warning("Failed to download tiktoken model", extra={"error": str(exc)})
        return None

    for threshold, suffix in _TOKEN_THRESHOLDS:
        if total_tokens >= threshold:
            return f"{total_tokens / threshold:.1f}{suffix}"

    return str(total_tokens)
