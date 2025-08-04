import os
from dotenv import load_dotenv
import asyncio
import discord
from discord.ext import commands, voice_recv
import time

load_dotenv()

# As credenciais NUNCA devem estar hard‑coded no código
# Defina a variável de ambiente DISCORD_BOT_TOKEN com o token gerado no portal de desenvolvedor.
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
#TOKEN = "QqF5h4ov_opNldlBznGodn1qF30zsb6e"

# Canal de texto onde será enviado o alerta (substitua pelo ID real)
ALERT_CHANNEL_ID = 1402039658375938240  # ID do canal de texto

# Limite de volume (0–127). Ajuste conforme testes.
VOLUME_THRESHOLD = 110

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"Logado como {bot.user} (ID {bot.user.id})")

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

        # Se ultrapassar o limite e não tiver sido alertado recentemente
        if power >= VOLUME_THRESHOLD and user.id not in recent_alerts:
            recent_alerts[user.id] = asyncio.get_event_loop().time()

            # Envia a mensagem de alerta de forma assíncrona
            async def send_alert():
                msg = f"earrape identificado, id {user.id}"
                await alert_channel.send(msg)

            asyncio.run_coroutine_threadsafe(send_alert(), bot.loop)

        # Remove usuário do mapa após 10 segundos para permitir novo alerta
        now = asyncio.get_event_loop().time()
        expired = [uid for uid, t in recent_alerts.items() if now - t > 10]
        for uid in expired:
            recent_alerts.pop(uid, None)

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
