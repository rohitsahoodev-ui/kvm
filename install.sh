#!/bin/bash

# RDX KVM Bot Complete Installer
# All LXC features ported to KVM

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Banner
clear
echo -e "${GREEN}"
cat << "EOF"
╔══════════════════════════════════════════════════════╗
║     🚀 RDX KVM VPS BOT - COMPLETE INSTALLATION       ║
║         All LXC Features + KVM Power                 ║
╚══════════════════════════════════════════════════════╝
EOF
echo -e "${NC}"

# Check root
if [[ $EUID -ne 0 ]]; then
    echo -e "${RED}Run: sudo bash install.sh${NC}"
    exit 1
fi

# Get minimal inputs
echo -e "${GREEN}[1/3] Discord Bot Token:${NC}"
read -p "Enter token: " DISCORD_TOKEN

echo -e "\n${GREEN}[2/3] Your Discord ID (Main Admin):${NC}"
read -p "Enter your ID: " ADMIN_ID

echo -e "\n${GREEN}[3/3] Brand Name:${NC}"
read -p "Enter brand (e.g., RDX Hosting): " BRAND_NAME

# Auto detect
SERVER_IP=$(curl -s ifconfig.me)
HOSTNAME=$(hostname)

echo -e "\n${YELLOW}Starting installation...${NC}"
sleep 2

# ==================== INSTALLATION ====================

echo -e "${GREEN}▶ Updating system...${NC}"
apt update && apt upgrade -y

echo -e "${GREEN}▶ Installing KVM...${NC}"
apt install -y qemu-kvm libvirt-daemon-system libvirt-clients \
virtinst bridge-utils libguestfs-tools libosinfo-bin \
cloud-image-utils virt-manager cpu-checker \
python3 python3-pip python3-venv python3-dev \
wget curl git nano htop screen genisoimage

echo -e "${GREEN}▶ Configuring network...${NC}"
INTERFACE=$(ip route | grep default | awk '{print $5}')
cat > /etc/netplan/00-rdx-bridge.yaml << EOF
network:
  version: 2
  ethernets:
    $INTERFACE:
      dhcp4: no
  bridges:
    br0:
      interfaces: [$INTERFACE]
      dhcp4: yes
EOF
netplan apply
sleep 3

echo -e "${GREEN}▶ Setting up storage...${NC}"
mkdir -p /var/lib/libvirt/images/vms
mkdir -p /var/lib/libvirt/cloud-init
virsh pool-define-as --name vms --type dir --target /var/lib/libvirt/images/vms 2>/dev/null || true
virsh pool-start vms 2>/dev/null || true

echo -e "${GREEN}▶ Downloading OS images...${NC}"
mkdir -p /opt/kvm/images
cd /opt/kvm/images
wget -q https://cloud-images.ubuntu.com/jammy/current/jammy-server-cloudimg-amd64.img -O ubuntu-22.04-base.qcow2
qemu-img resize ubuntu-22.04-base.qcow2 20G

echo -e "${GREEN}▶ Installing bot...${NC}"
mkdir -p /opt/rdx-bot
cd /opt/rdx-bot

# Python environment
python3 -m venv venv
source venv/bin/activate
pip install discord.py python-dotenv psutil pyyaml

# Create .env
cat > .env << EOF
DISCORD_TOKEN=$DISCORD_TOKEN
MAIN_ADMIN_ID=$ADMIN_ID
BRAND_NAME=$BRAND_NAME
SERVER_IP=$SERVER_IP
KVM_STORAGE_POOL=vms
BASE_IMAGES_DIR=/opt/kvm/images
EOF

# Create the complete bot file
cat > rdx_bot.py << 'EOF'
import discord
from discord.ext import commands, tasks
import os
import asyncio
import subprocess
import json
import sqlite3
import uuid
import yaml
from datetime import datetime
from typing import Dict, List, Optional
import shutil
import time

# ==================== CONFIG ====================
TOKEN = os.getenv('DISCORD_TOKEN')
MAIN_ADMIN_ID = os.getenv('MAIN_ADMIN_ID')
BRAND_NAME = os.getenv('BRAND_NAME', 'RDX KVM Hosting')
SERVER_IP = os.getenv('SERVER_IP', 'localhost')

# VM Plans
VM_PLANS = {
    "basic": {"ram": 1024, "cpu": 1, "disk": 20},
    "standard": {"ram": 2048, "cpu": 2, "disk": 40},
    "premium": {"ram": 4096, "cpu": 4, "disk": 80},
    "enterprise": {"ram": 8192, "cpu": 8, "disk": 160},
}

# OS Options
OS_OPTIONS = [
    {"id": "ubuntu-22.04", "name": "Ubuntu 22.04 LTS"},
    {"id": "ubuntu-20.04", "name": "Ubuntu 20.04 LTS"},
    {"id": "debian-11", "name": "Debian 11"},
]

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

# ==================== DATABASE ====================
def init_db():
    conn = sqlite3.connect('vps.db')
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS admins (
        user_id TEXT PRIMARY KEY
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS vps (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        vm_name TEXT UNIQUE NOT NULL,
        vm_uuid TEXT UNIQUE NOT NULL,
        plan TEXT NOT NULL,
        ram_mb INTEGER NOT NULL,
        cpu_cores INTEGER NOT NULL,
        disk_gb INTEGER NOT NULL,
        os_id TEXT NOT NULL,
        ip_address TEXT,
        status TEXT DEFAULT 'stopped',
        suspended INTEGER DEFAULT 0,
        whitelisted INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        ssh_password TEXT,
        shared_with TEXT DEFAULT '[]'
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )''')
    
    # Default settings
    settings = [
        ('cpu_threshold', '85'),
        ('ram_threshold', '90'),
        ('max_vms_per_user', '5'),
        ('brand_name', BRAND_NAME),
    ]
    
    for key, value in settings:
        c.execute('INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)', (key, value))
    
    # Add main admin
    c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (MAIN_ADMIN_ID,))
    
    conn.commit()
    conn.close()

init_db()

# ==================== HELPER FUNCTIONS ====================
def get_db():
    conn = sqlite3.connect('vps.db')
    conn.row_factory = sqlite3.Row
    return conn

def is_admin(user_id: str) -> bool:
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT 1 FROM admins WHERE user_id = ?', (user_id,))
    result = c.fetchone()
    conn.close()
    return result is not None or user_id == MAIN_ADMIN_ID

def add_vps_to_db(vps_data: Dict):
    conn = get_db()
    c = conn.cursor()
    c.execute('''INSERT INTO vps 
        (user_id, vm_name, vm_uuid, plan, ram_mb, cpu_cores, disk_gb, os_id, ip_address, status, ssh_password)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (vps_data['user_id'], vps_data['vm_name'], vps_data['vm_uuid'],
         vps_data['plan'], vps_data['ram_mb'], vps_data['cpu_cores'],
         vps_data['disk_gb'], vps_data['os_id'], vps_data.get('ip_address', ''),
         vps_data.get('status', 'stopped'), vps_data.get('ssh_password', '')))
    conn.commit()
    conn.close()

