# rdx_kvm_bot.py
# RDX KVM VPS Hosting Bot - Production Ready
# Complete KVM Virtual Machine Management System

import discord
from discord.ext import commands, tasks
import asyncio
import subprocess
import json
import os
import sys
import logging
import sqlite3
import shutil
import time
import re
import uuid
import yaml
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any
from pathlib import Path

# ==================== CONFIGURATION ====================
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
MAIN_ADMIN_ID = int(os.getenv('MAIN_ADMIN_ID', '1210291131301101618'))
BRAND_NAME = "RDX KVM Hosting"
BRAND_COLOR = 0x00AAFF  # RDX Blue
LOGO_URL = "https://i.imgur.com/xSsIERx.png"  # Replace with your logo

# KVM Configuration
KVM_STORAGE_POOL = "vms"
KVM_NETWORK = "default"
CLOUD_INIT_DIR = "/var/lib/libvirt/cloud-init"
BASE_IMAGES_DIR = "/home/ubuntu/base-images"  # Update path as needed

# OS Options for KVM VMs
OS_OPTIONS = [
    {"id": "ubuntu-22.04", "name": "Ubuntu 22.04 LTS", 
     "image": "ubuntu-22.04-base.qcow2", "virtio": True},
    {"id": "ubuntu-20.04", "name": "Ubuntu 20.04 LTS", 
     "image": "ubuntu-20.04-base.qcow2", "virtio": True},
    {"id": "debian-11", "name": "Debian 11 Bullseye", 
     "image": "debian-11-base.qcow2", "virtio": True},
    {"id": "almalinux-9", "name": "AlmaLinux 9", 
     "image": "", "iso": "AlmaLinux-9-latest-x86_64-dvd.iso", "virtio": True},
]

# VM Plans
VM_PLANS = {
    "basic": {"ram": 1024, "cpu": 1, "disk": 20, "price": 5},
    "standard": {"ram": 2048, "cpu": 2, "disk": 40, "price": 10},
    "premium": {"ram": 4096, "cpu": 4, "disk": 80, "price": 20},
    "enterprise": {"ram": 8192, "cpu": 8, "disk": 160, "price": 40},
}

# ==================== LOGGING SETUP ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('rdx_kvm.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('rdx_kvm_bot')

# ==================== DATABASE SETUP ====================
def init_database():
    """Initialize SQLite database for RDX KVM"""
    conn = sqlite3.connect('rdx_kvm.db')
    c = conn.cursor()
    
    # Admins table
    c.execute('''
        CREATE TABLE IF NOT EXISTS admins (
            user_id TEXT PRIMARY KEY,
            added_by TEXT,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Users/VMs table
    c.execute('''
        CREATE TABLE IF NOT EXISTS virtual_machines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            vm_name TEXT UNIQUE NOT NULL,
            vm_uuid TEXT UNIQUE NOT NULL,
            display_name TEXT,
            plan TEXT NOT NULL,
            ram_mb INTEGER NOT NULL,
            cpu_cores INTEGER NOT NULL,
            disk_gb INTEGER NOT NULL,
            os_id TEXT NOT NULL,
            ip_address TEXT,
            status TEXT DEFAULT 'stopped',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP,
            suspended BOOLEAN DEFAULT 0,
            whitelisted BOOLEAN DEFAULT 0,
            ssh_password TEXT,
            root_password TEXT,
            notes TEXT,
            FOREIGN KEY (user_id) REFERENCES admins(user_id) ON DELETE CASCADE
        )
    ''')
    
    # VM statistics table
    c.execute('''
        CREATE TABLE IF NOT EXISTS vm_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vm_uuid TEXT NOT NULL,
            cpu_usage REAL,
            ram_usage_mb INTEGER,
            disk_usage_gb REAL,
            network_rx_mb REAL,
            network_tx_mb REAL,
            recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (vm_uuid) REFERENCES virtual_machines(vm_uuid) ON DELETE CASCADE
        )
    ''')
    
    # Shared access table
    c.execute('''
        CREATE TABLE IF NOT EXISTS shared_access (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vm_uuid TEXT NOT NULL,
            shared_with_user_id TEXT NOT NULL,
            permission_level TEXT DEFAULT 'user',
            shared_by TEXT NOT NULL,
            shared_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(vm_uuid, shared_with_user_id),
            FOREIGN KEY (vm_uuid) REFERENCES virtual_machines(vm_uuid) ON DELETE CASCADE
        )
    ''')
    
    # Suspension logs
    c.execute('''
        CREATE TABLE IF NOT EXISTS suspension_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vm_uuid TEXT NOT NULL,
            reason TEXT,
            suspended_by TEXT,
            suspended_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            unsuspended_at TIMESTAMP,
            unsuspended_by TEXT,
            FOREIGN KEY (vm_uuid) REFERENCES virtual_machines(vm_uuid) ON DELETE CASCADE
        )
    ''')
    
    # Settings table
    c.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    ''')
    
    # Insert default settings
    default_settings = [
        ('cpu_threshold', '85'),
        ('ram_threshold', '90'),
        ('disk_threshold', '80'),
        ('max_vms_per_user', '5'),
        ('ssh_port_start', '22000'),
        ('vnc_port_start', '5900'),
        ('default_ssh_user', 'ubuntu'),
        ('brand_name', BRAND_NAME),
    ]
    
    c.executemany(
        'INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)',
        default_settings
    )
    
    # Insert main admin
    c.execute(
        'INSERT OR IGNORE INTO admins (user_id) VALUES (?)',
        (str(MAIN_ADMIN_ID),)
    )
    
    conn.commit()
    conn.close()
    logger.info("RDX KVM Database initialized successfully")

