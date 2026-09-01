#!/usr/bin/env python3
"""Actualiza los datos de LaLiga Fantasy para Fantasy Studio.

Descarga de la API oficial (fantasy-api.llt-services.com, sin login):
  - Lista de jugadores de LaLiga EA Sports (competicion 1) y Hypermotion (competicion 2)
  - Equipos (teams-master), jornada actual y calendario
  - Historico diario de valor de mercado por jugador

El historico se siembra una vez por jugador (endpoint market-value) y despues
se alimenta con un punto diario tomado de la lista de jugadores, asi el
workflow rutinario hace ~10 peticiones y no ~1500.
"""
import json
import os
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

try:
    from zoneinfo import ZoneInfo
    MADRID = ZoneInfo("Europe/Madrid")
except Exception:
    MADRID = None

BASE = "https://fantasy-api.llt-services.com/api"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
COMPS = ("1", "2")
HDRS = {
    "User-Agent": "Mozilla/5.0 (compatible; FantasyStudio/1.0)",
    "Accept": "application/json",
    "x-lang": "es",
}
# Limite de siembras por ejecucion para que el workflow no se eternice si
# aparecen muchos jugadores nuevos de golpe; el resto se siembra al dia siguiente.
SEED_CAP = 500
SEED_DELAY = 0.12


def get(path, retries=3):
    # La API va detrás de CloudFront con caché de hasta 8 h (max-age=28800):
    # sin esto, el workflow puede recibir valores viejos justo tras la 01:00,
    # que es cuando el juego publica los nuevos. Un parámetro único por
    # petición fuerza a la CDN a ir siempre al origen.
    bust = f"_ts={int(time.time() * 1000)}"
    url = BASE + path + ("&" if "?" in path else "?") + bust
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=HDRS)
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except Exception:
            if i == retries - 1:
                raise
            time.sleep(1.5 * (i + 1))


def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def now_madrid():
    return datetime.now(MADRID) if MADRID else datetime.utcnow()


def today_int():
    return int(now_madrid().strftime("%Y%m%d"))


def date_to_int(iso):
    # "2026-08-07T00:00:00+02:00" -> 20260807
    return int(iso[:10].replace("-", ""))


def _prev_day_value(series, today):
    for d, v in reversed(series or []):
        if d < today:
            return v
    return None


def deep_sync(comp, players, hist, today):
    """La AUTORIDAD de los valores: la serie market-value POR JUGADOR (entradas
    con fecha, cache propia casi siempre limpia). Sincroniza los ultimos 7 dias
    del historico con la serie oficial — repara tanto cache vieja como los
    valores PRELIMINARES que el juego publica a la 01:00 y revisa despues.
    Devuelve cuantos jugadores tienen ya entrada de HOY."""
    desde = int((now_madrid() - timedelta(days=7)).strftime("%Y%m%d"))
    got = [0]

    def one(p):
        pid = str(p["id"])
        try:
            raw = get(f"/v1/competition/{comp}/player/{pid}/market-value?x-lang=es")
            serie = sorted({date_to_int(e["date"]): int(e["marketValue"]) for e in raw}.items())
            if not serie:
                return
            porDia = dict(hist.get(pid) or [])
            for d, v in serie:
                if d >= desde:
                    porDia[d] = v
            hist[pid] = sorted([list(x) for x in porDia.items()])
            hoy = dict(serie).get(today)
            if hoy:
                p["marketValue"] = hoy
                got[0] += 1
            else:
                p["marketValue"] = serie[-1][1]
        except Exception:
            pass

    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(one, players))
    return got[0]