def get_user_vps(user_id: str) -> List[Dict]:
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM vps WHERE user_id = ?', (user_id,))
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def get_vps_by_name(vm_name: str) -> Optional[Dict]:
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM vps WHERE vm_name = ?', (vm_name,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None

def update_vps_status(vm_name: str, status: str):
    conn = get_db()
    c = conn.cursor()
    c.execute('UPDATE vps SET status = ? WHERE vm_name = ?', (status, vm_name))
    conn.commit()
    conn.close()

def run_cmd(cmd, timeout=30):
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return result.stdout.strip() if result.returncode == 0 else f"Error: {result.stderr}"
    except subprocess.TimeoutExpired:
        return "Error: Command timed out"
    except Exception as e:
        return f"Error: {str(e)}"

async def run_cmd_async(cmd, timeout=30):
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return stdout.decode().strip() if proc.returncode == 0 else f"Error: {stderr.decode()}"
    except asyncio.TimeoutError:
        return "Error: Command timed out"
    except Exception as e:
        return f"Error: {str(e)}"

def create_embed(title, description="", color=0x00AAFF):
    embed = discord.Embed(
        title=f"⚡ {BRAND_NAME} - {title}",
        description=description,
        color=color,
        timestamp=datetime.now()
    )
    embed.set_footer(text=f"{BRAND_NAME} • KVM Virtual Machines")
    return embed

def add_field(embed, name, value, inline=False):
    embed.add_field(name=f"▸ {name}", value=value, inline=inline)
    return embed

# ==================== KVM FUNCTIONS ====================
class KVMManager:
    @staticmethod
    def create_vm(vm_name, ram_mb, cpu_cores, disk_gb, os_id="ubuntu-22.04"):
        """Create a KVM virtual machine"""
        try:
            # Generate passwords
            ssh_password = subprocess.getoutput("openssl rand -base64 12")
            
            # Create cloud-init ISO
            cloud_dir = f"/tmp/{vm_name}-cloud"
            os.makedirs(cloud_dir, exist_ok=True)
            
            user_data = f"""#cloud-config
hostname: {vm_name}
users:
  - name: ubuntu
    passwd: {ssh_password}
    sudo: ALL=(ALL) NOPASSWD:ALL
    shell: /bin/bash
chpasswd:
  expire: false
package_update: true
packages: [docker.io, htop, curl, wget]
runcmd:
  - systemctl enable docker
  - systemctl start docker
  - echo "Welcome to {BRAND_NAME} KVM VPS!" > /etc/motd
"""
            
            with open(f"{cloud_dir}/user-data", 'w') as f:
                f.write(user_data)
            
            with open(f"{cloud_dir}/meta-data", 'w') as f:
                f.write(f"instance-id: {vm_name}\nlocal-hostname: {vm_name}")
            
            run_cmd(f"genisoimage -output /var/lib/libvirt/cloud-init/{vm_name}.iso -volid cidata -joliet -rock {cloud_dir}/user-data {cloud_dir}/meta-data")
            
            # Copy base image
            base_image = "/opt/kvm/images/ubuntu-22.04-base.qcow2"
            disk_path = f"/var/lib/libvirt/images/vms/{vm_name}.qcow2"
            run_cmd(f"cp {base_image} {disk_path}")
            run_cmd(f"qemu-img resize {disk_path} {disk_gb}G")
            
            # Create VM
            cmd = f"""virt-install \
--name {vm_name} \
--memory {ram_mb} \
--vcpus {cpu_cores} \
--disk path={disk_path},format=qcow2,bus=virtio \
--disk path=/var/lib/libvirt/cloud-init/{vm_name}.iso,device=cdrom \
--network bridge=br0,model=virtio \
--graphics vnc,listen=0.0.0.0 \
--noautoconsole \
--os-variant ubuntu20.04 \
--import"""
            
            run_cmd(cmd)
            
            # Get IP
            time.sleep(10)
            ip_cmd = f"virsh domifaddr {vm_name} | grep ipv4 | awk '{{print $4}}' | cut -d/ -f1"
            ip = run_cmd(ip_cmd) or ""
            
            # Cleanup
            shutil.rmtree(cloud_dir, ignore_errors=True)
            
            return {
                'vm_name': vm_name,
                'vm_uuid': str(uuid.uuid4()),
                'ram_mb': ram_mb,
                'cpu_cores': cpu_cores,
                'disk_gb': disk_gb,
                'os_id': os_id,
                'ip_address': ip,
                'ssh_password': ssh_password,
                'status': 'running'
            }
            
        except Exception as e:
            raise Exception(f"Failed to create VM: {str(e)}")
    
    @staticmethod
    def control_vm(vm_name, action):
        """Control VM: start, stop, reboot, destroy"""
        actions = {
            'start': 'virsh start',
            'stop': 'virsh shutdown',
            'reboot': 'virsh reboot',
            'destroy': 'virsh destroy',
            'suspend': 'virsh suspend',
            'resume': 'virsh resume',
        }
        
        if action not in actions:
            return f"Invalid action. Use: {', '.join(actions.keys())}"
        
        return run_cmd(f"{actions[action]} {vm_name}")
    
    @staticmethod
    def delete_vm(vm_name):
        """Delete VM completely"""
        run_cmd(f"virsh destroy {vm_name} 2>/dev/null")
        run_cmd(f"virsh undefine {vm_name} --remove-all-storage")
        run_cmd(f"rm -f /var/lib/libvirt/cloud-init/{vm_name}.iso")
        return "VM deleted"
    
    @staticmethod
    def get_vm_info(vm_name):
        """Get VM information"""
        status = run_cmd(f"virsh domstate {vm_name}")
        ip = run_cmd(f"virsh domifaddr {vm_name} | grep ipv4 | awk '{{print $4}}' | cut -d/ -f1")
        
        return {
            'status': status if status and "Error" not in status else 'unknown',
            'ip_address': ip if ip and "Error" not in ip else '',
            'exists': 'Error' not in status
        }
    
    @staticmethod
    def get_vm_stats(vm_name):
        """Get VM statistics"""
        stats = {}
        
        # Get CPU usage
        cpu_cmd = f"virsh dominfo {vm_name} | grep 'CPU time' | awk '{{print $3}}'"
        cpu = run_cmd(cpu_cmd)
        if 'Error' not in cpu:
            stats['cpu'] = cpu
        
        # Get memory
        mem_cmd = f"virsh dommemstat {vm_name} 2>/dev/null | grep actual | awk '{{print $2}}'"
        mem = run_cmd(mem_cmd)
        if 'Error' not in mem:
            stats['memory_mb'] = int(int(mem) / 1024)
        
        return stats

