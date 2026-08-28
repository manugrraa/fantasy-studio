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
from datetime import datetime

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
    url = BASE + path
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
    for wk in d.get("playerStats", []):
        s = wk.get("stats", {})
        for i, k in enumerate(STAT_KEYS):
            v = s.get(k)
            if isinstance(v, (list, tuple)) and v:
                agg[i] += v[0] or 0
    return agg


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
    out = {}

    def work(p):
        pid = str(p["id"])
        try:
            out[pid] = fetch_player_stats(comp, pid)
        except Exception:
            if pid in prev:
                out[pid] = prev[pid]

    with ThreadPoolExecutor(max_workers=6) as ex:
        list(ex.map(work, players))
    save(path, {"date": today, "keys": list(STAT_KEYS), "players": out})
    print(f"[comp {comp}] estadisticas: {len(out)} jugadores")


def main():
    os.makedirs(DATA, exist_ok=True)
    meta = {"updatedAt": now_madrid().isoformat(timespec="seconds"), "comps": {}}

    teams = get("/v3/teams-master?x-lang=es")
    save(os.path.join(DATA, "teams.json"), teams)
    print(f"equipos: {len(teams)}")

    for comp in COMPS:
        players = get(f"/v1/competition/{comp}/players?x-lang=es")
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

    save(os.path.join(DATA, "meta.json"), meta)
    print("meta actualizada:", meta["updatedAt"])


if __name__ == "__main__":
    main()
