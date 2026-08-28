#!/usr/bin/env python3
"""Alertas al instante por Telegram (corre con el workflow de datos, cada 15 min).

Avisa en el momento en que:
  - un jugador VIGILADO alcanza tu precio objetivo (se rearma si se aleja)
  - un jugador de TU PLANTILLA sube o baja fuerte en el dia (>= 400K)

Necesita TELEGRAM_BOT_TOKEN y FANTASY_SYNC_KEY (los mismos del resumen);
sin ellos termina en silencio. El estado anti-repeticion vive en
data/alert_state.json (lo commitea el workflow de datos).
"""
import json
import os
import sys
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
UA = {"User-Agent": "Mozilla/5.0 (compatible; FantasyStudio/1.0)"}
MOVE_THRESHOLD = 400000


def load(name, default):
    try:
        with open(os.path.join(DATA, name), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save(name, obj):
    with open(os.path.join(DATA, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


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


def today_int():
    from datetime import datetime, timezone, timedelta
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo("Europe/Madrid"))
    except Exception:
        now = datetime.now(timezone(timedelta(hours=2)))
    return int(now.strftime("%Y%m%d"))


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    key = os.environ.get("FANTASY_SYNC_KEY", "").strip()
    if not token or not key:
        print("sin token o sync key: alertas desactivadas")
        return
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip() or str(load("telegram_chat.json", {}).get("chat_id") or "")
    if not chat:
        print("sin chat conocido todavia")
        return

    try:
        req = urllib.request.Request(f"https://fantasy-proxy.manugrraa.workers.dev/sync/{key}", headers=UA)
        with urllib.request.urlopen(req, timeout=30) as r:
            personal = json.load(r)
    except Exception as e:
        print("sync no disponible:", e)
        return

    state = load("alert_state.json", {"obj": {}, "moves": {}})
    today = today_int()
    alerts = []

    for comp in ("2", "1"):
        players = {str(p["id"]): p for p in load(f"players_{comp}.json", [])}
        hist = load(f"history_{comp}.json", {})

        def val_d1(pid):
            p = players.get(str(pid))
            h = hist.get(str(pid))
            if not p or not h:
                return None
            d1 = h[-1][1] - h[-2][1] if len(h) >= 2 else 0
            return p, h[-1][1], d1

        # objetivos de vigilados
        for w in (personal.get("watch") or {}).get(comp) or []:
            pid, target = w.get("pid"), w.get("target")
            if not pid or not target:
                continue
            r = val_d1(pid)
            if not r:
                continue
            p, val, _ = r
            reached = val >= target if w.get("dir") == "above" else val <= target
            k = f"{comp}:{pid}:{target}"
            if reached and not state["obj"].get(k):
                state["obj"][k] = today
                verbo = "ha subido hasta" if w.get("dir") == "above" else "ha bajado hasta"
                alerts.append(f"🎯 <b>{p['nickname']}</b> {verbo} <b>{fmt_m(val)}</b> — tu objetivo de {fmt_m(target)} está alcanzado.")
            elif not reached and state["obj"].get(k):
                del state["obj"][k]  # se rearma

        # movimientos fuertes de mi plantilla (una vez al dia por jugador)
        for e in (personal.get("squad") or {}).get(comp) or []:
            pid = e.get("pid")
            if not pid or e.get("custom"):
                continue
            r = val_d1(pid)
            if not r:
                continue
            p, val, d1 = r
            k = f"{comp}:{pid}"
            if abs(d1) >= MOVE_THRESHOLD and state["moves"].get(k) != today:
                state["moves"][k] = today
                emoji = "🚀" if d1 > 0 else "⚠️"
                alerts.append(f"{emoji} <b>{p['nickname']}</b> (tu plantilla) {sign(d1)} hoy → {fmt_m(val)}.")

    save("alert_state.json", state)
    if not alerts:
        print("sin novedades")
        return

    msg = "\n".join(["<b>⚡ Fantasy Studio — alerta</b>"] + alerts +
                    ['<a href="https://manugrraa.github.io/fantasy-studio/">Abrir la app</a>'])
    body = urllib.parse.urlencode({
        "chat_id": chat, "text": msg, "parse_mode": "HTML", "disable_web_page_preview": "true",
    }).encode()
    try:
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=body, headers=UA)
        with urllib.request.urlopen(req, timeout=30) as r:
            ok = json.load(r).get("ok")
    except Exception as e:
        print("fallo al enviar:", e, file=sys.stderr)
        sys.exit(1)
    print("alerta enviada" if ok else "fallo al enviar")
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