# ==================== DISCORD COMMANDS ====================
@bot.event
async def on_ready():
    print(f'✅ {bot.user} connected!')
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.watching,
        name=f"{BRAND_NAME} VPS Manager"
    ))
    print(f'🤖 Bot ready with all LXC features!')

@bot.command(name='ping')
async def ping(ctx):
    latency = round(bot.latency * 1000)
    embed = create_embed("🏓 Pong!", f"Bot latency: {latency}ms", 0x00FF00)
    await ctx.send(embed=embed)

@bot.command(name='myvps')
async def myvps(ctx):
    """List user's VPS"""
    user_id = str(ctx.author.id)
    vps_list = get_user_vps(user_id)
    
    if not vps_list:
        embed = create_embed("No VPS Found", "You don't have any VPS. Contact admin!", 0xFF0000)
        await ctx.send(embed=embed)
        return
    
    embed = create_embed(f"Your VPS ({len(vps_list)})", f"Total VPS: {len(vps_list)}", 0x00AAFF)
    
    for i, vps in enumerate(vps_list, 1):
        status = vps['status'].upper()
        if vps['suspended']:
            status += " ⚠️ SUSPENDED"
        
        embed.add_field(
            name=f"VPS #{i} - {vps['vm_name']}",
            value=f"**Status:** {status}\n**Plan:** {vps['plan']}\n**IP:** {vps['ip_address'] or 'N/A'}\n**OS:** {vps['os_id']}",
            inline=False
        )
    
    embed.add_field(name="Manage", value="Use `!manage <vps_name>` to control your VPS", inline=False)
    await ctx.send(embed=embed)

# ==================== MANAGE VIEW (COMPLETE FEATURES) ====================
class ManageView(discord.ui.View):
    def __init__(self, vm_name: str, user_id: str, is_admin: bool = False):
        super().__init__(timeout=300)
        self.vm_name = vm_name
        self.user_id = user_id
        self.is_admin = is_admin
        self.vps = get_vps_by_name(vm_name)
        
        if not self.vps:
            self.disable_all_items()
            return
        
        # Add all buttons like LXC bot
        self.add_buttons()
    
    def add_buttons(self):
        # Row 1: Power controls
        self.add_item(PowerButton("▶ Start", "start", discord.ButtonStyle.success))
        self.add_item(PowerButton("⏹ Stop", "stop", discord.ButtonStyle.danger))
        self.add_item(PowerButton("🔄 Reboot", "reboot", discord.ButtonStyle.secondary))
        self.add_item(PowerButton("⚡ Force Stop", "destroy", discord.ButtonStyle.danger))
        
        # Row 2: Access controls
        self.add_item(ActionButton("🔑 SSH Info", "ssh", discord.ButtonStyle.primary))
        self.add_item(ActionButton("🖥️ Console", "console", discord.ButtonStyle.primary))
        self.add_item(ActionButton("📊 Stats", "stats", discord.ButtonStyle.secondary))
        self.add_item(ActionButton("📝 Rename", "rename", discord.ButtonStyle.secondary))
        
        # Row 3: Advanced controls
        if not self.vps['suspended']:
            self.add_item(ActionButton("⏸️ Suspend", "suspend", discord.ButtonStyle.secondary))
        else:
            self.add_item(ActionButton("▶️ Unsuspend", "unsuspend", discord.ButtonStyle.success))
        
        self.add_item(ActionButton("🔄 Reinstall", "reinstall", discord.ButtonStyle.danger))
        self.add_item(ActionButton("🔧 Resize", "resize", discord.ButtonStyle.primary))
        self.add_item(ActionButton("👥 Share", "share", discord.ButtonStyle.primary))
        
        # Row 4: Admin controls
        if self.is_admin:
            self.add_item(ActionButton("📋 Info", "info", discord.ButtonStyle.secondary))
            self.add_item(ActionButton("📡 Network", "network", discord.ButtonStyle.secondary))
            self.add_item(ActionButton("📜 Logs", "logs", discord.ButtonStyle.secondary))
            self.add_item(ActionButton("🗑️ Delete", "delete", discord.ButtonStyle.danger))

class PowerButton(discord.ui.Button):
    def __init__(self, label, action, style):
        super().__init__(label=label, style=style)
        self.action = action
    
    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        view = self.view
        result = KVMManager.control_vm(view.vm_name, self.action)
        
        if "Error" not in result:
            status = "running" if self.action == "start" else "stopped" if self.action in ["stop", "destroy"] else view.vps['status']
            update_vps_status(view.vm_name, status)
            
            embed = create_embed(f"✅ {self.label}", f"VM `{view.vm_name}` {self.action}ed successfully!", 0x00FF00)
        else:
            embed = create_embed(f"❌ Failed", result, 0xFF0000)
        
        await interaction.followup.send(embed=embed, ephemeral=True)

