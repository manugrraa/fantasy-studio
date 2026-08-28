#!/usr/bin/env python3
"""Sala de guerra: mensaje especial de Telegram la vispera de cada jornada.

Se ejecuta a diario; solo envia si la proxima jornada arranca en menos de 30 h
y aun no se ha enviado para esa jornada (estado en data/warroom_state.json).
Contenido: hora de inicio, bajas de TU plantilla, once sugerido (forma + media
+ dificultad del rival), vigilados a punto de objetivo y chollos del dia.
Necesita TELEGRAM_BOT_TOKEN y FANTASY_SYNC_KEY.
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
COMP = "2"  # Hypermotion, su liga principal


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


def form3(p):
    wp = (p.get("weekPoints") or [])[-3:]
    return sum(w.get("points") or 0 for w in wp) / len(wp) if wp else 0.0


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    key = os.environ.get("FANTASY_SYNC_KEY", "").strip()
    if not token or not key:
        print("sin token o sync key")
        return
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip() or str(load("telegram_chat.json", {}).get("chat_id") or "")
    if not chat:
        print("sin chat")
        return

    meta = load("meta.json", {})
    week = (meta.get("comps") or {}).get(COMP) or {}
    wk = week.get("weekNumber")
    opening = week.get("openingWeekDate")
    if not wk or not opening:
        print("sin datos de jornada")
        return
    try:
        start = datetime.fromisoformat(opening)
    except Exception:
        print("fecha de jornada ilegible")
        return
    now = datetime.now(start.tzinfo or timezone.utc)
    hours = (start - now).total_seconds() / 3600
    if not (0 < hours <= 30):
        print(f"jornada J{wk} arranca en {hours:.0f} h: fuera de ventana")
        return
    state = load("warroom_state.json", {})
    if state.get(COMP) == wk:
        print(f"sala de guerra de J{wk} ya enviada")
        return

    # datos personales desde la nube
    try:
        req = urllib.request.Request(f"https://fantasy-proxy.manugrraa.workers.dev/sync/{key}", headers=UA)
        with urllib.request.urlopen(req, timeout=30) as r:
            personal = json.load(r)
    except Exception as e:
        print("sync no disponible:", e)
        return

    players = load(f"players_{COMP}.json", [])
    by_id = {str(p["id"]): p for p in players}
    teams = {str(t["id"]): t for t in load("teams.json", [])}
    cal = (load(f"calendar_full_{COMP}.json", {}).get("weeks") or {}).get(str(wk)) or []

    # fuerza de cada equipo: media de puntos de sus 8 mejores
    strength = {}
    for tid in {str(p["teamId"]) for p in players}:
        pts = sorted((p.get("points") or 0 for p in players if str(p["teamId"]) == tid), reverse=True)[:8]
        strength[tid] = sum(pts) / len(pts) if pts else 0
    avg_str = sum(strength.values()) / len(strength) if strength else 0

    def rival_of(tid):
        for m in cal:
            if str(m.get("localId")) == tid:
                return str(m.get("visitorId")), True
            if str(m.get("visitorId")) == tid:
                return str(m.get("localId")), False
        return None, None

    # mi plantilla: bajas y puntuacion de cada uno
    squad = (personal.get("squad") or {}).get(COMP) or []
    bajas, cands = [], []
    ETIQ = {"injured": "lesionado", "suspended": "sancionado", "doubtful": "duda"}
    for e in squad:
        p = by_id.get(str(e.get("pid") or ""))
        if not p:
            continue
        st = p.get("playerStatus")
        if st in ETIQ:
            bajas.append(f"{p['nickname']} ({ETIQ[st]})")
            if st != "doubtful":
                continue
        riv, home = rival_of(str(p["teamId"]))
        bonus = 0.0
        riv_txt = ""
        if riv:
            s = strength.get(riv, avg_str)
            bonus = 1.5 if s < avg_str - 1 else -1.5 if s > avg_str + 1 else 0
            emoji = "🟢" if bonus > 0 else "🔴" if bonus < 0 else "🟡"
            riv_txt = f"{'vs' if home else 'en'} {teams.get(riv, {}).get('shortName', '?')} {emoji}"
        score = form3(p) * 2 + (p.get("averagePoints") or 0) + bonus
        cands.append((score, p, riv_txt))

    lines = [f"<b>⚔️ Sala de guerra — J{wk}</b>",
             f"🕗 Arranca {start.strftime('%A %H:%M').replace('Monday','lunes').replace('Tuesday','martes').replace('Wednesday','miércoles').replace('Thursday','jueves').replace('Friday','viernes').replace('Saturday','sábado').replace('Sunday','domingo')}"]
    if bajas:
        lines.append("🚑 <b>Ojo, no alinees:</b> " + " · ".join(bajas))
    if cands:
        cands.sort(key=lambda x: -x[0])
        POS = {"1": "POR", "2": "DEF", "3": "CEN", "4": "DEL"}
        top = [c for c in cands if str(c[1].get("positionId")) in POS][:11]
        lines.append("📋 <b>Tus mejores cartas hoy:</b>")
        for score, p, riv_txt in top[:6]:
            score_txt = f"{score:.1f}".replace(".", ",")
            lines.append(f"  {POS.get(str(p['positionId']), '?')} {p['nickname']} ({score_txt}) {riv_txt}")

    # vigilados a punto de objetivo
    hist = load(f"history_{COMP}.json", {})
    close = []
    for w in (personal.get("watch") or {}).get(COMP) or []:
        pid, target = str(w.get("pid") or ""), w.get("target")
        p, h = by_id.get(pid), hist.get(pid)
        if not p or not h or not target:
            continue
        val = h[-1][1]
        dist = (target - val) / target if w.get("dir") == "above" else (val - target) / target
        if 0 < dist <= 0.05:
            close.append(f"{p['nickname']} a {dist*100:.1f} % de {fmt_m(target)}".replace(".", ","))
    if close:
        lines.append("🎯 <b>Objetivos a punto:</b> " + " · ".join(close))

    lines.append('<a href="https://manugrraa.github.io/fantasy-studio/?view=liga&tab=mercado">Abrir el mercado antes del cierre</a>')

    if token == "dry":
        sys.stdout.buffer.write(("\n".join(lines) + "\n").encode("utf-8", "replace"))
        return

    body = urllib.parse.urlencode({
        "chat_id": chat, "text": "\n".join(lines), "parse_mode": "HTML", "disable_web_page_preview": "true",
    }).encode()
    try:
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=body, headers=UA)
        with urllib.request.urlopen(req, timeout=30) as r:
            ok = json.load(r).get("ok")
    except Exception as e:
        print("fallo al enviar:", e, file=sys.stderr)
        sys.exit(1)
    if ok:
        state[COMP] = wk
        save("warroom_state.json", state)
        print(f"sala de guerra J{wk} enviada")
    else:
        print("fallo al enviar")
        sys.exit(1)


if __name__ == "__main__":
    main()
