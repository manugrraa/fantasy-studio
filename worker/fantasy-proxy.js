/**
 * Fantasy Studio — servidor puente (Cloudflare Worker)
 *
 * La API de LaLiga Fantasy solo acepta peticiones de navegador desde su web
 * oficial (CORS). Este Worker reenvía las peticiones de TU app a la API,
 * conservando tu Authorization, y responde con CORS abierto solo para TU web.
 *
 * Seguridad:
 *  - Solo reenvía a fantasy-api.llt-services.com (nada más).
 *  - Solo acepta peticiones desde los orígenes de la lista ALLOWED_ORIGINS.
 *  - No guarda nada: ni tokens, ni contraseñas, ni registros.
 *  - El login NO pasa por aquí: va directo navegador → login.laliga.es.
 *
 * Cómo desplegarlo (5 minutos, gratis):
 *  1. Crea cuenta en https://dash.cloudflare.com (o entra si ya tienes).
 *  2. Menú "Workers y Pages" → "Crear" → "Crear Worker".
 *  3. Ponle de nombre: fantasy-proxy → "Implementar" (Deploy).
 *  4. Botón "Editar código" → borra lo que haya → pega ESTE archivo entero → "Implementar".
 *  5. Copia la URL que te da (https://fantasy-proxy.TU-SUBDOMINIO.workers.dev)
 *     y pégala en Fantasy Studio → Ajustes → "Servidor puente".
 */

const ALLOWED_ORIGINS = [
  "https://manugrraa.github.io",
  "http://localhost:8613",
  "http://127.0.0.1:8613",
  "http://127.0.0.1:8614", // la app de escritorio (Electron) sirve el repo aquí
];

const API_HOST = "https://fantasy-api.llt-services.com";

const SYNC_MAX_BYTES = 250000;
const SYNC_TTL_DAYS = 120;

function jsonRes(obj, status, cors){
  return new Response(JSON.stringify(obj), {
    status, headers: { ...cors, "Content-Type": "application/json" },
  });
}

async function handleSync(request, url, env, cors, originOk){
  if (!env.SYNC) return jsonRes({ error: "KV no configurado" }, 500, cors);
  // /sync/{clave} · /sync/{clave}/versions · /sync/{clave}/v/{yyyymmdd}
  const parts = url.pathname.slice("/sync/".length).split("/").filter(Boolean);
  const key = parts[0] || "";
  if (!/^[A-Za-z0-9_-]{20,64}$/.test(key)) return jsonRes({ error: "clave no válida" }, 400, cors);

  if (request.method === "PUT" || request.method === "POST") {
    if (parts.length > 1) return jsonRes({ error: "solo se escribe la copia actual" }, 405, cors);
    if (!originOk) return jsonRes({ error: "origen no permitido" }, 403, cors);
    const body = await request.text();
    if (body.length > SYNC_MAX_BYTES) return jsonRes({ error: "demasiado grande" }, 413, cors);
    try { JSON.parse(body); } catch(e) { return jsonRes({ error: "no es JSON" }, 400, cors); }
    await env.SYNC.put("s:" + key, body, { expirationTtl: 60 * 60 * 24 * SYNC_TTL_DAYS });
    // versión diaria (histórico de 60 días): se sobreescribe dentro del mismo día
    const day = new Date().toLocaleDateString("sv-SE", { timeZone: "Europe/Madrid" }).replace(/-/g, "");
    await env.SYNC.put(`v:${key}:${day}`, body, { expirationTtl: 60 * 60 * 24 * 60 });
    return jsonRes({ ok: true }, 200, cors);
  }

  if (request.method === "GET") {
    if (parts[1] === "versions") {
      const list = await env.SYNC.list({ prefix: `v:${key}:` });
      const days = list.keys.map(k => k.name.split(":").pop()).sort().reverse();
      return jsonRes({ versions: days }, 200, cors);
    }
    if (parts[1] === "v" && /^\d{8}$/.test(parts[2] || "")) {
      const v = await env.SYNC.get(`v:${key}:${parts[2]}`);
      if (v == null) return jsonRes({ error: "sin esa versión" }, 404, cors);
      return new Response(v, { status: 200, headers: { ...cors, "Content-Type": "application/json" } });
    }
    const v = await env.SYNC.get("s:" + key);
    if (v == null) return jsonRes({ error: "sin datos" }, 404, cors);
    return new Response(v, { status: 200, headers: { ...cors, "Content-Type": "application/json" } });
  }
  return jsonRes({ error: "método no permitido" }, 405, cors);
}

/* /pair/{id}: buzón de UN SOLO USO para vincular aparatos.
   El contenido llega YA CIFRADO de extremo a extremo (AES-GCM con una clave
   derivada del código que solo ven los dos aparatos): aquí no se puede leer.
   Caduca a los 5 minutos y se borra al leerlo. */
