#!/bin/bash
# 轻量云服务器一键配置(在服务器上跑一次,之后系统常驻)
# 前提:项目已 rsync 到 /opt/renovation-bot,配置已填好
# 用法: bash setup_server.sh
set -e
APP=/opt/renovation-bot
cd "$APP"

echo "=== 1/4 依赖(python3 + venv + flask) ==="
if command -v apt-get >/dev/null 2>&1; then PM="apt-get"
elif command -v yum >/dev/null 2>&1; then PM="yum"
else echo "✗ 不支持的包管理器(建议 Ubuntu/Debian 镜像)"; exit 1; fi
if ! command -v python3 >/dev/null 2>&1; then
    $PM update -y >/dev/null 2>&1 || true
    $PM install -y python3 python3-venv
fi
[ -d .venv ] || python3 -m venv .venv
env -u PYTHONPATH .venv/bin/pip install -q flask

echo "=== 2/4 配置检查 ==="
[ -f config.json ] || { echo "✗ 缺 config.json → cp config.example.json config.json 并填真实值"; exit 1; }
if [ -z "$DEEPSEEK_API_KEY" ] && [ ! -f "$HOME/.hermes/.env" ]; then
    echo "✗ 缺 DeepSeek key → 把 DEEPSEEK_API_KEY=xxx 写进 $HOME/.hermes/.env"
    exit 1
fi

echo "=== 3/4 注册 systemd 常驻 ==="
cat > /etc/systemd/system/renovbot.service <<'EOF'
[Unit]
Description=Renovation Leadgen Bot
After=network.target

[Service]
WorkingDirectory=/opt/renovation-bot
ExecStart=/opt/renovation-bot/.venv/bin/python renov_bot_web.py
Restart=always
Environment=PORT=8765

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now renovbot
sleep 2

echo "=== 4/4 自检 ==="
IP=$(curl -s ifconfig.me || echo "?")
systemctl is-active renovbot && echo "✅ 服务常驻中"
echo "客户页:  http://${IP}:8765/"
echo "老板面板: http://${IP}:8765/owner  (密码在 config.json)"
echo ""
echo "⚠ 别忘了在云控制台【安全组/防火墙】放行 TCP 8765 端口"
echo "⚠ 面板密码上线前务必改掉(demo1234 只是演示)"
