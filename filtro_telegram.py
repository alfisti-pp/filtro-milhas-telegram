"""
filtro_telegram.py
------------------
Lê mensagens novas do grupo público do Telegram @cartoesmilhaseviagens,
filtra por palavras-chave de milhas aéreas e reencaminha as relevantes
para o seu chat pessoal via bot.

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

# ─── Arquivo de estado (preservado entre execuções via cache do Actions) ──────

STATE_FILE = "state.json"

# ─── Palavras-chave principais (basta UMA estar presente) ─────────────────────
# A comparação ignora maiúsculas/minúsculas e acentos.

KEYWORDS = [
    # Programas nacionais
    "livelo",
    "itau",        # captura "itaú" após normalização
    "brb",
    "curtai",      # captura "curtaí"
    "dux",

    # Latam
    "latam",
    "latam pass",

    # Internacionais
    "american airlines",
    "aadvantage",
    "tap",
    "miles go",    # captura "miles&go" — o & some na normalização
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

# ─── Palavras-chave de bônus/promoção (filtro adicional opcional) ─────────────
# Se EXIGIR_BONUS=true, a mensagem precisa ter keyword principal E bônus.

BONUS_KEYWORDS = [
    "%",
    "bonus",       # captura "bônus"
    "transferencia",
    "oferta",
    "promocao",    # captura "promoção"
    "desconto",
    "pontos",
    "milhas",
]

# Leia do ambiente; padrão = False (filtro amplo)
EXIGIR_BONUS = os.environ.get("EXIGIR_BONUS", "false").strip().lower() == "true"

# ─── Utilidades ───────────────────────────────────────────────────────────────

def normalizar(texto: str) -> str:
    """Remove acentos e converte para minúsculas para comparação uniforme."""
    sem_acento = (
        unicodedata.normalize("NFD", texto)
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    # Substitui & por espaço para capturar "miles&go" como "miles go"
    sem_acento = sem_acento.replace("&", " ")
    # Colapsa múltiplos espaços
    sem_acento = re.sub(r"\s+", " ", sem_acento)
    return sem_acento.lower()


def contem_alguma(texto_norm: str, keywords: list[str]) -> bool:
    """Verifica se o texto contém pelo menos uma das keywords (já normalizadas)."""
    for kw in keywords:
        kw_norm = normalizar(kw)
        padrao = r"(?<![a-z0-9])" + re.escape(kw_norm) + r"(?![a-z0-9])"
        if re.search(padrao, texto_norm):
            return True
    return False


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
                    f"Ponto de partida: mensagem ID {msgs[0].id}. "
                    f"A partir da próxima execução, mensagens novas serão filtradas."
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

            tem_keyword = contem_alguma(texto_norm, KEYWORDS)
            tem_bonus   = contem_alguma(texto_norm, BONUS_KEYWORDS) if EXIGIR_BONUS else True

            if tem_keyword and tem_bonus:
                data_hora = msg.date.strftime("%d/%m/%Y %H:%M")
                cabecalho = (
                    f"✈️ <b>Milhas Alert</b> | {data_hora}\n"
                    f"📌 <i>@{GRUPO}</i>\n"
                    f"{'─' * 30}\n\n"
                )
                try:
                    enviar_mensagem_bot(cabecalho + texto)
                    enviadas += 1
                    time.sleep(0.5)
                except Exception as e:
                    print(f"[ERRO] Falha ao enviar mensagem ID {msg.id}: {e}")

        estado["last_message_id"] = msgs[-1].id
        salvar_estado(estado)
        print(
            f"[OK] Processadas {len(msgs)} mensagens | "
            f"{enviadas} enviadas | "
            f"Novo checkpoint: ID {msgs[-1].id}"
        )


asyncio.run(main())
