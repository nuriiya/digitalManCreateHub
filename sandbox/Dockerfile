FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir mcp python-pptx httpx
COPY server.py .
CMD ["python", "server.py"]