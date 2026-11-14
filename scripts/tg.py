#!/usr/bin/env python3
"""Utilidades comunes del bot de Telegram de Fantasy Studio.

UN solo estilo para todos los mensajes (decidido con él, 30/8):
  - título por CONCEPTO: «⚡ Fantasy Studio — Precios actualizados», nunca «alerta»;
  - jugadores en LISTA vertical con viñetas, nunca seguidos por comas;
  - línea en blanco entre bloques (suben / bajan…);
  - JAMÁS recortar información de sus jugadores («y 2 más» prohibido): si un
    mensaje no cabe, se parte en varios por líneas.

Además: envío con modo prueba (TELEGRAM_BOT_TOKEN=dry imprime y no toca el
estado) y guardas de «una sola vez» para que dos relojes que disparen el mismo
aviso (Cloudflare y GitHub) no lo manden repetido.
"""
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
UA = {"User-Agent": "Mozilla/5.0 (compatible; FantasyStudio/1.0)"}
WORKER = "https://fantasy-proxy.manugrraa.workers.dev"
APP_URL = "https://manugrraa.github.io/fantasy-studio/"
LIMITE = 3900  # Telegram corta en 4096


# ---------- ficheros ----------
def load(name, default):
    try:
        with open(os.path.join(DATA, name), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save(name, obj):
    with open(os.path.join(DATA, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


# ---------- tiempo ----------
def madrid_now():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Europe/Madrid"))
    except Exception:  # sin tzdata (Windows local): aproximación de verano
        return datetime.now(timezone(timedelta(hours=2)))


def today_int():
    return int(madrid_now().strftime("%Y%m%d"))


DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def fecha_larga(d=None):
    d = d or madrid_now()
    return f"{DIAS[d.weekday()]} {d.day} de {MESES[d.month - 1]}"


# ---------- formato ----------
def fmt_m(n):
    n = int(n)
    a = abs(n)
    if a >= 1_000_000:
        return (f"{n/1_000_000:.1f}".replace(".", ",").rstrip("0").rstrip(",")) + "M"
    if a >= 1_000:
        return f"{round(n/1000)}K"
    return str(n)


def sign(n):
    return ("+" if n > 0 else "") + fmt_m(n)


def esc(s):
    """Texto de usuario dentro de HTML de Telegram (nombres de rivales, etc.)."""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def titulo(concepto):
    return f"<b>⚡ Fantasy Studio — {concepto}</b>"


def seccion(cabecera, items):
    """Bloque con cabecera y lista vertical. Devuelve [] si no hay nada."""
    items = [i for i in items if i]
    if not items:
        return []
    return ["", cabecera] + [f"• {i}" for i in items]


def pie(texto="Abrir Fantasy Studio", url=APP_URL):
    return ["", f'<a href="{url}">{texto}</a>']


def trocear(msg, lim=LIMITE):
    """Parte por líneas ANTES de que lo haga Telegram: nunca se pierde nada."""
    if len(msg) <= lim:
        return [msg]
    partes, actual = [], ""
    for linea in msg.split("\n"):
        if actual and len(actual) + len(linea) + 1 > lim:
            partes.append(actual)
            actual = linea
        else:
            actual = actual + "\n" + linea if actual else linea
    if actual:
        partes.append(actual)
    return partes


# ---------- conexión ----------
def token():
    return os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()


def es_prueba():
    return token().lower() == "dry"


def chat_id():
    return os.environ.get("TELEGRAM_CHAT_ID", "").strip() or str(load("telegram_chat.json", {}).get("chat_id") or "")


def personal():
    """Copia personal en la nube (plantilla, vigilados, dinero…); None si no hay."""
    key = os.environ.get("FANTASY_SYNC_KEY", "").strip()
    if not key:
        return None
    try:
        req = urllib.request.Request(f"{WORKER}/sync/{key}", headers=UA)
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except Exception as e:
        print("nube personal no disponible:", e, file=sys.stderr)
        return None


def enviar(lineas):
    """Envía (troceando si hace falta). True si salió todo. En modo prueba imprime."""
    msg = "\n".join(lineas) if isinstance(lineas, list) else lineas
    partes = trocear(msg)
    if es_prueba():
        sys.stdout.buffer.write(("\n\n———— corte de mensaje ————\n\n".join(partes) + "\n").encode("utf-8", "replace"))
        return True
    tk, chat = token(), chat_id()
    if not tk or not chat:
        print("sin token o sin chat conocido: no se envía")
        return False
    for parte in partes:
        body = urllib.parse.urlencode({
            "chat_id": chat, "text": parte, "parse_mode": "HTML", "disable_web_page_preview": "true",
        }).encode()
        try:
            req = urllib.request.Request(f"https://api.telegram.org/bot{tk}/sendMessage", data=body, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                if not json.load(r).get("ok"):
                    raise Exception("Telegram respondió que no")
        except Exception as e:
            print("fallo al enviar:", e, file=sys.stderr)
            return False
    return True


# ---------- «una sola vez» ----------
def ya_enviado(fichero, clave, valor):
    return not es_prueba() and load(fichero, {}).get(clave) == valor


def marcar_enviado(fichero, clave, valor):
    if es_prueba():
        return
    st = load(fichero, {})
    st[clave] = valor
    save(fichero, st)
