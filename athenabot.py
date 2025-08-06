import os
from dotenv import load_dotenv
import asyncio
import discord
from discord.ext import commands, voice_recv
import time
from collections import defaultdict, deque

now = time.monotonic()  # em vez de asyncio.get_event_loop().time()
load_dotenv()
TOKEN = os.getenv("DISCORD_BOT_TOKEN") # Defina a variável de ambiente DISCORD_BOT_TOKEN com o token gerado no portal de desenvolvedor.

ALERT_CHANNEL_ID = 1402039658375938240  # ID do canal de texto # Canal de texto onde será enviado o alerta

# PARAMETROS DE DETECÇÃO DE EARRAPE
VOLUME_THRESHOLD = 125 # Limite de volume (0–135). Ajuste conforme testes.
WINDOW_SECONDS = 5 # janela de tempo para contar o número de ocorrências
MAX_OCCURRENCES = 5 # quantos picos para classificar earrape

#Fila processamento de pacotes
audio_queue: asyncio.Queue = asyncio.Queue()
user_events: dict[int,deque[float]] = defaultdict(
    lambda: deque(maxlen=MAX_OCCURRENCES)
        )

# Configura o Bot com Todos os Intents
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# Variaveis Globais para rastrear o canal, cliente de voz e temporizar
current_voice_client: voice_recv.VoiceRecvClient | None = None
current_voice_channel: discord.VoiceChannel | None = None
monitor_timeout_task: asyncio.Task | None = None

async def leave_current_voice() -> None:
    """Interrompe o monitoramente e desconecta do canal de voz"""
    global current_voice_client, current_voice_channel, monitor_timeout_task
    if current_voice_client is None:
        current_voice_client.stop()
        try:
            await current_voice_client.disconnect()
        except Exception:
            pass
        current_voice_cliente = None
        current_voice_channel = None
    if monitor_timeout_task is not None:
        monitor_timeout_task.cancel()
        monitor_timeout_task = None

async def monitor_timeout() -> None:
    """Espera 60 segundos e sai do canal"""
    try:
        await asyncio.sleep(60)
        await leave_current_voice()
    except asyncio.CancelledError:
        pass

def reset_monitor_timout() -> None:
    """Reinicia o Temporizador"""
    global monitor_timeout_task
    if monitor_timeout_task is not None:
        monitor_timeout_task.cancel()
    monitor_timeout_task = bot.loop.create_task(monitor_timeout())



@bot.event
async def on_ready() -> None:
    """Evento de quando o programa ficou Online"""
    print(f"Logado como {bot.user} (ID {bot.user.id})")
    channel = bot.get_channel(ALERT_CHANNEL_ID)
    if channel:
        await channel.send("Oiee")

async def audio_worker(alert_channel: discord.TextChannel):
    """Tarefa que consome pacotes da final e processa"""
    while True:
        user, power = await audio_queue.get()
        if user is None:
            break
        now = time.monotonic()
        events = user_events[user.id]
        events.append(now)
        while events and now - events[0] > WINDOW_SECONDS:
            events.popleft()
        if len(events) >= MAX_OCCURRENCES:
            await alert_channel.send(f"earrape identificado, id {user.id}")
            events.clear()

async def join_and_monitor(ctx):
    """Entra no canal de voz do autor e começa a monitorar."""
    voice_state = ctx.author.voice
    if not voice_state or not voice_state.channel:
        await ctx.reply("Você precisa estar em um canal de voz.")
        return

    # Conecta usando VoiceRecvClient para receber áudio:contentReference[oaicite:4]{index=4}
    vc = await voice_state.channel.connect(cls=voice_recv.VoiceRecvClient)

    # Pega o canal de texto onde os alertas serão enviados
    alert_channel = ctx.guild.get_channel(ALERT_CHANNEL_ID)

    # Dicionário para evitar alertas repetidos em sequência
    recent_alerts = {}
    user_events: dict[int, deque[float]] = defaultdict(lambda: deque(maxlen=MAX_OCCURRENCES))

    def callback(user: discord.Member | None, data: voice_recv.VoiceData):

        # Ignora pacotes sem usuário associado
        if user is None:
            return

        # Obtém o valor da potência de áudio:contentReference[oaicite:5]{index=5}.
        ext = data.packet.extension_data.get(voice_recv.ExtensionID.audio_power)
        if not ext:
            return

        value = int.from_bytes(ext, "big")
        # O exemplo da biblioteca usa 127 - (value & 127) como potência inversa
        power = 127 - (value & 0x7F)

        # Registra o Timestamp caso passe o limite
        #if power >= VOLUME_THRESHOLD and user.id not in recent_alerts:
        if power >= VOLUME_THRESHOLD:
            print(f"{user} -> {value} ({ext})")
            now = time.monotonic()
            events = user_events[user.id]
            events.append(now)

            # remove eventos antigos fora da janela
            while events and now - events[0] > WINDOW_SECONDS:
                events.popleft()

            if len(events) >= MAX_OCCURRENCES:
            #recent_alerts[user.id] = time.monotonic()

            # Envia a mensagem de alerta de forma assíncrona
                async def send_alert():
                    msg = f"earrape identificado, id {user.id}"
                    await alert_channel.send(msg)

                asyncio.run_coroutine_threadsafe(send_alert(), bot.loop)
                events.clear()

    # Inicia a escuta usando BasicSink:contentReference[oaicite:6]{index=6}
    vc.listen(voice_recv.BasicSink(callback))

@bot.command()
async def monitor(ctx):
    """Comando para começar a monitorar o volume no canal de voz."""
    await join_and_monitor(ctx)

@bot.command()
async def stopmonitor(ctx):
    """Para de monitorar e desconecta do canal de voz."""
    if ctx.voice_client:
        ctx.voice_client.stop()
        await ctx.voice_client.disconnect()
        await ctx.reply("Monitoramento parado.")

bot.run(TOKEN)
