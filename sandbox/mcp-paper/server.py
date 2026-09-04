# -*- coding: utf-8 -*-
"""Paper crawler MCP: 论文搜索 + 元数据 + PDF 全文下载与抽取。

数据源（用户拍板）：arXiv、Semantic Scholar、Google Scholar。
深度：元数据 + PDF 全文。

传输方式：stdio（标准 MCP 协议，任意 MCP client 可连）。

网络层统一 httpx（verify=False + 代理环境变量）：本机 6789 代理对 requests
的 SSL 握手不兼容（UNEXPECTED_EOF），但对 httpx 兼容——这是实测结论，故
arxiv 的 Atom API 直接用 httpx + stdlib 解析，不依赖 arxiv 库（其内部走
requests）。代理地址默认读 HTTP(S)_PROXY，也可用 PAPER_PROXY 显式指定。
"""
import os
import xml.etree.ElementTree as ET

import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("paper-crawler")

PAPER_DIR = os.environ.get("PAPER_DIR", "/tmp/papers")
PAPER_PROXY = os.environ.get("PAPER_PROXY", "")

_ATOM = "{http://www.w3.org/2005/Atom}"


def _client() -> httpx.Client:
    kwargs = {"verify": False, "timeout": 30, "follow_redirects": True}
    if PAPER_PROXY:
        kwargs["proxy"] = PAPER_PROXY
    return httpx.Client(**kwargs)


@mcp.tool()
def search_arxiv(query: str, max_results: int = 10) -> list[dict]:
    """在 arXiv 搜索论文，返回标题/作者/摘要/年份/PDF 链接。"""
    url = "https://export.arxiv.org/api/query"
    params = {"search_query": f"all:{query}", "start": 0,
              "max_results": max_results, "sortBy": "relevance"}
    r = _client().get(url, params=params)
    r.raise_for_status()
    root = ET.fromstring(r.text)
    out = []
    for e in root.findall(f"{_ATOM}entry"):
        aid = (e.findtext(f"{_ATOM}id") or "").strip()
        out.append({
            "source": "arxiv",
            "arxiv_id": aid.split("/abs/")[-1] if "/abs/" in aid else aid,
            "title": (e.findtext(f"{_ATOM}title") or "").strip(),
            "authors": [a.findtext(f"{_ATOM}name") or ""
                        for a in e.findall(f"{_ATOM}author")],
            "summary": (e.findtext(f"{_ATOM}summary") or "").strip(),
            "published": (e.findtext(f"{_ATOM}published") or "").strip()[:10],
            "pdf_url": next((l.get("href") for l in e.findall(f"{_ATOM}link")
                             if l.get("type") == "application/pdf"), ""),
            "url": aid,
        })
    return out


@mcp.tool()
def search_semantic_scholar(query: str, max_results: int = 10) -> list[dict]:
    """在 Semantic Scholar 搜索，附带引用数、DOI、开放 PDF 链接。"""
    url = "https://api.semanticscholar.org/graph/v1/paper/search"
    fields = "title,authors,abstract,year,externalIds,citationCount,url,openAccessPdf"
    r = _client().get(url, params={"query": query, "limit": max_results,
                                   "fields": fields})
    if r.status_code == 429:
        return [{"error": "Semantic Scholar 限流（429），请稍后重试"}]
    r.raise_for_status()
    out = []
    for p in r.json().get("data", []):
        ext = p.get("externalIds") or {}
        pdf = p.get("openAccessPdf") or {}
        out.append({
            "source": "semantic_scholar",
            "title": p.get("title"),
            "authors": [a.get("name") for a in p.get("authors", [])],
            "summary": p.get("abstract"),
            "year": p.get("year"),
            "citation_count": p.get("citationCount"),
            "arxiv_id": ext.get("ArXiv"),
            "doi": ext.get("DOI"),
            "url": p.get("url"),
            "pdf_url": pdf.get("url"),
        })
    return out


@mcp.tool()
def search_scholar(query: str, max_results: int = 10) -> list[dict]:
    """在 Google Scholar 搜索（scholarly 库，可能被反爬拦截）。"""
    from scholarly import scholarly
    out = []
    try:
        search = scholarly.search_pubs(query)
        for i, pub in enumerate(search):
            if i >= max_results:
                break
            bib = pub.get("bib", {})
            out.append({
                "source": "google_scholar",
                "title": bib.get("title"),
                "authors": bib.get("author"),
                "summary": bib.get("abstract"),
                "year": bib.get("pub_year"),
                "url": pub.get("pub_url") or bib.get("url"),
                "citation_count": pub.get("num_citations"),
            })
    except Exception as e:  # noqa: BLE001
        return [{"error": f"Google Scholar 反爬拦截: {e}"}]
    return out


@mcp.tool()
def download_pdf(arxiv_id: str, output_dir: str = "") -> dict:
    """下载 arXiv 论文 PDF，返回本地路径与大小。"""
    dest_dir = output_dir or PAPER_DIR
    os.makedirs(dest_dir, exist_ok=True)
    url = f"https://arxiv.org/pdf/{arxiv_id}"
    path = os.path.join(dest_dir, f"{arxiv_id}.pdf")
    with _client().stream("GET", url) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_bytes(65536):
                f.write(chunk)
    return {"arxiv_id": arxiv_id, "path": path, "size": os.path.getsize(path)}


@mcp.tool()
def extract_text(pdf_path: str) -> dict:
    """抽取 PDF 全文（pypdf），返回页数、字符数与正文。"""
    from pypdf import PdfReader
    reader = PdfReader(pdf_path)
    text = "\n".join((page.extract_text() or "") for page in reader.pages)
    return {"path": pdf_path, "pages": len(reader.pages),
            "chars": len(text), "text": text}


if __name__ == "__main__":
    mcp.run()
