FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

# /data is where the gitops Deployment (components/ticketing-system) mounts the PVC.
# group 0 + g=u: readable/writable by the arbitrary UID OpenShift assigns at runtime.
RUN useradd -u 1001 -r -g 0 -d /app -s /sbin/nologin appuser && \
    chown -R 1001:0 /app && \
    chmod -R g=u /app && \
    mkdir -p /data && chown 1001:0 /data && chmod g=u /data
USER 1001

EXPOSE 8080

ENV TICKETING_DB_PATH=/data/incidents.db \
    PYTHONUNBUFFERED=1

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
