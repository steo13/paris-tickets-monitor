"""
Monitor prenotazioni musei Parigi - controlla cambiamenti sulle pagine
di prenotazione e notifica via Telegram quando rileva una variazione.

Come funziona:
1. Apre ogni URL con un browser headless (necessario: questi siti sono
   Single Page App in JavaScript, un semplice requests.get() non basta)
2. Estrae il testo "pulito" della pagina (rimuove menu/footer/pubblicità)
3. Confronta con l'ultimo stato salvato (state.json)
4. Se è cambiato E contiene indizi legati alla data target -> notifica
5. Se è cambiato ma senza indizi chiari -> notifica comunque, ma con
   priorità bassa, così sai di andare a controllare a mano
"""

import json
import hashlib
import os
import sys
from datetime import datetime, timezone

import requests
from playwright.sync_api import sync_playwright

STATE_FILE = "state.json"
CONFIG_FILE = "sites.json"

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]


def send_telegram(message: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    resp = requests.post(
        url,
        data={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"},
        timeout=20,
    )
    resp.raise_for_status()


def load_json(path: str, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: str, data) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def fetch_rendered_text(page, url: str) -> str:
    page.goto(url, wait_until="networkidle", timeout=60000)
    # Aspetta un po' extra: molte SPA finiscono di popolare il calendario
    # anche dopo l'evento "networkidle"
    page.wait_for_timeout(3000)
    body_text = page.inner_text("body")
    # Normalizza whitespace per evitare falsi positivi dovuti a spazi
    return " ".join(body_text.split())


def check_site(page, site: dict, previous_state: dict) -> dict:
    name = site["name"]
    url = site["url"]
    keywords = site.get("keywords", [])  # es. ["5 décembre", "06/12/2026"]

    print(f"--- Controllo {name} ---")
    try:
        text = fetch_rendered_text(page, url)
    except Exception as e:
        print(f"Errore caricando {name}: {e}")
        return previous_state.get(name, {})

    current_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    prev = previous_state.get(name, {})
    prev_hash = prev.get("hash")

    keyword_hits = [kw for kw in keywords if kw.lower() in text.lower()]

    changed = current_hash != prev_hash
    is_first_run = prev_hash is None

    if changed and not is_first_run:
        if keyword_hits:
            send_telegram(
                f"🎯 <b>{name}</b>\n"
                f"La pagina è cambiata E contiene la data che cerchi!\n"
                f"Parole trovate: {', '.join(keyword_hits)}\n"
                f"Vai a controllare SUBITO:\n{url}"
            )
        else:
            send_telegram(
                f"⚠️ <b>{name}</b>\n"
                f"La pagina di prenotazione è cambiata (ma non ho trovato "
                f"riferimenti diretti alla tua data). Meglio dare un'occhiata:\n{url}"
            )
    elif is_first_run:
        print(f"{name}: primo run, salvo lo stato iniziale senza notificare.")

    return {
        "hash": current_hash,
        "last_checked": datetime.now(timezone.utc).isoformat(),
        "keyword_hits": keyword_hits,
    }


def main():
    sites = load_json(CONFIG_FILE, [])
    if not sites:
        print("Nessun sito configurato in sites.json")
        sys.exit(1)

    state = load_json(STATE_FILE, {})

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ))
        for site in sites:
            state[site["name"]] = check_site(page, site, state)
        browser.close()

    save_json(STATE_FILE, state)
    print("Fatto.")


if __name__ == "__main__":
    main()
