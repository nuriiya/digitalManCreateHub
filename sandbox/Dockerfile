FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1
RUN pip install --no-cache-dir mcp python-docx httpx
COPY server.py .
CMD ["python","server.py"]