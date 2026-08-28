#!/usr/bin/env python3
"""Resumen semanal (domingo por la noche): balance de tu semana Fantasy.

Patrimonio ganado, operaciones de la semana con su resultado, nota de
entrenador y plan para la siguiente. Necesita TELEGRAM_BOT_TOKEN y
FANTASY_SYNC_KEY. Sin ellos, termina en silencio (o imprime con token=dry).
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
COMP = "2"


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
        return (f"{n/1_000_000:.1f}".replace(".", ",").rstrip("0").rstrip(",")) + "M"
    if a >= 1_000:
        return f"{round(n/1000)}K"
    return str(n)


def sign(n):
    return ("+" if n > 0 else "") + fmt_m(n)


def madrid_now():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Europe/Madrid"))
    except Exception:
        return datetime.now(timezone(timedelta(hours=2)))


def hist_value_at(hist, pid, day):
    h = hist.get(str(pid))
    if not h:
        return None
    best = None
    for e in h:
        if e[0] <= day:
            best = e[1]
        else:
            break
    return best


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

    try:
        req = urllib.request.Request(f"https://fantasy-proxy.manugrraa.workers.dev/sync/{key}", headers=UA)
        with urllib.request.urlopen(req, timeout=30) as r:
            personal = json.load(r)
    except Exception as e:
        print("sync no disponible:", e)
        return

    players = {str(p["id"]): p for p in load(f"players_{COMP}.json", [])}
    hist = load(f"history_{COMP}.json", {})
    now = madrid_now()
    today = int(now.strftime("%Y%m%d"))
    week_ago = int((now - timedelta(days=7)).strftime("%Y%m%d"))

    lines = [f"<b>📊 Fantasy Studio — balance semanal</b>"]

    # patrimonio: del diario subido a la nube
    wealth = (personal.get("wealth") or {}).get(COMP) or []
    if len(wealth) >= 2:
        last = wealth[-1]
        base = wealth[0]
        for e in wealth:
            if e[0] <= week_ago:
                base = e
            else:
                break
        d7 = last[1] - base[1]
        lines.append(f"💼 Patrimonio: <b>{fmt_m(last[1])}</b> ({sign(d7)} esta semana)")

    # operaciones de la semana
    moves = (personal.get("moves") or {}).get(COMP) or []
    week_moves = [m for m in moves if m.get("date") and int(m["date"].replace("-", "")) >= week_ago]
    sells = [m for m in week_moves if m.get("type") == "sell"]
    buys = [m for m in week_moves if m.get("type") == "buy"]
    if sells:
        gan = sum(m.get("profit") or 0 for m in sells)
        lines.append(f"🔁 Ventas: {len(sells)} ({sign(gan)} de beneficio)")
    aciertos, errores = [], []
    for m in buys:
        pid = m.get("pid")
        if not pid or pid not in players:
            continue
        h = hist.get(str(pid))
        if not h:
            continue
        diff = h[-1][1] - (m.get("price") or h[-1][1])
        (aciertos if diff > 0 else errores).append((players[pid]["nickname"], diff))
    if aciertos:
        aciertos.sort(key=lambda x: -x[1])
        lines.append("✅ <b>Fichajes acertados:</b> " + " · ".join(f"{n} {sign(d)}" for n, d in aciertos[:4]))
    if errores:
        errores.sort(key=lambda x: x[1])
        lines.append("❌ <b>De momento en rojo:</b> " + " · ".join(f"{n} {sign(d)}" for n, d in errores[:3]))

    # nota de entrenador (histórico completo de ventas)
    all_sells = [m for m in moves if m.get("type") == "sell" and m.get("pid")]
    if len(all_sells) >= 2:
        with_p = [m for m in all_sells if m.get("profit") is not None]
        ok_pct = (sum(1 for m in with_p if m["profit"] > 0) / len(with_p)) if with_p else 0
        t_sum, t_n = 0.0, 0
        for m in all_sells:
            day = int(m["date"].replace("-", "")) if m.get("date") else None
            h = hist.get(str(m["pid"]))
            if not day or not h:
                continue
            upto = [e[1] for e in h if e[0] <= day][-60:]
            if len(upto) > 2 and max(upto) > 0:
                t_sum += (m.get("price") or 0) / max(upto)
                t_n += 1
        timing = t_sum / t_n if t_n else 0.7
        nota = round(min(10, max(0, ok_pct * 6 + timing * 4)), 1)
        lines.append(f"🎓 Nota de entrenador: <b>{str(nota).replace('.', ',')}</b> ({round(ok_pct*100)} % ventas en verde)")

    # plan para la próxima semana
    plan = []
    for w in (personal.get("watch") or {}).get(COMP) or []:
        pid, target = str(w.get("pid") or ""), w.get("target")
        if pid in players and target:
            val = hist_value_at(hist, pid, today)
            if val:
                dist = (target - val) / target if w.get("dir") == "above" else (val - target) / target
                if 0 < dist <= 0.06:
                    plan.append(f"{players[pid]['nickname']} a {str(round(dist*100,1)).replace('.', ',')} % del objetivo")
    for e in (personal.get("squad") or {}).get(COMP) or []:
        pid = str(e.get("pid") or "")
        p = players.get(pid)
        if p and p.get("playerStatus") in ("injured", "suspended"):
            plan.append(f"decide qué hacer con {p['nickname']} (baja)")
    if plan:
        lines.append("🗓 <b>Para la semana que entra:</b> " + " · ".join(plan[:4]))

    lines.append('<a href="https://manugrraa.github.io/fantasy-studio/">Abrir Fantasy Studio</a>')
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
    print("resumen semanal enviado" if ok else "fallo")
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
