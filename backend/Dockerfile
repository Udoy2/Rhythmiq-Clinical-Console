# CardioSense backend image. Build context is the repository root (see docker-compose.yml):
# the backend needs ml/ (inference module + model), sender/ (demo replay) and data/records/.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# CPU-only torch first, so the generic requirement below does not pull the CUDA build.
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch \
 && pip install -r backend/requirements.txt

COPY backend/cardiosense backend/cardiosense
COPY ml/cardiosense_beats.py ml/cardiosense_beats.py
COPY ml/models ml/models
COPY sender sender
COPY data/records data/records

WORKDIR /app/backend
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "cardiosense.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
