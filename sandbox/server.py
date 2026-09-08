import os
import re
import html as html_lib
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP('bing-baidu-search')

PAPER_PROXY = os.environ.get('PAPER_PROXY', '').strip()

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
}

def _get(url: str, params: dict[str, Any]) -> str:
    kwargs: dict[str, Any] = {
        'verify': False,
        'follow_redirects': True,
        'timeout': 20.0,
        'headers': HEADERS,
    }
    if PAPER_PROXY:
        kwargs['proxy'] = PAPER_PROXY
    with httpx.Client(**kwargs) as client:
        response = client.get(url, params=params)
        response.raise_for_status()
        return response.text

def _clean(segment: str) -> str:
    if not segment:
        return ''
    segment = re.sub(r'<script.*?</script>', ' ', segment, flags=re.S | re.I)
    segment = re.sub(r'<style.*?</style>', ' ', segment, flags=re.S | re.I)
    segment = re.sub(r'<[^>]+>', ' ', segment)
    return html_lib.unescape(' '.join(segment.split()))

def _parse_bing(page: str, limit: int) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    blocks = re.findall(r'<li[^>]*class=[^>]*b_algo[^>]*>(.*?)</li>', page, flags=re.S | re.I)
    for block in blocks:
        a = re.search(r'<a[^>]*href=([^ >]+)[^>]*>(.*?)</a>', block, flags=re.S | re.I)
        if not a:
            continue
        title = _clean(a.group(2))
        link = a.group(1).strip(chr(34) + chr(39))
        if not title or not link.startswith('http'):
            continue
        p = re.search(r'<p[^>]*>(.*?)</p>', block, flags=re.S | re.I)
        snippet = _clean(p.group(1)) if p else ''
        results.append({'title': title, 'link': link, 'snippet': snippet, 'engine': 'Bing'})
        if len(results) >= limit:
            break
    return results

def _parse_baidu(page: str, limit: int) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    pattern = re.compile(r'<h3[^>]*>(.*?)</h3>(.*?)(?=<h3|</body>|</html>|$)', flags=re.S | re.I)
    for match in pattern.finditer(page):
        title_block = match.group(1)
        tail = match.group(2)
        a = re.search(r'<a[^>]*href=([^ >]+)[^>]*>(.*?)</a>', title_block, flags=re.S | re.I)
        if not a:
            continue
        title = _clean(a.group(2))
        link = a.group(1).strip(chr(34) + chr(39))
        if not title or not link.startswith('http'):
            continue
        snippet = ''
        abs_match = re.search(r'class=[^>]*c-abstract[^>]*>(.*?)</div>', tail, flags=re.S | re.I)
        if abs_match:
            snippet = _clean(abs_match.group(1))
        results.append({'title': title, 'link': link, 'snippet': snippet, 'engine': 'Baidu'})
        if len(results) >= limit:
            break
    return results

@mcp.tool
def search_bing(query: str, num_results: int = 5) -> list[dict[str, Any]]:
    '''搜索 Bing 网页并返回结果。'''
    try:
        num_results = max(1, min(int(num_results), 10))
    except (TypeError, ValueError):
        num_results = 5
    try:
        page = _get('https://www.bing.com/search', {'q': query, 'count': str(num_results), 'mkt': 'zh-CN', 'setlang': 'zh-CN'})
    except httpx.HTTPError as exc:
        raise RuntimeError(f'Bing search request failed: {exc}') from exc
    parsed = _parse_bing(page, num_results)
    if not parsed:
        return [{'query': query, 'engine': 'Bing', 'warning': 'no result parsed, please refine query'}]
    return parsed

@mcp.tool
def search_baidu(query: str, num_results: int = 5) -> list[dict[str, Any]]:
    '''搜索百度网页并返回结果。'''
    try:
        num_results = max(1, min(int(num_results), 10))
    except (TypeError, ValueError):
        num_results = 5
    try:
        page = _get('https://www.baidu.com/s', {'wd': query, 'rn': str(num_results), 'ie': 'utf-8'})
    except httpx.HTTPError as exc:
        raise RuntimeError(f'Baidu search request failed: {exc}') from exc
    parsed = _parse_baidu(page, num_results)
    if not parsed:
        return [{'query': query, 'engine': 'Baidu', 'warning': 'no result parsed, please refine query'}]
    return parsed

if __name__ == '__main__':
    mcp.run(transport='stdio')