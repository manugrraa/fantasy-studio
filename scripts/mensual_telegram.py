#!/usr/bin/env python3
"""Resumen MENSUAL de Fantasy Studio por Telegram.

El dia 1 de cada mes repasa el mes que acaba de cerrar: como te fue a ti
(patrimonio, revalorizacion de la plantilla, operaciones) y que paso en el
mercado y en la competicion.

Se apoya SOLO en datos que el bot ya tiene: el historico diario de valores
(data/history_*.json, guarda todo el mes), players (puntos por jornada) y tu
copia personal en la nube (plantilla, movimientos, patrimonio).

Variables: TELEGRAM_BOT_TOKEN (o "dry" para imprimir), FANTASY_SYNC_KEY.
FS_MONTH=YYYYMM fuerza un mes concreto (para pruebas).
Estado anti-repeticion en data/mensual_state.json.
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
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


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


def mes_objetivo():
    """Por defecto, el mes que acaba de cerrar."""
    forz = os.environ.get("FS_MONTH", "").strip()
    if len(forz) == 6 and forz.isdigit():
        return int(forz[:4]), int(forz[4:])
    now = madrid_now()
    return (now.year - 1, 12) if now.month == 1 else (now.year, now.month - 1)


def valor_en(series, limite):
    """Ultimo valor con fecha <= limite (None si el jugador no existia)."""
    out = None
    for d, v in series or []:
        if d <= limite:
            out = v
        else:
            break
    return out


def valor_inicio(series, ini, fin):
    """Valor con el que se entra al mes; si el historico empieza DENTRO del mes
    (el primer mes de datos, o un jugador nuevo), el primero que haya en el."""
    v = valor_en(series, ini)
    if v is not None:
        return v
    for d, val in series or []:
        if ini < d <= fin:
            return val
    return None


def trocear(msg, lim=3900):
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
    if not token:
        print("sin TELEGRAM_BOT_TOKEN")
        return
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip() or str(load("telegram_chat.json", {}).get("chat_id") or "")
    if not chat and token != "dry":
        print("sin chat conocido todavia")
        return

    anio, mes = mes_objetivo()
    ini = anio * 10000 + mes * 100          # el dia 0: valor con el que se entra al mes
    fin = anio * 10000 + mes * 100 + 31
    etiqueta = f"{MESES[mes - 1]} de {anio}"

    state = load("mensual_state.json", {})
    marca = f"{anio}{mes:02d}"
    if state.get(COMP) == marca and token != "dry":
        print(f"resumen de {etiqueta} ya enviado")
        return

    players = load(f"players_{COMP}.json", [])
    hist = load(f"history_{COMP}.json", {})
    by_id = {str(p["id"]): p for p in players}

    # variacion de cada jugador durante el mes
    movs = []
    for p in players:
        s = hist.get(str(p["id"]))
        if not s:
            continue
        v0, v1 = valor_inicio(s, ini, fin), valor_en(s, fin)
        if v0 is None or v1 is None or v0 == v1:
            continue
        movs.append((p, v1 - v0, v0, v1))
    movs.sort(key=lambda x: -x[1])

    personal = None
    if key:
        try:
            req = urllib.request.Request(f"https://fantasy-proxy.manugrraa.workers.dev/sync/{key}", headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                personal = json.load(r)
        except Exception as e:
            print("sync no disponible:", e, file=sys.stderr)

    L = [f"<b>⚡ Fantasy Studio — Resumen de {etiqueta}</b>"]

    # ---------- lo tuyo ----------
    if personal:
        L.append("")
        L.append("<b>👤 Tu mes</b>")
        wealth = [w for w in ((personal.get("wealth") or {}).get(COMP) or []) if ini <= w[0] <= fin]
        if len(wealth) >= 2:
            a, b = wealth[0], wealth[-1]
            L.append(f"💼 Patrimonio: {fmt_m(a[1])} → <b>{fmt_m(b[1])}</b> ({sign(b[1] - a[1])})")
            L.append(f"   dinero {fmt_m(b[2])} · equipo {fmt_m(b[3])}")
        elif wealth:
            L.append(f"💼 Patrimonio a cierre: <b>{fmt_m(wealth[-1][1])}</b>")

        squad = [e for e in ((personal.get("squad") or {}).get(COMP) or []) if e.get("pid") and not e.get("custom")]
        deltas = []
        for e in squad:
            s = hist.get(str(e["pid"]))
            v0, v1 = valor_inicio(s, ini, fin), valor_en(s, fin)
            if v0 is None or v1 is None:
                continue
            p = by_id.get(str(e["pid"]))
            deltas.append(((p or {}).get("nickname") or e.get("name") or "?", v1 - v0))
        if deltas:
            total = sum(d for _, d in deltas)
            emoji = "🟢" if total > 0 else ("🔴" if total < 0 else "⚪")
            deltas.sort(key=lambda x: -x[1])
            L.append(f"📊 Tu plantilla de ahora, en el mes: {emoji} <b>{sign(total)}</b>")
            L.append("")
            for n, d in deltas:
                L.append(f"• {n}  <b>{sign(d)}</b>")

        moves = [m for m in ((personal.get("moves") or {}).get(COMP) or [])
                 if str(m.get("date", "")).replace("-", "")[:6] == marca]
        if moves:
            compras = [m for m in moves if m.get("type") == "buy"]
            ventas = [m for m in moves if m.get("type") == "sell"]
            benef = sum(m.get("profit") or 0 for m in ventas)
            L.append("")
            L.append(f"🔄 Operaciones: <b>{len(compras)}</b> compras · <b>{len(ventas)}</b> ventas"
                     + (f" · beneficio <b>{sign(benef)}</b>" if ventas else ""))
            mejor = max(ventas, key=lambda m: m.get("profit") or 0, default=None)
            if mejor and (mejor.get("profit") or 0) > 0:
                L.append(f"   💚 Mejor venta: {mejor.get('name')} {sign(mejor['profit'])}")

    # ---------- el mercado ----------
    if movs:
        L.append("")
        L.append("<b>📊 El mercado en el mes</b>")
        L.append("")
        L.append("🚀 <b>Los que más subieron</b>")
        for p, d, v0, v1 in movs[:8]:
            L.append(f"• {p['nickname']}  <b>{sign(d)}</b>  ({fmt_m(v0)} → {fmt_m(v1)})")
        bajan = [m for m in movs if m[1] < 0][-8:]
        if bajan:
            L.append("")
            L.append("📉 <b>Los que más bajaron</b>")
            for p, d, v0, v1 in reversed(bajan):
                L.append(f"• {p['nickname']}  <b>{sign(d)}</b>  ({fmt_m(v0)} → {fmt_m(v1)})")

    # ---------- la competicion ----------
    meta = load("meta.json", {})
    wn = ((meta.get("comps") or {}).get(COMP) or {}).get("weekNumber")
    jugadas = sorted({w.get("weekNumber") for p in players for w in (p.get("weekPoints") or [])
                      if w.get("weekNumber") and (not wn or w["weekNumber"] < wn)})
    if jugadas:
        L.append("")
        L.append("<b>⚽ La competición</b>")
        L.append(f"📅 Jornadas disputadas: {', '.join('J' + str(j) for j in jugadas)}")
        tot = {}
        for p in players:
            s = sum((w.get("points") or 0) for w in (p.get("weekPoints") or []) if w.get("weekNumber") in jugadas)
            if s:
                tot[p["nickname"]] = s
        top = sorted(tot.items(), key=lambda x: -x[1])[:5]
        if top:
            L.append("")
            L.append("👑 <b>Máximos puntuadores</b>")
            for n, s in top:
                L.append(f"• {n}  <b>{s} pts</b>")

    L.append("")
    L.append('<a href="https://manugrraa.github.io/fantasy-studio/">Abrir Fantasy Studio</a>')
    msg = "\n".join(L)

    if token == "dry":
        sys.stdout.buffer.write(("\n\n————————————\n\n".join(trocear(msg))).encode("utf-8", "replace"))
        return

    for parte in trocear(msg):
        body = urllib.parse.urlencode({
            "chat_id": chat, "text": parte, "parse_mode": "HTML", "disable_web_page_preview": "true",
        }).encode()
        try:
            req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=body, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                if not json.load(r).get("ok"):
                    raise Exception("telegram dijo que no")
        except Exception as e:
            print("fallo al enviar:", e, file=sys.stderr)
            sys.exit(1)
    state[COMP] = marca
    save("mensual_state.json", state)
    print(f"resumen de {etiqueta} enviado")


if __name__ == "__main__":
    main()