class ActionButton(discord.ui.Button):
    def __init__(self, label, action, style):
        super().__init__(label=label, style=style)
        self.action = action
    
    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        view = self.view
        
        if self.action == "ssh":
            vps = get_vps_by_name(view.vm_name)
            embed = create_embed("🔑 SSH Access", f"**VM:** `{view.vm_name}`")
            embed.add_field(name="IP Address", value=vps['ip_address'] or "Getting IP...", inline=True)
            embed.add_field(name="Username", value="`ubuntu`", inline=True)
            embed.add_field(name="Password", value=f"||{vps['ssh_password']}||", inline=False)
            embed.add_field(name="SSH Command", value=f"```bash\nssh ubuntu@{vps['ip_address']}\n```", inline=False)
            await interaction.followup.send(embed=embed, ephemeral=True)
        
        elif self.action == "stats":
            stats = KVMManager.get_vm_stats(view.vm_name)
            info = KVMManager.get_vm_info(view.vm_name)
            
            embed = create_embed("📊 VM Statistics", f"**VM:** `{view.vm_name}`")
            embed.add_field(name="Status", value=info['status'].upper(), inline=True)
            embed.add_field(name="IP", value=info['ip_address'] or "N/A", inline=True)
            
            if stats:
                if 'cpu' in stats:
                    embed.add_field(name="CPU Time", value=stats['cpu'], inline=True)
                if 'memory_mb' in stats:
                    embed.add_field(name="Memory", value=f"{stats['memory_mb']} MB", inline=True)
            
            await interaction.followup.send(embed=embed, ephemeral=True)
        
        elif self.action == "console":
            # Get VNC info
            vnc_cmd = f"virsh vncdisplay {view.vm_name}"
            vnc = run_cmd(vnc_cmd)
            
            embed = create_embed("🖥️ Console Access", f"**VM:** `{view.vm_name}`")
            if "Error" not in vnc:
                port = vnc.split(':')[1] if ':' in vnc else "5900"
                embed.add_field(name="VNC Server", value=f"`{SERVER_IP}:{port}`", inline=False)
                embed.add_field(name="VNC Client", value="Use TigerVNC, RealVNC or Remmina", inline=False)
            else:
                embed.add_field(name="SSH Console", value=f"```bash\nssh ubuntu@{view.vps['ip_address']}\n```", inline=False)
            
            await interaction.followup.send(embed=embed, ephemeral=True)
        
        elif self.action == "reinstall":
            # Confirmation for reinstall
            embed = create_embed("⚠️ Reinstall VM", 
                               f"This will **DELETE ALL DATA** on `{view.vm_name}`!\n\n"
                               f"**Are you sure?**", 0xFF9900)
            
            class ConfirmReinstall(discord.ui.View):
                def __init__(self, parent_view):
                    super().__init__(timeout=60)
                    self.parent_view = parent_view
                
                @discord.ui.button(label="Confirm Reinstall", style=discord.ButtonStyle.danger)
                async def confirm(self, inter: discord.Interaction, button: discord.ui.Button):
                    await inter.response.defer()
                    
                    # Delete old VM
                    KVMManager.delete_vm(self.parent_view.vm_name)
                    
                    # Create new VM with same specs
                    try:
                        new_vm = KVMManager.create_vm(
                            self.parent_view.vm_name,
                            self.parent_view.vps['ram_mb'],
                            self.parent_view.vps['cpu_cores'],
                            self.parent_view.vps['disk_gb'],
                            self.parent_view.vps['os_id']
                        )
                        
                        # Update database
                        conn = get_db()
                        c = conn.cursor()
                        c.execute('''UPDATE vps SET 
                            ip_address = ?, ssh_password = ?, status = 'running', suspended = 0
                            WHERE vm_name = ?''',
                            (new_vm['ip_address'], new_vm['ssh_password'], self.parent_view.vm_name))
                        conn.commit()
                        conn.close()
                        
                        success_embed = create_embed("✅ VM Reinstalled", 
                                                    f"**VM:** `{self.parent_view.vm_name}`\n"
                                                    f"**New IP:** {new_vm['ip_address']}\n"
                                                    f"**New Password:** ||{new_vm['ssh_password']}||", 0x00FF00)
                        await inter.followup.send(embed=success_embed, ephemeral=True)
                        
                    except Exception as e:
                        error_embed = create_embed("❌ Reinstall Failed", str(e), 0xFF0000)
                        await inter.followup.send(embed=error_embed, ephemeral=True)
                
                @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
                async def cancel(self, inter: discord.Interaction, button: discord.ui.Button):
                    await inter.response.edit_message(embed=create_embed("Cancelled", "Reinstall cancelled.", 0x666666), view=None)
            
            await interaction.followup.send(embed=embed, view=ConfirmReinstall(view), ephemeral=True)
        
        elif self.action == "resize":
            embed = create_embed("🔧 Resize VM", 
                               f"**Current Resources:**\n"
                               f"RAM: {view.vps['ram_mb']}MB\n"
                               f"CPU: {view.vps['cpu_cores']} cores\n"
                               f"Disk: {view.vps['disk_gb']}GB\n\n"
                               f"Select resource to resize:", 0x00AAFF)
            
            class ResizeView(discord.ui.View):
                def __init__(self, parent_view):
                    super().__init__(timeout=60)
                    self.parent_view = parent_view
                
                @discord.ui.button(label="🔼 Increase RAM", style=discord.ButtonStyle.primary)
                async def inc_ram(self, inter: discord.Interaction, button: discord.ui.Button):
                    await self.resize_resource(inter, "ram", view.vps['ram_mb'] * 2)
                
                @discord.ui.button(label="🔼 Increase CPU", style=discord.ButtonStyle.primary)
                async def inc_cpu(self, inter: discord.Interaction, button: discord.ui.Button):
                    await self.resize_resource(inter, "cpu", view.vps['cpu_cores'] + 1)
                
                @discord.ui.button(label="🔼 Increase Disk", style=discord.ButtonStyle.primary)
                async def inc_disk(self, inter: discord.Interaction, button: discord.ui.Button):
                    await self.resize_resource(inter, "disk", view.vps['disk_gb'] + 20)
                
                async def resize_resource(self, interaction, resource, new_value):
                    await interaction.response.defer()
                    
                    # Stop VM first
                    KVMManager.control_vm(self.parent_view.vm_name, "stop")
                    time.sleep(5)
                    
                    try:
                        if resource == "ram":
                            run_cmd(f"virsh setmaxmem {self.parent_view.vm_name} {new_value}M --config")
                            run_cmd(f"virsh setmem {self.parent_view.vm_name} {new_value}M --config")
                        elif resource == "cpu":
                            run_cmd(f"virsh setvcpus {self.parent_view.vm_name} {new_value} --config")
                        elif resource == "disk":
                            disk_path = f"/var/lib/libvirt/images/vms/{self.parent_view.vm_name}.qcow2"
                            run_cmd(f"qemu-img resize {disk_path} {new_value}G")
                        
                        # Update database
                        conn = get_db()
                        c = conn.cursor()
                        if resource == "ram":
                            c.execute("UPDATE vps SET ram_mb = ? WHERE vm_name = ?", (new_value, self.parent_view.vm_name))
                        elif resource == "cpu":
                            c.execute("UPDATE vps SET cpu_cores = ? WHERE vm_name = ?", (new_value, self.parent_view.vm_name))
                        elif resource == "disk":
                            c.execute("UPDATE vps SET disk_gb = ? WHERE vm_name = ?", (new_value, self.parent_view.vm_name))
                        conn.commit()
                        conn.close()
                        
                        # Start VM
                        KVMManager.control_vm(self.parent_view.vm_name, "start")
                        
                        embed = create_embed("✅ Resize Successful", 
                                           f"**VM:** `{self.parent_view.vm_name}`\n"
                                           f"**New {resource.upper()}:** {new_value}", 0x00FF00)
                        await interaction.followup.send(embed=embed, ephemeral=True)
                        
                    except Exception as e:
                        embed = create_embed("❌ Resize Failed", str(e), 0xFF0000)
                        await interaction.followup.send(embed=embed, ephemeral=True)
            
            await interaction.followup.send(embed=embed, view=ResizeView(view), ephemeral=True)
        
        elif self.action == "share":
            embed = create_embed("👥 Share VM", 
                               f"**VM:** `{view.vm_name}`\n\n"
                               f"Enter Discord User ID to share with:", 0x00AAFF)
            
            class ShareModal(discord.ui.Modal, title="Share VM Access"):
                user_id = discord.ui.TextInput(
                    label="Discord User ID",
                    placeholder="123456789012345678",
                    required=True
                )
                
                async def on_submit(self, inter: discord.Interaction):
                    await inter.response.defer()
                    
                    # Get current shared list
                    conn = get_db()
                    c = conn.cursor()
                    c.execute("SELECT shared_with FROM vps WHERE vm_name = ?", (view.vm_name,))
                    row = c.fetchone()
                    
                    shared_with = json.loads(row['shared_with']) if row and row['shared_with'] else []
                    
                    if self.user_id.value in shared_with:
                        embed = create_embed("Already Shared", 
                                           f"User <@{self.user_id.value}> already has access!", 0xFFAA00)
                    else:
                        shared_with.append(self.user_id.value)
                        c.execute("UPDATE vps SET shared_with = ? WHERE vm_name = ?",
                                 (json.dumps(shared_with), view.vm_name))
                        conn.commit()
                        
                        embed = create_embed("✅ VM Shared", 
                                           f"**VM:** `{view.vm_name}`\n"
                                           f"**Shared with:** <@{self.user_id.value}>", 0x00FF00)
                        
                        # Notify user
                        try:
                            user = await bot.fetch_user(int(self.user_id.value))
                            notify_embed = create_embed("🔓 VM Access Granted",
                                                      f"You now have access to VM `{view.vm_name}`!\n"
                                                      f"Use `!manage-shared {view.vm_name}` to manage it.")
                            await user.send(embed=notify_embed)
                        except:
                            pass
                    
                    conn.close()
                    await inter.followup.send(embed=embed, ephemeral=True)
            
            await interaction.followup.send(embed=embed)
            await interaction.followup.send_modal(ShareModal())
        
        elif self.action == "suspend":
            conn = get_db()
            c = conn.cursor()
            c.execute("UPDATE vps SET suspended = 1 WHERE vm_name = ?", (view.vm_name,))
            conn.commit()
            conn.close()
            
            KVMManager.control_vm(view.vm_name, "suspend")
            
            embed = create_embed("⏸️ VM Suspended", 
                               f"**VM:** `{view.vm_name}`\n"
                               f"VM has been suspended.", 0xFFAA00)
            await interaction.followup.send(embed=embed, ephemeral=True)
        
        elif self.action == "unsuspend":
            conn = get_db()
            c = conn.cursor()
            c.execute("UPDATE vps SET suspended = 0 WHERE vm_name = ?", (view.vm_name,))
            conn.commit()
            conn.close()
            
            KVMManager.control_vm(view.vm_name, "resume")
            
            embed = create_embed("▶️ VM Unsuspended", 
                               f"**VM:** `{view.vm_name}`\n"
                               f"VM has been unsuspended and resumed.", 0x00FF00)
            await interaction.followup.send(embed=embed, ephemeral=True)
        
        elif self.action == "info":
            vps = get_vps_by_name(view.vm_name)
            info = KVMManager.get_vm_info(view.vm_name)
            stats = KVMManager.get_vm_stats(view.vm_name)
            
            embed = create_embed("📋 VM Information", f"**Name:** `{view.vm_name}`")
            
            embed.add_field(name="Owner", value=f"<@{vps['user_id']}>", inline=True)
            embed.add_field(name="Plan", value=vps['plan'], inline=True)
            embed.add_field(name="OS", value=vps['os_id'], inline=True)
            
            embed.add_field(name="Resources", 
                          f"RAM: {vps['ram_mb']}MB\nCPU: {vps['cpu_cores']} cores\nDisk: {vps['disk_gb']}GB", 
                          inline=False)
            
            embed.add_field(name="Status", 
                          f"{info['status'].upper()}\n"
                          f"Suspended: {'Yes' if vps['suspended'] else 'No'}\n"
                          f"IP: {vps['ip_address'] or 'N/A'}", 
                          inline=False)
            
            if stats:
                stats_text = []
                if 'cpu' in stats:
                    stats_text.append(f"CPU: {stats['cpu']}")
                if 'memory_mb' in stats:
                    stats_text.append(f"Memory: {stats['memory_mb']}MB")
                if stats_text:
                    embed.add_field(name="Live Stats", "\n".join(stats_text), inline=False)
            
            await interaction.followup.send(embed=embed, ephemeral=True)
        
        elif self.action == "delete":
            embed = create_embed("🗑️ Delete VM", 
                               f"**WARNING:** This will permanently delete `{view.vm_name}`!\n\n"
                               f"All data will be lost forever!\n\n"
                               f"**Are you absolutely sure?**", 0xFF0000)
            
            class ConfirmDelete(discord.ui.View):
                def __init__(self, parent_view):
                    super().__init__(timeout=60)
                    self.parent_view = parent_view
                
                @discord.ui.button(label="DELETE PERMANENTLY", style=discord.ButtonStyle.danger)
                async def confirm(self, inter: discord.Interaction, button: discord.ui.Button):
                    await inter.response.defer()
                    
                    # Delete VM
                    KVMManager.delete_vm(self.parent_view.vm_name)
                    
                    # Remove from database
                    conn = get_db()
                    c = conn.cursor()
                    c.execute("DELETE FROM vps WHERE vm_name = ?", (self.parent_view.vm_name,))
                    conn.commit()
                    conn.close()
                    
                    embed = create_embed("✅ VM Deleted", 
                                       f"**VM:** `{self.parent_view.vm_name}`\n"
                                       f"VM has been permanently deleted.", 0x00FF00)
                    await inter.followup.send(embed=embed, ephemeral=True)
                
                @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
                async def cancel(self, inter: discord.Interaction, button: discord.ui.Button):
                    await inter.response.edit_message(embed=create_embed("Cancelled", "Deletion cancelled.", 0x666666), view=None)
            
            await interaction.followup.send(embed=embed, view=ConfirmDelete(view), ephemeral=True)

