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

        update_history(comp, players)

    save(os.path.join(DATA, "meta.json"), meta)
    print("meta actualizada:", meta["updatedAt"])


if __name__ == "__main__":
    main()