# ==================== KVM UTILITY FUNCTIONS ====================
class KVMManager:
    """Professional KVM Virtual Machine Manager"""
    
    @staticmethod
    async def execute_command(cmd: List[str], timeout: int = 30) -> Tuple[str, str, int]:
        """Execute shell command safely"""
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout
            )
            
            return (
                stdout.decode('utf-8', errors='ignore').strip(),
                stderr.decode('utf-8', errors='ignore').strip(),
                proc.returncode
            )
        except asyncio.TimeoutError:
            logger.error(f"Command timed out: {' '.join(cmd)}")
            return "", "Command timed out", 1
        except Exception as e:
            logger.error(f"Command execution error: {e}")
            return "", str(e), 1
    
    @staticmethod
    def generate_mac_address() -> str:
        """Generate a unique MAC address for VM"""
        return "52:54:00:%02x:%02x:%02x" % (
            uuid.uuid4().int & 0xFF,
            uuid.uuid4().int & 0xFF,
            uuid.uuid4().int & 0xFF,
        )
    
    @staticmethod
    def generate_vm_name(user_id: str) -> str:
        """Generate unique VM name"""
        timestamp = int(time.time())
        random_str = uuid.uuid4().hex[:6]
        return f"rdx-vm-{user_id[:8]}-{timestamp}-{random_str}"
    
    @staticmethod
    async def create_cloud_init_iso(vm_name: str, hostname: str, 
                                   username: str, password: str,
                                   ssh_key: str = None) -> str:
        """Create cloud-init ISO for VM provisioning"""
        
        # Create temporary directory
        temp_dir = f"/tmp/cloud-init-{vm_name}"
        os.makedirs(temp_dir, exist_ok=True)
        
        # user-data
        user_data = {
            'hostname': hostname,
            'manage_etc_hosts': True,
            'users': [
                {
                    'name': username,
                    'passwd': password,
                    'sudo': 'ALL=(ALL) NOPASSWD:ALL',
                    'ssh_authorized_keys': [ssh_key] if ssh_key else [],
                    'shell': '/bin/bash'
                }
            ],
            'chpasswd': {
                'expire': False,
                'users': [
                    {'name': 'root', 'password': password},
                    {'name': username, 'password': password}
                ]
            },
            'package_update': True,
            'package_upgrade': True,
            'packages': ['qemu-guest-agent', 'curl', 'wget', 'htop'],
            'runcmd': [
                'systemctl enable qemu-guest-agent',
                'systemctl start qemu-guest-agent',
                'echo "RDX KVM Hosting - Premium Cloud Server" > /etc/motd'
            ]
        }
        
        # meta-data
        meta_data = {
            'instance-id': vm_name,
            'local-hostname': hostname
        }
        
        # Write files
        with open(f"{temp_dir}/user-data", 'w') as f:
            f.write('#cloud-config\n' + yaml.dump(user_data))
        
        with open(f"{temp_dir}/meta-data", 'w') as f:
            f.write(yaml.dump(meta_data))
        
        # Create ISO
        iso_path = f"{CLOUD_INIT_DIR}/{vm_name}-cloud-init.iso"
        mkisofs_cmd = [
            'genisoimage', '-output', iso_path,
            '-volid', 'cidata', '-joliet', '-rock',
            f"{temp_dir}/user-data", f"{temp_dir}/meta-data"
        ]
        
        stdout, stderr, rc = await KVMManager.execute_command(mkisofs_cmd)
        
        if rc != 0:
            logger.error(f"Failed to create cloud-init ISO: {stderr}")
            raise Exception(f"Cloud-init ISO creation failed: {stderr}")
        
        # Cleanup temp directory
        shutil.rmtree(temp_dir, ignore_errors=True)
        
        logger.info(f"Created cloud-init ISO: {iso_path}")
        return iso_path
    
    @staticmethod
    async def create_vm(vm_name: str, ram_mb: int, cpu_cores: int, 
                       disk_gb: int, os_id: str, hostname: str = None) -> Dict:
        """Create a new KVM Virtual Machine"""
        
        # Find OS configuration
        os_config = next((os for os in OS_OPTIONS if os['id'] == os_id), None)
        if not os_config:
            raise Exception(f"OS {os_id} not supported")
        
        if hostname is None:
            hostname = vm_name
        
        # Generate passwords
        root_password = uuid.uuid4().hex[:12]
        ssh_password = uuid.uuid4().hex[:12]
        
        # Create cloud-init ISO
        cloud_init_iso = await KVMManager.create_cloud_init_iso(
            vm_name, hostname, "ubuntu", ssh_password
        )
        
        # Generate unique VM UUID
        vm_uuid = str(uuid.uuid4())
        
        # Prepare disk image
        disk_path = f"/var/lib/libvirt/images/vms/{vm_name}.qcow2"
        
        if os_config.get('image'):
            # Copy base image
            base_image = f"{BASE_IMAGES_DIR}/{os_config['image']}"
            if not os.path.exists(base_image):
                raise Exception(f"Base image not found: {base_image}")
            
            cp_cmd = ['cp', base_image, disk_path]
            stdout, stderr, rc = await KVMManager.execute_command(cp_cmd)
            
            if rc != 0:
                raise Exception(f"Failed to copy base image: {stderr}")
            
            # Resize disk
            resize_cmd = ['qemu-img', 'resize', disk_path, f'{disk_gb}G']
            stdout, stderr, rc = await KVMManager.execute_command(resize_cmd)
            
            if rc != 0:
                raise Exception(f"Failed to resize disk: {stderr}")
        
        # Generate MAC address
        mac_address = KVMManager.generate_mac_address()
        
        # Build virt-install command
        virt_install_cmd = [
            'virt-install',
            '--connect', 'qemu:///system',
            '--name', vm_name,
            '--uuid', vm_uuid,
            '--memory', str(ram_mb),
            '--vcpus', str(cpu_cores),
            '--disk', f'path={disk_path},format=qcow2,bus=virtio',
            '--disk', f'path={cloud_init_iso},device=cdrom',
            '--network', f'network={KVM_NETWORK},mac={mac_address},model=virtio',
            '--graphics', 'vnc,listen=0.0.0.0,port=auto',
            '--video', 'qxl',
            '--channel', 'unix,target_type=virtio,name=org.qemu.guest_agent.0',
            '--noautoconsole',
            '--os-variant', 'ubuntu20.04' if 'ubuntu' in os_id else 'debian10',
            '--import',
            '--boot', 'hd,menu=on'
        ]
        
        if os_config.get('virtio'):
            virt_install_cmd.extend(['--virtio'])
        
        logger.info(f"Creating VM with command: {' '.join(virt_install_cmd)}")
        
        # Execute virt-install
        stdout, stderr, rc = await KVMManager.execute_command(virt_install_cmd, timeout=120)
        
        if rc != 0:
            logger.error(f"Failed to create VM: {stderr}")
            
            # Cleanup on failure
            cleanup_cmds = [
                ['virsh', 'undefine', vm_name, '--remove-all-storage'],
                ['rm', '-f', disk_path],
                ['rm', '-f', cloud_init_iso]
            ]
            
            for cmd in cleanup_cmds:
                await KVMManager.execute_command(cmd)
            
            raise Exception(f"VM creation failed: {stderr}")
        
        # Wait for VM to get IP
        ip_address = await KVMManager.get_vm_ip(vm_name)
        
        return {
            'vm_name': vm_name,
            'vm_uuid': vm_uuid,
            'mac_address': mac_address,
            'ip_address': ip_address,
            'disk_path': disk_path,
            'cloud_init_iso': cloud_init_iso,
            'root_password': root_password,
            'ssh_password': ssh_password,
            'status': 'running'
        }
    
    @staticmethod
    async def get_vm_ip(vm_name: str, timeout: int = 60) -> str:
        """Get VM IP address by parsing virsh domifaddr"""
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            cmd = ['virsh', 'domifaddr', vm_name]
            stdout, stderr, rc = await KVMManager.execute_command(cmd)
            
            if rc == 0 and stdout:
                lines = stdout.split('\n')
                for line in lines:
                    if 'ipv4' in line.lower():
                        parts = line.split()
                        if len(parts) >= 4:
                            return parts[3].split('/')[0]
            
            await asyncio.sleep(5)
        
        return "Pending..."
    
    @staticmethod
    async def control_vm(vm_name: str, action: str) -> bool:
        """Control VM state (start, stop, reboot, destroy)"""
        valid_actions = ['start', 'shutdown', 'reboot', 'destroy', 'reset', 'suspend', 'resume']
        
        if action not in valid_actions:
            raise Exception(f"Invalid action. Must be one of: {', '.join(valid_actions)}")
        
        cmd = ['virsh', action, vm_name]
        
        if action == 'destroy':
            cmd.append('--graceful')
        
        stdout, stderr, rc = await KVMManager.execute_command(cmd, timeout=30)
        
        if rc != 0:
            logger.error(f"Failed to {action} VM {vm_name}: {stderr}")
            return False
        
        logger.info(f"Successfully {action} VM {vm_name}")
        return True
    
    @staticmethod
    async def get_vm_info(vm_name: str) -> Dict:
        """Get detailed VM information"""
        info = {'name': vm_name, 'exists': False}
        
        # Check if VM exists
        cmd = ['virsh', 'dominfo', vm_name]
        stdout, stderr, rc = await KVMManager.execute_command(cmd)
        
        if rc != 0:
            return info
        
        info['exists'] = True
        
        # Parse dominfo output
        for line in stdout.split('\n'):
            if 'State:' in line:
                info['state'] = line.split(':')[1].strip().lower()
            elif 'CPU(s):' in line:
                info['cpu'] = line.split(':')[1].strip()
            elif 'Max memory:' in line:
                info['max_memory'] = line.split(':')[1].strip()
            elif 'Used memory:' in line:
                info['used_memory'] = line.split(':')[1].strip()
        
        # Get IP address
        info['ip'] = await KVMManager.get_vm_ip(vm_name)
        
        # Get UUID
        cmd = ['virsh', 'domuuid', vm_name]
        stdout, stderr, rc = await KVMManager.execute_command(cmd)
        if rc == 0:
            info['uuid'] = stdout.strip()
        
        # Get disk info
        cmd = ['virsh', 'domblklist', vm_name]
        stdout, stderr, rc = await KVMManager.execute_command(cmd)
        if rc == 0:
            info['disks'] = stdout
        
        # Get network info
        cmd = ['virsh', 'domiflist', vm_name]
        stdout, stderr, rc = await KVMManager.execute_command(cmd)
        if rc == 0:
            info['network'] = stdout
        
        return info
    
    @staticmethod
    async def get_vm_stats(vm_name: str) -> Dict:
        """Get real-time VM statistics"""
        stats = {}
        
        # Get CPU usage via virsh domstats
        cmd = ['virsh', 'domstats', vm_name]
        stdout, stderr, rc = await KVMManager.execute_command(cmd)
        
        if rc == 0:
            for line in stdout.split('\n'):
                if 'cpu.time' in line:
                    stats['cpu_time'] = line.split('=')[1].strip()
                elif 'balloon.current' in line:
                    stats['ram_used'] = int(line.split('=')[1].strip()) // 1024  # Convert to MB
                elif 'balloon.maximum' in line:
                    stats['ram_total'] = int(line.split('=')[1].strip()) // 1024  # Convert to MB
        
        # Get disk usage
        cmd = ['virsh', 'domblkinfo', vm_name, 'vda']
        stdout, stderr, rc = await KVMManager.execute_command(cmd)
        
        if rc == 0:
            for line in stdout.split('\n'):
                if 'Capacity:' in line:
                    stats['disk_total'] = line.split(':')[1].strip()
                elif 'Allocation:' in line:
                    stats['disk_used'] = line.split(':')[1].strip()
        
        # Calculate percentages
        if 'ram_used' in stats and 'ram_total' in stats and stats['ram_total'] > 0:
            stats['ram_percent'] = (stats['ram_used'] / stats['ram_total']) * 100
        
        return stats
    
    @staticmethod
    async def list_all_vms() -> List[str]:
        """List all KVM VMs"""
        cmd = ['virsh', 'list', '--all']
        stdout, stderr, rc = await KVMManager.execute_command(cmd)
        
        if rc != 0:
            return []
        
        vms = []
        lines = stdout.split('\n')
        
        for line in lines[2:]:  # Skip header
            if line.strip():
                parts = line.split()
                if len(parts) >= 3:
                    vms.append({
                        'id': parts[0],
                        'name': parts[1],
                        'state': parts[2]
                    })
        
        return vms
    
    @staticmethod
    async def resize_vm_disk(vm_name: str, new_size_gb: int) -> bool:
        """Resize VM disk"""
        # Get disk path
        cmd = ['virsh', 'domblklist', vm_name]
        stdout, stderr, rc = await KVMManager.execute_command(cmd)
        
        if rc != 0:
            return False
        
        disk_path = None
        for line in stdout.split('\n'):
            if 'vda' in line or 'sda' in line:
                parts = line.split()
                if len(parts) >= 2:
                    disk_path = parts[1]
                    break
        
        if not disk_path:
            return False
        
        # Resize disk
        resize_cmd = ['qemu-img', 'resize', disk_path, f'{new_size_gb}G']
        stdout, stderr, rc = await KVMManager.execute_command(resize_cmd)
        
        if rc != 0:
            logger.error(f"Failed to resize disk: {stderr}")
            return False
        
        logger.info(f"Resized disk {disk_path} to {new_size_gb}GB")
        return True
    
    @staticmethod
    async def delete_vm(vm_name: str) -> bool:
        """Completely delete a VM"""
        # Undefine VM and remove storage
        cmd = ['virsh', 'undefine', vm_name, '--remove-all-storage']
        stdout, stderr, rc = await KVMManager.execute_command(cmd)
        
        if rc != 0:
            # Try force destroy first
            await KVMManager.execute_command(['virsh', 'destroy', vm_name])
            stdout, stderr, rc = await KVMManager.execute_command(cmd)
            
            if rc != 0:
                logger.error(f"Failed to delete VM {vm_name}: {stderr}")
                return False
        
        # Remove cloud-init ISO
        iso_path = f"{CLOUD_INIT_DIR}/{vm_name}-cloud-init.iso"
        if os.path.exists(iso_path):
            os.remove(iso_path)
        
        logger.info(f"Successfully deleted VM {vm_name}")
        return True
    
    @staticmethod
    async def clone_vm(source_vm: str, new_name: str) -> bool:
        """Clone a VM"""
        cmd = ['virt-clone', '--original', source_vm, '--name', new_name, '--auto-clone']
        stdout, stderr, rc = await KVMManager.execute_command(cmd, timeout=60)
        
        if rc != 0:
            logger.error(f"Failed to clone VM: {stderr}")
            return False
        
        logger.info(f"Successfully cloned {source_vm} to {new_name}")
        return True
    
    @staticmethod
    async def execute_in_vm(vm_name: str, command: str) -> Tuple[str, str]:
        """Execute command inside VM via guest agent"""
        cmd = ['virsh', 'qemu-agent-command', vm_name, 
               f'{{"execute":"guest-exec","arguments":{{"path":"/bin/bash","arg":["-c","{command}"],"capture-output":true}}}}']
        
        stdout, stderr, rc = await KVMManager.execute_command(cmd)
        
        if rc != 0:
            return "", f"Failed to execute command: {stderr}"
        
        try:
            result = json.loads(stdout)
            pid = result['return']['pid']
            
            # Wait and get output
            await asyncio.sleep(2)
            
            cmd2 = ['virsh', 'qemu-agent-command', vm_name,
                   f'{{"execute":"guest-exec-status","arguments":{{"pid":{pid}}}}}']
            
            stdout2, stderr2, rc2 = await KVMManager.execute_command(cmd2)
            
            if rc2 == 0:
                result2 = json.loads(stdout2)
                if 'return' in result2 and 'out-data' in result2['return']:
                    import base64
                    output = base64.b64decode(result2['return']['out-data']).decode('utf-8')
                    exitcode = result2['return'].get('exitcode', 0)
                    return output, f"Exit code: {exitcode}"
            
            return "", "No output received"
        except Exception as e:
            return "", f"Error parsing output: {e}"

