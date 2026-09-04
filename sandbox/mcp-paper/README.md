# mcp-paper — 论文爬取 MCP

专门爬论文的 MCP server，覆盖三个数据源 + PDF 全文下载，全部基于现成库。

## 工具（MCP tools）

| 工具 | 数据源 / 库 | 说明 |
|---|---|---|
| `search_arxiv(query, max_results)` | arXiv / `arxiv` | 标题/作者/摘要/年份/PDF 链接 |
| `search_semantic_scholar(query, max_results)` | Semantic Scholar / `requests` | 附加引用数、DOI、开放 PDF |
| `search_scholar(query, max_results)` | Google Scholar / `scholarly` | 学术搜索（可能被反爬拦截） |
| `download_pdf(arxiv_id, output_dir)` | arXiv / `requests` | 下载 PDF 到指定目录 |
| `extract_text(pdf_path)` | `pypdf` | 抽取全文（页数/字符数/正文） |

## 网络与代理

国内直连 arXiv 常被 SSL 干扰，且本机 6789 代理对 `requests` 的 SSL 握手不兼容
（实测 `UNEXPECTED_EOF`）、但对 `httpx` 兼容——故网络层统一 httpx（`verify=False`）。

代理地址优先级：`PAPER_PROXY` 显式指定 > `HTTP(S)_PROXY` 环境变量。示例：

```bash
# 本机（代理在 127.0.0.1:6789）
PAPER_PROXY=http://127.0.0.1:6789 python server.py

# Docker（宿主代理，用宿主机网关 IP）
docker run -i -e PAPER_PROXY=http://host.docker.internal:6789 mcp-paper:latest
```

## 本地运行（stdio）

```bash
pip install -r requirements.txt
python server.py          # 以 MCP stdio 协议启动
```

## Docker 隔离运行

```bash
docker build -t mcp-paper:latest .
docker run -i --name rag_mcp_paper mcp-paper:latest
```

## 挂到项目 MCP 沙盒

「MCP 沙盒」页注册：`image=mcp-paper:latest`、`transport=stdio`、`command` 留空
（Dockerfile 的 CMD 会自动启动 server.py）。stdio 型 MCP 无监听端口。

## 典型链路

```
search_arxiv("visual grounding") → 命中列表
  → download_pdf(arxiv_id)       → 本地 PDF
  → extract_text(pdf_path)       → 全文 → 喂给项目 RAG 入库
```