const PAIR_TTL_S = 300;
const PAIR_MAX_BYTES = 16000;
async function handlePair(request, url, env, cors, originOk){
  if (!env.SYNC) return jsonRes({ error: "KV no configurado" }, 500, cors);
  if (!originOk) return jsonRes({ error: "origen no permitido" }, 403, cors);
  const id = url.pathname.slice("/pair/".length);
  if (!/^[a-f0-9]{24,64}$/.test(id)) return jsonRes({ error: "id no válido" }, 400, cors);
  const k = "p:" + id;
  if (request.method === "PUT") {
    const body = await request.text();
    if (body.length > PAIR_MAX_BYTES) return jsonRes({ error: "demasiado grande" }, 413, cors);
    let o;
    try { o = JSON.parse(body); } catch(e) { return jsonRes({ error: "no es JSON" }, 400, cors); }
    if (!o || typeof o.ct !== "string" || typeof o.iv !== "string" || typeof o.salt !== "string")
      return jsonRes({ error: "formato no válido" }, 400, cors);
    await env.SYNC.put(k, body, { expirationTtl: PAIR_TTL_S });
    return jsonRes({ ok: true }, 200, cors);
  }
  if (request.method === "GET") {
    const v = await env.SYNC.get(k);
    if (v == null) return jsonRes({ error: "no hay nada (aún no está o ya caducó)" }, 404, cors);
    await env.SYNC.delete(k); // un solo uso
    return new Response(v, { status: 200, headers: { ...cors, "Content-Type": "application/json", "Cache-Control": "no-store" } });
  }
  return jsonRes({ error: "método no permitido" }, 405, cors);
}

export default {
  /**
   * RELOJ PUNTUAL de todo el bot (el de GitHub llega con 6 h de retraso de
   * mediana: el «resumen de las 8:45» se recibía hacia las 15:00).
   * Un único cron cada 5 min (wrangler.toml) y aquí se decide, en hora de
   * Madrid (cambio de hora incluido), qué workflow lanzar. Los crons de GitHub
   * siguen de respaldo: cada script tiene su guarda de «una sola vez».
   * Necesita el secret GH_TOKEN (scopes repo+workflow); sin él no hace nada.
   */
  async scheduled(event, env, ctx) {
    if (!env.GH_TOKEN) return;
    const t = new Date(event.scheduledTime || Date.now());
    const p = Object.fromEntries(new Intl.DateTimeFormat("en-GB", {
      timeZone: "Europe/Madrid", hour: "2-digit", minute: "2-digit", day: "2-digit", weekday: "short", hourCycle: "h23",
    }).formatToParts(t).map(x => [x.type, x.value]));
    const hh = +p.hour, mm = +p.minute, dia = +p.day, dow = p.weekday; // dow: Mon..Sun
    const at = (h, m) => hh === h && mm === m;
    const lanzar = [];
    if (mm % 15 === 5) lanzar.push("datos.yml");                 // xx:05/20/35/50 (01:05 valores, 01:35 parte)
    if (at(8, 45)) lanzar.push("resumen.yml");                   // resumen de la mañana
    if (at(9, 15) || at(17, 15) || at(20, 15)) lanzar.push("guerra.yml"); // sala de guerra y crónica
    if (dow === "Sun" && at(20, 30)) lanzar.push("semanal.yml"); // balance semanal
    if (dia === 1 && at(10, 0)) lanzar.push("mensual.yml");      // resumen mensual
    ctx.waitUntil(Promise.all(lanzar.map(wf =>
      fetch(`https://api.github.com/repos/manugrraa/fantasy-studio/actions/workflows/${wf}/dispatches`, {
        method: "POST",
        headers: {
          "Authorization": "Bearer " + env.GH_TOKEN,
          "Accept": "application/vnd.github+json",
          "Content-Type": "application/json",
          "User-Agent": "FantasyStudio-cron",
        },
        body: JSON.stringify({ ref: "main" }),
      }).catch(() => null))));
  },

  async fetch(request, env) {
    const url = new URL(request.url);
    const origin = request.headers.get("Origin") || "";
    const originOk = ALLOWED_ORIGINS.includes(origin);

    const cors = {
      "Access-Control-Allow-Origin": originOk ? origin : ALLOWED_ORIGINS[0],
      "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS",
      "Access-Control-Allow-Headers": "Authorization,Content-Type,x-lang,Accept",
      "Access-Control-Max-Age": "86400",
      "Vary": "Origin",
    };

    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: cors });
    }

    // /sync/{clave}: almacén personal (plantilla, vigilados…) en KV.
    // Escritura solo desde la app (origen permitido); lectura con la clave
    // (que es un secreto largo) también sin origen, para GitHub Actions.
    if (url.pathname.startsWith("/sync/")) {
      return handleSync(request, url, env, cors, originOk);
    }
    if (url.pathname.startsWith("/pair/")) {
      return handlePair(request, url, env, cors, originOk);
    }

    if (!originOk) {
      return new Response(JSON.stringify({ error: "origen no permitido" }), {
        status: 403, headers: { ...cors, "Content-Type": "application/json" },
      });
    }
    if (!url.pathname.startsWith("/api/") && !url.pathname.startsWith("/stats/") && !url.pathname.startsWith("/dsp/")) {
      return new Response(JSON.stringify({ error: "ruta no permitida" }), {
        status: 404, headers: { ...cors, "Content-Type": "application/json" },
      });
    }

    const headers = new Headers();
    for (const h of ["authorization", "content-type", "x-lang", "accept"]) {
      const v = request.headers.get(h);
      if (v) headers.set(h, v);
    }
    headers.set("User-Agent", "Mozilla/5.0 (compatible; FantasyStudio/1.0)");

    const init = { method: request.method, headers };
    if (request.method !== "GET" && request.method !== "HEAD") {
      init.body = await request.arrayBuffer();
    }

    const upstream = await fetch(API_HOST + url.pathname + url.search, init);
    const response = new Response(upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: upstream.headers,
    });
    for (const [k, v] of Object.entries(cors)) response.headers.set(k, v);
    response.headers.delete("Set-Cookie");
    return response;
  },
};