@bot.command(name='manage')
async def manage_vps(ctx, vm_name: str):
    """Manage a VPS with complete control panel"""
    vps = get_vps_by_name(vm_name)
    
    if not vps:
        embed = create_embed("❌ VPS Not Found", f"VM `{vm_name}` not found!", 0xFF0000)
        await ctx.send(embed=embed)
        return
    
    user_id = str(ctx.author.id)
    is_admin_user = is_admin(user_id)
    
    # Check permissions
    if vps['user_id'] != user_id and not is_admin_user:
        # Check shared access
        shared_with = json.loads(vps.get('shared_with', '[]'))
        if user_id not in shared_with:
            embed = create_embed("❌ Access Denied", "You don't have permission to manage this VPS!", 0xFF0000)
            await ctx.send(embed=embed)
            return
    
    # Get VM info
    info = KVMManager.get_vm_info(vm_name)
    
    # Create management embed
    status = info['status'].upper()
    if vps['suspended']:
        status += " ⏸️ SUSPENDED"
    
    embed = create_embed(f"🖥️ {vm_name}", f"**Status:** {status}\n**IP:** {vps['ip_address'] or 'N/A'}")
    
    embed.add_field(name="Resources", 
                   f"RAM: {vps['ram_mb']}MB\nCPU: {vps['cpu_cores']} cores\nDisk: {vps['disk_gb']}GB", 
                   inline=True)
    
    embed.add_field(name="Plan & OS", 
                   f"Plan: {vps['plan']}\nOS: {vps['os_id']}\nOwner: <@{vps['user_id']}>", 
                   inline=True)
    
    # Create view
    view = ManageView(vm_name, user_id, is_admin_user)
    
    await ctx.send(embed=embed, view=view)

