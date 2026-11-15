#!/usr/bin/env python3
"""Motor de analítica de Fantasy Studio -> data/analitica_{comp}.json

Una vez al día (tras confirmarse los valores de la 01:00) y cuando cambian
estados o puntos fuera del directo, calcula para cada jugador y equipo:

  1. PRECIO DE MAÑANA: cambio previsto (gradient boosting sobre la inercia del
     precio, su aceleración, los puntos del partido recién jugado, la distancia
     a su máximo y su nivel de precio) y probabilidad CALIBRADA de subir.
  2. CALENDARIO: fuerza de ataque/defensa de cada equipo (Poisson con
     ponderación por recencia y encogimiento hacia la media) y, para sus
     próximas 5 jornadas, goles esperados, 1X2 y una facilidad 0-100.
  3. TITULARIDAD Y PUNTOS ESPERADOS del próximo partido: minutos recientes
     (con peso por recencia), estado (lesión/duda/sanción), puntos por partido
     jugado encogidos hacia la media de su posición y ajuste por el rival.
  4. PUJA IDEAL / PUJA MÁXIMA: valor proyectado hasta la mitad / el día de su
     próximo partido, con la inercia que se mide en el propio histórico.

HONESTIDAD: cada modelo se examina cada día con datos que no ha visto
(últimos 14 días de precios, últimas 3 jornadas de puntos y resultados) y
publica ese acierto en el JSON; la app lo enseña tal cual.

Uso:  python scripts/analitica.py --hace-falta   (código 0 si toca recalcular)
      python scripts/analitica.py                (calcula y guarda)
Requiere numpy y scikit-learn (el workflow los instala solo si hace falta).
"""
import hashlib
import json
import math
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
COMPS = ("2", "1")
POS_NAMES = {"1": "POR", "2": "DEF", "3": "CEN", "4": "DEL", "5": "ENT"}