# ==================== DATABASE MANAGER ====================
class DatabaseManager:
    """Database operations for RDX KVM"""
    
    @staticmethod
    def get_connection():
        """Get database connection"""
        conn = sqlite3.connect('rdx_kvm.db')
        conn.row_factory = sqlite3.Row
        return conn
    
    # VM Management
    @staticmethod
    def add_vm(vm_data: Dict) -> int:
        """Add VM to database"""
        conn = DatabaseManager.get_connection()
        c = conn.cursor()
        
        c.execute('''
            INSERT INTO virtual_machines 
            (user_id, vm_name, vm_uuid, display_name, plan, ram_mb, cpu_cores, 
             disk_gb, os_id, ip_address, status, ssh_password, root_password)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            vm_data['user_id'],
            vm_data['vm_name'],
            vm_data['vm_uuid'],
            vm_data.get('display_name', vm_data['vm_name']),
            vm_data['plan'],
            vm_data['ram_mb'],
            vm_data['cpu_cores'],
            vm_data['disk_gb'],
            vm_data['os_id'],
            vm_data.get('ip_address', ''),
            vm_data.get('status', 'stopped'),
            vm_data.get('ssh_password', ''),
            vm_data.get('root_password', '')
        ))
        
        vm_id = c.lastrowid
        conn.commit()
        conn.close()
        
        return vm_id
    
    @staticmethod
    def get_vm_by_name(vm_name: str) -> Optional[Dict]:
        """Get VM by name"""
        conn = DatabaseManager.get_connection()
        c = conn.cursor()
        
        c.execute('SELECT * FROM virtual_machines WHERE vm_name = ?', (vm_name,))
        row = c.fetchone()
        conn.close()
        
        if row:
            return dict(row)
        return None
    
    @staticmethod
    def get_user_vms(user_id: str) -> List[Dict]:
        """Get all VMs for a user"""
        conn = DatabaseManager.get_connection()
        c = conn.cursor()
        
        c.execute('SELECT * FROM virtual_machines WHERE user_id = ?', (user_id,))
        rows = c.fetchall()
        conn.close()
        
        return [dict(row) for row in rows]
    
    @staticmethod
    def update_vm_status(vm_name: str, status: str):
        """Update VM status"""
        conn = DatabaseManager.get_connection()
        c = conn.cursor()
        
        c.execute('UPDATE virtual_machines SET status = ? WHERE vm_name = ?', 
                 (status, vm_name))
        conn.commit()
        conn.close()
    
    @staticmethod
    def update_vm_ip(vm_name: str, ip_address: str):
        """Update VM IP address"""
        conn = DatabaseManager.get_connection()
        c = conn.cursor()
        
        c.execute('UPDATE virtual_machines SET ip_address = ? WHERE vm_name = ?', 
                 (ip_address, vm_name))
        conn.commit()
        conn.close()
    
    @staticmethod
    def delete_vm(vm_name: str) -> bool:
        """Delete VM from database"""
        conn = DatabaseManager.get_connection()
        c = conn.cursor()
        
        c.execute('DELETE FROM virtual_machines WHERE vm_name = ?', (vm_name,))
        affected = c.rowcount
        conn.commit()
        conn.close()
        
        return affected > 0
    
    # Admin Management
    @staticmethod
    def is_admin(user_id: str) -> bool:
        """Check if user is admin"""
        conn = DatabaseManager.get_connection()
        c = conn.cursor()
        
        c.execute('SELECT 1 FROM admins WHERE user_id = ?', (user_id,))
        result = c.fetchone()
        conn.close()
        
        return result is not None
    
    @staticmethod
    def add_admin(user_id: str, added_by: str):
        """Add admin"""
        conn = DatabaseManager.get_connection()
        c = conn.cursor()
        
        c.execute('INSERT OR IGNORE INTO admins (user_id, added_by) VALUES (?, ?)',
                 (user_id, added_by))
        conn.commit()
        conn.close()
    
    @staticmethod
    def remove_admin(user_id: str):
        """Remove admin"""
        conn = DatabaseManager.get_connection()
        c = conn.cursor()
        
        c.execute('DELETE FROM admins WHERE user_id = ? AND user_id != ?',
                 (user_id, str(MAIN_ADMIN_ID)))
        conn.commit()
        conn.close()
    
    @staticmethod
    def get_all_admins() -> List[str]:
        """Get all admins"""
        conn = DatabaseManager.get_connection()
        c = conn.cursor()
        
        c.execute('SELECT user_id FROM admins')
        rows = c.fetchall()
        conn.close()
        
        return [row['user_id'] for row in rows]
    
    # Statistics
    @staticmethod
    def record_vm_stats(vm_uuid: str, stats: Dict):
        """Record VM statistics"""
        conn = DatabaseManager.get_connection()
        c = conn.cursor()
        
        c.execute('''
            INSERT INTO vm_stats 
            (vm_uuid, cpu_usage, ram_usage_mb, disk_usage_gb, network_rx_mb, network_tx_mb)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            vm_uuid,
            stats.get('cpu_percent'),
            stats.get('ram_used'),
            stats.get('disk_used_gb'),
            stats.get('network_rx'),
            stats.get('network_tx')
        ))
        
        conn.commit()
        conn.close()
    
    # Settings
    @staticmethod
    def get_setting(key: str, default: Any = None) -> Any:
        """Get setting value"""
        conn = DatabaseManager.get_connection()
        c = conn.cursor()
        
        c.execute('SELECT value FROM settings WHERE key = ?', (key,))
        row = c.fetchone()
        conn.close()
        
        if row:
            return row['value']
        return default
    
    @staticmethod
    def set_setting(key: str, value: str):
        """Update setting"""
        conn = DatabaseManager.get_connection()
        c = conn.cursor()
        
        c.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)',
                 (key, value))
        conn.commit()
        conn.close()

# ==================== DISCORD BOT ====================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

# Initialize database
init_database()

# ==================== HELPER FUNCTIONS ====================
def create_embed(title: str, description: str = "", color: int = BRAND_COLOR) -> discord.Embed:
    """Create professional RDX branded embed"""
    embed = discord.Embed(
        title=f"⚡ {BRAND_NAME} - {title}",
        description=description,
        color=color,
        timestamp=datetime.now()
    )
    embed.set_footer(
        text=f"Powered by RDX KVM Infrastructure • {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        icon_url=LOGO_URL
    )
    return embed

def add_field(embed: discord.Embed, name: str, value: str, inline: bool = False):
    """Add field to embed with proper formatting"""
    embed.add_field(name=f"▸ {name}", value=value, inline=inline)
    return embed

def create_success_embed(title: str, description: str = ""):
    """Create success embed"""
    return create_embed(title, description, 0x00FF88)

def create_error_embed(title: str, description: str = ""):
    """Create error embed"""
    return create_embed(title, description, 0xFF3366)

def create_warning_embed(title: str, description: str = ""):
    """Create warning embed"""
    return create_embed(title, description, 0xFFAA00)

def create_info_embed(title: str, description: str = ""):
    """Create info embed"""
    return create_embed(title, description, 0x00CCFF)

# Permission checks
def is_admin():
    async def predicate(ctx):
        user_id = str(ctx.author.id)
        if user_id == str(MAIN_ADMIN_ID) or DatabaseManager.is_admin(user_id):
            return True
        raise commands.CheckFailure(
            f"🔒 You need admin permissions to use this command. "
            f"Contact {BRAND_NAME} support."
        )
    return commands.check(predicate)

def is_main_admin():
    async def predicate(ctx):
        if str(ctx.author.id) == str(MAIN_ADMIN_ID):
            return True
        raise commands.CheckFailure("👑 Only the main admin can use this command.")
    return commands.check(predicate)

# ==================== BOT EVENTS ====================
@bot.event
async def on_ready():
    """Bot startup"""
    logger.info(f'{bot.user} has connected to Discord!')
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name=f"{BRAND_NAME} VPS Manager"
        )
    )
    
    # Start monitoring task
    monitor_vms.start()
    
    logger.info(f"{BRAND_NAME} Bot is ready!")

@bot.event
async def on_command_error(ctx, error):
    """Handle command errors"""
    if isinstance(error, commands.CommandNotFound):
        return
    
    error_msg = str(error) if str(error) else "An unknown error occurred"
    
    if isinstance(error, commands.MissingRequiredArgument):
        embed = create_error_embed("Missing Argument", 
                                  "Please check command usage with `!help`.")
    elif isinstance(error, commands.BadArgument):
        embed = create_error_embed("Invalid Argument", 
                                  "Please check your input and try again.")
    elif isinstance(error, commands.CheckFailure):
        embed = create_error_embed("Access Denied", error_msg)
    else:
        logger.error(f"Command error: {error}")
        embed = create_error_embed("System Error", 
                                  "An unexpected error occurred. RDX support has been notified.")
    
    await ctx.send(embed=embed)

# ==================== MONITORING TASK ====================
@tasks.loop(minutes=5)
async def monitor_vms():
    """Monitor all VMs and collect statistics"""
    logger.info("Running VM monitoring task...")
    
    # Get all VMs from database
    conn = DatabaseManager.get_connection()
    c = conn.cursor()
    c.execute('SELECT vm_name, vm_uuid, status FROM virtual_machines WHERE status = "running"')
    vms = c.fetchall()
    conn.close()
    
    for vm in vms:
        try:
            # Get VM stats
            stats = await KVMManager.get_vm_stats(vm['vm_name'])
            
            if stats:
                # Record stats
                DatabaseManager.record_vm_stats(vm['vm_uuid'], stats)
                
                # Check for high resource usage
                ram_percent = stats.get('ram_percent', 0)
                cpu_time = stats.get('cpu_time', '0')
                
                # Auto-suspend if abuse detected (optional)
                cpu_threshold = int(DatabaseManager.get_setting('cpu_threshold', 85))
                ram_threshold = int(DatabaseManager.get_setting('ram_threshold', 90))
                
                if ram_percent > ram_threshold:
                    logger.warning(f"High RAM usage detected on {vm['vm_name']}: {ram_percent}%")
                    # Could implement auto-suspend here
                
        except Exception as e:
            logger.error(f"Error monitoring VM {vm['vm_name']}: {e}")