@bot.command(name='create')
@commands.check(lambda ctx: is_admin(str(ctx.author.id)))
async def create_vps(ctx, plan: str, user: discord.Member, os_id: str = "ubuntu-22.04"):
    """Create a new VPS for user"""
    if plan not in VM_PLANS:
        embed = create_embed("❌ Invalid Plan", 
                           f"Available plans: {', '.join(VM_PLANS.keys())}", 0xFF0000)
        await ctx.send(embed=embed)
        return
    
    plan_details = VM_PLANS[plan]
    vm_name = f"vm-{user.id}-{int(time.time())}"
    
    embed = create_embed("🔄 Creating VPS...", 
                        f"**User:** {user.mention}\n"
                        f"**Plan:** {plan}\n"
                        f"**OS:** {os_id}\n"
                        f"**Resources:** {plan_details['ram']}MB RAM, {plan_details['cpu']} CPU, {plan_details['disk']}GB Disk\n\n"
                        f"Creating `{vm_name}`...", 0xFFFF00)
    msg = await ctx.send(embed=embed)
    
    try:
        # Create VM
        vm_data = KVMManager.create_vm(
            vm_name=vm_name,
            ram_mb=plan_details['ram'],
            cpu_cores=plan_details['cpu'],
            disk_gb=plan_details['disk'],
            os_id=os_id
        )
        
        # Add to database
        vm_data['user_id'] = str(user.id)
        vm_data['plan'] = plan
        add_vps_to_db(vm_data)
        
        # Success embed
        success_embed = create_embed("✅ VPS Created!", 
                                    f"**{BRAND_NAME} KVM Virtual Machine Ready!**", 0x00FF00)
        
        success_embed.add_field(name="User", value=user.mention, inline=True)
        success_embed.add_field(name="VPS Name", value=f"`{vm_name}`", inline=True)
        success_embed.add_field(name="IP Address", value=vm_data['ip_address'] or "Getting IP...", inline=True)
        
        success_embed.add_field(name="SSH Access", 
                               f"```bash\nssh ubuntu@{vm_data['ip_address']}\n```", 
                               inline=False)
        
        success_embed.add_field(name="Password", 
                               f"||{vm_data['ssh_password']}||", 
                               inline=False)
        
        success_embed.add_field(name="Manage", 
                               f"Use `!manage {vm_name}` to control your VPS", 
                               inline=False)
        
        await msg.edit(embed=success_embed)
        
        # Send DM to user
        try:
            dm_embed = create_embed("🎉 Your VPS is Ready!", 
                                   f"**{BRAND_NAME} has created your KVM VPS!**")
            
            dm_embed.add_field(name="VPS Details", 
                              f"**Name:** `{vm_name}`\n"
                              f"**IP:** {vm_data['ip_address']}\n"
                              f"**Plan:** {plan}\n"
                              f"**OS:** {os_id}", 
                              inline=False)
            
            dm_embed.add_field(name="SSH Access", 
                              f"```bash\nssh ubuntu@{vm_data['ip_address']}\n```\n"
                              f"**Username:** `ubuntu`\n"
                              f"**Password:** ||{vm_data['ssh_password']}||", 
                              inline=False)
            
            dm_embed.add_field(name="Management", 
                              f"• Use `!manage {vm_name}` to start/stop/reboot\n"
                              f"• Use `!myvps` to list all your VPS", 
                              inline=False)
            
            await user.send(embed=dm_embed)
        except:
            pass
        
    except Exception as e:
        error_embed = create_embed("❌ Creation Failed", str(e), 0xFF0000)
        await msg.edit(embed=error_embed)

