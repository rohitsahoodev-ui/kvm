#!/bin/bash

set -e

echo "==============================="
echo " RDX KVM BOT INSTALLER "
echo "==============================="

# Root check
if [ "$EUID" -ne 0 ]; then
  echo "❌ Please run as root"
  exit 1
fi

echo "[1/8] Updating system..."
apt update -y

echo "[2/8] Installing system dependencies..."
apt install -y \
  python3 python3-pip python3-venv \
  qemu-kvm libvirt-daemon-system virtinst bridge-utils \
  curl sudo git

echo "[3/8] Enabling libvirt..."
systemctl enable --now libvirtd

echo "[4/8] Checking KVM support..."
KVM_CHECK=$(egrep -c '(vmx|svm)' /proc/cpuinfo || true)
if [ "$KVM_CHECK" -eq 0 ]; then
  echo "❌ KVM NOT SUPPORTED on this server"
  exit 1
fi
echo "✅ KVM supported"

echo "[5/8] Creating bot directory..."
mkdir -p /opt/rdx-kvm
cd /opt/rdx-kvm

echo "[6/8] Creating Python virtual environment..."
python3 -m venv venv
source venv/bin/activate

echo "[7/8] Installing Python requirements..."
pip install --upgrade pip
pip install discord.py python-dotenv psutil

echo "[8/8] Creating .env file..."
cat > .env <<EOF
# Discord Bot Token
DISCORD_TOKEN=PUT_YOUR_DISCORD_BOT_TOKEN

# Main Admin Discord ID
MAIN_ADMIN_ID=123456789012345678

# Branding
BRAND=RDX KVM Hosting

# Database
DB_FILE=kvm_vps.db
EOF

echo "==============================="
echo " ✅ INSTALLATION COMPLETE "
echo "==============================="
echo ""
echo "👉 Next steps:"
echo "1. Upload rdx-kvm-bot.py into /opt/rdx-kvm"
echo "2. Edit .env file:"
echo "   nano /opt/rdx-kvm/.env"
echo "3. Run bot:"
echo "   cd /opt/rdx-kvm && source venv/bin/activate && python3 rdx-kvm-bot.py"
echo ""
echo "🔥 Powered by RDX KVM Hosting"
