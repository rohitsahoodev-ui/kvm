#!/bin/bash
set -e

echo "==============================="
echo "  RDX KVM BOT FULL INSTALLER"
echo "==============================="

# Root check
if [ "$EUID" -ne 0 ]; then
  echo "❌ Run as root"
  exit 1
fi

echo "[1/9] Updating system..."
apt update -y

echo "[2/9] Installing system packages..."
apt install -y \
 python3 python3-pip python3-venv \
 qemu-kvm libvirt-daemon-system virtinst bridge-utils \
 curl git sudo

echo "[3/9] Enabling libvirt..."
systemctl enable --now libvirtd

echo "[4/9] Checking KVM support..."
if [ "$(egrep -c '(vmx|svm)' /proc/cpuinfo)" -eq 0 ]; then
  echo "❌ KVM not supported"
  exit 1
fi
echo "✅ KVM supported"

echo "[5/9] Creating directories..."
mkdir -p /opt/rdx-kvm
cd /opt/rdx-kvm

echo "[6/9] Creating virtual environment..."
python3 -m venv venv
source venv/bin/activate

echo "[7/9] Installing Python dependencies..."
pip install --upgrade pip
pip install discord.py python-dotenv psutil

echo "[8/9] Creating .env file..."
cat > .env <<EOF
DISCORD_TOKEN=PUT_YOUR_BOT_TOKEN
MAIN_ADMIN_ID=123456789012345678
BRAND=RDX KVM Hosting
DB_FILE=kvm_vps.db
EOF

echo "[9/9] Installing systemd service..."
cat > /etc/systemd/system/rdx-kvm-bot.service <<EOF
[Unit]
Description=RDX KVM Hosting Discord Bot
After=network.target libvirtd.service
Requires=libvirtd.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/rdx-kvm
Environment="PATH=/opt/rdx-kvm/venv/bin"
EnvironmentFile=/opt/rdx-kvm/.env
ExecStart=/opt/rdx-kvm/venv/bin/python /opt/rdx-kvm/rdx-kvm-bot.py
Restart=always
RestartSec=10
KillSignal=SIGINT
TimeoutStopSec=30
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable rdx-kvm-bot.service

echo "==============================="
echo " ✅ INSTALL COMPLETE"
echo "==============================="
echo ""
echo "👉 Next:"
echo "1. Upload rdx-kvm-bot.py to /opt/rdx-kvm"
echo "2. Edit .env: nano /opt/rdx-kvm/.env"
echo "3. Start bot:"
echo "   systemctl start rdx-kvm-bot"
echo ""
echo "Logs:"
echo " journalctl -u rdx-kvm-bot -f"
echo ""
echo "🔥 Powered by RDX KVM Hosting"
