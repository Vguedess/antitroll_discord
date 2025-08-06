import os
import asyncio
import signal
import time
import json
from collections import defaultdict, deque

import discord
from discord.ext import commands, voice_recv
from dotenv import load_dotenv

# ── CONFIGURAÇÃO ────────────────────────────────────────────────────────────────

load_dotenv()
TOKEN                = os.getenv("DISCORD_BOT_TOKEN")
ALERT_CHANNEL_ID     = 1402039658375938240  # canal de texto de alertas
PUNISHMENTS_FILE     = "punishments.json"   # arquivo de persistência

# parâmetros de earrape
VOLUME_THRESHOLD     = 118
WINDOW_SECONDS       = 10
MAX_OCCURRENCES      = 5

# bloqueio inicial e timeout
INITIAL_BLOCK_SECS   = 10
IDLE_TIMEOUT_SECS    = 60

# cooldown de alerta
ALERT_COOLDOWN_SECS  = 3

# durações escalonadas
MUTE_DURATIONS       = [60, 3600, 86400, 604800, 2592000, 31536000, 315360000]
MUTE_LABELS          = ["1 minuto", "1 hora", "1 dia", "1 semana", "1 mês", "1 ano", "1 década"]

# ── ESTADO GLOBAL ───────────────────────────────────────────────────────────────

intents = discord.Intents.all()
bot     = commands.Bot(command_prefix="!", intents=intents)

audio_queue          = asyncio.Queue()
user_events          = defaultdict(lambda: deque(maxlen=MAX_OCCURRENCES))
last_alert_times     = defaultdict(float)
infraction_counts    = defaultdict(int)
punishments          = {}  # {user_id: {"infraction": int, "unmute_at": timestamp}}

current_voice_client = None
current_voice_channel = None
worker_task          = None
monitor_timeout_task = None
initial_block_end    = 0.0

# ── I/O DE PUNIÇÕES ────────────────────────────────────────────────────────────

def load_punishments():
    global punishments, infraction_counts
    if os.path.isfile(PUNISHMENTS_FILE):
        with open(PUNISHMENTS_FILE, "r") as f:
            data = json.load(f)
        # recarrega contadores e agendas de unmute
        now = time.monotonic()
        for uid, info in data.items():
            punishments[int(uid)] = info
            infraction_counts[int(uid)] = info["infraction"]
    else:
        punishments = {}

def save_punishments():
    # serializa keys como str
    with open(PUNISHMENTS_FILE, "w") as f:
        json.dump({str(uid): info for uid, info in punishments.items()}, f)

# ── FUNÇÕES AUXILIARES ──────────────────────────────────────────────────────────

async def send_alert(channel, msg):
    try:
        await channel.send(msg)
    except:
        pass

async def schedule_unmute(user_id, channel):
    """Agenda o unmute para um punishment existente."""
    info = punishments.get(user_id)
    if not info:
        return
    delay = info["unmute_at"] - time.monotonic()
    if delay > 0:
        await asyncio.sleep(delay)
    # realiza unmute
    member = channel.guild.get_member(user_id)
    if member:
        try:
            await member.edit(mute=False, reason="Fim da punição")
            await send_alert(channel, f"{member.mention} foi desmutado ({MUTE_LABELS[min(infraction_counts[user_id]-1, len(MUTE_LABELS)-1)]})")
        except:
            pass
    # limpa registro
    punishments.pop(user_id, None)
    save_punishments()

async def punish_user(member, channel):
    """Muta, agenda unmute e persiste em disco."""
    uid = member.id
    # incrementa infração
    infraction_counts[uid] += 1
    count = infraction_counts[uid]
    idx = min(count-1, len(MUTE_DURATIONS)-1)
    duration = MUTE_DURATIONS[idx]
    label = MUTE_LABELS[idx]

    # server mute
    try:
        await member.edit(mute=True, reason="Punição por earrape")
    except:
        pass

    # registra em memória e disco
    unmute_at = time.monotonic() + duration
    punishments[uid] = {"infraction": count, "unmute_at": unmute_at}
    save_punishments()

    # alerta
    await send_alert(channel, f"{member.mention} mutado por {label} (infração #{count})")

    # agenda desmutar
    asyncio.create_task(schedule_unmute(uid, channel))

# ── WORKER E CALLBACK ──────────────────────────────────────────────────────────