# ==================== BOT COMMANDS ====================

# ==================== USER COMMANDS ====================
@bot.command(name='ping')
async def ping(ctx):
    """Check bot latency"""
    latency = round(bot.latency * 1000)
    embed = create_success_embed("Pong!", f"{BRAND_NAME} Bot latency: {latency}ms")
    await ctx.send(embed=embed)

@bot.command(name='myvps')
async def my_vps(ctx):
    """List user's VMs"""
    user_id = str(ctx.author.id)
    vms = DatabaseManager.get_user_vms(user_id)
    
    if not vms:
        embed = create_error_embed(
            "No VPS Found",
            f"You don't have any {BRAND_NAME} Virtual Machines."
        )
        add_field(embed, "Get Started", 
                 "Contact an admin to create your first KVM VPS!", False)
        await ctx.send(embed=embed)
        return
    
    embed = create_info_embed("My Virtual Machines", 
                             f"You have {len(vms)} {BRAND_NAME} VPS")
    
    for i, vm in enumerate(vms, 1):
        status = vm['status'].upper()
        if vm['suspended']:
            status += " ⚠️ SUSPENDED"
        if vm['whitelisted']:
            status += " ✅ WHITELISTED"
        
        vm_info = (
            f"**Name:** `{vm['vm_name']}`\n"
            f"**Plan:** {vm['plan'].title()}\n"
            f"**Status:** {status}\n"
            f"**IP:** {vm['ip_address'] or 'Pending...'}\n"
            f"**OS:** {vm['os_id']}\n"
            f"**Resources:** {vm['ram_mb']}MB RAM / {vm['cpu_cores']} CPU / {vm['disk_gb']}GB Disk"
        )
        
        add_field(embed, f"VPS #{i}", vm_info, False)
    
    add_field(embed, "Management", 
             "Use `!manage <vm_name>` to control your VPS", False)
    
    await ctx.send(embed=embed)

@bot.command(name='vpsinfo')
async def vps_info(ctx, vm_name: str):
    """Get detailed VM information"""
    vm = DatabaseManager.get_vm_by_name(vm_name)
    
    if not vm:
        embed = create_error_embed("VPS Not Found", 
                                  f"No VM found with name: `{vm_name}`")
        await ctx.send(embed=embed)
        return
    
    # Get live VM info
    try:
        vm_info = await KVMManager.get_vm_info(vm_name)
        vm_stats = await KVMManager.get_vm_stats(vm_name)
    except Exception as e:
        vm_info = {}
        vm_stats = {}
        logger.error(f"Error getting VM info: {e}")
    
    embed = create_info_embed(f"VPS Details - {vm_name}")
    
    # Basic info
    add_field(embed, "📋 Basic Information", 
             f"**Owner:** <@{vm['user_id']}>\n"
             f"**Display Name:** {vm['display_name']}\n"
             f"**Plan:** {vm['plan'].title()}\n"
             f"**OS:** {vm['os_id']}\n"
             f"**Created:** {vm['created_at']}", False)
    
    # Resources
    add_field(embed, "💻 Allocated Resources", 
             f"**RAM:** {vm['ram_mb']} MB\n"
             f"**CPU:** {vm['cpu_cores']} Cores\n"
             f"**Disk:** {vm['disk_gb']} GB", True)
    
    # Status
    status_text = vm['status'].upper()
    if vm['suspended']:
        status_text += " ⚠️ SUSPENDED"
    if vm['whitelisted']:
        status_text += " ✅ WHITELISTED"
    
    add_field(embed, "📊 Status", 
             f"**Status:** {status_text}\n"
             f"**IP Address:** {vm['ip_address'] or 'Pending...'}\n"
             f"**Expires:** {vm['expires_at'] or 'Never'}", True)
    
    # Live stats
    if vm_stats:
        stats_text = []
        if 'ram_percent' in vm_stats:
            stats_text.append(f"**RAM Usage:** {vm_stats['ram_percent']:.1f}%")
        if 'cpu_time' in vm_stats:
            stats_text.append(f"**CPU Time:** {vm_stats['cpu_time']}")
        if 'disk_used' in vm_stats:
            stats_text.append(f"**Disk Used:** {vm_stats['disk_used']}")
        
        if stats_text:
            add_field(embed, "📈 Live Statistics", "\n".join(stats_text), False)
    
    # SSH Access
    if vm['ssh_password']:
        ssh_info = (
            f"**Username:** ubuntu\n"
            f"**Password:** ||{vm['ssh_password']}||\n"
            f"**IP:** {vm['ip_address'] or 'Waiting for IP...'}\n"
            f"**Command:** `ssh ubuntu@{vm['ip_address']}`"
        )
        add_field(embed, "🔐 SSH Access", ssh_info, False)
    
    await ctx.send(embed=embed)

