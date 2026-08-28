#!/usr/bin/env python3
"""Cronica post-jornada: la manana siguiente al cierre de cada jornada.

Tu once (el montado en Mi Plantilla, via nube), sus puntos, MVP y pufo,
premio estimado a 100k/punto y el mejor de la jornada en toda la liga.
Se envia una sola vez por jornada (estado en data/cronica_state.json).
Necesita TELEGRAM_BOT_TOKEN y FANTASY_SYNC_KEY. token=dry imprime.
"""
import json
import os
import sys
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
UA = {"User-Agent": "Mozilla/5.0 (compatible; FantasyStudio/1.0)"}
COMP = "2"


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


def wpts(p, week):
    for w in p.get("weekPoints") or []:
        if w.get("weekNumber") == week:
            return w.get("points") or 0
    return None


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    key = os.environ.get("FANTASY_SYNC_KEY", "").strip()
    if not token or not key:
        print("sin token o sync key")
        return
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip() or str(load("telegram_chat.json", {}).get("chat_id") or "")
    if not chat and token != "dry":
        print("sin chat")
        return

    meta = load("meta.json", {})
    week = (meta.get("comps") or {}).get(COMP) or {}
    prev = week.get("previousWeek")
    if not prev:
        print("sin jornada previa")
        return
    if week.get("isLive"):
        print("jornada en juego: la crónica espera al cierre")
        return
    state = load("cronica_state.json", {})
    if state.get(COMP) == prev:
        print(f"crónica de J{prev} ya enviada")
        return

    try:
        req = urllib.request.Request(f"https://fantasy-proxy.manugrraa.workers.dev/sync/{key}", headers=UA)
        with urllib.request.urlopen(req, timeout=30) as r:
            personal = json.load(r)
    except Exception as e:
        print("sync no disponible:", e)
        return

    players = load(f"players_{COMP}.json", [])
    by_id = {str(p["id"]): p for p in players}

    # mi once: el montado en la app; si no, toda la plantilla
    lineup = ((personal.get("lineup") or {}).get(COMP) or {}).get("ids") or []
    if not lineup:
        lineup = [e.get("pid") for e in (personal.get("squad") or {}).get(COMP) or [] if e.get("pid")]
    mine = []
    for pid in lineup:
        p = by_id.get(str(pid))
        if not p:
            continue
        pts = wpts(p, prev)
        if pts is not None:
            mine.append((p["nickname"], pts))
    if not mine:
        print("sin datos de mi once para esa jornada")
        return

    total = sum(pts for _, pts in mine)
    mine.sort(key=lambda x: -x[1])
    mvp, pufo = mine[0], mine[-1]

    # el crack de la jornada en toda la competición
    best = max(((p, wpts(p, prev)) for p in players), key=lambda x: (x[1] or -99))

    lines = [f"<b>📰 Crónica de la J{prev}</b>",
             f"⚽ Tu once: <b>{total} puntos</b> → <b>+{fmt_m(total * 100000)}</b> de premio",
             f"🏅 Tu MVP: <b>{mvp[0]}</b> ({mvp[1]} pts)"]
    if pufo[1] <= 2 and pufo != mvp:
        lines.append(f"🫠 El pufo: <b>{pufo[0]}</b> ({pufo[1]} pts)")
    if best[1] is not None:
        lines.append(f"👑 Crack de la jornada en la liga: <b>{best[0]['nickname']}</b> ({best[1]} pts)")
    lines.append('<a href="https://manugrraa.github.io/fantasy-studio/?view=liga&tab=rivales">Ver cómo queda tu liga</a>')
    msg = "\n".join(lines)

    if token == "dry":
        sys.stdout.buffer.write((msg + "\n").encode("utf-8", "replace"))
        return
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
    if ok:
        state[COMP] = prev
        save("cronica_state.json", state)
        print(f"crónica J{prev} enviada")
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
