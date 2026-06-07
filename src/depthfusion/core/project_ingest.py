from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

_GITHUB_NAME_RE = re.compile(r'^[A-Za-z0-9_.\-]+$')


class _NoTokenOnRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Strip Authorization header if a redirect crosses to a different host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_req is None:
            return None
        from urllib.parse import urlparse
        if urlparse(newurl).netloc != urlparse(req.full_url).netloc:
            new_req.remove_header('Authorization')
        return new_req


INGEST_EXTENSIONS = {
    '.py', '.ts', '.tsx', '.js', '.jsx', '.md', '.yaml', '.yml', '.toml', '.json',
}
SKIP_DIRS = {
    '.git', '__pycache__', 'node_modules', '.next', 'dist', 'build', '.venv', 'venv', '.mypy_cache',
}
MAX_FILE_SIZE = 100_000  # 100KB per file
MAX_TOTAL_FILES = 2500
STRUCTURAL_KEY_FILES = {
    'BACKLOG.md', 'CLAUDE.md', 'README.md', 'README.rst', 'pyproject.toml', 'package.json',
}
STRUCTURAL_KEY_DIRS = {'core', 'src'}


class ProjectIngestor:
    def __init__(self, publish_fn: Callable):
        self._publish = publish_fn

    def ingest_local(self, slug: str, local_path: str, mode: str = 'structural') -> dict:
        """Ingest a local project directory. mode: 'structural' or 'full'."""
        project_dir = Path(local_path).resolve()
        if not project_dir.exists():
            raise ValueError(f'Path does not exist: {local_path}')

        files_ingested = 0
        bytes_ingested = 0

        def should_include(path: Path) -> bool:
            try:
                rel_parts = path.relative_to(project_dir).parts
            except ValueError:
                return False
            # Skip hidden dirs and known noise dirs
            if any(part.startswith('.') or part in SKIP_DIRS for part in rel_parts[:-1]):
                return False
            if mode == 'full':
                return path.suffix in INGEST_EXTENSIONS
            # structural: key root files + files in core dirs
            if len(rel_parts) == 1 and path.name in STRUCTURAL_KEY_FILES:
                return True
            if len(rel_parts) == 1 and path.suffix in ('.toml', '.yaml', '.yml'):
                return True
            if (any(part in STRUCTURAL_KEY_DIRS for part in rel_parts)
                    and path.suffix in ('.py', '.ts', '.tsx')):
                return True
            return False

        for path in sorted(project_dir.rglob('*')):
            if files_ingested >= MAX_TOTAL_FILES:
                break
            if not path.is_file():
                continue
            if not should_include(path):
                continue
            try:
                # Enforce MAX_FILE_SIZE BEFORE loading content into memory.
                # Stat first; for oversized files do a bounded read (never load
                # the whole file) to avoid memory-exhaustion DoS on large blobs.
                file_size = path.stat().st_size
                if file_size > MAX_FILE_SIZE:
                    with path.open('r', encoding='utf-8', errors='replace') as f:
                        content = f.read(MAX_FILE_SIZE) + '\n... [truncated at 100KB]'
                else:
                    content = path.read_text(encoding='utf-8', errors='replace')
                rel_path = str(path.relative_to(project_dir))
                doc = f'# File: {slug}/{rel_path}\n\n```\n{content}\n```'
                self._publish(slug=slug, content=doc, tags=[slug, 'ingest', f'file:{rel_path}'])
                files_ingested += 1
                bytes_ingested += len(content)
            except Exception:
                continue

        return {
            'files_ingested': files_ingested, 'bytes_ingested': bytes_ingested,
            'mode': mode, 'source': 'local',
        }

    def ingest_github(self, slug: str, github_url: str, mode: str = 'structural') -> dict:
        """Ingest a GitHub repo via API. Uses GITHUB_TOKEN env var if available."""
        token = os.environ.get('GITHUB_TOKEN', '')

        # Parse owner/repo from URL
        url = github_url.rstrip('/')
        if 'github.com/' in url:
            tail = url.split('github.com/')[-1]
        else:
            tail = url
        parts = tail.rstrip('/').split('/')
        if len(parts) < 2:
            raise ValueError(f'Cannot parse GitHub owner/repo from: {github_url}')
        owner = parts[0]
        repo = parts[1].removesuffix('.git')

        # Validate owner/repo to prevent path traversal and SSRF via crafted URLs
        if not _GITHUB_NAME_RE.match(owner) or not _GITHUB_NAME_RE.match(repo):
            raise ValueError(f'Invalid GitHub owner/repo characters: {owner}/{repo}')

        headers = {
            'Accept': 'application/vnd.github.v3+json',
            'User-Agent': 'DepthFusion/1.0',
        }
        if token:
            headers['Authorization'] = f'token {token}'

        # Use custom opener that strips Authorization on cross-host redirects
        _opener = urllib.request.build_opener(_NoTokenOnRedirectHandler)

        def gh_get(path: str) -> Any:
            req = urllib.request.Request(
                f'https://api.github.com/{path}',
                headers=headers,
            )
            with _opener.open(req, timeout=30) as resp:
                return json.loads(resp.read())

        # Repo metadata
        repo_meta = gh_get(f'repos/{owner}/{repo}')
        branch = repo_meta.get('default_branch', 'main')
        meta_doc = (
            f'# GitHub Repo: {owner}/{repo}\n\n'
            f'Description: {repo_meta.get("description", "")}\n'
            f'Language: {repo_meta.get("language", "")}\n'
            f'Stars: {repo_meta.get("stargazers_count", 0)}\n'
            f'Default branch: {branch}\n'
        )
        self._publish(slug=slug, content=meta_doc, tags=[slug, 'ingest', 'github-meta'])

        # File tree
        try:
            tree_data = gh_get(f'repos/{owner}/{repo}/git/trees/{branch}?recursive=1')
        except Exception as e:
            return {
                'files_ingested': 0, 'mode': mode,
                'source': f'github:{owner}/{repo}', 'error': str(e),
            }

        tree = tree_data.get('tree', [])
        files_ingested = 0
        import base64

        for item in tree:
            if files_ingested >= MAX_TOTAL_FILES:
                break
            if item.get('type') != 'blob':
                continue
            path = item['path']
            ext = Path(path).suffix
            name = Path(path).name

            # Skip noise directories
            if any(skip in path.split('/') for skip in SKIP_DIRS):
                continue

            if mode == 'structural':
                is_key_file = name in STRUCTURAL_KEY_FILES
                is_key_dir = any(d in path.split('/') for d in STRUCTURAL_KEY_DIRS)
                is_key_ext = ext in ('.py', '.ts', '.tsx')
                if not (is_key_file or (is_key_dir and is_key_ext)):
                    continue
            elif ext not in INGEST_EXTENSIONS:
                continue

            size = item.get('size', 0)
            if size > MAX_FILE_SIZE:
                continue

            try:
                file_data = gh_get(f'repos/{owner}/{repo}/contents/{urllib.parse.quote(path)}')
                raw = file_data.get('content', '')
                content = base64.b64decode(raw.replace('\n', '')).decode('utf-8', errors='replace')
                doc = f'# GitHub: {owner}/{repo}/{path}\n\n```\n{content}\n```'
                self._publish(slug=slug, content=doc, tags=[slug, 'ingest', f'github:{path}'])
                files_ingested += 1
            except Exception:
                continue

        return {'files_ingested': files_ingested, 'mode': mode, 'source': f'github:{owner}/{repo}'}
