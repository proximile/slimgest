# slimgest

**slimgest** is a [Proximile LLC](https://proximile.llc) fork of
[gitingest](https://github.com/coderamp-labs/gitingest) that adds
intelligent collapsing of oversized folders so big repos still fit in an
LLM context window. The original gitingest API and CLI surface remain
intact — slimgest is meant to be a drop-in upgrade.

> **Fork status:** slimgest is built on top of upstream gitingest and
> tracks it as the canonical base. CLI and Python API features (URL
> ingest, branch/tag selection, include/exclude patterns, GitHub PAT
> support, submodule handling, .gitignore filtering) work the same as
> upstream. The web UI / self-hosted server surface from upstream is
> **not** carried over in this release — slimgest is currently focused
> on the CLI and library; if you want the hosted UI, run upstream
> gitingest. See [What's new in slimgest](#whats-new-in-slimgest) for
> the additions and [Roadmap](#roadmap) for a hosted-variant note.

---

## What's new in slimgest

Gitingest's default behaviour is to expand every file in every folder
into the digest. For repos with very wide directories (build outputs,
generated SDKs, vendored deps, large `tests/` trees) that blows the
output size out and floods the LLM with low-signal content.

slimgest lets you collapse oversized directories. When a folder has more
than `N` direct children, its tree view and content section both get
truncated, with markers that show how many entries were elided.

Three truncation modes:

| Mode | Layout | Use case |
|---|---|---|
| `middle` (default) | first K + … + last K | Preserve alphabetical anchors at both ends; classic head/tail compression. |
| `end` | first K + … (tail elided) | Cheapest — only the first K entries survive. Good when you only care about the start of a directory. |
| `ends-and-middle` | first K + … + middle K + … + last K | Two elided gaps with a middle window preserved. Useful when interior entries are interesting (e.g., alphabetically-grouped modules). |

### Tree-view example

A directory with 12 Python files at `--max-folder-children 6 --folder-truncate-mode ends-and-middle`:

```text
└── gitingest/
    ├── __init__.py
    ├── __main__.py
    ├── ... 2 items collapsed ...
    ├── entrypoint.py
    ├── ingestion.py
    ├── ... 2 items collapsed ...
    ├── schemas/
    │   ├── __init__.py
    │   ├── cloning.py
    │   ├── filesystem.py
    │   └── ingestion.py
    └── utils/
        ├── __init__.py
        ├── auth.py
        ├── ... 4 items collapsed ...
        ├── git_utils.py
        ├── ignore_patterns.py
        ├── ... 5 items collapsed ...
        ├── query_parser_utils.py
        └── timeout_wrapper.py
```

The file-contents section is collapsed consistently with the tree:

```text
================================================
[2 items collapsed in gitingest/]
================================================
```

Subdirectories below the threshold are untouched, so the structural
shape of small folders is preserved.

---

## Install

slimgest is currently distributed from GitHub. Install it directly from
the repository:

```bash
pip install git+https://github.com/proximile/slimgest.git
```

Or with `pipx` for an isolated CLI:

```bash
pipx install git+https://github.com/proximile/slimgest.git
```

slimgest exposes **two CLI commands** pointing at the same entry point:

- `gitingest` — drop-in compatible with upstream invocations.
- `slimgest` — same binary, clearer that you're running the fork.

The Python import name remains `gitingest`, so existing code keeps working:

```python
from gitingest import ingest
```

## CLI usage

All upstream flags continue to work. The new flags are:

| Flag | Type | Default | Effect |
|---|---|---|---|
| `--max-folder-children` | int | unset (off) | Collapse any directory with more than this many direct children. Omit to disable folder truncation. |
| `--folder-truncate-mode` | `middle` \| `end` \| `ends-and-middle` | `middle` | Which collapse layout to use. |
| `--folder-truncate-keep` | int | matches `--max-folder-children` | How many children to keep visible in a collapsed directory. |

Examples:

```bash
# Collapse any directory with more than 10 entries; keep first 5 + last 5.
gitingest /path/to/repo --max-folder-children 10

# Aggressive: cut down to 6 visible entries per oversized dir.
slimgest /path/to/repo --max-folder-children 6 --folder-truncate-mode ends-and-middle

# Quick scan: show only the head of every wide folder.
slimgest https://github.com/some/repo --max-folder-children 8 --folder-truncate-mode end -o -
```

See `gitingest --help` (or `slimgest --help`) for the full option list.

## Python API

```python
from gitingest import ingest
from gitingest.output_formatter import FolderTruncateConfig

summary, tree, content = ingest(
    "/path/to/repo",
    folder_truncate=FolderTruncateConfig(
        threshold=10,
        keep=6,
        mode="ends-and-middle",
    ),
)
```

`FolderTruncateConfig`:

- `threshold: int` — collapse a directory when it has more than this many direct children.
- `keep: int | None` — number of children to keep visible (defaults to `threshold`).
- `mode: str` — `"middle"`, `"end"`, or `"ends-and-middle"`. Constants
  `TRUNC_MIDDLE`, `TRUNC_END`, `TRUNC_ENDS_AND_MIDDLE` are exported from
  the same module.

The async API (`ingest_async`) and `ingest_query` accept the same
`folder_truncate` argument.

## Roadmap

slimgest is starting with folder-level truncation. Planned next:

- **AST-aware compression** via `tree-sitter` or per-language CSTs:
  collapse function bodies, keep signatures and docstrings, drop
  comment-only blocks.
- **Symbol-graph collapsing**: build a call/reference graph and keep
  only what's reachable from a chosen entry point.
- **Per-file token budgets**: extend the head/tail/middle layout to
  individual files, not just directories.
- **Type-driven prioritisation**: always preserve type definitions,
  schema/migration files, and public API surfaces; aggressively truncate
  vendored, generated, or fixture content.
- **Repo-level token budget**: solve the inverse problem — "fit this
  repo into N tokens" — by combining the techniques above.
- **Hosted variant** (web UI / self-hosted server). Upstream gitingest
  ships a FastAPI server; we removed it from slimgest's first release
  because it didn't yet expose the new truncation flags. A slimgest
  hosted variant may return once the UI is wired to the new features.

These are direction, not promises.

## Upstream credit

slimgest is built directly on
[`coderamp-labs/gitingest`](https://github.com/coderamp-labs/gitingest)
by Romain Courtois and contributors. All credit for the original tool
goes to them. Bug reports specific to upstream behaviour are best filed
on the upstream repo; slimgest-specific issues belong here.

## License

slimgest is MIT-licensed, matching upstream. See [`LICENSE`](./LICENSE)
for the full text. Original work copyright (c) 2024 Romain Courtois;
modifications copyright (c) 2026 Proximile LLC.

## Contributing & contact

- **Bug reports / feature requests**: use
  [GitHub Issues](https://github.com/proximile/slimgest/issues) on this
  repository.
- **Security disclosures**: use the
  [Security Advisories](https://github.com/proximile/slimgest/security/advisories)
  tab on this repository for private reporting.
- See [`CONTRIBUTING.md`](./CONTRIBUTING.md) for the development setup.
