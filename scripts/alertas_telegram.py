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
API = "https://fantasy-api.llt-services.com/api"


def api_get(path):
    req = urllib.request.Request(API + path, headers={**UA, "Accept": "application/json", "x-lang": "es"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def partido_terminado(m):
    """Solo cuenta como acabado lo que la API marca como acabado (matchState 7):
    los marcadores existen ya DURANTE el partido y no valen como señal. De
    respaldo (sin matchState): marcador presente y 2h15 desde el inicio."""
    st = m.get("matchState")
    if st is not None:
        return st == 7
    if m.get("localScore") is None or m.get("visitorScore") is None:
        return False
    try:
        from datetime import datetime, timezone
        ini = datetime.fromisoformat(str(m.get("matchDate")))
        return (datetime.now(timezone.utc) - ini.astimezone(timezone.utc)).total_seconds() > 135 * 60
    except Exception:
        return False


def match_end_alerts(personal, meta, state):
    """🏁 Cuando TERMINA (confirmado) un partido con jugadores MÍOS: sus puntos
    finales y el total acumulado de mi jornada. Un aviso por partido."""
    alerts = []
    fin = state.setdefault("fin", {})
    for comp in ("2", "1"):
        week = (meta.get("comps") or {}).get(comp) or {}
        wn = week.get("weekNumber")
        if not wn:
            continue
        cal = (load(f"calendar_full_{comp}.json", {}).get("weeks") or {}).get(str(wn)) or []
        if not cal:
            continue
        players = {str(p["id"]): p for p in load(f"players_{comp}.json", [])}
        teams = {str(t["id"]): t for t in load("teams.json", [])}

        def wpts(pid):
            p = players.get(str(pid))
            if not p:
                return None
            for w in p.get("weekPoints") or []:
                if w.get("weekNumber") == wn:
                    return w.get("points") or 0
            return None

        # mis jugadores (plantilla + once, sin duplicar) agrupados por equipo real
        pids = []
        for e in (personal.get("squad") or {}).get(comp) or []:
            if e.get("pid") and not e.get("custom"):
                pids.append(str(e["pid"]))
        lineup = [str(x) for x in (((personal.get("lineup") or {}).get(comp) or {}).get("ids") or [])]
        for pid in lineup:
            if pid not in pids:
                pids.append(pid)
        if not pids:
            continue
        mine_by_team = {}
        for pid in pids:
            p = players.get(pid)
            if p:
                mine_by_team.setdefault(str(p["teamId"]), []).append(p)

        # total de mi jornada hasta ahora (el once si lo hay; si no, toda la plantilla)
        base = lineup or pids
        total = sum(wpts(pid) or 0 for pid in base)

        for m in cal:
            if not partido_terminado(m):
                continue
            mid = f"{comp}:{wn}:{m.get('id')}"
            if fin.get(mid):
                continue
            lid, vid = str(m.get("localId")), str(m.get("visitorId"))
            involved = (mine_by_team.get(lid) or []) + (mine_by_team.get(vid) or [])
            fin[mid] = 1
            if not involved:
                continue
            ln = teams.get(lid, {}).get("shortName") or teams.get(lid, {}).get("name") or "?"
            vn = teams.get(vid, {}).get("shortName") or teams.get(vid, {}).get("name") or "?"
            partes = [f"• {p['nickname']} — <b>{(wpts(p['id']) if wpts(p['id']) is not None else '—')} pts</b>" for p in involved]
            alerts.append(("Final de partido",
                f"🏁 <b>{ln} {m['localScore']}-{m['visitorScore']} {vn}</b> · puntos ya confirmados\n"
                + "\n".join(partes)
                + f"\n\n📊 Llevas <b>{total} pts</b> en la J{wn} (+{fmt_m(total * 100000)} de premio)"))
    return alerts


def live_goal_alerts(personal, meta, state):
    """⚽/🅰️ En jornada en vivo: goles y asistencias de MIS jugadores al momento.

    Consulta el detalle público de cada jugador de mi plantilla/once (~15
    peticiones) y compara los goles/asistencias de la jornada en curso con lo
    ya avisado (state['ga']). Solo corre con la jornada en juego (o FS_FORCE_LIVE).
    """
    alerts = []
    ga = state.setdefault("ga", {})
    force = os.environ.get("FS_FORCE_LIVE") == "1"
    for comp in ("2", "1"):
        week = (meta.get("comps") or {}).get(comp) or {}
        wn = week.get("weekNumber")
        if not wn or (not week.get("isLive") and not force):
            continue
        pids = []
        for e in (personal.get("squad") or {}).get(comp) or []:
            if e.get("pid") and not e.get("custom"):
                pids.append(str(e["pid"]))
        for pid in ((personal.get("lineup") or {}).get(comp) or {}).get("ids") or []:
            if str(pid) not in pids:
                pids.append(str(pid))
        for pid in pids[:20]:
            try:
                d = api_get(f"/v1/competition/{comp}/player/{pid}?x-lang=es")
            except Exception:
                continue
            name = d.get("nickname") or d.get("name") or "Tu jugador"
            wk = next((w for w in d.get("playerStats") or [] if w.get("weekNumber") == wn), None)
            if not wk:
                continue
            s = wk.get("stats") or {}
            goals = (s.get("goals") or [0])[0] or 0
            assists = (s.get("goal_assist") or [0])[0] or 0
            k = f"{comp}:{pid}:{wn}"
            prev = ga.get(k) or [0, 0]
            if goals > prev[0]:
                alerts.append(("¡Gol de los tuyos!", f"⚽ ¡GOOOL de <b>{name}</b>!{' (x' + str(goals) + ')' if goals > 1 else ''} — J{wn}"))
            if assists > prev[1]:
                alerts.append(("¡Gol de los tuyos!", f"🅰️ ¡Asistencia de <b>{name}</b>! — J{wn}"))
            if goals > prev[0] or assists > prev[1]:
                ga[k] = [goals, assists]
    return alerts


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


def madrid_now():
    from datetime import datetime, timezone, timedelta
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Europe/Madrid"))
    except Exception:
        return datetime.now(timezone(timedelta(hours=2)))


def today_int():
    return int(madrid_now().strftime("%Y%m%d"))


def trocear(msg, lim=3900):
    """Telegram corta en 4096: partimos por lineas ANTES de que corte el, para
    no perder jamas informacion de jugadores."""
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

    # goles y asistencias en vivo de mis jugadores
    meta = load("meta.json", {})
    alerts.extend(live_goal_alerts(personal, meta, state))
    # finales de partido con jugadores míos
    alerts.extend(match_end_alerts(personal, meta, state))

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
                alerts.append(("Objetivo alcanzado", f"🎯 <b>{p['nickname']}</b> {verbo} <b>{fmt_m(val)}</b> — tu objetivo de {fmt_m(target)} está alcanzado."))
            elif not reached and state["obj"].get(k):
                del state["obj"][k]  # se rearma

        # 💰 el parte del dia: valores de HOY confirmados (values_day.json) y
        # nunca antes de la 01:25 (los publica el juego a la 01:00; margen para
        # que todo asiente). UNA vez al dia: balance total de mi plantilla y
        # TODAS sus subidas y bajadas en lista, sin recortar nombres.
        vday = load("values_day.json", {})
        dia = state.setdefault("dia", {})
        ahora = madrid_now()
        pasa_hora = (ahora.hour, ahora.minute) >= (1, 25)
        if vday.get(comp) == today and dia.get(comp) != today and pasa_hora:
            ups, downs, total, known = [], [], 0, 0
            for e in (personal.get("squad") or {}).get(comp) or []:
                pid = e.get("pid")
                if not pid or e.get("custom"):
                    continue
                r = val_d1(pid)
                if not r:
                    continue
                p, val, d1 = r
                known += 1
                total += d1
                if d1 > 0:
                    ups.append((p, d1))
                elif d1 < 0:
                    downs.append((p, d1))
            if known:
                dia[comp] = today
                ups.sort(key=lambda x: -x[1])
                downs.sort(key=lambda x: x[1])
                emoji = "🟢" if total > 0 else ("🔴" if total < 0 else "⚪")
                bloque = [f"👥 Tu plantilla hoy: {emoji} <b>{sign(total) if total else 'sin cambios'}</b>"]
                if ups:
                    bloque.append("")
                    bloque.append("📈 <b>Suben</b>")
                    bloque.extend(f"• {p['nickname']}  <b>{sign(d)}</b>" for p, d in ups)
                if downs:
                    bloque.append("")
                    bloque.append("📉 <b>Bajan</b>")
                    bloque.extend(f"• {p['nickname']}  <b>{sign(d)}</b>" for p, d in downs)
                alerts.append(("Precios actualizados", "\n".join(bloque)))

    if not alerts:
        save("alert_state.json", state)  # re-armes y limpieza aunque no haya avisos
        print("sin novedades")
        return

    # un mensaje por CONCEPTO, con su propio título — nada de "alerta" genérica
    grupos = []
    for titulo, texto in alerts:
        g = next((g for g in grupos if g[0] == titulo), None)
        if g:
            g[1].append(texto)
        else:
            grupos.append((titulo, [texto]))
    mensajes = []
    for titulo, textos in grupos:
        cuerpo = (f"<b>⚡ Fantasy Studio — {titulo}</b>\n\n" + "\n\n".join(textos)
                  + '\n\n<a href="https://manugrraa.github.io/fantasy-studio/">Abrir la app</a>')
        mensajes.extend(trocear(cuerpo))

    if token.lower() == "dry":
        # modo prueba: imprime y NO toca el estado (el envio real sigue pendiente)
        out = "\n\n————————————\n\n".join(mensajes)
        sys.stdout.buffer.write((f"(dry) se enviarian {len(mensajes)} mensaje(s):\n\n" + out).encode("utf-8", "replace"))
        return

    for msg in mensajes:
        body = urllib.parse.urlencode({
            "chat_id": chat, "text": msg, "parse_mode": "HTML", "disable_web_page_preview": "true",
        }).encode()
        try:
            req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=body, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                if not json.load(r).get("ok"):
                    raise Exception("telegram dijo que no")
        except Exception as e:
            print("fallo al enviar:", e, file=sys.stderr)
            sys.exit(1)  # sin guardar estado: el proximo ciclo reintenta
    print(f"{len(mensajes)} mensaje(s) enviados")
    save("alert_state.json", state)  # solo tras enviar con éxito


if __name__ == "__main__":
    main()
