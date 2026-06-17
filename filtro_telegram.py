"""
filtro_telegram.py
------------------
Lê mensagens novas do grupo público do Telegram @cartoesmilhaseviagens,
filtra por dois critérios e reencaminha as relevantes para o seu chat via bot.

CRITÉRIO 1 — Bônus de transferência
  A mensagem precisa mencionar:
  · uma FONTE (Itaú, BRB/Curtaí ou Livelo)
  · um PROGRAMA DE DESTINO (Latam Pass ou internacionais)
  · pelo menos um INDICADOR de bônus/transferência

CRITÉRIO 2 — Compra de milhas/pontos
  A mensagem precisa mencionar:
  · qualquer programa de interesse (fonte ou destino)
  · pelo menos um INDICADOR de compra/venda

Rodado pelo GitHub Actions a cada 15 minutos.
"""

import asyncio
import json
import os
import re
import time
import unicodedata

import requests
from telethon import TelegramClient
from telethon.sessions import StringSession

# ─── Configuração via GitHub Secrets ─────────────────────────────────────────

API_ID    = int(os.environ["TELEGRAM_API_ID"])
API_HASH  = os.environ["TELEGRAM_API_HASH"]
SESSION   = os.environ["TELEGRAM_SESSION"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID   = os.environ["MEU_CHAT_ID"]

# ─── Grupo alvo ───────────────────────────────────────────────────────────────

GRUPO = "cartoesmilhaseviagens"

# ─── Arquivo de estado ────────────────────────────────────────────────────────

STATE_FILE = "state.json"

# ─── CRITÉRIO 1: Bônus de transferência ──────────────────────────────────────

# Bancos/programas de origem que você acompanha
FONTES = [
    "livelo",
    "itau",       # captura "itaú"
    "brb",
    "curtai",     # captura "curtaí" (programa BRB)
]

# Programas de destino que você quer receber
PROGRAMAS_DESTINO = [
    # Nacional
    "latam",
    "latam pass",
    # Internacionais
    "american airlines",
    "aadvantage",
    "tap",
    "miles go",       # captura "miles&go"
    "united",
    "mileageplus",
    "mileage plus",
    "delta",
    "skymiles",
    "sky miles",
    "air france",
    "klm",
    "flying blue",
    "iberia",
    "british airways",
    "avios",
    "emirates",
    "qatar",
    "avianca",
    "lifemiles",
    "life miles",
]

# Palavras que indicam que é um bônus ou transferência
INDICADORES_BONUS = [
    "bonus",           # captura "bônus"
    "transferencia",   # captura "transferência"
    "bonus de transferencia",
    "transferencia com bonus",
]

# ─── CRITÉRIO 2: Compra de milhas/pontos ─────────────────────────────────────

# Todos os programas relevantes (fontes + destinos) — para check de compra
TODOS_PROGRAMAS = FONTES + PROGRAMAS_DESTINO

# Palavras que indicam compra/venda de pontos ou milhas
INDICADORES_COMPRA = [
    "compra de milhas",
    "compra de pontos",
    "comprar milhas",
    "comprar pontos",
    "milhas a venda",
    "venda de milhas",
    "compra milhas",
    "compra pontos",
    "milhas por ",    # ex: "milhas por R$"
    "pontos por ",    # ex: "pontos por R$"
    "preco por milha",
    "preco por ponto",
]

# ─── Utilidades ───────────────────────────────────────────────────────────────

def normalizar(texto: str) -> str:
    """Remove acentos, converte para minúsculas e normaliza espaços."""
    sem_acento = (
        unicodedata.normalize("NFD", texto)
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    sem_acento = sem_acento.replace("&", " ")
    sem_acento = re.sub(r"\s+", " ", sem_acento)
    return sem_acento.lower()


def contem_alguma(texto_norm: str, keywords: list[str]) -> bool:
    """Retorna True se o texto contém pelo menos uma das keywords."""
    for kw in keywords:
        kw_norm = normalizar(kw)
        padrao = r"(?<![a-z0-9])" + re.escape(kw_norm.strip()) + r"(?![a-z0-9])"
        if re.search(padrao, texto_norm):
            return True
    return False


def classificar_mensagem(texto_norm: str) -> str | None:
    """
    Retorna o tipo de alerta se a mensagem for relevante, ou None.
    Tipos: 'bonus_transferencia' | 'compra_pontos'
    """
    # Critério 1: bônus de transferência
    tem_fonte   = contem_alguma(texto_norm, FONTES)
    tem_destino = contem_alguma(texto_norm, PROGRAMAS_DESTINO)
    tem_bonus   = contem_alguma(texto_norm, INDICADORES_BONUS)

    if tem_fonte and tem_destino and tem_bonus:
        return "bonus_transferencia"

    # Critério 2: compra de pontos/milhas
    tem_programa = contem_alguma(texto_norm, TODOS_PROGRAMAS)
    tem_compra   = contem_alguma(texto_norm, INDICADORES_COMPRA)

    if tem_programa and tem_compra:
        return "compra_pontos"

    return None


def carregar_estado() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {"last_message_id": None}


def salvar_estado(estado: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(estado, f)


def enviar_mensagem_bot(texto: str) -> None:
    """Envia texto para o seu chat via Bot API do Telegram."""
    if len(texto) > 4000:
        texto = texto[:4000] + "\n\n[mensagem truncada]"

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": texto,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    resp = requests.post(url, json=payload, timeout=15)
    resp.raise_for_status()


# ─── Lógica principal ─────────────────────────────────────────────────────────

EMOJI_TIPO = {
    "bonus_transferencia": "🔁",
    "compra_pontos": "🛒",
}

LABEL_TIPO = {
    "bonus_transferencia": "Bônus de Transferência",
    "compra_pontos": "Compra de Pontos",
}

async def main() -> None:
    estado = carregar_estado()
    primeira_execucao = estado["last_message_id"] is None

    async with TelegramClient(StringSession(SESSION), API_ID, API_HASH) as client:
        grupo = await client.get_entity(GRUPO)

        if primeira_execucao:
            msgs = await client.get_messages(grupo, limit=1)
            if msgs:
                estado["last_message_id"] = msgs[0].id
                salvar_estado(estado)
                print(
                    f"[INIT] Primeira execução concluída. "
                    f"Ponto de partida: mensagem ID {msgs[0].id}."
                )
            else:
                print("[INIT] Grupo sem mensagens — nada a fazer.")
            return

        msgs = await client.get_messages(
            grupo,
            min_id=estado["last_message_id"],
            limit=300,
        )

        if not msgs:
            print(f"[OK] Nenhuma mensagem nova desde ID {estado['last_message_id']}.")
            return

        msgs = sorted(msgs, key=lambda m: m.id)
        print(f"[INFO] {len(msgs)} mensagens novas encontradas.")

        enviadas = 0
        for msg in msgs:
            texto = msg.text or ""
            if not texto.strip():
                continue

            texto_norm = normalizar(texto)
            tipo = classificar_mensagem(texto_norm)

            if tipo:
                data_hora = msg.date.strftime("%d/%m/%Y %H:%M")
                emoji = EMOJI_TIPO[tipo]
                label = LABEL_TIPO[tipo]
                cabecalho = (
                    f"{emoji} <b>{label}</b> | {data_hora}\n"
                    f"📌 <i>@{GRUPO}</i>\n"
                    f"{'─' * 30}\n\n"
                )
                try:
                    enviar_mensagem_bot(cabecalho + texto)
                    enviadas += 1
                    print(f"[ENVIADA] ID {msg.id} — tipo: {tipo}")
                    time.sleep(0.5)
                except Exception as e:
                    print(f"[ERRO] Falha ao enviar mensagem ID {msg.id}: {e}")
            else:
                print(f"[IGNORADA] ID {msg.id} — não atende aos critérios")

        estado["last_message_id"] = msgs[-1].id
        salvar_estado(estado)
        print(
            f"[OK] Processadas {len(msgs)} mensagens | "
            f"{enviadas} enviadas | "
            f"Novo checkpoint: ID {msgs[-1].id}"
        )


asyncio.run(main())
