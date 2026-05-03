# Lightweight single-stage image for ITSM demo (OpenShift-friendly arbitrary UID + group 0)
FROM python:3.12-slim-bookworm

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Writable paths for random OpenShift UIDs (supplemental GID 0)
RUN mkdir -p /data \
    && chgrp -R 0 /app /data \
    && chmod -R g=u /app /data

ENV PYTHONUNBUFFERED=1 \
    ITSM_DATABASE=/data/itsm.db

USER 1001

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
