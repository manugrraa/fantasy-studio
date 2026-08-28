#!/usr/bin/env python3
"""Resumen diario del mercado Fantasy por Telegram.

Analiza los datos ya descargados en data/ (no llama a la API del Fantasy) y
envia un mensaje con lo mas relevante del dia: subidas, bajadas, chollos y
jornada. Necesita dos secrets del repositorio:
  TELEGRAM_BOT_TOKEN  (de @BotFather)
  TELEGRAM_CHAT_ID    (tu chat con el bot)
Sin secrets, imprime el mensaje y termina sin error (para probar en local).
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
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        out = "(sin secrets de Telegram; mensaje que se habria enviado:)\n\n" + msg
        sys.stdout.buffer.write(out.encode("utf-8", "replace"))
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