def refresh_values(comp, players, marker):
    """Valores fiables pese a la CDN (que cachea horas e ignora la query) y a
    las REVISIONES del juego (publica preliminares a la 01:00 y los asienta
    despues — comprobado el 31/8-1/9, cuando el candado congelo preliminares).

    - Madrugada (01:00-03:00): pasada PROFUNDA con la serie oficial por jugador
      cada >=20 min (repara ademas los ultimos 7 dias del historico). Confirma
      el dia cuando >=50% tienen entrada de hoy.
    - Dia sin confirmar fuera de madrugada: tambien pasada profunda (max 1/h).
    - Resto de ciclos: los valores de la lista solo entran si son NUEVOS de
      verdad — un valor ya visto en los ultimos 3 dias del historico es cache
      vieja y se pisa con el valor guardado. Una revision genuina (valor nunca
      visto) si se acepta.
    """
    path = os.path.join(DATA, f"history_{comp}.json")
    hist = load(path, {})
    today = today_int()
    ahora = now_madrid()
    confirmed = marker.get(str(comp)) == today
    epoch = int(time.time())
    last_deep = marker.get("deep_" + str(comp)) or 0

    madrugada = 1 <= ahora.hour < 3
    deep = (madrugada and epoch - last_deep >= 20 * 60) or \
           (not confirmed and 1 <= ahora.hour < 24 and epoch - last_deep >= 60 * 60)
    if deep:
        con_hoy = deep_sync(comp, players, hist, today)
        save(path, hist)
        marker["deep_" + str(comp)] = epoch
        if con_hoy >= len(players) * 0.5:
            marker[str(comp)] = today
        print(f"[comp {comp}] valores: pasada profunda oficial ({con_hoy}/{len(players)} con valor de hoy)"
              + (" -> dia confirmado" if marker.get(str(comp)) == today else " (dia aun sin confirmar)"))
        return

    # sin pasada profunda: filtro de la lista contra el historico reciente
    protegidos = revisados = 0
    for p in players:
        pid = str(p["id"])
        v = int(p.get("marketValue") or 0)
        s = hist.get(pid)
        if not v or not s:
            continue
        stored = s[-1][1] if s[-1][0] == today else None
        if stored is None or v == stored:
            continue
        recientes = {x[1] for x in s[-4:] if x[0] < today}
        if v in recientes:
            p["marketValue"] = stored  # valor ya visto dias atras = cache vieja
            protegidos += 1
        else:
            revisados += 1  # valor nunca visto = revision del juego: se acepta
    if protegidos or revisados:
        print(f"[comp {comp}] valores: {protegidos} protegidos de cache vieja, {revisados} revisiones aceptadas")


def update_history(comp, players):
    """Mantiene data/history_{comp}.json = {pid: [[yyyymmdd, valor], ...]}."""
    path = os.path.join(DATA, f"history_{comp}.json")
    hist = load(path, {})
    today = today_int()
    seeded = 0
    appended = 0

    for p in players:
        pid = str(p["id"])
        value = int(p.get("marketValue") or 0)
        if pid not in hist:
            if seeded >= SEED_CAP:
                continue
            try:
                raw = get(f"/v1/competition/{comp}/player/{pid}/market-value?x-lang=es")
                entries = sorted(
                    {date_to_int(e["date"]): int(e["marketValue"]) for e in raw}.items()
                )
                hist[pid] = [[d, v] for d, v in entries]
                seeded += 1
                time.sleep(SEED_DELAY)
            except Exception as e:
                print(f"  [comp {comp}] siembra fallida p{pid}: {e}", file=sys.stderr)
                continue
        series = hist[pid]
        if value and (not series or series[-1][0] < today):
            series.append([today, value])
            appended += 1
        elif value and series and series[-1][0] == today and series[-1][1] != value:
            series[-1][1] = value

    save(path, hist)
    print(f"[comp {comp}] historico: {seeded} sembrados, {appended} puntos nuevos, {len(hist)} jugadores")


def update_full_calendar(comp, week, cal_now):
    """Mantiene data/calendar_full_{comp}.json = {"weeks": {n: partidos}, "fetchedAt": iso}.

    La temporada entera se baja solo si el fichero no existe o tiene mas de 20 h
    (las fechas de jornadas lejanas cambian poco); la jornada actual, la anterior
    y la siguiente se refrescan en cada ejecucion para tener resultados frescos.
    """
    path = os.path.join(DATA, f"calendar_full_{comp}.json")
    full = load(path, {"weeks": {}, "fetchedAt": None})
    stale = True
    if full.get("fetchedAt"):
        try:
            age = datetime.now(MADRID) - datetime.fromisoformat(full["fetchedAt"])
            stale = age.total_seconds() > 20 * 3600
        except Exception:
            pass
    if stale or not full["weeks"]:
        weeks = {}
        for n in range(1, 47):
            try:
                ms = get(f"/v1/competition/{comp}/calendar?weekNumber={n}&x-lang=es")
            except Exception:
                break
            if not ms:
                break
            weeks[str(n)] = ms
            time.sleep(0.08)
        if weeks:
            full = {"weeks": weeks, "fetchedAt": now_madrid().isoformat(timespec="seconds")}
        print(f"[comp {comp}] calendario completo: {len(full['weeks'])} jornadas")
    else:
        cur = week.get("weekNumber")
        full["weeks"][str(cur)] = cal_now["matches"]
        if cal_now.get("nextMatches") and week.get("nextWeek"):
            full["weeks"][str(week["nextWeek"])] = cal_now["nextMatches"]
        prev = week.get("previousWeek")
        if prev:
            try:
                full["weeks"][str(prev)] = get(f"/v1/competition/{comp}/calendar?weekNumber={prev}&x-lang=es")
            except Exception:
                pass
    save(path, full)


