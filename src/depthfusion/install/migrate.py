"""Tier 1 → Tier 2 migration — indexes existing session/memory files into ChromaDB."""
from __future__ import annotations

import argparse
from pathlib import Path


def run_migration(dry_run: bool = False) -> None:
    """Index all existing content into ChromaDB for Tier 2."""
    home = Path.home()
    sources = [
        (home / ".claude" / "sessions", "*.tmp", "session"),
        (home / ".claude" / "shared" / "discoveries", "*.md", "discovery"),
        (home / ".claude" / "projects" / "-home-gregmorris" / "memory", "*.md", "memory"),
    ]

    files_to_index: list[tuple[Path, str]] = []
    for directory, pattern, source_type in sources:
        if directory.exists():
            for f in directory.glob(pattern):
                if f.name not in ("MEMORY.md", "README.md"):
                    files_to_index.append((f, source_type))

    print(f"Found {len(files_to_index)} files to index into ChromaDB Tier 2")

    if dry_run:
        for f, src in files_to_index[:10]:
            print(f"  [DRY-RUN] Would index: {f.name} ({src})")
        if len(files_to_index) > 10:
            print(f"  ... and {len(files_to_index) - 10} more")
        return

    from depthfusion.storage.vector_store import ChromaDBStore, is_chromadb_available
    if not is_chromadb_available():
        print("Error: chromadb not installed. Run: pip install 'depthfusion[vps-gpu]'")
        raise SystemExit(1)

    store = ChromaDBStore()
    indexed = 0
    for file_path, source_type in files_to_index:
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
            if content.strip():
                store.add_document(
                    doc_id=file_path.stem,
                    content=content[:8000],
                    metadata={"source": source_type, "filename": file_path.name},
                )
                indexed += 1
                if indexed % 10 == 0:
                    print(f"  Indexed {indexed}/{len(files_to_index)}...")
        except Exception as exc:
            print(f"  Warning: could not index {file_path.name}: {exc}")

    print(f"Migration complete. Indexed {indexed} documents into ChromaDB.")
    print(f"Total vectors in store: {store.count()}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate DepthFusion to Tier 2 (ChromaDB)")
    parser.add_argument("--dry-run", action="store_true",
                        help="List files without indexing")
    args = parser.parse_args()
    run_migration(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
