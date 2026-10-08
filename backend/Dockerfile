# 后端服务镜像
# 构建：docker build -t saveany-backend ./backend
# 说明：yt-dlp 需要 ffmpeg 合并音视频；curl_cffi 用于 TLS 指纹伪装

FROM python:3.12-slim

# ffmpeg：音视频合并必需
# curl-cffi 依赖 libcurl/证书，slim 镜像需补齐 ca-certificates
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先装依赖，利用 Docker 层缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -U pip \
    && pip install --no-cache-dir -r requirements.txt

# 再拷代码
COPY . .

# 运行时数据目录（可挂载卷持久化）
RUN mkdir -p /app/downloads /app/data /app/logs

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000

EXPOSE 8000

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

CMD ["python", "main.py"]
