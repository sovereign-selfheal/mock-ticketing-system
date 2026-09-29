FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .

RUN useradd -u 1001 -r -g 0 -d /app -s /sbin/nologin appuser && \
    chown -R 1001:0 /app && \
    chmod -R g=u /app
USER 1001

EXPOSE 8080

ENV MCP_PORT=8080 \
    PYTHONUNBUFFERED=1

CMD ["python", "server.py"]
