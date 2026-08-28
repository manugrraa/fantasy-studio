#!/usr/bin/env python3
"""Resumen diario del mercado Fantasy por Telegram.

Analiza los datos ya descargados en data/ (no llama a la API del Fantasy) y
envia un mensaje con lo mas relevante del dia: subidas, bajadas, chollos y
jornada. Solo necesita UN secret del repositorio:
  TELEGRAM_BOT_TOKEN  (de @BotFather)
El chat id se detecta solo: la primera vez, el dueno le escribe un "hola" al
bot y el script lo descubre via getUpdates y lo guarda en
data/telegram_chat.json (el workflow lo commitea). TELEGRAM_CHAT_ID sigue
funcionando como override opcional. Sin token, imprime el mensaje y sale.
"""
import json
import os
import sys
import urllib.request
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
COMPS = [("2", "LaLiga Hypermotion"), ("1", "LaLiga EA Sports")]


def load(name, default):
    try:
        with open(os.path.join(DATA, name), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def fmt_m(n):
    n = int(n)
    a = abs(n)
    if a >= 1_000_000:
        s = f"{n/1_000_000:.1f}".replace(".", ",").rstrip("0").rstrip(",")
        return s + "M"
    if a >= 1_000:
        return f"{round(n/1000)}K"
    return str(n)


def sign(n):
    return ("+" if n > 0 else "") + fmt_m(n)


def today_madrid_int():
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        return int(datetime.now(ZoneInfo("Europe/Madrid")).strftime("%Y%m%d"))
    except Exception:
        return None


def values_fresh(comp):
    """True si el historico ya tiene el valor de HOY (el juego los cambia de madrugada)."""
    today = today_madrid_int()
    if not today:
        return True
    hist = load(f"history_{comp}.json", {})
    for series in list(hist.values())[:80]:
        if series and series[-1][0] >= today:
            return True
    return False


def discover_chat(token):
    """Chat id: primero el guardado; si no, getUpdates (el dueno saluda al bot una vez)."""
    saved = load("telegram_chat.json", {})
    if saved.get("chat_id"):
        return str(saved["chat_id"])
    try:
        with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/getUpdates", timeout=30) as r:
            data = json.load(r)
        for upd in reversed(data.get("result", [])):
            chat = ((upd.get("message") or upd.get("edited_message") or {}).get("chat")) or {}
            if chat.get("id"):
                cid = str(chat["id"])
                with open(os.path.join(DATA, "telegram_chat.json"), "w", encoding="utf-8") as f:
                    json.dump({"chat_id": cid, "name": chat.get("first_name") or chat.get("title") or ""}, f)
                print("chat descubierto y guardado:", cid)
                return cid
    except Exception as e:
        print("getUpdates fallo:", e, file=sys.stderr)
    return None


def analyze(comp):
    players = load(f"players_{comp}.json", [])
    hist = load(f"history_{comp}.json", {})
    rows = []
    for p in players:
        h = hist.get(str(p["id"]))
        if not h or len(h) < 2:
            continue
        d1 = h[-1][1] - h[-2][1]
        rows.append((p, d1))
    ups = sorted([r for r in rows if r[1] > 0], key=lambda r: -r[1])[:5]
    downs = sorted([r for r in rows if r[1] < 0], key=lambda r: r[1])[:5]

    # chollos: rendimiento por millon entre los que juegan
    cheap = []
    for p in players:
        v = int(p.get("marketValue") or 0)
        pts = p.get("points") or 0
        if v > 0 and pts >= 10 and p.get("playerStatus") == "ok":
            cheap.append((p, pts / (v / 1_000_000)))
    cheap.sort(key=lambda r: -r[1])
    return ups, downs, cheap[:3]


def main():
    meta = load("meta.json", {})
    lines = ["<b>⚡ Fantasy Studio — resumen del día</b>"]
    if not values_fresh("2"):
        lines.append("⏳ <i>Ojo: los valores de hoy aún no se habían publicado al enviar esto.</i>")

    for comp, label in COMPS:
        week = (meta.get("comps") or {}).get(comp) or {}
        wk = week.get("weekNumber")
        live = " · <b>EN JUEGO</b>" if week.get("isLive") else ""
        ups, downs, cheap = analyze(comp)
        lines.append("")
        lines.append(f"<b>{'🔵' if comp == '2' else '🔴'} {label}</b>" + (f" · J{wk}{live}" if wk else ""))
        if ups:
            lines.append("📈 <b>Suben:</b> " + " · ".join(f"{p['nickname']} {sign(d)}" for p, d in ups))
        if downs:
            lines.append("📉 <b>Caen:</b> " + " · ".join(f"{p['nickname']} {sign(d)}" for p, d in downs))
        if cheap and comp == "2":
            lines.append("💎 <b>Chollos (pts/M€):</b> " + " · ".join(
                f"{p['nickname']} ({v:.1f})".replace(".", ",") for p, v in cheap))

    lines.append("")
    lines.append('<a href="https://manugrraa.github.io/fantasy-studio/">Abrir Fantasy Studio</a>')
    msg = "\n".join(lines)

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        out = "(sin TELEGRAM_BOT_TOKEN; mensaje que se habria enviado:)\n\n" + msg
        sys.stdout.buffer.write(out.encode("utf-8", "replace"))
        return

    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip() or discover_chat(token)
    if not chat:
        print("Aun no se conoce el chat: escribele cualquier mensaje al bot en "
              "Telegram y este workflow lo detectara en la proxima ejecucion.")
        return

    body = urllib.parse.urlencode({
        "chat_id": chat, "text": msg, "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=body)
    with urllib.request.urlopen(req, timeout=30) as r:
        ok = json.load(r).get("ok")
    print("enviado" if ok else "fallo al enviar", file=sys.stderr if not ok else sys.stdout)
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
