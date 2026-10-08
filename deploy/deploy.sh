#!/usr/bin/env bash
# ============================================================
# SaveAny 后端 —— 云服务器一键部署脚本（Ubuntu / Debian）
#
# 用法（在服务器上）：
#   git clone <你的仓库> /opt/saveany && cd /opt/saveany
#   cp deploy/.env.deploy.example .env.deploy
#   vim .env.deploy          # 填 DEEPSEEK_API_KEY / JWT_SECRET / BILI_COOKIE
#   sudo bash deploy/deploy.sh
#
# 脚本做四件事：
#   1. 安装系统依赖（python3 / ffmpeg）
#   2. 创建虚拟环境并安装 Python 依赖
#   3. 注册 systemd 服务并启动
#   4. 自检：/api/health + B 站解析
# ============================================================
set -euo pipefail

APP_DIR="/opt/saveany"
BACKEND_DIR="$APP_DIR/backend"
VENV_DIR="$APP_DIR/venv"
SERVICE_NAME="saveany-backend"
RUN_USER="${SUDO_USER:-www-data}"

green() { printf "\033[32m%s\033[0m\n" "$1"; }
red()   { printf "\033[31m%s\033[0m\n" "$1"; }
info()  { printf "\033[36m%s\033[0m\n" "$1"; }

info "==> 1/5 安装系统依赖"
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip ffmpeg curl git

info "==> 2/5 创建虚拟环境并安装依赖"
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet -r "$BACKEND_DIR/requirements.txt"

info "==> 3/5 校验环境变量文件"
if [ ! -f "$APP_DIR/.env.deploy" ]; then
    red "缺少 $APP_DIR/.env.deploy"
    red "请先：cp deploy/.env.deploy.example .env.deploy 并填写真实值"
    exit 1
fi
if grep -q "CHANGE_ME\|sk-你的真实key" "$APP_DIR/.env.deploy"; then
    red "警告：.env.deploy 中仍有占位符未替换（JWT_SECRET / DEEPSEEK_API_KEY）"
    read -rp "仍要继续？(y/N) " ans
    [ "$ans" = "y" ] || exit 1
fi

info "==> 4/5 注册 systemd 服务"
mkdir -p "$BACKEND_DIR/downloads" "$BACKEND_DIR/data" "$BACKEND_DIR/logs"
chown -R "$RUN_USER":"$RUN_USER" "$BACKEND_DIR/downloads" "$BACKEND_DIR/data" "$BACKEND_DIR/logs"

sed -e "s#/opt/saveany#$APP_DIR#g" \
    -e "s#User=www-data#User=$RUN_USER#" \
    -e "s#Group=www-data#Group=$RUN_USER#" \
    "$APP_DIR/deploy/saveany-backend.service" > "/etc/systemd/system/$SERVICE_NAME.service"

systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME"
sleep 3

info "==> 5/5 自检"
if systemctl is-active --quiet "$SERVICE_NAME"; then
    green "✓ 服务已启动"
else
    red "✗ 服务启动失败，查看日志：sudo journalctl -u $SERVICE_NAME -n 50"
    exit 1
fi

PORT="$(grep -E '^PORT=' "$APP_DIR/.env.deploy" | cut -d= -f2 | tr -d '[:space:]')"
PORT="${PORT:-8000}"

echo -n "  健康检查 /api/health ... "
if curl -fsS "http://127.0.0.1:$PORT/api/health" >/dev/null; then
    green "OK"
else
    red "失败"
fi

echo -n "  B 站 Cookie 是否注入 ... "
if grep -qE '^BILI_COOKIE=.+' "$APP_DIR/.env.deploy"; then
    green "已配置"
else
    red "未配置（B 站解析会失败，编辑 .env.deploy 填入 BILI_COOKIE 后 systemctl restart）"
fi

green ""
green "部署完成！"
echo "  日志：sudo journalctl -u $SERVICE_NAME -f"
echo "  重启：sudo systemctl restart $SERVICE_NAME"
echo "  下一步：配置 Nginx 反代（见 deploy/nginx-saveany.conf）"
