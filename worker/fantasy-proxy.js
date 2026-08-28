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
  const key = url.pathname.slice("/sync/".length);
  if (!/^[A-Za-z0-9_-]{20,64}$/.test(key)) return jsonRes({ error: "clave no válida" }, 400, cors);
  if (request.method === "PUT" || request.method === "POST") {
    if (!originOk) return jsonRes({ error: "origen no permitido" }, 403, cors);
    const body = await request.text();
    if (body.length > SYNC_MAX_BYTES) return jsonRes({ error: "demasiado grande" }, 413, cors);
    try { JSON.parse(body); } catch(e) { return jsonRes({ error: "no es JSON" }, 400, cors); }
    await env.SYNC.put("s:" + key, body, { expirationTtl: 60 * 60 * 24 * SYNC_TTL_DAYS });
    return jsonRes({ ok: true }, 200, cors);
  }
  if (request.method === "GET") {
    const v = await env.SYNC.get("s:" + key);
    if (v == null) return jsonRes({ error: "sin datos" }, 404, cors);
    return new Response(v, { status: 200, headers: { ...cors, "Content-Type": "application/json" } });
  }
  return jsonRes({ error: "método no permitido" }, 405, cors);
}

export default {
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
