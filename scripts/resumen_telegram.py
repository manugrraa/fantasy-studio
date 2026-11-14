#!/usr/bin/env python3
"""Resumen diario del mercado Fantasy por Telegram (por la mañana).

Analiza los datos ya descargados en data/ y su copia personal en la nube:
lo suyo (dinero, objetivos, su plantilla con el balance del día, vigilados) y
el mercado de cada competición (suben, bajan, chollos).

Una sola vez al día (data/resumen_state.json): pueden dispararlo a la vez el
reloj de Cloudflare y el de GitHub. TELEGRAM_BOT_TOKEN=dry imprime sin enviar.
"""
import tg

COMPS = [("2", "LaLiga Hypermotion", "🔵"), ("1", "LaLiga EA Sports", "🔴")]


def values_fresh(comp):
    """True si el histórico ya tiene el valor de HOY (el juego los cambia a la 01:00)."""
    today = tg.today_int()
    hist = tg.load(f"history_{comp}.json", {})
    return any(s and s[-1][0] >= today for s in list(hist.values())[:80])


def deltas_hoy(comp):
    """pid -> (jugador, cambio de hoy, valor)."""
    players = tg.load(f"players_{comp}.json", [])
    hist = tg.load(f"history_{comp}.json", {})
    out = {}
    for p in players:
        h = hist.get(str(p["id"]))
        if h and len(h) >= 2:
            out[str(p["id"])] = (p, h[-1][1] - h[-2][1], h[-1][1])
    return out


def mercado(comp, dmap):
    rows = [(p, d) for p, d, _ in dmap.values()]
    ups = sorted([r for r in rows if r[1] > 0], key=lambda r: -r[1])[:5]
    downs = sorted([r for r in rows if r[1] < 0], key=lambda r: r[1])[:5]
    cheap = []
    for p, _, v in dmap.values():
        pts = p.get("points") or 0
        if v > 0 and pts >= 10 and p.get("playerStatus") == "ok":
            cheap.append((p, pts / (v / 1_000_000)))
    cheap.sort(key=lambda r: -r[1])
    return ups, downs, cheap[:3]


def lo_tuyo(pers, dmap):
    comp = "2"
    money = (pers.get("money") or {}).get(comp)
    squad = [e for e in (pers.get("squad") or {}).get(comp) or [] if e.get("pid") and not e.get("custom")]
    watch = [w for w in (pers.get("watch") or {}).get(comp) or [] if w.get("pid")]
    L = []
    ups, downs, total, conocidos = [], [], 0, 0
    for e in squad:
        r = dmap.get(str(e["pid"]))
        if not r:
            continue
        p, d, _ = r
        conocidos += 1
        total += d
        (ups if d > 0 else downs if d < 0 else []).append((p, d))
    hits, vig = [], []
    for w in watch:
        r = dmap.get(str(w["pid"]))
        if not r:
            continue
        p, d, val = r
        vig.append(f"{p['nickname']}  <b>{tg.sign(d) if d else '='}</b>")
        t = w.get("target")
        if t and (val >= t if w.get("dir") == "above" else val <= t):
            hits.append(f"{p['nickname']}: {tg.fmt_m(val)} (objetivo {tg.fmt_m(t)})")
    if money is None and not conocidos and not vig:
        return L
    L += ["", "<b>⭐ Lo tuyo</b>" + (f" · dinero <b>{tg.fmt_m(money)}</b>" if money is not None else "")]
    L += tg.seccion("🎯 <b>Objetivo alcanzado</b>", hits)
    if conocidos:
        emoji = "🟢" if total > 0 else ("🔴" if total < 0 else "⚪")
        L += ["", f"👥 Tu plantilla hoy: {emoji} <b>{tg.sign(total) if total else 'sin cambios'}</b>"]
        L += tg.seccion("📈 <b>Suben</b>", [f"{p['nickname']}  <b>{tg.sign(d)}</b>" for p, d in sorted(ups, key=lambda x: -x[1])])
        L += tg.seccion("📉 <b>Bajan</b>", [f"{p['nickname']}  <b>{tg.sign(d)}</b>" for p, d in sorted(downs, key=lambda x: x[1])])
    L += tg.seccion("👁 <b>Tus vigilados</b>", vig)
    return L


def main():
    hoy = tg.today_int()
    if tg.ya_enviado("resumen_state.json", "dia", hoy):
        print("el resumen de hoy ya se envió")
        return
    meta = tg.load("meta.json", {})
    L = [tg.titulo("Resumen del " + tg.fecha_larga())]
    if not values_fresh("2"):
        L += ["", "⏳ <i>Los valores de hoy aún no se habían publicado al preparar esto.</i>"]

    dmaps = {comp: deltas_hoy(comp) for comp, _, _ in COMPS}
    pers = tg.personal()
    if pers:
        L += lo_tuyo(pers, dmaps["2"])

    for comp, label, icono in COMPS:
        week = (meta.get("comps") or {}).get(comp) or {}
        wk = week.get("weekNumber")
        live = " · <b>EN JUEGO</b>" if week.get("isLive") else ""
        ups, downs, cheap = mercado(comp, dmaps[comp])
        L += ["", "", f"{icono} <b>{label}</b>" + (f" · J{wk}{live}" if wk else "")]
        L += tg.seccion("📈 <b>Top subidas</b>", [f"{p['nickname']}  <b>{tg.sign(d)}</b>" for p, d in ups])
        L += tg.seccion("📉 <b>Top bajadas</b>", [f"{p['nickname']}  <b>{tg.sign(d)}</b>" for p, d in downs])
        if comp == "2":
            L += tg.seccion("💎 <b>Chollos</b> (puntos por millón)",
                            [f"{p['nickname']}  <b>{v:.1f}</b>".replace(".", ",") for p, v in cheap])
    L += tg.pie()
    if tg.enviar(L):
        tg.marcar_enviado("resumen_state.json", "dia", hoy)
        print("resumen enviado")


if __name__ == "__main__":
    main()
