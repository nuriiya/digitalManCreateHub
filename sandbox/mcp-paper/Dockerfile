# Paper crawler MCP — 论文爬取（stdio 传输，无需暴露端口）
# 构建:  docker build -t mcp-paper:latest .
# 注册:  image=mcp-paper:latest, transport=stdio, command=""（默认 CMD 启动 server.py）
# 连接:  MCP client 以 stdio 方式连接容器（docker exec / docker run -i）

FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .

# 论文 PDF 落地目录（可挂 volume 持久化）
ENV PAPER_DIR=/data/papers

CMD ["python", "server.py"]