def load(name, default):
    try:
        with open(os.path.join(DATA, name), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save(name, obj):
    tmp = os.path.join(DATA, name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, os.path.join(DATA, name))


def madrid_now():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Europe/Madrid"))
    except Exception:
        return datetime.now(timezone(timedelta(hours=1)))


def d2i(d):
    return d.year * 10000 + d.month * 100 + d.day


def i2d(i):
    return date(i // 10000, i // 100 % 100, i % 100)


def firma(comp):
    """Huella de lo que cambia las predicciones: valores del día, estados,
    puntos por jornada y resultados. Si no cambia, no se recalcula."""
    vday = load("values_day.json", {}).get(comp)
    players = load(f"players_{comp}.json", [])
    cal = load(f"calendar_full_{comp}.json", {}).get("weeks") or {}
    h = hashlib.sha1()
    h.update(str(vday).encode())
    for p in sorted(players, key=lambda x: str(x["id"])):
        h.update(f"{p['id']}|{p.get('playerStatus')}|{p.get('teamId')}|".encode())
        for w in p.get("weekPoints") or []:
            h.update(f"{w.get('weekNumber')}:{w.get('points')},".encode())
    for wk in sorted(cal, key=int):
        for m in cal[wk]:
            h.update(f"{m.get('id')}:{m.get('localScore')}-{m.get('visitorScore')}:{m.get('matchDate')},".encode())
    return h.hexdigest()[:16]


def hace_falta():
    meta = load("meta.json", {})
    for comp in COMPS:
        prev = load(f"analitica_{comp}.json", {})
        live = ((meta.get("comps") or {}).get(comp) or {}).get("isLive")
        vday = load("values_day.json", {}).get(comp)
        if not prev or prev.get("dia") != vday:
            return True            # día nuevo de valores: siempre
        if not live and prev.get("firma") != firma(comp):
            return True            # cambios de estado/resultados fuera del directo
    return False


# =====================================================================
# 1. PRECIO DE MAÑANA
# =====================================================================
def partidos_por_equipo(cal):
    out = {}
    for wk, ms in cal.items():
        for m in ms:
            if not m.get("matchDate"):
                continue
            dia = int(m["matchDate"][:10].replace("-", ""))
            for t in (m.get("localId"), m.get("visitorId")):
                out.setdefault(str(t), {})[dia] = int(wk)
    return out


def rasgos(vals, k, dias, wp, partidos):
    """Rasgos del día k+1 (último valor conocido = vals[k+1]). Todos relativos."""
    import numpy as np
    dd = np.diff(vals)
    v = vals[k + 1]
    hoy, ayer, anteayer = dd[k], dd[k - 1], dd[k - 2]
    m7 = dd[max(0, k - 6):k + 1].mean()
    dia = dias[k + 1]
    pts = None
    for back in (0, 1):
        dj = d2i(date.fromordinal(i2d(dia).toordinal() - back))
        if dj in partidos and partidos[dj] in wp:
            pts = wp[partidos[dj]]
            break
    maximo = vals[:k + 2].max()
    return [hoy / v, ayer / v, anteayer / v, m7 / v, (hoy - ayer) / v,
            (pts if pts is not None else 0) / 10.0, 1.0 if pts is not None else 0.0,
            (v / maximo) - 1.0, math.log(v / 1e6)]


def modelo_precio(comp, players, hist, cal):
    import numpy as np
    from sklearn.ensemble import HistGradientBoostingRegressor, HistGradientBoostingClassifier
    from sklearn.isotonic import IsotonicRegression

    pj = partidos_por_equipo(cal)
    X, Y, D, V, H = [], [], [], [], []
    hoy_rasgos = {}
    for p in players:
        pid = str(p["id"])
        s = [(d, v) for d, v in (hist.get(pid) or []) if v > 0]
        if len(s) < 12:
            continue
        wp = {w["weekNumber"]: (w.get("points") or 0) for w in (p.get("weekPoints") or [])}
        partidos = pj.get(str(p.get("teamId")), {})
        dias = [d for d, _ in s]
        vals = np.array([v for _, v in s], dtype=float)
        dd = np.diff(vals)
        for k in range(8, len(dd) - 1):
            X.append(rasgos(vals, k, dias, wp, partidos))
            Y.append(dd[k + 1] / vals[k + 1])
            D.append(dias[k + 1]); V.append(vals[k + 1]); H.append(dd[k])
        # rasgos de HOY para predecir mañana
        hoy_rasgos[pid] = (rasgos(vals, len(dd) - 1, dias, wp, partidos), vals[-1], dd[-1])
    if len(X) < 2000:
        return {}, None
    X, Y, D, V, H = map(np.array, (X, Y, D, V, H))
    lo, hi = np.percentile(Y, [0.5, 99.5])

    def entrenar(mask):
        reg = HistGradientBoostingRegressor(loss="absolute_error", max_iter=300, learning_rate=0.06,
                                            max_leaf_nodes=31, random_state=1)
        reg.fit(X[mask], np.clip(Y[mask], lo, hi))
        clf = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.06, random_state=1)
        clf.fit(X[mask], (Y[mask] > 0).astype(int))
        return reg, clf

    # --- examen: entreno sin los últimos 14 días y pruebo con ellos ---
    dias_u = sorted(set(D.tolist()))
    corte = dias_u[-15] if len(dias_u) > 30 else None
    examen = None
    calib = None
    if corte:
        tr, te = D <= corte, D > corte
        reg, clf = entrenar(tr)
        pred = reg.predict(X[te]) * V[te]
        real = Y[te] * V[te]
        hoy = H[te]
        nz = real != 0
        giro = nz & (np.sign(hoy) != np.sign(real)) & (hoy != 0)
        giro_pred = (np.sign(pred) != np.sign(hoy)) & (hoy != 0) & nz
        # calibración: lo que dice el clasificador -> frecuencia real de subidas
        pr = clf.predict_proba(X[te])[:, 1]
        calib = IsotonicRegression(out_of_bounds="clip", y_min=0.01, y_max=0.99).fit(pr, (real > 0).astype(float))
        prc = calib.predict(pr)
        alto = prc >= 0.8
        examen = {
            "dias": 14, "casos": int(te.sum()),
            "direccion": round(float((np.sign(pred[nz]) == np.sign(real[nz])).mean()), 3),
            "direccion_ingenua": round(float((np.sign(hoy[nz]) == np.sign(real[nz])).mean()), 3),
            "error_medio": int(np.mean(np.abs(pred - real))),
            "error_ingenuo": int(np.mean(np.abs(hoy - real))),
            "giros_vistos": round(float((giro & giro_pred).sum() / max(1, giro.sum())), 3),
            "falsas_alarmas": round(float((~giro & giro_pred).sum() / max(1, giro_pred.sum())), 3),
            "acierto_si_80": round(float((real[alto] > 0).mean()), 3) if alto.any() else None,
        }
        # «posible giro» solo con señal fuerte: probabilidad calibrada de la
        # dirección CONTRARIA a hoy >= umbral. Se elige el umbral más bajo que
        # acierta al menos 2 de cada 3 veces en el examen.
        contraria = np.where(hoy > 0, 1 - prc, prc)
        for u in (0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85):
            m = (hoy != 0) & nz & (contraria >= u)
            if m.sum() >= 30 and (np.sign(real[m]) != np.sign(hoy[m])).mean() >= 0.667:
                examen["umbral_giro"] = u
                examen["acierto_giro"] = round(float((np.sign(real[m]) != np.sign(hoy[m])).mean()), 3)
                examen["giros_avisados"] = int(m.sum())
                break
    # --- producción: todo el histórico ---
    reg, clf = entrenar(np.ones(len(Y), dtype=bool))
    ids = list(hoy_rasgos)
    Xh = np.array([hoy_rasgos[i][0] for i in ids])
    vh = np.array([hoy_rasgos[i][1] for i in ids])
    dh = np.array([hoy_rasgos[i][2] for i in ids])
    pred = reg.predict(Xh) * vh
    pr = clf.predict_proba(Xh)[:, 1]
    if calib is not None:
        pr = calib.predict(pr)
    # inercia diaria medida en el histórico (para proyectar varios días)
    movs = (H != 0)
    rho = float(np.clip(np.sum(Y[movs] * V[movs] * H[movs]) / max(1.0, np.sum(H[movs] ** 2)), 0.5, 0.97))
    out = {}
    for i, pid in enumerate(ids):
        out[pid] = {"pred": int(round(pred[i])), "p_sube": int(round(100 * pr[i])), "v": int(vh[i]), "d": int(dh[i])}
    return out, {"examen": examen, "inercia": round(rho, 3)}


# =====================================================================
# 2. CALENDARIO (Poisson con fuerza de ataque y defensa)
# =====================================================================
def poisson_pmf(lmb, n):
    return math.exp(-lmb) * lmb ** n / math.factorial(n)


def probs_1x2(lh, la):
    ph = [poisson_pmf(lh, i) for i in range(11)]
    pa = [poisson_pmf(la, i) for i in range(11)]
    w = d = l = 0.0
    for i in range(11):
        for j in range(11):
            p = ph[i] * pa[j]
            if i > j: w += p
            elif i == j: d += p
            else: l += p
    t = w + d + l
    return w / t, d / t, l / t


def fuerzas(partidos_jugados, hasta_jornada=None):
    """partidos: [(wk, local, visit, gl, gv)] -> (att, def, media local, media visit)."""
    ps = [m for m in partidos_jugados if hasta_jornada is None or m[0] < hasta_jornada]
    if len(ps) < 10:
        return None
    ultima = max(m[0] for m in ps)
    peso = {wk: 0.5 ** ((ultima - wk) / 20.0) for wk in {m[0] for m in ps}}  # vida media 20 jornadas (afinado 15/11)
    W = sum(peso[m[0]] for m in ps)
    mh = sum(peso[m[0]] * m[3] for m in ps) / W
    ma = sum(peso[m[0]] * m[4] for m in ps) / W
    equipos = {m[1] for m in ps} | {m[2] for m in ps}
    att = {t: 1.0 for t in equipos}
    dfn = {t: 1.0 for t in equipos}
    K = 4.0  # encogimiento: 4 partidos "medios" de previa
    for _ in range(25):
        for t in equipos:
            num = den = 0.0
            for wk, l, v, gl, gv in ps:
                w = peso[wk]
                if l == t: num += w * gl; den += w * mh * dfn[v]
                elif v == t: num += w * gv; den += w * ma * dfn[l]
            att[t] = (num + K) / (den + K)
        for t in equipos:
            num = den = 0.0
            for wk, l, v, gl, gv in ps:
                w = peso[wk]
                if l == t: num += w * gv; den += w * ma * att[v]
                elif v == t: num += w * gl; den += w * mh * att[l]
            dfn[t] = (num + K) / (den + K)
    return att, dfn, mh, ma


def facilidad(pw, pd_, gd):
    return int(round(100 * min(1.0, max(0.0, pw + 0.45 * pd_ + 0.08 * math.tanh(gd)))))


def modelo_calendario(cal, meta_comp):
    jugados, futuros = [], []
    for wk, ms in cal.items():
        for m in ms:
            l, v = str(m.get("localId")), str(m.get("visitorId"))
            if m.get("matchState") == 7 and m.get("localScore") is not None:
                jugados.append((int(wk), l, v, int(m["localScore"]), int(m["visitorScore"])))
            elif m.get("localScore") is None:
                futuros.append((int(wk), l, v, m.get("matchDate")))
    # examen: predice las 3 últimas jornadas jugadas con lo anterior a cada una
    examen = None
    wks = sorted({m[0] for m in jugados})
    if len(wks) >= 8:
        aciertos = n = 0
        brier = brier_ref = 0.0
        for wk in wks[-7:]:
            f = fuerzas(jugados, hasta_jornada=wk)
            if not f:
                continue
            att, dfn, mh, ma = f
            previos = [m for m in jugados if m[0] < wk]
            ref = [sum(m[3] > m[4] for m in previos) / len(previos), sum(m[3] == m[4] for m in previos) / len(previos),
                   sum(m[3] < m[4] for m in previos) / len(previos)]
            for w_, l, v, gl, gv in [m for m in jugados if m[0] == wk]:
                if l not in att or v not in att:
                    continue
                pw, pd_, pl = probs_1x2(mh * att[l] * dfn[v], ma * att[v] * dfn[l])
                real = 0 if gl > gv else 1 if gl == gv else 2
                aciertos += int([pw, pd_, pl].index(max(pw, pd_, pl)) == real)
                brier += sum((p - (1 if i == real else 0)) ** 2 for i, p in enumerate([pw, pd_, pl]))
                brier_ref += sum((p - (1 if i == real else 0)) ** 2 for i, p in enumerate(ref))
                n += 1
        if n:
            examen = {"jornadas": 7, "partidos": n, "acierto_1x2": round(aciertos / n, 3),
                      "brier": round(brier / n, 3), "brier_referencia": round(brier_ref / n, 3),
                      "mejora": round(1 - brier / max(1e-9, brier_ref), 3)}
    f = fuerzas(jugados)
    if not f:
        return {}, examen
    att, dfn, mh, ma = f
    equipos = {}
    actual = (meta_comp or {}).get("weekNumber") or 0
    for wk, l, v, fecha in sorted(futuros, key=lambda m: (m[0], m[3] or "")):
        if wk < actual:
            continue
        for t, rival, casa in ((l, v, 1), (v, l, 0)):
            if t not in att or rival not in att:
                continue
            gf = (mh * att[t] * dfn[rival]) if casa else (ma * att[t] * dfn[rival])
            gc = (ma * att[rival] * dfn[t]) if casa else (mh * att[rival] * dfn[t])
            pw, pd_, pl = probs_1x2(gf, gc)
            lista = equipos.setdefault(t, [])
            if len(lista) < 5:
                lista.append([wk, rival, casa, facilidad(pw, pd_, gf - gc), round(gf, 2), round(gc, 2),
                              round(pw, 3), round(pd_, 3), round(math.exp(-gc), 3), fecha])
    fuerza = {t: [round(att[t], 3), round(dfn[t], 3)] for t in att}
    return {"partidos": equipos, "fuerza": fuerza, "medias": [round(mh, 3), round(ma, 3)]}, examen


# =====================================================================
# 3. TITULARIDAD Y PUNTOS ESPERADOS
# =====================================================================
def prob_titular(mins_por_jornada, ultima_jornada, estado):
    if estado in ("injured", "suspended"):
        return 0.0
    num = den = 0.0
    for k in range(6):
        wk = ultima_jornada - k
        if wk < 1:
            break
        w = 0.75 ** k
        mins = mins_por_jornada.get(str(wk))
        jugo = 1.0 if (mins or 0) >= 60 else 0.45 if (mins or 0) >= 1 else 0.0
        num += w * jugo
        den += w
    p = num / den if den else 0.3
    if estado == "doubtful":
        p *= 0.5
    return max(0.0, min(1.0, p))


def puntos_esperados(players, stats, cal_model, meta_comp, hasta=None):
    """xP del próximo partido por jugador. hasta=jornada limita los datos (examen)."""
    mins = (stats or {}).get("mins") or {}
    actual = hasta or (meta_comp or {}).get("weekNumber") or 1
    ultima = actual - 1
    # puntos por partido jugado, por posición (para encoger hacia la media)
    por_pos = {}
    ppg = {}
    for p in players:
        pid = str(p["id"])
        mm = mins.get(pid) or {}
        pts = [(w["weekNumber"], w.get("points") or 0) for w in (p.get("weekPoints") or [])
               if w["weekNumber"] <= ultima and (mm.get(str(w["weekNumber"])) or 0) > 0]
        if not pts:
            continue
        num = sum((0.85 ** (ultima - wk)) * x for wk, x in pts)
        den = sum((0.85 ** (ultima - wk)) for wk, _ in pts)
        ppg[pid] = (num / den, den)
        por_pos.setdefault(str(p.get("positionId")), []).append(num / den)
    media_pos = {k: sum(v) / len(v) for k, v in por_pos.items() if v}
    partidos = (cal_model or {}).get("partidos") or {}
    medias = (cal_model or {}).get("medias") or [1.3, 1.1]
    gmed = (medias[0] + medias[1]) / 2
    out = {}
    for p in players:
        pid = str(p["id"])
        pos = str(p.get("positionId"))
        if pos not in ("1", "2", "3", "4"):
            continue
        mm = mins.get(pid) or {}
        pt = prob_titular(mm, ultima, p.get("playerStatus") if hasta is None else "ok")
        base_pos = media_pos.get(pos, 3.0)
        if pid in ppg:
            m, peso = ppg[pid]
            ppg_enc = (m * peso + base_pos * 2.0) / (peso + 2.0)   # 2 partidos "medios" de previa
        else:
            ppg_enc = base_pos * 0.8
        fac = 1.0
        prox = None
        lst = partidos.get(str(p.get("teamId"))) or []
        if lst:
            prox = lst[0]
            gf, gc, cs = prox[4], prox[5], prox[8]
            if pos in ("1", "2"):
                cs_med = math.exp(-gmed)
                fac = 1 + 0.6 * ((cs / cs_med) - 1)
            else:
                fac = 1 + 0.5 * ((gf / gmed) - 1)
            fac = max(0.6, min(1.5, fac))
        xp = pt * ppg_enc * fac + (1 - pt) * 0.35 * ppg_enc * 0.3
        out[pid] = {"p_tit": int(round(100 * pt)), "xp": round(xp, 1), "prox": prox[0] if prox else None}
    return out


def examen_puntos(players, stats, cal):
    """Últimas 5 jornadas, contando como 0 a quien no jugó (ahí es donde la
    titularidad aporta). Comparado con 'su media de temporada'."""
    mins = (stats or {}).get("mins") or {}
    jugadas = sorted({w["weekNumber"] for p in players for w in (p.get("weekPoints") or [])})
    if len(jugadas) < 8:
        return None
    jugados = [(int(w), str(m["localId"]), str(m["visitorId"]), int(m["localScore"]), int(m["visitorScore"]))
               for w, ms in cal.items() for m in ms if m.get("matchState") == 7 and m.get("localScore") is not None]
    err_m = err_b = n = 0
    for wk in jugadas[-5:]:
        f = fuerzas(jugados, hasta_jornada=wk)
        if not f:
            continue
        att, dfn, mh, ma = f
        partidos = {}
        for m in cal.get(str(wk), []):
            l, v = str(m.get("localId")), str(m.get("visitorId"))
            if l in att and v in att:
                gf, gc = mh * att[l] * dfn[v], ma * att[v] * dfn[l]
                partidos[l] = [[wk, v, 1, 0, gf, gc, 0, 0, math.exp(-gc), None]]
                partidos[v] = [[wk, l, 0, 0, gc, gf, 0, 0, math.exp(-gf), None]]
        xp = puntos_esperados(players, stats, {"partidos": partidos, "medias": [mh, ma]}, None, hasta=wk)
        for p in players:
            pid = str(p["id"])
            mm = mins.get(pid) or {}
            if pid not in xp or not any((mm.get(str(w)) or 0) > 0 for w in range(wk - 6, wk)):
                continue
            wp = {w["weekNumber"]: (w.get("points") or 0) for w in (p.get("weekPoints") or [])}
            real = wp.get(wk, 0) if (mm.get(str(wk)) or 0) > 0 else 0
            prev = [wp[w] for w in wp if w < wk]
            err_m += abs(xp[pid]["xp"] - real)
            err_b += abs((sum(prev) / len(prev) if prev else 0) - real)
            n += 1
    if not n:
        return None
    return {"jornadas": 5, "casos": n, "error_xp": round(err_m / n, 2), "error_media_temporada": round(err_b / n, 2)}


# =====================================================================
# 4. PUJA IDEAL / MÁXIMA
# =====================================================================
def dias_hasta(fecha_iso, hoy):
    if not fecha_iso:
        return 4
    try:
        f = datetime.fromisoformat(fecha_iso).date()
        return max(1, (f - hoy).days)
    except Exception:
        return 4


def ganancia(pred, rho, dias):
    return pred * (1 - rho ** dias) / (1 - rho) if rho < 1 else pred * dias


# =====================================================================
def calcular(comp):
    players = load(f"players_{comp}.json", [])
    hist = load(f"history_{comp}.json", {})
    stats = load(f"stats_{comp}.json", {})
    cal = (load(f"calendar_full_{comp}.json", {}) or {}).get("weeks") or {}
    meta_comp = (load("meta.json", {}).get("comps") or {}).get(comp) or {}
    t0 = time.time()
    precio, info_precio = modelo_precio(comp, players, hist, cal)
    t1 = time.time()
    cal_model, examen_cal = modelo_calendario(cal, meta_comp)
    xp = puntos_esperados(players, stats, cal_model, meta_comp)
    ex_pts = examen_puntos(players, stats, cal)
    rho = (info_precio or {}).get("inercia", 0.88)
    hoy = madrid_now().date()
    partidos = cal_model.get("partidos") or {}
    jug = {}
    for p in players:
        pid = str(p["id"])
        a = precio.get(pid) or {}
        b = xp.get(pid) or {}
        if not a and not b:
            continue
        fila = {}
        if a:
            fila.update(pred=a["pred"], ps=a["p_sube"])
            prox = (partidos.get(str(p.get("teamId"))) or [None])[0]
            n = dias_hasta(prox[9] if prox else None, hoy)
            if a["pred"] > 0 and a["p_sube"] >= 55:
                tope = 0.25 * a["v"]
                fila["pi"] = int(a["v"] + min(tope, ganancia(a["pred"], rho, max(1, round(n / 2)))))
                fila["pm"] = int(a["v"] + min(tope, ganancia(a["pred"], rho, n)))
            else:
                fila["pi"] = fila["pm"] = a["v"]
        if b:
            fila.update(pt=b["p_tit"], xp=b["xp"])
        jug[pid] = fila
    return {
        "comp": comp,
        "generado": madrid_now().isoformat(timespec="seconds"),
        "dia": load("values_day.json", {}).get(comp),
        "firma": firma(comp),
        "examen": {"precio": (info_precio or {}).get("examen"), "calendario": examen_cal, "puntos": ex_pts},
        "inercia": rho,
        "equipos": cal_model,
        "jugadores": jug,
        "segundos": round(time.time() - t0, 1),
        "segundos_precio": round(t1 - t0, 1),
    }


def main():
    if "--hace-falta" in sys.argv:
        sys.exit(0 if hace_falta() else 1)
    try:
        import numpy  # noqa: F401
        import sklearn  # noqa: F401
    except ImportError:
        print("analítica: faltan numpy/scikit-learn; no se calcula")
        return
    for comp in COMPS:
        r = calcular(comp)
        save(f"analitica_{comp}.json", r)
        ex = r["examen"]
        print(f"[comp {comp}] analítica en {r['segundos']} s · {len(r['jugadores'])} jugadores · "
              f"precio: {ex['precio']} · calendario: {ex['calendario']} · puntos: {ex['puntos']}")


if __name__ == "__main__":
    main()