STAT_KEYS = (
    "mins_played", "goals", "goal_assist", "yellow_card", "red_card",
    "saves", "goals_conceded", "penalty_save", "total_scoring_att", "ball_recovery",
)


def fetch_player_stats(comp, pid):
    d = get(f"/v1/competition/{comp}/player/{pid}?x-lang=es")
    agg = [0] * len(STAT_KEYS)
    week_mins = {}
    for wk in d.get("playerStats", []):
        s = wk.get("stats", {})
        for i, k in enumerate(STAT_KEYS):
            v = s.get(k)
            if isinstance(v, (list, tuple)) and v:
                agg[i] += v[0] or 0
        wn = wk.get("weekNumber")
        m = s.get("mins_played")
        if wn is not None and isinstance(m, (list, tuple)) and m:
            week_mins[str(wn)] = m[0] or 0
    return agg, week_mins


def update_stats(comp, players):
    """data/stats_{comp}.json = {date, keys, players: {pid: [totales]}}.

    Una peticion por jugador, asi que solo se baja en la primera ejecucion de
    cada dia (los totales solo cambian cuando hay partidos).
    """
    path = os.path.join(DATA, f"stats_{comp}.json")
    st = load(path, {})
    today = today_int()
    if st.get("date") == today and st.get("players"):
        print(f"[comp {comp}] estadisticas: ya bajadas hoy")
        return
    prev = st.get("players", {})
    prev_mins = st.get("mins", {})
    out = {}
    mins = {}

    def work(p):
        pid = str(p["id"])
        try:
            agg, wm = fetch_player_stats(comp, pid)
            out[pid] = agg
            if wm:
                mins[pid] = wm
        except Exception:
            if pid in prev:
                out[pid] = prev[pid]
            if pid in prev_mins:
                mins[pid] = prev_mins[pid]

    with ThreadPoolExecutor(max_workers=6) as ex:
        list(ex.map(work, players))
    save(path, {"date": today, "keys": list(STAT_KEYS), "players": out, "mins": mins})
    print(f"[comp {comp}] estadisticas: {len(out)} jugadores ({len(mins)} con minutos por jornada)")


def update_status_log(comp, players):
    """data/status_log_{comp}.json = {pid: {s: estado, d: desde, prev, prevd}}.

    Registra CUANDO cambia el estado de cada jugador: la base del radar de
    regresos (lesionado -> ok con el valor aun hundido = ventana de compra).
    """
    path = os.path.join(DATA, f"status_log_{comp}.json")
    log = load(path, {})
    today = today_int()
    changes = 0
    for p in players:
        pid = str(p["id"])
        cur = p.get("playerStatus") or "ok"
        e = log.get(pid)
        if e is None:
            log[pid] = {"s": cur, "d": today}
        elif e.get("s") != cur:
            log[pid] = {"s": cur, "d": today, "prev": e.get("s"), "prevd": e.get("d")}
            changes += 1
    save(path, log)
    if changes:
        print(f"[comp {comp}] estados: {changes} cambios")


def main():
    os.makedirs(DATA, exist_ok=True)
    meta = {"updatedAt": now_madrid().isoformat(timespec="seconds"), "comps": {}}

    teams = get("/v3/teams-master?x-lang=es")
    save(os.path.join(DATA, "teams.json"), teams)
    print(f"equipos: {len(teams)}")

    values_marker = load(os.path.join(DATA, "values_day.json"), {})
    for comp in COMPS:
        players = get(f"/v1/competition/{comp}/players?x-lang=es")
        refresh_values(comp, players, values_marker)
        save(os.path.join(DATA, f"players_{comp}.json"), players)
        print(f"[comp {comp}] jugadores: {len(players)}")

        week = get(f"/v1/competition/{comp}/week/current?x-lang=es")
        meta["comps"][comp] = week

        cal = {"week": week.get("weekNumber"), "matches": [], "next": week.get("nextWeek"), "nextMatches": []}
        try:
            cal["matches"] = get(f"/v1/competition/{comp}/calendar?weekNumber={week['weekNumber']}&x-lang=es")
            if week.get("nextWeek"):
                cal["nextMatches"] = get(f"/v1/competition/{comp}/calendar?weekNumber={week['nextWeek']}&x-lang=es")
        except Exception as e:
            print(f"  [comp {comp}] calendario fallo: {e}", file=sys.stderr)
        save(os.path.join(DATA, f"calendar_{comp}.json"), cal)

        update_full_calendar(comp, week, cal)

        update_history(comp, players)
        update_stats(comp, players)
        update_status_log(comp, players)

    save(os.path.join(DATA, "values_day.json"), values_marker)
    save(os.path.join(DATA, "meta.json"), meta)
    print("meta actualizada:", meta["updatedAt"])


if __name__ == "__main__":
    main()
