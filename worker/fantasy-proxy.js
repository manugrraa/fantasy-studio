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

export default {
  async fetch(request) {
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