async def audio_worker(alert_channel):
    while True:
        user, power = await audio_queue.get()
        if user is None:
            break
        now = time.monotonic()
        ev = user_events[user.id]
        ev.append(now)
        while ev and now - ev[0] > WINDOW_SECONDS:
            ev.popleft()
        if len(ev) >= MAX_OCCURRENCES:
            if now - last_alert_times[user.id] >= ALERT_COOLDOWN_SECS:
                last_alert_times[user.id] = now
                await send_alert(alert_channel, f"⚠️ Earrape detectado: {user.mention}")
                # aplica punição
                member = alert_channel.guild.get_member(user.id)
                if member:
                    await punish_user(member, alert_channel)
            ev.clear()

def callback(user, data):
    if user is None or user.bot:
        return
    ext = data.packet.extension_data.get(voice_recv.ExtensionID.audio_power)
    if not ext:
        return
    power = 127 - (int.from_bytes(ext, "big") & 0x7F)
    if power >= VOLUME_THRESHOLD:
        asyncio.run_coroutine_threadsafe(audio_queue.put((user, power)), bot.loop)

# ── FUNÇÕES DE VOZ ──────────────────────────────────────────────────────────────

async def leave_current_voice():
    global worker_task, monitor_timeout_task, current_voice_client, current_voice_channel
    await audio_queue.put((None, None))
    if worker_task:
        await worker_task
    worker_task = None
    if monitor_timeout_task:
        monitor_timeout_task.cancel()
    monitor_timeout_task = None
    if current_voice_client:
        current_voice_client.stop()
        try:
            await current_voice_client.disconnect()
        except:
            pass
    current_voice_client = None
    current_voice_channel = None

async def monitor_timeout():
    try:
        await asyncio.sleep(IDLE_TIMEOUT_SECS)
        await leave_current_voice()
    except asyncio.CancelledError:
        pass

def reset_monitor_timeout():
    global monitor_timeout_task
    if monitor_timeout_task:
        monitor_timeout_task.cancel()
    monitor_timeout_task = bot.loop.create_task(monitor_timeout())

async def join_and_start(channel):
    global current_voice_client, current_voice_channel, worker_task, initial_block_end
    vc = await channel.connect(cls=voice_recv.VoiceRecvClient)
    current_voice_client = vc
    current_voice_channel = channel
    initial_block_end = time.monotonic() + INITIAL_BLOCK_SECS
    alert_ch = channel.guild.get_channel(ALERT_CHANNEL_ID)
    worker_task = asyncio.create_task(audio_worker(alert_ch))
    vc.listen(voice_recv.BasicSink(callback))
    reset_monitor_timeout()

# ── EVENTOS ─────────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    print(f"Logado como {bot.user} (ID {bot.user.id})")
    ch = bot.get_channel(ALERT_CHANNEL_ID)
    if ch:
        await send_alert(ch, "Oie 👋")
    # carrega punições pendentes e agenda desmutes
    load_punishments()
    for uid, info in punishments.items():
        ch = bot.get_channel(ALERT_CHANNEL_ID)
        asyncio.create_task(schedule_unmute(uid, ch))

@bot.event
async def on_voice_state_update(member, before, after):
    global current_voice_channel
    if member.bot:
        return
    if after.channel and after.channel != before.channel:
        now = time.monotonic()
        if not current_voice_channel:
            await join_and_start(after.channel)
            return
        if after.channel.id == current_voice_channel.id:
            reset_monitor_timeout()
            return
        if now >= initial_block_end:
            await leave_current_voice()
            await join_and_start(after.channel)

# ── COMANDOS LEGACY ─────────────────────────────────────────────────────────────

@bot.command()
async def monitor(ctx):
    if ctx.author.voice and ctx.author.voice.channel:
        await join_and_start(ctx.author.voice.channel)
        await ctx.reply("Monitoramento iniciado!")
    else:
        await ctx.reply("Entre em um canal de voz primeiro.")

@bot.command()
async def stopmonitor(ctx):
    if current_voice_client:
        await leave_current_voice()
        await ctx.reply("Monitoramento parado.")
    else:
        await ctx.reply("Não estou monitorando nenhum canal.")

# ── SHUTDOWN PARA “Tchau” ───────────────────────────────────────────────────────

shutdown = asyncio.Event()
def _sig_handler(sig, frame):
    shutdown.set()

signal.signal(signal.SIGINT, _sig_handler)
signal.signal(signal.SIGTERM, _sig_handler)

async def main():
    load_punishments()  # garante que contadores já existam mesmo antes de on_ready
    bot_task = asyncio.create_task(bot.start(TOKEN))
    await shutdown.wait()
    ch = bot.get_channel(ALERT_CHANNEL_ID)
    if ch:
        await send_alert(ch, "Tchau 👋")
    await bot.close()
    await bot_task

if __name__ == "__main__":
    asyncio.run(main())
