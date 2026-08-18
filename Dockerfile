FROM node:22-bookworm-slim AS frontend-build
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim-bookworm AS python-build
WORKDIR /build
RUN python -m pip install --no-cache-dir --upgrade pip
COPY pyproject.toml README.md ./
# 의존성 레이어는 패키지 메타데이터만으로 만들고, 실제 소스는 아래에서 복사한다.
# 따라서 일반 코드 수정은 무거운 voice 의존성 재설치를 무효화하지 않는다.
COPY src/smart_desk/__init__.py ./src/smart_desk/__init__.py
RUN python -m pip install --no-cache-dir --prefix=/install ".[voice]"
COPY src/ ./src/

FROM python:3.11-slim-bookworm AS runtime-base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends libasound2 libglib2.0-0 libgl1 libportaudio2 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 smartdesk \
    && useradd --uid 10001 --gid 10001 --create-home --home-dir /app smartdesk
COPY --from=python-build /install /usr/local
COPY src/ ./src/
FROM runtime-base AS main-runtime
COPY --from=frontend-build /frontend/dist ./frontend/dist
COPY assets/voice/ ./assets/voice/
USER smartdesk
EXPOSE 9090
CMD ["uvicorn", "smart_desk.main:app", "--host", "0.0.0.0", "--port", "9090", "--workers", "1"]

FROM runtime-base AS vision-runtime
COPY assets/vision/models/ ./assets/vision/models/
ENV SMART_DESK_FACE__DETECTOR_MODEL_PATH=/app/assets/vision/models/face_detection_yunet_2023mar.onnx \
    SMART_DESK_FACE__EMBEDDING_MODEL_PATH=/app/assets/vision/models/face_recognition_sface_2021dec.onnx \
    SMART_DESK_VISION__LOWER_POSE_MODEL_PATH=/app/assets/vision/models/yolo26n-pose.onnx
USER smartdesk
EXPOSE 9091
CMD ["uvicorn", "smart_desk.vision_main:create_vision_application", "--factory", "--host", "0.0.0.0", "--port", "9091", "--workers", "1"]
