# 前端镜像 —— 微信云托管
#
# 构建上下文 = frontend/ 目录（微信云托管「代码目录」设为 frontend）
# 本地验证：
#   cd frontend && docker build -t saveany-frontend . && docker run -p 8080:80 saveany-frontend
#
# 设计要点：
#   1. 多阶段构建：node 编译 → nginx 托管静态资源，最终镜像不含 node_modules
#   2. 前端所有请求使用相对路径 /api，由 nginx 反向代理到后端 → 同源，无 CORS
#   3. 后端地址通过环境变量 BACKEND_URL 注入（nginx 启动时模板替换），无需重新构建

# ---------- 阶段 1：编译 Vue ----------
FROM node:20-alpine AS build

WORKDIR /app

COPY package*.json ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund

COPY . .
RUN npm run build

# ---------- 阶段 2：Nginx 托管 ----------
FROM nginx:alpine

# 静态资源
COPY --from=build /app/dist /usr/share/nginx/html

# 配置模板：容器启动时由 envsubst 替换 ${BACKEND_URL}
COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template

# 只替换 BACKEND_URL，避免误伤 nginx 内置变量（$uri / $host 等）
ENV NGINX_ENVSUBST_FILTER=BACKEND_URL
# 部署时在微信云托管控制台覆盖为真实后端地址
ENV BACKEND_URL=https://your-backend-domain.sh.run.tcloudbase.com

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