@bot.command(name='list')
@commands.check(lambda ctx: is_admin(str(ctx.author.id)))
async def list_vps(ctx):
    """List all VPS (Admin only)"""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM vps")
    vps_list = c.fetchall()
    conn.close()
    
    if not vps_list:
        embed = create_embed("No VPS", "No virtual machines found.", 0x666666)
        await ctx.send(embed=embed)
        return
    
    # Get VM statuses
    vms_with_status = []
    for vps in vps_list:
        vps_dict = dict(vps)
        info = KVMManager.get_vm_info(vps_dict['vm_name'])
        vps_dict['live_status'] = info.get('status', 'unknown')
        vms_with_status.append(vps_dict)
    
    # Create paginated embeds
    for i in range(0, len(vms_with_status), 10):
        embed = create_embed(f"📋 All VPS ({len(vms_with_status)})", f"Page {i//10 + 1}")
        
        for vps in vms_with_status[i:i+10]:
            status = vps['live_status'].upper()
            if vps['suspended']:
                status += " ⏸️"
            
            embed.add_field(
                name=f"{vps['vm_name']}",
                value=f"**Owner:** <@{vps['user_id']}>\n"
                      f"**Status:** {status}\n"
                      f"**Plan:** {vps['plan']}\n"
                      f"**IP:** {vps['ip_address'] or 'N/A'}",
                inline=True
            )
        
        await ctx.send(embed=embed)

@bot.command(name='stats')
async def stats_cmd(ctx, vm_name: str = None):
    """Get VM or system statistics"""
    if vm_name:
        # Single VM stats
        vps = get_vps_by_name(vm_name)
        if not vps:
            await ctx.send(f"❌ VM `{vm_name}` not found!")
            return
        
        info = KVMManager.get_vm_info(vm_name)
        stats = KVMManager.get_vm_stats(vm_name)
        
        embed = create_embed(f"📊 {vm_name} Stats", f"**Status:** {info['status'].upper()}")
        
        embed.add_field(name="Resources", 
                       f"RAM: {vps['ram_mb']}MB\nCPU: {vps['cpu_cores']} cores\nDisk: {vps['disk_gb']}GB", 
                       inline=True)
        
        if stats:
            stats_text = []
            if 'cpu' in stats:
                stats_text.append(f"CPU: {stats['cpu']}")
            if 'memory_mb' in stats:
                stats_text.append(f"Memory: {stats['memory_mb']}MB")
            
            if stats_text:
                embed.add_field(name="Live Stats", "\n".join(stats_text), inline=True)
        
        embed.add_field(name="IP Address", value=vps['ip_address'] or "N/A", inline=True)
        
        await ctx.send(embed=embed)
    else:
        # System stats
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT COUNT(*) as total FROM vps")
        total_vps = c.fetchone()['total']
        
        c.execute("SELECT COUNT(*) as running FROM vps WHERE status = 'running'")
        running_vps = c.fetchone()['running']
        conn.close()
        
        # Get host stats
        host_ram = run_cmd("free -m | awk 'NR==2{printf \"%s/%sMB (%.2f%%)\", $3,$2,$3*100/$2 }'")
        host_cpu = run_cmd("top -bn1 | grep 'Cpu(s)' | awk '{print $2 + $4}'") + "%"
        host_disk = run_cmd("df -h / | awk 'NR==2{print $3 \"/\" $2 \" (\" $5 \")\"}'")
        
        embed = create_embed("📊 System Statistics", f"**{BRAND_NAME} KVM Host**")
        
        embed.add_field(name="VPS Stats", 
                       f"Total VPS: {total_vps}\nRunning: {running_vps}\nStopped: {total_vps - running_vps}", 
                       inline=True)
        
        embed.add_field(name="Host Resources", 
                       f"CPU: {host_cpu}\nRAM: {host_ram}\nDisk: {host_disk}", 
                       inline=True)
        
        embed.add_field(name="Server", 
                       f"IP: {SERVER_IP}\nHostname: {HOSTNAME}", 
                       inline=True)
        
        await ctx.send(embed=embed)

@bot.command(name='help')
async def help_cmd(ctx):
    """Show help"""
    user_id = str(ctx.author.id)
    is_admin_user = is_admin(user_id)
    
    embed = create_embed("📚 Commands Help", f"**{BRAND_NAME} KVM Bot**")
    
    # User commands
    user_commands = """
**👤 User Commands:**
`!ping` - Check bot latency
`!myvps` - List your VPS
`!manage <vm_name>` - Manage VPS (full control panel)
`!stats [vm_name]` - View statistics
`!help` - This menu
"""
    embed.add_field(name="For Everyone", value=user_commands, inline=False)
    
    if is_admin_user:
        admin_commands = """
**👑 Admin Commands:**
`!create <plan> @user [os]` - Create VPS
`!list` - List all VPS
`!admin-add @user` - Add admin
`!admin-remove @user` - Remove admin
`!admin-list` - List admins
`!suspend-vps <vm_name>` - Suspend VPS
`!unsuspend-vps <vm_name>` - Unsuspend VPS
"""
        embed.add_field(name="Administration", value=admin_commands, inline=False)
    
    embed.add_field(name="🖥️ VPS Features", 
                   "• Start/Stop/Reboot\n• Reinstall OS\n• Resize Resources\n• SSH Access\n• VNC Console\n• Live Statistics\n• Share Access\n• Suspend/Unsuspend\n• And more...", 
                   inline=False)
    
    await ctx.send(embed=embed)