# ==================== VM MANAGEMENT ====================
class ManageView(discord.ui.View):
    """Interactive VM management view"""
    
    def __init__(self, vm_name: str, user_id: str, is_admin: bool = False):
        super().__init__(timeout=300)
        self.vm_name = vm_name
        self.user_id = user_id
        self.is_admin = is_admin
        
        # Get VM info
        self.vm = DatabaseManager.get_vm_by_name(vm_name)
        
        if not self.vm:
            self.disable_all_items()
            return
        
        # Add buttons based on VM status and permissions
        self.add_buttons()
    
    def add_buttons(self):
        """Add appropriate management buttons"""
        # Start/Stop buttons
        if self.vm['status'] == 'running':
            stop_btn = discord.ui.Button(label="⏹ Stop", style=discord.ButtonStyle.danger)
            stop_btn.callback = self.stop_vm
            self.add_item(stop_btn)
            
            reboot_btn = discord.ui.Button(label="🔄 Reboot", style=discord.ButtonStyle.secondary)
            reboot_btn.callback = self.reboot_vm
            self.add_item(reboot_btn)
            
            console_btn = discord.ui.Button(label="🖥 Console", style=discord.ButtonStyle.primary)
            console_btn.callback = self.show_console
            self.add_item(console_btn)
        else:
            start_btn = discord.ui.Button(label="▶ Start", style=discord.ButtonStyle.success)
            start_btn.callback = self.start_vm
            self.add_item(start_btn)
        
        # SSH button
        ssh_btn = discord.ui.Button(label="🔑 SSH Info", style=discord.ButtonStyle.primary)
        ssh_btn.callback = self.show_ssh
        self.add_item(ssh_btn)
        
        # Stats button
        stats_btn = discord.ui.Button(label="📊 Stats", style=discord.ButtonStyle.secondary)
        stats_btn.callback = self.show_stats
        self.add_item(stats_btn)
        
        # Admin-only buttons
        if self.is_admin:
            reinstall_btn = discord.ui.Button(label="🔄 Reinstall", style=discord.ButtonStyle.danger)
            reinstall_btn.callback = self.reinstall_vm
            self.add_item(reinstall_btn)
            
            delete_btn = discord.ui.Button(label="🗑 Delete", style=discord.ButtonStyle.danger)
            delete_btn.callback = self.delete_vm
            self.add_item(delete_btn)
    
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Check if user can interact with this view"""
        if str(interaction.user.id) == self.user_id or self.is_admin:
            return True
        
        await interaction.response.send_message(
            embed=create_error_embed("Access Denied", "This is not your VPS!"),
            ephemeral=True
        )
        return False
    
    async def start_vm(self, interaction: discord.Interaction):
        """Start VM"""
        await interaction.response.defer(ephemeral=True)
        
        try:
            success = await KVMManager.control_vm(self.vm_name, 'start')
            
            if success:
                DatabaseManager.update_vm_status(self.vm_name, 'running')
                
                # Get new IP
                ip = await KVMManager.get_vm_ip(self.vm_name)
                if ip and ip != "Pending...":
                    DatabaseManager.update_vm_ip(self.vm_name, ip)
                
                embed = create_success_embed(
                    "VPS Started",
                    f"KVM Virtual Machine `{self.vm_name}` is now running!"
                )
            else:
                embed = create_error_embed("Start Failed", 
                                          "Failed to start the VPS. Check logs.")
        
        except Exception as e:
            embed = create_error_embed("Error", str(e))
        
        await interaction.followup.send(embed=embed, ephemeral=True)
        await self.update_message(interaction)
    
    async def stop_vm(self, interaction: discord.Interaction):
        """Stop VM"""
        await interaction.response.defer(ephemeral=True)
        
        try:
            success = await KVMManager.control_vm(self.vm_name, 'shutdown')
            
            if success:
                DatabaseManager.update_vm_status(self.vm_name, 'stopped')
                embed = create_success_embed(
                    "VPS Stopped",
                    f"KVM Virtual Machine `{self.vm_name}` has been stopped gracefully."
                )
            else:
                embed = create_error_embed("Stop Failed", 
                                          "Failed to stop the VPS. Trying force stop...")
                
                # Try force stop
                success = await KVMManager.control_vm(self.vm_name, 'destroy')
                if success:
                    DatabaseManager.update_vm_status(self.vm_name, 'stopped')
                    embed = create_success_embed(
                        "VPS Force Stopped",
                        f"KVM Virtual Machine `{self.vm_name}` has been force stopped."
                    )
        
        except Exception as e:
            embed = create_error_embed("Error", str(e))
        
        await interaction.followup.send(embed=embed, ephemeral=True)
        await self.update_message(interaction)
    
    async def reboot_vm(self, interaction: discord.Interaction):
        """Reboot VM"""
        await interaction.response.defer(ephemeral=True)
        
        try:
            success = await KVMManager.control_vm(self.vm_name, 'reboot')
            
            if success:
                embed = create_success_embed(
                    "VPS Rebooting",
                    f"KVM Virtual Machine `{self.vm_name}` is now rebooting..."
                )
            else:
                embed = create_error_embed("Reboot Failed", 
                                          "Failed to reboot the VPS.")
        
        except Exception as e:
            embed = create_error_embed("Error", str(e))
        
        await interaction.followup.send(embed=embed, ephemeral=True)
    
    async def show_console(self, interaction: discord.Interaction):
        """Show console/NoVNC information"""
        await interaction.response.defer(ephemeral=True)
        
        # Get VNC port
        try:
            cmd = ['virsh', 'vncdisplay', self.vm_name]
            stdout, stderr, rc = await KVMManager.execute_command(cmd)
            
            if rc == 0 and stdout:
                vnc_port = stdout.strip().split(':')[1]
                vnc_url = f"vnc://your-server-ip:{vnc_port}"
                
                embed = create_info_embed(
                    "VPS Console Access",
                    f"**VNC Console:**\n```{vnc_url}```\n\n"
                    f"Use a VNC client to connect.\n"
                    f"**Username:** ubuntu\n**Password:** Use SSH password"
                )
            else:
                embed = create_info_embed(
                    "Console Access",
                    "**SSH Access Only**\n\n"
                    f"Use SSH to connect:\n```ssh ubuntu@{self.vm['ip_address']}```"
                )
        
        except Exception as e:
            embed = create_error_embed("Error", f"Failed to get console info: {e}")
        
        await interaction.followup.send(embed=embed, ephemeral=True)
    
    async def show_ssh(self, interaction: discord.Interaction):
        """Show SSH connection information"""
        await interaction.response.defer(ephemeral=True)
        
        embed = create_info_embed(
            "SSH Access Information",
            f"**KVM Virtual Machine:** `{self.vm_name}`\n"
            f"**IP Address:** {self.vm['ip_address'] or 'Waiting for IP...'}\n\n"
            f"**SSH Command:**\n```bash\nssh ubuntu@{self.vm['ip_address']}\n```\n"
            f"**Username:** `ubuntu`\n"
            f"**Password:** ||{self.vm['ssh_password']}||\n\n"
            f"*Password is hidden. Click to reveal.*"
        )
        
        await interaction.followup.send(embed=embed, ephemeral=True)
    
    async def show_stats(self, interaction: discord.Interaction):
        """Show live VM statistics"""
        await interaction.response.defer(ephemeral=True)
        
        try:
            stats = await KVMManager.get_vm_stats(self.vm_name)
            
            if stats:
                embed = create_info_embed(
                    f"Live Statistics - {self.vm_name}",
                    f"**CPU Usage:** {stats.get('cpu_time', 'N/A')}\n"
                    f"**RAM Usage:** {stats.get('ram_used', 'N/A')} MB / "
                    f"{stats.get('ram_total', 'N/A')} MB "
                    f"({stats.get('ram_percent', 0):.1f}%)\n"
                    f"**Disk Usage:** {stats.get('disk_used', 'N/A')} / "
                    f"{stats.get('disk_total', 'N/A')}\n"
                )
            else:
                embed = create_info_embed(
                    "Statistics",
                    "No live statistics available. VM might be stopped."
                )
        
        except Exception as e:
            embed = create_error_embed("Error", f"Failed to get statistics: {e}")
        
        await interaction.followup.send(embed=embed, ephemeral=True)
    
    async def reinstall_vm(self, interaction: discord.Interaction):
        """Reinstall VM OS (admin only)"""
        if not self.is_admin:
            await interaction.response.send_message(
                embed=create_error_embed("Access Denied", "Admin only!"),
                ephemeral=True
            )
            return
        
        await interaction.response.defer(ephemeral=True)
        
        embed = create_warning_embed(
            "⚠️ Reinstall VPS",
            f"This will **COMPLETELY WIPE** `{self.vm_name}` and reinstall the OS.\n\n"
            f"**ALL DATA WILL BE LOST!**\n\n"
            f"Are you sure you want to continue?"
        )
        
        class ConfirmReinstall(discord.ui.View):
            def __init__(self, parent_view):
                super().__init__(timeout=60)
                self.parent_view = parent_view
            
            @discord.ui.button(label="Confirm Reinstall", style=discord.ButtonStyle.danger)
            async def confirm(self, inter: discord.Interaction, button: discord.ui.Button):
                await inter.response.defer(ephemeral=True)
                
                # Stop VM first
                await KVMManager.control_vm(self.parent_view.vm_name, 'destroy')
                
                # Delete VM
                await KVMManager.delete_vm(self.parent_view.vm_name)
                
                # Recreate VM with same specs
                vm_data = {
                    'user_id': self.parent_view.vm['user_id'],
                    'plan': self.parent_view.vm['plan'],
                    'ram_mb': self.parent_view.vm['ram_mb'],
                    'cpu_cores': self.parent_view.vm['cpu_cores'],
                    'disk_gb': self.parent_view.vm['disk_gb'],
                    'os_id': self.parent_view.vm['os_id']
                }
                
                try:
                    new_vm = await KVMManager.create_vm(
                        self.parent_view.vm_name,
                        vm_data['ram_mb'],
                        vm_data['cpu_cores'],
                        vm_data['disk_gb'],
                        vm_data['os_id']
                    )
                    
                    # Update database
                    DatabaseManager.update_vm_status(self.parent_view.vm_name, 'running')
                    DatabaseManager.update_vm_ip(self.parent_view.vm_name, new_vm['ip_address'])
                    
                    embed = create_success_embed(
                        "VPS Reinstalled",
                        f"KVM Virtual Machine `{self.parent_view.vm_name}` has been successfully reinstalled!\n\n"
                        f"**New IP:** {new_vm['ip_address']}\n"
                        f"**New SSH Password:** ||{new_vm['ssh_password']}||"
                    )
                
                except Exception as e:
                    embed = create_error_embed("Reinstall Failed", str(e))
                
                await inter.followup.send(embed=embed, ephemeral=True)
            
            @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
            async def cancel(self, inter: discord.Interaction, button: discord.ui.Button):
                await inter.response.edit_message(
                    embed=create_info_embed("Cancelled", "Reinstall cancelled."),
                    view=None
                )
        
        await interaction.followup.send(embed=embed, view=ConfirmReinstall(self), ephemeral=True)
    
    async def delete_vm(self, interaction: discord.Interaction):
        """Delete VM (admin only)"""
        if not self.is_admin:
            await interaction.response.send_message(
                embed=create_error_embed("Access Denied", "Admin only!"),
                ephemeral=True
            )
            return
        
        await interaction.response.defer(ephemeral=True)
        
        embed = create_warning_embed(
            "⚠️ Delete VPS",
            f"This will **PERMANENTLY DELETE** `{self.vm_name}`.\n\n"
            f"**ALL DATA WILL BE LOST FOREVER!**\n\n"
            f"Are you absolutely sure?"
        )
        
        class ConfirmDelete(discord.ui.View):
            def __init__(self, parent_view):
                super().__init__(timeout=60)
                self.parent_view = parent_view
            
            @discord.ui.button(label="Delete Permanently", style=discord.ButtonStyle.danger)
            async def confirm(self, inter: discord.Interaction, button: discord.ui.Button):
                await inter.response.defer(ephemeral=True)
                
                try:
                    # Delete from KVM
                    success = await KVMManager.delete_vm(self.parent_view.vm_name)
                    
                    if success:
                        # Delete from database
                        DatabaseManager.delete_vm(self.parent_view.vm_name)
                        
                        embed = create_success_embed(
                            "VPS Deleted",
                            f"KVM Virtual Machine `{self.parent_view.vm_name}` has been permanently deleted."
                        )
                    else:
                        embed = create_error_embed("Delete Failed", 
                                                  "Failed to delete VPS. Check logs.")
                
                except Exception as e:
                    embed = create_error_embed("Error", str(e))
                
                await inter.followup.send(embed=embed, ephemeral=True)
                await inter.message.edit(view=None)
            
            @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
            async def cancel(self, inter: discord.Interaction, button: discord.ui.Button):
                await inter.response.edit_message(
                    embed=create_info_embed("Cancelled", "Deletion cancelled."),
                    view=None
                )
        
        await interaction.followup.send(embed=embed, view=ConfirmDelete(self), ephemeral=True)
    
    async def update_message(self, interaction: discord.Interaction):
        """Update the original message with new VM status"""
        try:
            # Refresh VM info
            self.vm = DatabaseManager.get_vm_by_name(self.vm_name)
            vm_info = await KVMManager.get_vm_info(self.vm_name)
            
            if self.vm:
                status = self.vm['status'].upper()
                if self.vm['suspended']:
                    status += " ⚠️ SUSPENDED"
                if self.vm['whitelisted']:
                    status += " ✅ WHITELISTED"
                
                embed = create_info_embed(
                    f"VPS Management - {self.vm_name}",
                    f"**Status:** {status}\n"
                    f"**IP:** {self.vm['ip_address'] or 'Pending...'}\n"
                    f"**Resources:** {self.vm['ram_mb']}MB RAM / {self.vm['cpu_cores']} CPU / {self.vm['disk_gb']}GB Disk"
                )
                
                # Recreate buttons
                self.clear_items()
                self.add_buttons()
                
                await interaction.message.edit(embed=embed, view=self)
        
        except Exception as e:
            logger.error(f"Error updating message: {e}")

@bot.command(name='manage')
async def manage_vps(ctx, vm_name: str):
    """Manage a specific VM"""
    vm = DatabaseManager.get_vm_by_name(vm_name)
    
    if not vm:
        embed = create_error_embed("VPS Not Found", 
                                  f"No VM found with name: `{vm_name}`")
        await ctx.send(embed=embed)
        return
    
    user_id = str(ctx.author.id)
    is_admin_user = user_id == str(MAIN_ADMIN_ID) or DatabaseManager.is_admin(user_id)
    
    # Check permission
    if vm['user_id'] != user_id and not is_admin_user:
        embed = create_error_embed("Access Denied", 
                                  "You don't have permission to manage this VPS.")
        await ctx.send(embed=embed)
        return
    
    # Create management view
    view = ManageView(vm_name, user_id, is_admin_user)
    
    status = vm['status'].upper()
    if vm['suspended']:
        status += " ⚠️ SUSPENDED"
    if vm['whitelisted']:
        status += " ✅ WHITELISTED"
    
    embed = create_info_embed(
        f"VPS Management - {vm_name}",
        f"**Owner:** <@{vm['user_id']}>\n"
        f"**Status:** {status}\n"
        f"**IP:** {vm['ip_address'] or 'Pending...'}\n"
        f"**Resources:** {vm['ram_mb']}MB RAM / {vm['cpu_cores']} CPU / {vm['disk_gb']}GB Disk\n\n"
        f"Use the buttons below to manage this KVM Virtual Machine."
    )
    
    await ctx.send(embed=embed, view=view)

# ==================== ADMIN COMMANDS ====================
@bot.command(name='create')
@is_admin()
async def create_vps(ctx, plan: str, user: discord.Member, os_id: str = "ubuntu-22.04"):
    """Create a new KVM VPS for a user"""
    
    # Validate plan
    if plan not in VM_PLANS:
        valid_plans = ", ".join(VM_PLANS.keys())
        embed = create_error_embed(
            "Invalid Plan",
            f"Valid plans are: {valid_plans}\n"
            f"Example: `!create basic @user ubuntu-22.04`"
        )
        await ctx.send(embed=embed)
        return
    
    # Validate OS
    valid_os = [os['id'] for os in OS_OPTIONS]
    if os_id not in valid_os:
        valid_os_str = ", ".join(valid_os)
        embed = create_error_embed(
            "Invalid OS",
            f"Valid OS options: {valid_os_str}"
        )
        await ctx.send(embed=embed)
        return
    
    # Get plan details
    plan_details = VM_PLANS[plan]
    
    # Generate VM name
    vm_name = KVMManager.generate_vm_name(str(user.id))
    
    # Create VM
    try:
        embed = create_info_embed(
            "Creating KVM Virtual Machine",
            f"**User:** {user.mention}\n"
            f"**Plan:** {plan.title()}\n"
            f"**OS:** {os_id}\n"
            f"**Resources:** {plan_details['ram']}MB RAM / {plan_details['cpu']} CPU / {plan_details['disk']}GB Disk\n\n"
            f"Creating VM `{vm_name}`... This may take 2-3 minutes."
        )
        
        msg = await ctx.send(embed=embed)
        
        # Create the VM
        vm_data = await KVMManager.create_vm(
            vm_name=vm_name,
            ram_mb=plan_details['ram'],
            cpu_cores=plan_details['cpu'],
            disk_gb=plan_details['disk'],
            os_id=os_id,
            hostname=vm_name
        )
        
        # Save to database
        db_vm_data = {
            'user_id': str(user.id),
            'vm_name': vm_name,
            'vm_uuid': vm_data['vm_uuid'],
            'plan': plan,
            'ram_mb': plan_details['ram'],
            'cpu_cores': plan_details['cpu'],
            'disk_gb': plan_details['disk'],
            'os_id': os_id,
            'ip_address': vm_data['ip_address'],
            'status': 'running',
            'ssh_password': vm_data['ssh_password'],
            'root_password': vm_data['root_password']
        }
        
        DatabaseManager.add_vm(db_vm_data)
        
        # Success embed
        success_embed = create_success_embed(
            "✅ KVM VPS Created Successfully",
            f"**User:** {user.mention}\n"
            f"**VPS Name:** `{vm_name}`\n"
            f"**Plan:** {plan.title()}\n"
            f"**IP Address:** {vm_data['ip_address']}\n"
            f"**OS:** {os_id}\n"
            f"**Resources:** {plan_details['ram']}MB RAM / {plan_details['cpu']} CPU / {plan_details['disk']}GB Disk\n\n"
            f"**SSH Access:**\n"
            f"```bash\nssh ubuntu@{vm_data['ip_address']}\n```\n"
            f"**Username:** `ubuntu`\n"
            f"**Password:** ||{vm_data['ssh_password']}||\n\n"
            f"*Password is hidden. Click to reveal.*"
        )
        
        await msg.edit(embed=success_embed)
        
        # Send DM to user
        try:
            user_embed = create_success_embed(
                "🎉 Your RDX KVM VPS is Ready!",
                f"Your KVM Virtual Machine has been successfully created!\n\n"
                f"**VPS Details:**\n"
                f"• **Name:** `{vm_name}`\n"
                f"• **IP:** {vm_data['ip_address']}\n"
                f"• **Plan:** {plan.title()}\n"
                f"• **OS:** {os_id}\n\n"
                f"**SSH Access:**\n"
                f"```bash\nssh ubuntu@{vm_data['ip_address']}\n```\n"
                f"**Username:** `ubuntu`\n"
                f"**Password:** ||{vm_data['ssh_password']}||\n\n"
                f"**Management:**\n"
                f"Use `!manage {vm_name}` to start/stop/reboot your VPS.\n\n"
                f"**Important:**\n"
                f"• This is a real KVM Virtual Machine with full root access\n"
                f"• Back up important data regularly\n"
                f"• Contact support for assistance"
            )
            
            await user.send(embed=user_embed)
        
        except discord.Forbidden:
            await ctx.send(
                f"{user.mention}, I couldn't send you a DM. Please enable DMs to receive your VPS credentials.",
                delete_after=10
            )
    
    except Exception as e:
        error_embed = create_error_embed(
            "❌ VPS Creation Failed",
            f"Error: {str(e)}\n\n"
            f"Please check KVM configuration and try again."
        )
        await ctx.send(embed=error_embed)
        logger.error(f"Failed to create VM: {e}")

@bot.command(name='listvms')
@is_admin()
async def list_all_vms(ctx):
    """List all KVM VMs on the host"""
    try:
        vms = await KVMManager.list_all_vms()
        
        if not vms:
            embed = create_info_embed("No Virtual Machines", 
                                     "No KVM VMs are currently defined.")
            await ctx.send(embed=embed)
            return
        
        embed = create_info_embed(
            f"KVM Virtual Machines ({len(vms)})",
            "All VMs on this RDX KVM host:"
        )
        
        for vm in vms:
            # Get additional info from database
            db_vm = DatabaseManager.get_vm_by_name(vm['name'])
            
            if db_vm:
                owner = f"<@{db_vm['user_id']}>"
                plan = db_vm['plan']
            else:
                owner = "Unknown (Not in DB)"
                plan = "N/A"
            
            vm_info = (
                f"**ID:** {vm['id']} | **State:** {vm['state']}\n"
                f"**Owner:** {owner}\n"
                f"**Plan:** {plan}"
            )
            
            add_field(embed, f"📦 {vm['name']}", vm_info, False)
        
        await ctx.send(embed=embed)
    
    except Exception as e:
        embed = create_error_embed("Error", f"Failed to list VMs: {e}")
        await ctx.send(embed=embed)

@bot.command(name='resize')
@is_admin()
async def resize_vps(ctx, vm_name: str, resource: str, value: int):
    """Resize VM resources"""
    valid_resources = ['ram', 'cpu', 'disk']
    
    if resource not in valid_resources:
        embed = create_error_embed(
            "Invalid Resource",
            f"Valid resources: {', '.join(valid_resources)}\n"
            f"Example: `!resize my-vm ram 4096`"
        )
        await ctx.send(embed=embed)
        return
    
    if value <= 0:
        embed = create_error_embed("Invalid Value", "Value must be positive.")
        await ctx.send(embed=embed)
        return
    
    # Get VM info
    vm = DatabaseManager.get_vm_by_name(vm_name)
    if not vm:
        embed = create_error_embed("VPS Not Found", f"VM `{vm_name}` not found.")
        await ctx.send(embed=embed)
        return
    
    # Stop VM if running
    was_running = vm['status'] == 'running'
    
    if was_running:
        embed = create_info_embed("Stopping VPS", 
                                 f"Stopping `{vm_name}` to apply changes...")
        msg = await ctx.send(embed=embed)
        
        await KVMManager.control_vm(vm_name, 'shutdown')
        DatabaseManager.update_vm_status(vm_name, 'stopped')
    
    try:
        if resource == 'ram':
            # Update RAM in libvirt
            cmd = ['virsh', 'setmaxmem', vm_name, f'{value}M', '--config']
            stdout, stderr, rc = await KVMManager.execute_command(cmd)
            
            cmd = ['virsh', 'setmem', vm_name, f'{value}M', '--config']
            stdout, stderr, rc = await KVMManager.execute_command(cmd)
            
            # Update database
            conn = DatabaseManager.get_connection()
            c = conn.cursor()
            c.execute('UPDATE virtual_machines SET ram_mb = ? WHERE vm_name = ?',
                     (value, vm_name))
            conn.commit()
            conn.close()
        
        elif resource == 'cpu':
            # Update CPU in libvirt
            cmd = ['virsh', 'setvcpus', vm_name, str(value), '--config']
            stdout, stderr, rc = await KVMManager.execute_command(cmd)
            
            # Update database
            conn = DatabaseManager.get_connection()
            c = conn.cursor()
            c.execute('UPDATE virtual_machines SET cpu_cores = ? WHERE vm_name = ?',
                     (value, vm_name))
            conn.commit()
            conn.close()
        
        elif resource == 'disk':
            # Resize disk
            success = await KVMManager.resize_vm_disk(vm_name, value)
            
            if success:
                # Update database
                conn = DatabaseManager.get_connection()
                c = conn.cursor()
                c.execute('UPDATE virtual_machines SET disk_gb = ? WHERE vm_name = ?',
                         (value, vm_name))
                conn.commit()
                conn.close()
            else:
                raise Exception("Failed to resize disk")
        
        # Restart VM if it was running
        if was_running:
            await KVMManager.control_vm(vm_name, 'start')
            DatabaseManager.update_vm_status(vm_name, 'running')
            
            # Get new IP
            ip = await KVMManager.get_vm_ip(vm_name)
            if ip and ip != "Pending...":
                DatabaseManager.update_vm_ip(vm_name, ip)
        
        embed = create_success_embed(
            "✅ VPS Resized Successfully",
            f"**VPS:** `{vm_name}`\n"
            f"**Resource:** {resource.upper()}\n"
            f"**New Value:** {value} {'MB' if resource == 'ram' else 'Cores' if resource == 'cpu' else 'GB'}\n\n"
            f"*Changes have been applied successfully.*"
        )
        
        if was_running:
            await msg.edit(embed=embed)
        else:
            await ctx.send(embed=embed)
    
    except Exception as e:
        error_embed = create_error_embed("❌ Resize Failed", f"Error: {str(e)}")
        
        if was_running:
            await msg.edit(embed=error_embed)
        else:
            await ctx.send(embed=error_embed)

@bot.command(name='suspend')
@is_admin()
async def suspend_vps(ctx, vm_name: str, *, reason: str = "Administrative action"):
    """Suspend a VM"""
    vm = DatabaseManager.get_vm_by_name(vm_name)
    
    if not vm:
        embed = create_error_embed("VPS Not Found", f"VM `{vm_name}` not found.")
        await ctx.send(embed=embed)
        return
    
    if vm['suspended']:
        embed = create_error_embed("Already Suspended", 
                                  f"VM `{vm_name}` is already suspended.")
        await ctx.send(embed=embed)
        return
    
    # Stop VM
    await KVMManager.control_vm(vm_name, 'shutdown')
    
    # Update database
    conn = DatabaseManager.get_connection()
    c = conn.cursor()
    c.execute('UPDATE virtual_machines SET suspended = 1 WHERE vm_name = ?',
             (vm_name,))
    
    # Log suspension
    c.execute('''
        INSERT INTO suspension_logs (vm_uuid, reason, suspended_by)
        VALUES (?, ?, ?)
    ''', (vm['vm_uuid'], reason, str(ctx.author.id)))
    
    conn.commit()
    conn.close()
    
    # Notify owner
    try:
        owner = await bot.fetch_user(int(vm['user_id']))
        
        dm_embed = create_warning_embed(
            "⚠️ Your VPS Has Been Suspended",
            f"**VPS:** `{vm_name}`\n"
            f"**Reason:** {reason}\n"
            f"**Suspended By:** {ctx.author.mention}\n\n"
            f"Your KVM Virtual Machine has been suspended. "
            f"Contact an admin to unsuspend it."
        )
        
        await owner.send(embed=dm_embed)
    
    except Exception as e:
        logger.error(f"Failed to DM owner: {e}")
    
    embed = create_success_embed(
        "✅ VPS Suspended",
        f"**VPS:** `{vm_name}`\n"
        f"**Reason:** {reason}\n\n"
        f"The owner has been notified."
    )
    
    await ctx.send(embed=embed)

@bot.command(name='unsuspend')
@is_admin()
async def unsuspend_vps(ctx, vm_name: str):
    """Unsuspend a VM"""
    vm = DatabaseManager.get_vm_by_name(vm_name)
    
    if not vm:
        embed = create_error_embed("VPS Not Found", f"VM `{vm_name}` not found.")
        await ctx.send(embed=embed)
        return
    
    if not vm['suspended']:
        embed = create_error_embed("Not Suspended", 
                                  f"VM `{vm_name}` is not suspended.")
        await ctx.send(embed=embed)
        return
    
    # Update database
    conn = DatabaseManager.get_connection()
    c = conn.cursor()
    c.execute('UPDATE virtual_machines SET suspended = 0 WHERE vm_name = ?',
             (vm_name,))
    
    # Update suspension log
    c.execute('''
        UPDATE suspension_logs 
        SET unsuspended_at = CURRENT_TIMESTAMP, unsuspended_by = ?
        WHERE vm_uuid = ? AND unsuspended_at IS NULL
    ''', (str(ctx.author.id), vm['vm_uuid']))
    
    conn.commit()
    conn.close()
    
    # Start VM
    await KVMManager.control_vm(vm_name, 'start')
    DatabaseManager.update_vm_status(vm_name, 'running')
    
    # Get new IP
    ip = await KVMManager.get_vm_ip(vm_name)
    if ip and ip != "Pending...":
        DatabaseManager.update_vm_ip(vm_name, ip)
    
    # Notify owner
    try:
        owner = await bot.fetch_user(int(vm['user_id']))
        
        dm_embed = create_success_embed(
            "✅ Your VPS Has Been Unsuspended",
            f"**VPS:** `{vm_name}`\n"
            f"**Unsuspended By:** {ctx.author.mention}\n\n"
            f"Your KVM Virtual Machine has been unsuspended and started. "
            f"You can now access it normally."
        )
        
        await owner.send(embed=dm_embed)
    
    except Exception as e:
        logger.error(f"Failed to DM owner: {e}")
    
    embed = create_success_embed(
        "✅ VPS Unsuspended",
        f"**VPS:** `{vm_name}`\n\n"
        f"The VPS has been unsuspended and started. The owner has been notified."
    )
    
    await ctx.send(embed=embed)

@bot.command(name='execute')
@is_admin()
async def execute_command(ctx, vm_name: str, *, command: str):
    """Execute command inside VM via guest agent"""
    vm = DatabaseManager.get_vm_by_name(vm_name)
    
    if not vm:
        embed = create_error_embed("VPS Not Found", f"VM `{vm_name}` not found.")
        await ctx.send(embed=embed)
        return
    
    if vm['status'] != 'running':
        embed = create_error_embed("VPS Not Running", 
                                  f"VM `{vm_name}` must be running to execute commands.")
        await ctx.send(embed=embed)
        return
    
    embed = create_info_embed(
        "Executing Command",
        f"**VPS:** `{vm_name}`\n"
        f"**Command:** ```bash\n{command}\n```\n\n"
        f"Executing via QEMU Guest Agent..."
    )
    
    msg = await ctx.send(embed=embed)
    
    try:
        output, error = await KVMManager.execute_in_vm(vm_name, command)
        
        result_embed = create_info_embed(
            "Command Execution Result",
            f"**VPS:** `{vm_name}`\n"
            f"**Command:** ```bash\n{command}\n```"
        )
        
        if output:
            # Truncate if too long
            if len(output) > 1000:
                output = output[:1000] + "\n... (truncated)"
            add_field(result_embed, "📤 Output", f"```\n{output}\n```", False)
        
        if error and "Exit code: 0" not in error:
            add_field(result_embed, "⚠️ Error", f"```\n{error}\n```", False)
        
        await msg.edit(embed=result_embed)
    
    except Exception as e:
        error_embed = create_error_embed("Execution Failed", f"Error: {str(e)}")
        await msg.edit(embed=error_embed)

@bot.command(name='stats')
@is_admin()
async def show_stats(ctx, vm_name: str = None):
    """Show detailed VM statistics"""
    if vm_name:
        # Single VM stats
        vm = DatabaseManager.get_vm_by_name(vm_name)
        
        if not vm:
            embed = create_error_embed("VPS Not Found", f"VM `{vm_name}` not found.")
            await ctx.send(embed=embed)
            return
        
        # Get live stats
        try:
            live_stats = await KVMManager.get_vm_stats(vm_name)
            
            embed = create_info_embed(
                f"📊 VPS Statistics - {vm_name}",
                f"**Owner:** <@{vm['user_id']}>\n"
                f"**Plan:** {vm['plan'].title()}\n"
                f"**Status:** {vm['status'].upper()}\n"
                f"**IP:** {vm['ip_address'] or 'N/A'}"
            )
            
            if live_stats:
                add_field(embed, "💻 CPU", 
                         f"**Time:** {live_stats.get('cpu_time', 'N/A')}", True)
                
                if 'ram_percent' in live_stats:
                    add_field(embed, "🧠 RAM", 
                             f"**Usage:** {live_stats['ram_used']}MB / {live_stats['ram_total']}MB\n"
                             f"**Percent:** {live_stats['ram_percent']:.1f}%", True)
                
                if 'disk_used' in live_stats:
                    add_field(embed, "💾 Disk", 
                             f"**Used:** {live_stats.get('disk_used', 'N/A')}\n"
                             f"**Total:** {live_stats.get('disk_total', 'N/A')}", True)
            else:
                add_field(embed, "Live Stats", "Not available (VM may be stopped)", False)
            
            # Get historical stats from database
            conn = DatabaseManager.get_connection()
            c = conn.cursor()
            c.execute('''
                SELECT AVG(cpu_usage) as avg_cpu, AVG(ram_usage_mb) as avg_ram,
                       COUNT(*) as samples
                FROM vm_stats 
                WHERE vm_uuid = ? AND recorded_at > datetime('now', '-1 day')
            ''', (vm['vm_uuid'],))
            
            row = c.fetchone()
            conn.close()
            
            if row and row['samples'] > 0:
                add_field(embed, "📈 24h Averages",
                         f"**CPU:** {row['avg_cpu']:.1f}%\n"
                         f"**RAM:** {row['avg_ram']:.0f} MB\n"
                         f"**Samples:** {row['samples']}", False)
        
        except Exception as e:
            embed = create_error_embed("Error", f"Failed to get stats: {e}")
        
        await ctx.send(embed=embed)
    
    else:
        # System-wide stats
        total_vms = 0
        running_vms = 0
        total_ram = 0
        total_cpu = 0
        total_disk = 0
        
        # Get all VMs
        conn = DatabaseManager.get_connection()
        c = conn.cursor()
        c.execute('SELECT * FROM virtual_machines')
        vms = c.fetchall()
        conn.close()
        
        for vm in vms:
            total_vms += 1
            if vm['status'] == 'running':
                running_vms += 1
            
            total_ram += vm['ram_mb']
            total_cpu += vm['cpu_cores']
            total_disk += vm['disk_gb']
        
        # Get host stats
        try:
            # CPU usage
            cpu_cmd = ['top', '-bn1']
            stdout, stderr, rc = await KVMManager.execute_command(cpu_cmd)
            
            cpu_usage = "N/A"
            if rc == 0:
                for line in stdout.split('\n'):
                    if '%Cpu(s):' in line:
                        parts = line.split()
                        idle = float(parts[7])
                        cpu_usage = f"{100 - idle:.1f}%"
                        break
            
            # RAM usage
            ram_cmd = ['free', '-m']
            stdout, stderr, rc = await KVMManager.execute_command(ram_cmd)
            
            ram_usage = "N/A"
            if rc == 0:
                lines = stdout.split('\n')
                if len(lines) > 1:
                    parts = lines[1].split()
                    total = int(parts[1])
                    used = int(parts[2])
                    ram_usage = f"{used}/{total} MB ({used/total*100:.1f}%)"
            
            # Disk usage
            disk_cmd = ['df', '-h', '/']
            stdout, stderr, rc = await KVMManager.execute_command(disk_cmd)
            
            disk_usage = "N/A"
            if rc == 0:
                lines = stdout.split('\n')
                if len(lines) > 1:
                    parts = lines[1].split()
                    disk_usage = f"{parts[2]}/{parts[1]} ({parts[4]})"
        
        except Exception as e:
            cpu_usage = ram_usage = disk_usage = f"Error: {e}"
        
        embed = create_info_embed(
            "📊 RDX KVM Host Statistics",
            f"**Host:** {BRAND_NAME} KVM Server\n"
            f"**Bot:** {bot.user.name}"
        )
        
        add_field(embed, "👥 Users & VMs",
                 f"**Total Users:** {len(set(vm['user_id'] for vm in vms))}\n"
                 f"**Total VMs:** {total_vms}\n"
                 f"**Running VMs:** {running_vms}\n"
                 f"**Suspended VMs:** {sum(1 for vm in vms if vm['suspended'])}", False)
        
        add_field(embed, "📦 Allocated Resources",
                 f"**Total RAM:** {total_ram} MB\n"
                 f"**Total CPU:** {total_cpu} Cores\n"
                 f"**Total Disk:** {total_disk} GB", True)
        
        add_field(embed, "🖥 Host Resources",
                 f"**CPU Usage:** {cpu_usage}\n"
                 f"**RAM Usage:** {ram_usage}\n"
                 f"**Disk Usage:** {disk_usage}", True)
        
        await ctx.send(embed=embed)

# ==================== ADMIN MANAGEMENT ====================
@bot.command(name='admin-add')
@is_main_admin()
async def add_admin(ctx, user: discord.Member):
    """Add admin"""
    user_id = str(user.id)
    
    if user_id == str(MAIN_ADMIN_ID):
        embed = create_error_embed("Already Admin", 
                                  "This user is already the main admin!")
        await ctx.send(embed=embed)
        return
    
    if DatabaseManager.is_admin(user_id):
        embed = create_error_embed("Already Admin", 
                                  f"{user.mention} is already an admin!")
        await ctx.send(embed=embed)
        return
    
    DatabaseManager.add_admin(user_id, str(ctx.author.id))
    
    embed = create_success_embed(
        "✅ Admin Added",
        f"{user.mention} is now a {BRAND_NAME} admin!"
    )
    
    await ctx.send(embed=embed)
    
    # DM the new admin
    try:
        dm_embed = create_success_embed(
            "🎉 You've Been Promoted!",
            f"You are now a {BRAND_NAME} admin!\n\n"
            f"**Added By:** {ctx.author.mention}\n"
            f"**Permissions:**\n"
            f"• Create/Delete VMs\n"
            f"• Manage all users' VMs\n"
            f"• Suspend/Unsuspend VMs\n"
            f"• Execute commands in VMs\n\n"
            f"Use `!help` to see all admin commands."
        )
        
        await user.send(embed=dm_embed)
    
    except discord.Forbidden:
        await ctx.send(f"{user.mention}, please enable DMs to receive admin instructions.")

@bot.command(name='admin-remove')
@is_main_admin()
async def remove_admin(ctx, user: discord.Member):
    """Remove admin"""
    user_id = str(user.id)
    
    if user_id == str(MAIN_ADMIN_ID):
        embed = create_error_embed("Cannot Remove", 
                                  "You cannot remove the main admin!")
        await ctx.send(embed=embed)
        return
    
    if not DatabaseManager.is_admin(user_id):
        embed = create_error_embed("Not Admin", 
                                  f"{user.mention} is not an admin!")
        await ctx.send(embed=embed)
        return
    
    DatabaseManager.remove_admin(user_id)
    
    embed = create_success_embed(
        "✅ Admin Removed",
        f"{user.mention} is no longer a {BRAND_NAME} admin."
    )
    
    await ctx.send(embed=embed)

@bot.command(name='admin-list')
@is_admin()
async def list_admins(ctx):
    """List all admins"""
    admins = DatabaseManager.get_all_admins()
    
    embed = create_info_embed(
        f"👑 {BRAND_NAME} Admin Team",
        f"Total Admins: {len(admins)}"
    )
    
    # Add main admin
    main_admin = await bot.fetch_user(MAIN_ADMIN_ID)
    add_field(embed, "🔰 Main Admin", 
             f"{main_admin.mention} (ID: {MAIN_ADMIN_ID})", False)
    
    # Add other admins
    if len(admins) > 1:
        admin_list = []
        for admin_id in admins:
            if admin_id != str(MAIN_ADMIN_ID):
                try:
                    admin = await bot.fetch_user(int(admin_id))
                    admin_list.append(f"• {admin.mention} (ID: {admin_id})")
                except:
                    admin_list.append(f"• Unknown User (ID: {admin_id})")
        
        if admin_list:
            add_field(embed, "🛡️ Admins", "\n".join(admin_list), False)
    
    await ctx.send(embed=embed)

# ==================== HELP COMMAND ====================
@bot.command(name='help')
async def show_help(ctx):
    """Show help information"""
    user_id = str(ctx.author.id)
    is_admin_user = user_id == str(MAIN_ADMIN_ID) or DatabaseManager.is_admin(user_id)
    
    # User commands
    user_embed = create_info_embed(
        "📚 User Commands",
        f"**{BRAND_NAME} KVM VPS Manager**\n\n"
        f"Basic commands for all users:"
    )
    
    user_commands = [
        ("!ping", "Check bot latency"),
        ("!myvps", "List your KVM Virtual Machines"),
        ("!vpsinfo <vm_name>", "Get detailed VM information"),
        ("!manage <vm_name>", "Manage your VPS (start/stop/reboot)"),
    ]
    
    for cmd, desc in user_commands:
        add_field(user_embed, cmd, desc, False)
    
    await ctx.send(embed=user_embed)
    
    # Admin commands (if user is admin)
    if is_admin_user:
        admin_embed = create_info_embed(
            "🛡️ Admin Commands",
            f"**{BRAND_NAME} Administrative Tools**\n\n"
            f"Commands for administrators:"
        )
        
        admin_commands = [
            ("!create <plan> @user [os]", "Create new KVM VPS"),
            ("!listvms", "List all KVM VMs on host"),
            ("!resize <vm> <ram|cpu|disk> <value>", "Resize VM resources"),
            ("!suspend <vm> [reason]", "Suspend a VM"),
            ("!unsuspend <vm>", "Unsuspend a VM"),
            ("!execute <vm> <command>", "Execute command in VM"),
            ("!stats [vm]", "Show VM/host statistics"),
            ("!admin-list", "List all admins"),
        ]
        
        for cmd, desc in admin_commands:
            add_field(admin_embed, cmd, desc, False)
        
        await ctx.send(embed=admin_embed)
    
    # Main admin commands
    if user_id == str(MAIN_ADMIN_ID):
        main_admin_embed = create_info_embed(
            "👑 Main Admin Commands",
            f"**{BRAND_NAME} System Administration**\n\n"
            f"Commands for main administrator only:"
        )
        
        main_admin_commands = [
            ("!admin-add @user", "Add new admin"),
            ("!admin-remove @user", "Remove admin"),
        ]
        
        for cmd, desc in main_admin_commands:
            add_field(main_admin_embed, cmd, desc, False)
        
        await ctx.send(embed=main_admin_embed)

# ==================== UTILITY COMMANDS ====================
@bot.command(name='status')
async def bot_status(ctx):
    """Show bot and system status"""
    # Get bot uptime
    import psutil
    process = psutil.Process()
    uptime_seconds = time.time() - process.create_time()
    uptime_str = time.strftime("%H:%M:%S", time.gmtime(uptime_seconds))
    
    # Get VM counts
    conn = DatabaseManager.get_connection()
    c = conn.cursor()
    c.execute('SELECT COUNT(*) as total, '
             'SUM(CASE WHEN status = "running" THEN 1 ELSE 0 END) as running '
             'FROM virtual_machines')
    stats = c.fetchone()
    conn.close()
    
    # Get KVM status
    kvm_status = "✅ Active"
    try:
        cmd = ['virsh', 'list', '--count']
        stdout, stderr, rc = await KVMManager.execute_command(cmd)
        if rc != 0:
            kvm_status = "⚠️ Issues detected"
    except:
        kvm_status = "❌ Not responding"
    
    embed = create_info_embed(
        "📊 System Status",
        f"**{BRAND_NAME} KVM Hosting Platform**\n\n"
        f"Real KVM Virtual Machine Management System"
    )
    
    add_field(embed, "🤖 Bot Status",
             f"**Uptime:** {uptime_str}\n"
             f"**Latency:** {round(bot.latency * 1000)}ms\n"
             f"**Servers:** {len(bot.guilds)}", True)
    
    add_field(embed, "🖥 KVM Status",
             f"**Status:** {kvm_status}\n"
             f"**Total VMs:** {stats['total']}\n"
             f"**Running VMs:** {stats['running']}", True)
    
    add_field(embed, "📈 Performance",
             f"**CPU Usage:** {psutil.cpu_percent()}%\n"
             f"**RAM Usage:** {psutil.virtual_memory().percent}%\n"
             f"**Python:** {sys.version.split()[0]}", True)
    
    add_field(embed, "🔧 Features",
             "• Real KVM Virtual Machines\n"
             "• Full root access via SSH\n"
             "• Multiple OS support\n"
             "• Live resource monitoring\n"
             "• Professional admin panel\n"
             "• Auto protection systems", False)
    
    await ctx.send(embed=embed)

# ==================== MAIN EXECUTION ====================
if __name__ == "__main__":
    # Check for KVM
    logger.info("Checking KVM configuration...")
    
    # Verify KVM is installed
    if not shutil.which("virsh"):
        logger.error("KVM/Libvirt not found. Please install KVM first.")
        sys.exit(1)
    
    # Verify storage pool exists
    try:
        cmd = ['virsh', 'pool-info', KVM_STORAGE_POOL]
        stdout, stderr, rc = KVMManager.execute_command(cmd)
        if rc != 0:
            logger.warning(f"Storage pool '{KVM_STORAGE_POOL}' not found. Creating...")
    except:
        logger.warning("Could not verify storage pool")
    
    # Check cloud-init directory
    if not os.path.exists(CLOUD_INIT_DIR):
        os.makedirs(CLOUD_INIT_DIR, exist_ok=True)
        logger.info(f"Created cloud-init directory: {CLOUD_INIT_DIR}")
    
    # Check base images directory
    if not os.path.exists(BASE_IMAGES_DIR):
        logger.warning(f"Base images directory not found: {BASE_IMAGES_DIR}")
        logger.info("Please download OS images to this directory")
    
    # Check Discord token
    if not DISCORD_TOKEN:
        logger.error("No Discord token found. Set DISCORD_TOKEN environment variable.")
        sys.exit(1)
    
    logger.info(f"Starting {BRAND_NAME} KVM Bot...")
    logger.info(f"Main Admin ID: {MAIN_ADMIN_ID}")
    logger.info(f"Storage Pool: {KVM_STORAGE_POOL}")
    logger.info(f"Network: {KVM_NETWORK}")
    
    try:
        bot.run(DISCORD_TOKEN)
    except discord.LoginFailure:
        logger.error("Invalid Discord token. Please check your DISCORD_TOKEN.")
    except Exception as e:
        logger.error(f"Failed to start bot: {e}")