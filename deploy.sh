#!/bin/bash
# 装修获客 bot v1 一键部署
# 用法:
#   ./deploy.sh            启动客户页 (0.0.0.0:8765)
#   ./deploy.sh --owner    启动并打开老板面板
#   PORT=9000 ./deploy.sh  指定端口
#
# 轻量云部署(一次性):
#   1. 上传项目: rsync -av ~/projects/renovation-bot/ root@服务器IP:/opt/renovation-bot/
#   2. 服务器上放好密钥与配置:
#      - ~/.hermes/.env 含 DEEPSEEK_API_KEY(或用环境变量)
#      - cp config.example.json config.json 并填真实 webhook / 面板密码 / dashboard_url
#      - 换客户资料就编辑 kb_client.md
#   3. 开安全组放行端口 (8765)
#   4. 常驻可加 systemd(见文件尾部注释)
set -e
cd "$(dirname "$0")"
PORT="${PORT:-8765}"
OPEN_TARGET="/"

if [ "$1" = "--owner" ]; then OPEN_TARGET="/owner"; fi

echo "=== 1/4 检查依赖 ==="
if [ ! -d .venv ]; then
    echo "创建虚拟环境..."
    python3 -m venv .venv
fi
env -u PYTHONPATH .venv/bin/pip install -q flask 2>/dev/null || true

echo "=== 2/4 检查配置 ==="
if [ ! -f config.json ]; then
    echo "⚠ 缺 config.json → 先 cp config.example.json config.json 并填真实值"
    exit 1
fi
KB_FILE=$(python3 -c "import json;print(json.load(open('config.json')).get('kb_file','kb_client.md'))" 2>/dev/null || echo kb_client.md)
[ -f "$KB_FILE" ] || { echo "⚠ 缺知识库: $KB_FILE"; exit 1; }
echo "知识库: $KB_FILE ($(wc -c < "$KB_FILE") 字节)"

echo "=== 3/4 检查 DeepSeek key ==="
if [ -z "$DEEPSEEK_API_KEY" ] && [ ! -f "$HOME/.hermes/.env" ]; then
    echo "⚠ 缺 DEEPSEEK_API_KEY(放 ~/.hermes/.env 或用环境变量)"
    exit 1
fi

echo "=== 4/4 启动服务 (0.0.0.0:${PORT}) ==="
PORT="$PORT" env -u PYTHONPATH .venv/bin/python renov_bot_web.py &
sleep 2
LOCAL="http://127.0.0.1:${PORT}${OPEN_TARGET}"
if command -v open >/dev/null 2>&1; then open "$LOCAL"; else echo "✅ 已启动: $LOCAL"; fi
echo "   老板面板: http://127.0.0.1:${PORT}/owner"
wait

# ---- 轻量云常驻(systemd)示例 ----
# /etc/systemd/system/renovbot.service:
#   [Unit]
#   Description=Renovation Leadgen Bot
#   After=network.target
#   [Service]
#   WorkingDirectory=/opt/renovation-bot
#   ExecStart=/opt/renovation-bot/.venv/bin/python renov_bot_web.py
#   Restart=always
#   Environment=PORT=8765
#   [Install]
#   WantedBy=multi-user.target
#   # systemctl daemon-reload && systemctl enable --now renovbot
