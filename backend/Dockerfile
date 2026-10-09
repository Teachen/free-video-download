# 后端镜像 —— 微信云托管
#
# 构建上下文 = backend/ 目录（微信云托管「代码目录」设为 backend）
# 本地验证：
#   cd backend && docker build -t saveany-backend . && docker run -p 80:80 saveany-backend
#
# 说明：
#   - ffmpeg：yt-dlp 合并音视频流必需（高清下载）
#   - curl_cffi：TLS 指纹伪装，破解 B 站 412 风控
#   - 监听端口 80：与 container.config.json 的 containerPort 一致

FROM python:3.12-slim

# ffmpeg 合并音视频；ca-certificates 供 curl_cffi / HTTPS 使用
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先装依赖，利用镜像层缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -U pip \
    && pip install --no-cache-dir -r requirements.txt

# 再拷贝代码
COPY . .

# 运行时目录（云托管容器为临时文件系统，数据库请用内置 MySQL）
RUN mkdir -p /app/downloads /app/data /app/logs

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=80

EXPOSE 80

CMD ["python", "main.py"]
