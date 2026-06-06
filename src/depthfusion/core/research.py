from __future__ import annotations
import json
import os
import re
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional


RESEARCH_DIR = Path.home() / '.claude' / 'shared' / 'research'
MAX_WEB_RESULTS = 10
MAX_ARXIV_RESULTS = 5
MAX_GITHUB_RESULTS = 10


def _ddg_search(query: str) -> list:
    """DuckDuckGo Instant Answer API — no API key required."""
    url = (
        'https://api.duckduckgo.com/?'
        + urllib.parse.urlencode({'q': query, 'format': 'json', 'no_html': '1', 'skip_disambig': '1'})
    )
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'DepthFusion/1.0'})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        results = []
        if data.get('AbstractText'):
            results.append({
                'title': data.get('Heading', query),
                'snippet': data['AbstractText'],
                'url': data.get('AbstractURL', ''),
            })
        for r in data.get('RelatedTopics', [])[:MAX_WEB_RESULTS]:
            if isinstance(r, dict) and r.get('Text'):
                results.append({
                    'title': r.get('Text', '')[:100],
                    'snippet': r.get('Text', ''),
                    'url': r.get('FirstURL', ''),
                })
        return results[:MAX_WEB_RESULTS]
    except Exception as e:
        return [{'error': str(e)}]


def _arxiv_search(query: str) -> list:
    """arXiv API search — no API key required."""
    url = (
        'https://export.arxiv.org/api/query?'
        + urllib.parse.urlencode({
            'search_query': f'all:{query}',
            'max_results': MAX_ARXIV_RESULTS,
            'sortBy': 'relevance',
        })
    )
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'DepthFusion/1.0'})
        with urllib.request.urlopen(req, timeout=15) as resp:
            content = resp.read().decode('utf-8', errors='replace')
        papers = []
        for entry in re.findall(r'<entry>(.*?)</entry>', content, re.DOTALL):
            title_m = re.search(r'<title>(.*?)</title>', entry, re.DOTALL)
            summary_m = re.search(r'<summary>(.*?)</summary>', entry, re.DOTALL)
            id_m = re.search(r'<id>(http[^<]+)</id>', entry)
            if title_m:
                papers.append({
                    'title': title_m.group(1).strip(),
                    'summary': (summary_m.group(1).strip()[:500] if summary_m else ''),
                    'url': (id_m.group(1).strip() if id_m else ''),
                })
        return papers
    except Exception as e:
        return [{'error': str(e)}]


def _github_search(query: str, github_token: str = '') -> list:
    """GitHub repository search."""
    headers = {
        'Accept': 'application/vnd.github.v3+json',
        'User-Agent': 'DepthFusion/1.0',
    }
    if github_token:
        headers['Authorization'] = f'token {github_token}'
    url = (
        'https://api.github.com/search/repositories?'
        + urllib.parse.urlencode({'q': query, 'sort': 'stars', 'per_page': MAX_GITHUB_RESULTS})
    )
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        return [
            {
                'name': r['full_name'],
                'description': r.get('description', ''),
                'stars': r.get('stargazers_count', 0),
                'url': r.get('html_url', ''),
            }
            for r in data.get('items', [])
        ]
    except Exception as e:
        return [{'error': str(e)}]


def _format_research_doc(topic: str, results: dict) -> str:
    lines = [f'# Research: {topic}', f'Timestamp: {results["timestamp"]}', '']

    web = results['sources'].get('web', [])
    if web:
        lines.append('## Web Search Results')
        for r in web:
            if 'error' not in r:
                lines.append(f"- **{r.get('title', '')}**: {r.get('snippet', '')}")
                if r.get('url'):
                    lines.append(f"  URL: {r['url']}")
        lines.append('')

    arxiv = results['sources'].get('arxiv', [])
    if arxiv:
        lines.append('## arXiv Papers')
        for p in arxiv:
            if 'error' not in p:
                lines.append(f"- **{p.get('title', '')}**: {p.get('summary', '')}")
                if p.get('url'):
                    lines.append(f"  URL: {p['url']}")
        lines.append('')

    gh = results['sources'].get('github', [])
    if gh:
        lines.append('## GitHub Repositories')
        for r in gh:
            if 'error' not in r:
                lines.append(f"- **{r.get('name', '')}** ({r.get('stars', 0)} stars): {r.get('description', '')}")
                if r.get('url'):
                    lines.append(f"  URL: {r['url']}")
        lines.append('')

    return '\n'.join(lines)


class TopicResearcher:
    def __init__(self, publish_fn: Optional[Callable] = None):
        self._publish = publish_fn

    def research(self, topic: str, slug: str, sources: Optional[list] = None) -> dict:
        """Research a topic and store results in ~/.claude/shared/research/."""
        if sources is None:
            sources = ['web', 'arxiv', 'github']

        github_token = os.environ.get('GITHUB_TOKEN', '')
        timestamp = datetime.now(timezone.utc).isoformat()
        results: dict = {'topic': topic, 'slug': slug, 'timestamp': timestamp, 'sources': {}}

        if 'web' in sources:
            results['sources']['web'] = _ddg_search(topic)

        if 'arxiv' in sources:
            results['sources']['arxiv'] = _arxiv_search(topic)

        if 'github' in sources:
            results['sources']['github'] = _github_search(topic, github_token=github_token)

        # Format and save
        doc = _format_research_doc(topic, results)
        RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r'[^\w\-]', '_', topic)[:50]
        out_path = RESEARCH_DIR / f'{safe_name}_{timestamp[:10]}.md'
        tmp = out_path.with_suffix('.tmp')
        tmp.write_text(doc, encoding='utf-8')
        os.replace(tmp, out_path)

        # Publish to DepthFusion KB
        if self._publish:
            self._publish(slug=slug, content=doc, tags=[slug, 'research', topic])

        results['saved_to'] = str(out_path)
        return results
