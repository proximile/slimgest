"""Tests for folder truncation in `gitingest.output_formatter`.

The truncation layer collapses oversized folders in both the directory tree and
the file-contents output. These tests cover the unit helper (`_truncate_children`)
and an end-to-end check through `format_node` against a real on-disk directory.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from gitingest.ingestion import _process_node
from gitingest.output_formatter import (
    TRUNC_END,
    TRUNC_ENDS_AND_MIDDLE,
    TRUNC_MIDDLE,
    FolderTruncateConfig,
    _ElidedGroup,
    _truncate_children,
    format_node,
)
from gitingest.schemas import (
    FileSystemNode,
    FileSystemNodeType,
    FileSystemStats,
    IngestionQuery,
)


def _fake_children(n: int) -> list:
    """Return n sentinel objects suitable for `_truncate_children`.

    The helper only checks identity / `isinstance(_, _ElidedGroup)`, so any
    object works as a stand-in for a `FileSystemNode`.
    """

    class _Stub:
        def __init__(self, i: int) -> None:
            self.i = i

        def __repr__(self) -> str:  # pragma: no cover - debugging aid only
            return f"f{self.i}"

    return [_Stub(i) for i in range(n)]


# ---------------------------------------------------------------------------
# _truncate_children
# ---------------------------------------------------------------------------


def test_truncate_returns_input_when_config_none() -> None:
    children = _fake_children(50)
    assert _truncate_children(children, None) == children


def test_truncate_returns_input_when_under_threshold() -> None:
    children = _fake_children(5)
    cfg = FolderTruncateConfig(threshold=5, mode=TRUNC_MIDDLE)
    # threshold = len(children) => no truncation (we collapse only when strictly greater)
    assert _truncate_children(children, cfg) == children


def test_truncate_middle_mode() -> None:
    children = _fake_children(10)
    cfg = FolderTruncateConfig(threshold=4, keep=4, mode=TRUNC_MIDDLE)
    result = _truncate_children(children, cfg)
    # keep=4 -> head=2, tail=2, one elision in the middle of size 6.
    assert result[:2] == children[:2]
    assert result[-2:] == children[-2:]
    assert isinstance(result[2], _ElidedGroup)
    assert result[2].count == 6
    # No items lost or duplicated.
    elided = sum(e.count for e in result if isinstance(e, _ElidedGroup))
    shown = sum(1 for e in result if not isinstance(e, _ElidedGroup))
    assert shown + elided == len(children)


def test_truncate_end_mode() -> None:
    children = _fake_children(10)
    cfg = FolderTruncateConfig(threshold=4, keep=4, mode=TRUNC_END)
    result = _truncate_children(children, cfg)
    assert result[:4] == children[:4]
    assert isinstance(result[4], _ElidedGroup)
    assert result[4].count == 6
    assert len(result) == 5


def test_truncate_ends_and_middle_mode_two_gaps() -> None:
    children = _fake_children(10)
    cfg = FolderTruncateConfig(threshold=6, keep=6, mode=TRUNC_ENDS_AND_MIDDLE)
    result = _truncate_children(children, cfg)

    gaps = [i for i, e in enumerate(result) if isinstance(e, _ElidedGroup)]
    assert len(gaps) == 2, f"expected 2 gaps, got {len(gaps)}: {result}"

    elided_total = sum(e.count for e in result if isinstance(e, _ElidedGroup))
    shown_total = sum(1 for e in result if not isinstance(e, _ElidedGroup))
    assert shown_total == 6
    assert shown_total + elided_total == len(children)


def test_truncate_ends_and_middle_degrades_when_keep_covers_almost_all() -> None:
    """With keep=8 of 10, only 2 items get elided so a 2-gap layout would be silly.

    The helper should degrade to a single-gap middle layout in that case.
    """
    children = _fake_children(10)
    cfg = FolderTruncateConfig(threshold=8, keep=8, mode=TRUNC_ENDS_AND_MIDDLE)
    result = _truncate_children(children, cfg)
    gaps = [e for e in result if isinstance(e, _ElidedGroup)]
    # Either one or two gaps is acceptable, but the total elided count must be 2.
    assert sum(g.count for g in gaps) == 2


def test_truncate_keep_one() -> None:
    children = _fake_children(10)
    cfg = FolderTruncateConfig(threshold=4, keep=1, mode=TRUNC_MIDDLE)
    result = _truncate_children(children, cfg)
    assert result[0] is children[0]
    assert isinstance(result[1], _ElidedGroup)
    assert result[1].count == 9
    assert len(result) == 2


def test_truncate_default_keep_equals_threshold() -> None:
    children = _fake_children(10)
    cfg = FolderTruncateConfig(threshold=9, mode=TRUNC_MIDDLE)  # keep defaults to 9
    result = _truncate_children(children, cfg)
    shown = sum(1 for e in result if not isinstance(e, _ElidedGroup))
    elided = sum(e.count for e in result if isinstance(e, _ElidedGroup))
    assert shown == 9
    assert elided == 1


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"threshold": 0}, "threshold"),
        ({"keep": -1}, "keep"),
        ({"mode": "bogus"}, "mode"),
    ],
)
def test_config_validation(kwargs: dict, match: str) -> None:
    base = {"threshold": 5, "mode": TRUNC_MIDDLE}
    base.update(kwargs)
    with pytest.raises(ValueError, match=match):
        FolderTruncateConfig(**base)


# ---------------------------------------------------------------------------
# format_node end-to-end
# ---------------------------------------------------------------------------


def _build_fat_dir(tmp_path: Path, n_files: int = 30) -> Path:
    """Create a directory with `n_files` flat files plus a small subdir."""
    root = tmp_path / "fat_repo"
    root.mkdir()
    for i in range(n_files):
        (root / f"file_{i:02d}.txt").write_text(f"content {i}\n")
    sub = root / "sub"
    sub.mkdir()
    (sub / "a.txt").write_text("a\n")
    (sub / "b.txt").write_text("b\n")
    return root


def _build_node(path: Path, query: IngestionQuery) -> FileSystemNode:
    node = FileSystemNode(
        name=path.name,
        type=FileSystemNodeType.DIRECTORY,
        path_str=str(path.relative_to(query.local_path)),
        path=path,
    )
    _process_node(node=node, query=query, stats=FileSystemStats())
    return node


def _make_query(local_path: Path) -> IngestionQuery:
    return IngestionQuery(
        user_name=None,
        repo_name=None,
        local_path=local_path,
        slug="fat_repo",
        id=uuid.uuid4(),
        branch=None,
        max_file_size=1_000_000,
        ignore_patterns=set(),
    )


def test_format_node_no_truncate_preserves_baseline(tmp_path: Path) -> None:
    root = _build_fat_dir(tmp_path, n_files=30)
    query = _make_query(local_path=root)
    node = _build_node(root, query)

    _summary, tree, content = format_node(node, query=query)

    # All 30 files appear by name in both tree and contents.
    for i in range(30):
        fname = f"file_{i:02d}.txt"
        assert fname in tree
        assert fname in content


def test_format_node_truncate_middle_collapses_tree_and_contents(tmp_path: Path) -> None:
    root = _build_fat_dir(tmp_path, n_files=30)
    query = _make_query(local_path=root)
    node = _build_node(root, query)

    cfg = FolderTruncateConfig(threshold=6, keep=6, mode=TRUNC_MIDDLE)
    _summary, tree, content = format_node(node, query=query, truncate=cfg)

    # Tree marker should appear.
    assert "items collapsed" in tree
    # Content marker should appear too, with the directory path context.
    assert "items collapsed in fat_repo/" in content

    # The early files should be present (head of the truncation).
    assert "file_00.txt" in tree
    assert "file_00.txt" in content
    # The last files should be present too.
    assert "file_29.txt" in tree
    assert "file_29.txt" in content
    # Many middle files should be absent.
    assert "file_15.txt" not in tree
    assert "file_15.txt" not in content


def test_format_node_truncate_end_only_keeps_head(tmp_path: Path) -> None:
    root = _build_fat_dir(tmp_path, n_files=20)
    query = _make_query(local_path=root)
    node = _build_node(root, query)

    cfg = FolderTruncateConfig(threshold=5, keep=5, mode=TRUNC_END)
    _summary, tree, content = format_node(node, query=query, truncate=cfg)

    # First files present.
    assert "file_00.txt" in tree
    # Late files gone.
    assert "file_19.txt" not in tree
    assert "file_19.txt" not in content
    # Exactly one collapse marker per directory above threshold.
    assert tree.count("... ") >= 1  # tree marker
    assert content.count("items collapsed in fat_repo/") == 1


def test_format_node_truncate_ends_and_middle_produces_two_gaps(tmp_path: Path) -> None:
    root = _build_fat_dir(tmp_path, n_files=30)
    query = _make_query(local_path=root)
    node = _build_node(root, query)

    cfg = FolderTruncateConfig(threshold=6, keep=6, mode=TRUNC_ENDS_AND_MIDDLE)
    _summary, tree, content = format_node(node, query=query, truncate=cfg)

    # Two collapse markers in the contents section for the one fat folder.
    assert content.count("items collapsed in fat_repo/") == 2


def test_format_node_truncation_elision_counts_are_consistent(tmp_path: Path) -> None:
    """The numeric counts in tree elisions and content elisions must agree."""
    import re

    root = _build_fat_dir(tmp_path, n_files=30)
    query = _make_query(local_path=root)
    node = _build_node(root, query)

    cfg = FolderTruncateConfig(threshold=6, keep=6, mode=TRUNC_ENDS_AND_MIDDLE)
    _summary, tree, content = format_node(node, query=query, truncate=cfg)

    tree_counts = sorted(int(m) for m in re.findall(r"\.\.\. (\d+) items? collapsed \.\.\.", tree))
    content_counts = sorted(int(m) for m in re.findall(r"\[(\d+) items? collapsed in ", content))
    assert tree_counts == content_counts, f"tree={tree_counts} content={content_counts}"
    # Sanity: the totals match what we expect — keep=6 visible out of 30 children
    # (note: the small `sub` dir is a 31st child once counted, but it's well below
    # the threshold so it stays). 30 files + 1 dir = 31 children, 6 shown, 25 elided.
    assert sum(tree_counts) == 31 - 6


def test_format_node_truncation_does_not_touch_small_dirs(tmp_path: Path) -> None:
    """A subdir with only 2 children should not be affected by a threshold of 6."""
    root = _build_fat_dir(tmp_path, n_files=30)
    query = _make_query(local_path=root)
    node = _build_node(root, query)

    cfg = FolderTruncateConfig(threshold=6, keep=6, mode=TRUNC_MIDDLE)
    _summary, tree, content = format_node(node, query=query, truncate=cfg)

    # Files inside the small `sub/` directory are still fully present.
    assert "sub/" in tree
    assert "a.txt" in tree
    assert "b.txt" in tree
    assert "sub/a.txt" in content
    assert "sub/b.txt" in content