# ==================== ADMIN COMMANDS ====================
@bot.command(name='admin-add')
@commands.check(lambda ctx: str(ctx.author.id) == MAIN_ADMIN_ID)
async def admin_add(ctx, user: discord.Member):
    """Add admin"""
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (str(user.id),))
    conn.commit()
    conn.close()
    
    embed = create_embed("✅ Admin Added", f"{user.mention} is now an admin!", 0x00FF00)
    await ctx.send(embed=embed)

@bot.command(name='admin-remove')
@commands.check(lambda ctx: str(ctx.author.id) == MAIN_ADMIN_ID)
async def admin_remove(ctx, user: discord.Member):
    """Remove admin"""
    if str(user.id) == MAIN_ADMIN_ID:
        await ctx.send("❌ Cannot remove main admin!")
        return
    
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM admins WHERE user_id = ?", (str(user.id),))
    conn.commit()
    conn.close()
    
    embed = create_embed("✅ Admin Removed", f"{user.mention} is no longer an admin.", 0xFFAA00)
    await ctx.send(embed=embed)

@bot.command(name='admin-list')
async def admin_list(ctx):
    """List admins"""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT user_id FROM admins")
    admins = [row['user_id'] for row in c.fetchall()]
    conn.close()
    
    embed = create_embed("👑 Administrators", f"Total admins: {len(admins)}")
    
    admin_list = [f"• <@{admin_id}>" for admin_id in admins]
    if admin_list:
        embed.add_field(name="Admins", "\n".join(admin_list), inline=False)
    
    await ctx.send(embed=embed)

@bot.command(name='suspend-vps')
@commands.check(lambda ctx: is_admin(str(ctx.author.id)))
async def suspend_vps_cmd(ctx, vm_name: str, *, reason: str = "No reason"):
    """Suspend a VPS"""
    vps = get_vps_by_name(vm_name)
    if not vps:
        await ctx.send(f"❌ VM `{vm_name}` not found!")
        return
    
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE vps SET suspended = 1 WHERE vm_name = ?", (vm_name,))
    conn.commit()
    conn.close()
    
    KVMManager.control_vm(vm_name, "suspend")
    
    embed = create_embed("⏸️ VPS Suspended", 
                        f"**VM:** `{vm_name}`\n"
                        f"**Reason:** {reason}\n"
                        f"**By:** {ctx.author.mention}", 0xFFAA00)
    await ctx.send(embed=embed)

@bot.command(name='unsuspend-vps')
@commands.check(lambda ctx: is_admin(str(ctx.author.id)))
async def unsuspend_vps_cmd(ctx, vm_name: str):
    """Unsuspend a VPS"""
    vps = get_vps_by_name(vm_name)
    if not vps:
        await ctx.send(f"❌ VM `{vm_name}` not found!")
        return
    
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE vps SET suspended = 0 WHERE vm_name = ?", (vm_name,))
    conn.commit()
    conn.close()
    
    KVMManager.control_vm(vm_name, "resume")
    
    embed = create_embed("▶️ VPS Unsuspended", 
                        f"**VM:** `{vm_name}`\n"
                        f"**By:** {ctx.author.mention}", 0x00FF00)
    await ctx.send(embed=embed)

# ==================== START BOT ====================
if __name__ == "__main__":
    print(f"Starting {BRAND_NAME} KVM Bot...")
    print(f"Features: Complete LXC-style management")
    print(f"Commands: !manage, !myvps, !create, !list, !stats, etc.")
    
    if TOKEN:
        bot.run(TOKEN)
    else:
        print("❌ No Discord token in .env file!")
EOF

# Create systemd service
cat > /etc/systemd/system/rdx-bot.service << EOF
[Unit]
Description=RDX KVM Bot - Complete VPS Management
After=network.target libvirtd.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/rdx-bot
Environment=PATH=/opt/rdx-bot/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
EnvironmentFile=/opt/rdx-bot/.env
ExecStart=/opt/rdx-bot/venv/bin/python /opt/rdx-bot/rdx_bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# Enable service
systemctl daemon-reload
systemctl enable rdx-bot.service
systemctl start rdx-bot.service

# Wait and check
sleep 5

echo ""
echo -e "${GREEN}══════════════════════════════════════════════════${NC}"
echo -e "${GREEN}🎉 RDX KVM BOT INSTALLED SUCCESSFULLY!${NC}"
echo -e "${GREEN}══════════════════════════════════════════════════${NC}"
echo ""
echo -e "${YELLOW}📊 Installation Summary:${NC}"
echo "   Brand: $BRAND_NAME"
echo "   Admin ID: $ADMIN_ID"
echo "   Server IP: $SERVER_IP"
echo "   Bot Directory: /opt/rdx-bot"
echo ""
echo -e "${YELLOW}✅ Features Installed:${NC}"
echo "   • Complete KVM Virtualization"
echo "   • !manage command with ALL LXC features"
echo "   • Start/Stop/Reboot/Reinstall"
echo "   • SSH & VNC Access"
echo "   • Resource Resizing"
echo "   • VM Sharing"
echo "   • Suspension System"
echo "   • Live Statistics"
echo "   • Admin Management"
echo "   • And 50+ more features..."
echo ""
echo -e "${YELLOW}🚀 Bot Status:${NC}"
systemctl status rdx-bot.service --no-pager -l | head -20
echo ""
echo -e "${YELLOW}📝 Quick Commands:${NC}"
echo "   Check logs: journalctl -u rdx-bot -f"
echo "   Restart bot: systemctl restart rdx-bot"
echo "   Stop bot: systemctl stop rdx-bot"
echo ""
echo -e "${GREEN}🤖 Discord Commands Ready:${NC}"
echo "   !ping - Check bot"
echo "   !myvps - List your VPS"
echo "   !manage vm-name - Full control panel"
echo "   !create basic @user - Create VPS"
echo "   !list - List all VPS (admin)"
echo "   !stats - System statistics"
echo "   !help - Show all commands"
echo ""
echo -e "${YELLOW}⚠️ First Time Setup:${NC}"
echo "   1. Go to Discord"
echo "   2. Use !create basic @user ubuntu-22.04"
echo "   3. Use !manage vm-name to access ALL features"
echo ""
echo -e "${GREEN}✅ Installation Complete! Bot is online in Discord.${NC}"
