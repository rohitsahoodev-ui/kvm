import discord
from discord.ext import commands
import os
import subprocess
from dotenv import load_dotenv

# Load env
load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
IMAGES_PATH = os.getenv("KVM_IMAGES_PATH", "/opt/kvm/images")

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ---------- EVENTS ----------

@bot.event
async def on_ready():
    print("🚀 RDX KVM Hosting Bot Online")
    print(f"Logged in as {bot.user}")

# ---------- CHECKS ----------

def is_admin(ctx):
    return ctx.author.id == ADMIN_ID

# ---------- BASIC COMMANDS ----------

@bot.command()
async def ping(ctx):
    await ctx.send("🏓 Pong! RDX KVM Alive")

@bot.command()
async def detect(ctx):
    try:
        out = subprocess.check_output(["virt-what"]).decode()
        await ctx.send(f"🖥️ Virtualization Detected:\n```{out}```")
    except:
        await ctx.send("❌ virt-what not installed")

# ---------- ADMIN PANEL ----------

@bot.command()
@commands.check(is_admin)
async def images(ctx):
    try:
        files = os.listdir(IMAGES_PATH)
        msg = "\n".join(files)
        await ctx.send(f"📦 Available OS Images:\n```{msg}```")
    except:
        await ctx.send("❌ Images path error")

@bot.command()
@commands.check(is_admin)
async def listvm(ctx):
    try:
        out = subprocess.check_output(["virsh", "list", "--all"]).decode()
        await ctx.send(f"🧾 KVM VPS List:\n```{out}```")
    except:
        await ctx.send("❌ libvirt / virsh error")

@bot.command()
@commands.check(is_admin)
async def startvm(ctx, name: str):
    subprocess.call(["virsh", "start", name])
    await ctx.send(f"▶️ VPS `{name}` started")

@bot.command()
@commands.check(is_admin)
async def stopvm(ctx, name: str):
    subprocess.call(["virsh", "shutdown", name])
    await ctx.send(f"⏹️ VPS `{name}` stopped")

@bot.command()
@commands.check(is_admin)
async def deletevm(ctx, name: str):
    subprocess.call(["virsh", "destroy", name])
    subprocess.call(["virsh", "undefine", name, "--remove-all-storage"])
    await ctx.send(f"🗑️ VPS `{name}` deleted")

# ---------- CREATE VPS (BASIC) ----------

@bot.command()
@commands.check(is_admin)
async def createvm(ctx, name: str, osname: str):
    """
    osname:
    ubuntu-22.04-base.qcow2
    debian-12-base.qcow2
    """
    image = f"{IMAGES_PATH}/{osname}"
    disk = f"/var/lib/libvirt/images/{name}.qcow2"

    if not os.path.exists(image):
        await ctx.send("❌ OS image not found")
        return

    subprocess.call([
        "qemu-img", "create", "-f", "qcow2", "-b", image, disk, "10G"
    ])

    subprocess.call([
        "virt-install",
        "--name", name,
        "--ram", "1024",
        "--vcpus", "1",
        "--disk", f"path={disk},format=qcow2",
        "--import",
        "--os-variant", "generic",
        "--network", "network=default",
        "--noautoconsole"
    ])

    await ctx.send(f"✅ VPS `{name}` created successfully")

# ---------- ERROR HANDLER ----------

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CheckFailure):
        await ctx.send("❌ You are not authorized")
    else:
        await ctx.send(f"⚠️ Error: `{error}`")

# ---------- START BOT ----------

bot.run(TOKEN)
