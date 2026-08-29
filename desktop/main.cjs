/* Fantasy Studio — proceso principal de Electron.

   Tres cosas que la web no puede hacer:
     1. Servir la app desde la carpeta del repo (un solo código fuente: el mismo
        index.html que usa el iPhone) en un servidor local, así los data/*.json
        se leen igual que en GitHub Pages y funciona sin conexión.
     2. Llamar a la API oficial sin CORS: aquí no hace falta el Worker puente,
        y el login de Google se hace en una ventana propia (sin copiar enlaces).
     3. Motor de datos: el PC ejecuta la actualización a la 01:00 (cuando el
        juego publica los valores) y la sube al repo, así el iPhone también la
        recibe. Desde casa la API responde con datos frescos; los servidores de
        GitHub reciben copias cacheadas de CloudFront.
*/
const { app, BrowserWindow, ipcMain, shell, dialog, Tray, Menu } = require("electron");
const fs = require("fs");
const path = require("path");
const http = require("http");
const { execFile } = require("child_process");

const PORT = 8614;                 // fijo: localStorage depende del origen
const HOST = "127.0.0.1";
const API_HOST = "https://fantasy-api.llt-services.com";
const DEFAULT_REPO = "E:\\A RdeRandom\\Webs\\Fantasy Studio";

app.setAppUserModelId("com.manugrraa.fantasystudio");
// Solo una instancia: si ya hay un Fantasy Studio (aunque sea en la bandeja),
// esta se va YA y aquella se trae al frente (evento second-instance de abajo).
// app.quit() no basta: es asíncrono y el arranque seguía hasta chocar con el
// puerto ocupado y enseñar un error que no tocaba.
if (!app.requestSingleInstanceLock()) app.exit(0);

let mainWindow = null;
let tray = null;
let saliendo = false;
const consolaRenderer = [];
app.on("web-contents-created", (e, wc) => {
  wc.on("console-message", (ev, nivel, msg) => {
    const texto = (typeof ev === "object" && ev.message !== undefined) ? ev.message : msg;
    if (texto) consolaRenderer.push(String(texto).slice(0, 300));
  });
  wc.on("did-fail-load", (ev, code, desc, url) => consolaRenderer.push(`fallo-carga ${code} ${desc} ${url}`));
});

/* ---------- configuración local ---------- */
const cfgFile = () => path.join(app.getPath("userData"), "config.json");
function readCfg(){
  try { return JSON.parse(fs.readFileSync(cfgFile(), "utf8")); } catch(e) { return {}; }
}
function writeCfg(obj){
  try {
    fs.mkdirSync(app.getPath("userData"), { recursive: true });
    fs.writeFileSync(cfgFile(), JSON.stringify(obj, null, 2));
  } catch(e) {}
}

/* ---------- dónde está el repo (la app y los datos) ---------- */
let REPO = null;
function findRepo(){
  const cands = [
    process.env.FS_REPO,
    readCfg().repo,
    path.join(__dirname, ".."),                          // desktop/ dentro del repo
    path.join(path.dirname(app.getPath("exe")), ".."),   // exe junto al repo
    DEFAULT_REPO,
  ];
  for (const c of cands) {
    try { if (c && fs.existsSync(path.join(c, "index.html"))) return path.resolve(c); } catch(e) {}
  }
  return null;
}
async function askRepo(){
  const r = await dialog.showOpenDialog({
    title: "¿Dónde está la carpeta de Fantasy Studio?",
    properties: ["openDirectory"],
  });
  if (r.canceled || !r.filePaths.length) return null;
  const p = r.filePaths[0];
  if (!fs.existsSync(path.join(p, "index.html"))) {
    dialog.showErrorBox("Carpeta incorrecta", "Ahí no está el index.html de Fantasy Studio.");
    return null;
  }
  const cfg = readCfg(); cfg.repo = p; writeCfg(cfg);
  return p;
}

/* ---------- servidor local (solo lectura, solo 127.0.0.1) ---------- */
const MIME = {
  ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8",
  ".webmanifest": "application/manifest+json", ".png": "image/png",
  ".jpg": "image/jpeg", ".svg": "image/svg+xml", ".ico": "image/x-icon",
  ".woff2": "font/woff2", ".txt": "text/plain; charset=utf-8",
};
function startServer(){
  return new Promise((resolve, reject) => {
    const server = http.createServer((req, res) => {
      if (req.method !== "GET" && req.method !== "HEAD") { res.writeHead(405); res.end(); return; }
      let rel;
      try { rel = decodeURIComponent(new URL(req.url, "http://x").pathname); } catch(e) { res.writeHead(400); res.end(); return; }
      if (rel === "/" || rel === "") rel = "/index.html";
      const file = path.join(REPO, rel);
      // nunca fuera de la carpeta del repo
      if (!file.startsWith(REPO + path.sep) && file !== REPO) { res.writeHead(403); res.end(); return; }
      fs.readFile(file, (err, buf) => {
        if (err) { res.writeHead(404, { "Content-Type": "text/plain" }); res.end("no encontrado"); return; }
        res.writeHead(200, {
          "Content-Type": MIME[path.extname(file).toLowerCase()] || "application/octet-stream",
          "Cache-Control": "no-store",
        });
        res.end(req.method === "HEAD" ? undefined : buf);
      });
    });
    server.on("error", reject);
    server.listen(PORT, HOST, () => resolve(server));
  });
}

/* ---------- API oficial sin CORS ---------- */
ipcMain.handle("fs-api", async (e, rutaApi, init) => {
  if (typeof rutaApi !== "string" || !rutaApi.startsWith("/")) throw new Error("ruta no válida");
  const opts = init || {};
  const headers = { "User-Agent": "Mozilla/5.0 (compatible; FantasyStudio/1.0)", "Accept": "application/json" };
  for (const k of ["authorization", "x-lang", "content-type"]) {
    const v = opts.headers && (opts.headers[k] ?? opts.headers[k.replace(/(^|-)([a-z])/g, (m,a,b) => a + b.toUpperCase())]);
    if (v) headers[k] = v;
  }
  const r = await fetch(API_HOST + rutaApi, {
    method: opts.method || "GET",
    headers,
    body: opts.body || undefined,
    signal: AbortSignal.timeout(20000),
  });
  return { status: r.status, ok: r.ok, body: await r.text() };
});

/* ---------- login de LaLiga en ventana propia ---------- */
ipcMain.handle("fs-oauth", (ev, url, redirect) => new Promise((resolve) => {
  let win = new BrowserWindow({
    width: 520, height: 700, show: false,
    parent: mainWindow || undefined, autoHideMenuBar: true,
    title: "Iniciar sesión en LaLiga Fantasy",
    webPreferences: { nodeIntegration: false, contextIsolation: true },
  });
  let done = false;
  const finish = (q) => {
    if (done) return; done = true;
    try { win.destroy(); } catch(e) {}
    win = null;
    resolve(q || "");
  };
  const check = (u) => {
    if (!u || typeof u !== "string" || !u.startsWith(redirect)) return false;
    const i = u.indexOf("?"), h = u.indexOf("#");
    finish(u.slice(Math.min(...[i, h].filter(x => x >= 0).concat([u.length]))));
    return true;
  };
  win.webContents.on("will-redirect", (e, u) => { if (check(u)) e.preventDefault(); });
  win.webContents.on("will-navigate", (e, u) => { if (check(u)) e.preventDefault(); });
  win.webContents.on("did-navigate", (e, u) => { check(u); });
  win.on("closed", () => finish(""));
  win.once("ready-to-show", () => { try { win.show(); } catch(e) {} });
  win.loadURL(url).catch(() => finish(""));
}));

/* =====================================================================
   MOTOR DE DATOS: actualiza y sube desde este PC
   ===================================================================== */
const motor = {
  activo: readCfg().motor !== false,
  corriendo: false,
  ultimo: readCfg().ultimo || null,   // { at, ok, msg }
  hechos: {},                         // "20260829-01:05" -> true
  log: [],
};

function logMotor(txt){
  motor.log.unshift({ at: new Date().toISOString(), txt });
  motor.log = motor.log.slice(0, 60);
  avisarRenderer();
}
function avisarRenderer(){
  try { mainWindow?.webContents.send("fs-motor", estadoMotor()); } catch(e) {}
}
function estadoMotor(){
  return { activo: motor.activo, corriendo: motor.corriendo, ultimo: motor.ultimo, log: motor.log.slice(0, 12), repo: REPO };
}

function run(cmd, args, opts){
  return new Promise((resolve) => {
    execFile(cmd, args, { cwd: REPO, windowsHide: true, maxBuffer: 8 * 1024 * 1024, ...(opts || {}) },
      (err, stdout, stderr) => resolve({ code: err ? (err.code ?? 1) : 0, out: String(stdout || ""), err: String(stderr || "") }));
  });
}

const GIT_ID = ["-c", "user.name=manugrraa", "-c", "user.email=manugrraa@gmail.com"];

/* Los JSON de data/ se regeneran enteros en cada pasada, así que NUNCA se
   fusionan: si el remoto se ha movido, se descarta lo local y se vuelve a
   generar. Mezclarlos (pull --rebase --autostash) llegó a dejar marcadores de
   conflicto dentro de los JSON y a publicarlos: no volver a intentarlo. */
async function ponerseAlDia(){
  await run("git", ["checkout", "--", "data"]);   // datos locales sueltos: regenerables
  await run("git", ["fetch", "origin", "-q"]);
  // avance directo, nunca fusión: si el repo tiene trabajo a medias esto falla
  // solo, sin tocar nada (y la próxima pasada lo reintenta)
  const detras = await run("git", ["rev-list", "--count", "HEAD..origin/main"]);
  if (+detras.out.trim() > 0) {
    const ff = await run("git", ["merge", "--ff-only", "origin/main", "-q"]);
    if (ff.code !== 0) throw new Error("el repo tiene cambios sin guardar; no toco nada");
  }
}
/* Red de seguridad: jamás subir un data/ que no sea JSON válido. */
function dataSana(){
  const dir = path.join(REPO, "data");
  for (const f of fs.readdirSync(dir).filter(n => n.endsWith(".json"))) {
    const txt = fs.readFileSync(path.join(dir, f), "utf8");
    if (/^<<<<<<< |^>>>>>>> /m.test(txt)) return `${f} tiene marcadores de conflicto`;
    try { JSON.parse(txt); } catch(e) { return `${f} no es JSON válido`; }
  }
  return null;
}

async function actualizarDatos(motivo){
  if (motor.corriendo) return { ok: false, msg: "ya estaba actualizando" };
  motor.corriendo = true;
  avisarRenderer();
  const t0 = Date.now();
  let msg = "";
  try {
    logMotor(`Actualizando (${motivo})…`);
    await ponerseAlDia();
    const py = await run("python", ["scripts/update_data.py"]);
    const linea = (py.out + py.err).split(/\r?\n/).filter(l => /valores:|historico:|meta actualizada/.test(l)).slice(-3).join(" · ");
    if (py.code !== 0) throw new Error("el script falló: " + (py.err.split(/\r?\n/).slice(-2).join(" ") || "código " + py.code));
    logMotor(linea || "datos descargados");

    const mal = dataSana();
    if (mal) { await run("git", ["checkout", "--", "data"]); throw new Error("datos descartados: " + mal); }

    // el commit lleva SIEMPRE la ruta data: así nunca puede arrastrar un
    // index.html a medio editar aunque esté suelto en el repo
    const commitDatos = async () => {
      const hay = await run("git", ["status", "--porcelain", "--", "data"]);
      if (!hay.out.trim()) return false;
      const cm = await run("git", [...GIT_ID, "commit", "-q", "-m", `datos: actualización desde el PC (${motivo})`, "--", "data"]);
      if (cm.code !== 0) throw new Error("no se pudo guardar el commit");
      return true;
    };
    if (!(await commitDatos())) {
      msg = "sin cambios que subir";
      logMotor("Nada nuevo que subir");
    } else {
      let push = await run("git", ["push", "-q"]);
      if (push.code !== 0) {
        // el remoto se movió: ponerse al día, regenerar y reintentar UNA vez
        logMotor("GitHub se adelantó; regenerando sobre lo último");
        await run("git", ["reset", "--soft", "HEAD~1"]);   // deshacer solo nuestro commit
        await ponerseAlDia();
        const py2 = await run("python", ["scripts/update_data.py"]);
        if (py2.code !== 0) throw new Error("el script falló al reintentar");
        const mal2 = dataSana();
        if (mal2) { await run("git", ["checkout", "--", "data"]); throw new Error("datos descartados: " + mal2); }
        await commitDatos();
        push = await run("git", ["push", "-q"]);
      }
      if (push.code !== 0) throw new Error("no se pudo subir a GitHub (¿sin conexión?)");
      msg = "datos subidos";
      logMotor("Subido a GitHub — el iPhone ya lo tiene");
    }
    motor.ultimo = { at: Date.now(), ok: true, msg: msg + ` · ${((Date.now()-t0)/1000).toFixed(0)}s` };
  } catch(e) {
    motor.ultimo = { at: Date.now(), ok: false, msg: e.message };
    logMotor("⚠ " + e.message);
  }
  motor.corriendo = false;
  const cfg = readCfg(); cfg.ultimo = motor.ultimo; writeCfg(cfg);
  avisarRenderer();
  // la app recarga sus datos al ver meta.json nuevo (autoCheck)
  return { ok: !!motor.ultimo.ok, msg: motor.ultimo.msg };
}

/* Horarios (hora local = hora de Madrid).
   Madrugada: el juego publica los valores a la 01:00; varios intentos porque la
   primera respuesta puede llegar cacheada (el script tiene su propio rescate).
   Día: cada cuarto de hora para puntos en vivo, estados y mercado. */
const SLOTS_NOCHE = ["01:04", "01:14", "01:34", "02:04"];
function slotAhora(){
  const d = new Date();
  const hh = String(d.getHours()).padStart(2, "0"), mm = String(d.getMinutes()).padStart(2, "0");
  const hm = `${hh}:${mm}`;
  if (SLOTS_NOCHE.includes(hm)) return { key: hm, motivo: "valores de la 01:00" };
  if (d.getHours() >= 9 && [3, 18, 33, 48].includes(d.getMinutes())) return { key: hm, motivo: "rutina" };
  return null;
}
setInterval(() => {
  if (!motor.activo || motor.corriendo || !REPO) return;
  const s = slotAhora();
  if (!s) return;
  const dia = new Date().toISOString().slice(0, 10);
  const k = dia + " " + s.key;
  if (motor.hechos[k]) return;
  motor.hechos[k] = true;
  actualizarDatos(s.motivo);
}, 20000);

ipcMain.handle("fs-motor-estado", () => estadoMotor());
ipcMain.handle("fs-motor-ahora", () => actualizarDatos("a mano"));
ipcMain.handle("fs-motor-activo", (e, v) => {
  motor.activo = !!v;
  const cfg = readCfg(); cfg.motor = motor.activo; writeCfg(cfg);
  logMotor(motor.activo ? "Motor de datos activado" : "Motor de datos en pausa");
  return estadoMotor();
});

/* ---------- ventana e icono de bandeja ---------- */
function createWindow(){
  mainWindow = new BrowserWindow({
    width: 1280, height: 900, minWidth: 380, minHeight: 560,
    backgroundColor: "#000000", autoHideMenuBar: true, show: false,
    title: "Fantasy Studio",
    icon: path.join(REPO, "icon-512.png"),
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true, nodeIntegration: false,
      // con la ventana oculta en la bandeja, Chromium ralentiza los timers;
      // las pujas programadas necesitan el reloj exacto
      backgroundThrottling: false,
    },
  });
  mainWindow.loadURL(`http://${HOST}:${PORT}/`);
  mainWindow.once("ready-to-show", () => mainWindow.show());
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:/i.test(url)) shell.openExternal(url);
    return { action: "deny" };
  });
  mainWindow.webContents.on("will-navigate", (e, u) => {
    if (!u.startsWith(`http://${HOST}:${PORT}`)) {
      e.preventDefault();
      if (/^https?:/i.test(u)) shell.openExternal(u);
    }
  });
  // cerrar la ventana deja el motor trabajando en la bandeja
  mainWindow.on("close", (e) => {
    if (!saliendo && motor.activo) {
      e.preventDefault();
      mainWindow.hide();
      if (!readCfg().avisadoBandeja) {
        const cfg = readCfg(); cfg.avisadoBandeja = true; writeCfg(cfg);
        try { tray?.displayBalloon?.({ title: "Fantasy Studio sigue trabajando", content: "Mantiene los datos al día desde la bandeja. Para cerrarlo del todo: clic derecho en el icono → Salir." }); } catch(err) {}
      }
    }
  });
  mainWindow.on("closed", () => { mainWindow = null; });
}

function createTray(){
  const ico = path.join(REPO, "icon-192.png");
  if (!fs.existsSync(ico)) return;
  tray = new Tray(ico);
  tray.setToolTip("Fantasy Studio");
  const menu = Menu.buildFromTemplate([
    { label: "Abrir Fantasy Studio", click: () => { if (mainWindow) { mainWindow.show(); mainWindow.focus(); } else createWindow(); } },
    { label: "Actualizar datos ahora", click: () => actualizarDatos("a mano") },
    { type: "separator" },
    { label: "Salir", click: () => { saliendo = true; app.quit(); } },
  ]);
  tray.setContextMenu(menu);
  tray.on("double-click", () => { if (mainWindow) { mainWindow.show(); mainWindow.focus(); } else createWindow(); });
}

app.on("second-instance", () => {
  if (mainWindow) { if (mainWindow.isMinimized()) mainWindow.restore(); mainWindow.show(); mainWindow.focus(); }
});

app.whenReady().then(async () => {
  REPO = findRepo();
  if (!REPO) REPO = await askRepo();
  if (!REPO) { dialog.showErrorBox("Fantasy Studio", "No he encontrado la carpeta de la app."); app.quit(); return; }
  try {
    await startServer();
  } catch(e) {
    dialog.showErrorBox("Fantasy Studio", e.code === "EADDRINUSE"
      ? "Ya hay un Fantasy Studio abierto en este equipo (mira el icono de la bandeja, junto al reloj). Ciérralo con clic derecho → Salir y vuelve a abrir este."
      : `No he podido abrir el servidor local en el puerto ${PORT}.\n\n${e.message}`);
    app.exit(1); return;
  }
  createTray();
  createWindow();
});

app.on("activate", () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); });
app.on("before-quit", () => { saliendo = true; });
app.on("window-all-closed", () => { if (!motor.activo) app.quit(); });

/* ---------- autocomprobación (FS_SELFTEST=1): revisa el circuito y sale ----------
   Comprueba servidor local, carga de la app, puente de API sin CORS y las
   herramientas del motor. No toca el repo ni sube nada. */
if (process.env.FS_SELFTEST) {
  app.whenReady().then(async () => {
    const res = {};
    const wait = ms => new Promise(r => setTimeout(r, ms));
    try {
      await wait(2500);
      res.repo = REPO;
      for (const ruta of ["/index.html", "/data/meta.json", "/icon-192.png"]) {
        const r = await fetch(`http://${HOST}:${PORT}${ruta}`);
        res["servidor" + ruta] = r.status;
      }
      const wc = mainWindow?.webContents;
      res.appCargada = !!wc && !wc.isLoading();
      res.consola = consolaRenderer.slice(0, 8);
      if (wc) {
        res.pruebaFetch = await wc.executeJavaScript(
          `fetch("data/players_2.json").then(r => r.status + " · " + r.headers.get("content-type")).catch(e => "ERROR " + e.message)`
        ).catch(e => "no evaluable: " + e.message);
        res.diagnostico = await wc.executeJavaScript(`(async () => {
          const antes = { comps: Object.keys(state.players), modo: state.mode, comp: comp(), meta: state.meta?.updatedAt || null };
          let errorCarga = null;
          try { await loadAll(); } catch(e) { errorCarga = e.message; }
          return { ...antes, errorCarga, trasCargar: Object.fromEntries(Object.entries(state.players).map(([k,v]) => [k, v.length])) };
        })()`).catch(e => "no evaluable: " + e.message);
      }
      // esperar a que la app termine de cargar sus datos (hasta 20 s)
      for (let i = 0; wc && i < 40; i++) {
        const n = await wc.executeJavaScript(`(state.players[comp()] || []).length`).catch(() => 0);
        if (n > 0) break;
        await wait(500);
      }
      if (wc) {
        res.renderer = await wc.executeJavaScript(`(() => {
          // sin Worker configurado, el PC debe saltarse el paso del puente
          const guardado = state.workerUrl;
          state.workerUrl = "";
          const sinPuente = !viewLiga().includes("servidor puente propio");
          state.workerUrl = guardado;
          return {
            puente: !!window.fsDesktop,
            jugadores: (state.players[comp()] || []).length,
            modoEscritorio: !!DESK,
            sePasaElPasoDelPuente: sinPuente,
            botonLoginNativo: typeof socialDesktop === "function",
            panelMotor: typeof motorHtml === "function",
            pujasProgramadas: typeof runSchedBids === "function" && typeof cancelSchedBid === "function",
            plantillasCongeladas: typeof squadAtWeekStart === "function",
          };
        })()`);
      }
      const api = await fetch(API_HOST + "/api/v3/teams-master?x-lang=es", { headers: { "x-lang": "es" } });
      res.apiDirecta = api.status;
      const py = await run("python", ["--version"]);
      res.python = py.out.trim() || py.err.trim();
      const git = await run("git", ["rev-parse", "--abbrev-ref", "HEAD"]);
      res.gitRama = git.out.trim();
      const remoto = await run("git", ["ls-remote", "--exit-code", "origin", "HEAD"]);
      res.gitCredenciales = remoto.code === 0 ? "ok" : "fallan";
      // la red de seguridad debe cazar JSON roto y marcadores de conflicto
      const cobaya = path.join(REPO, "data", "status_log_2.json");
      const bueno = fs.readFileSync(cobaya, "utf8");
      fs.writeFileSync(cobaya, "<<<<<<< Updated upstream\n{}\n>>>>>>> Stashed changes");
      res.guardaConflicto = dataSana();
      fs.writeFileSync(cobaya, "{ esto no es json");
      res.guardaJsonRoto = dataSana();
      fs.writeFileSync(cobaya, bueno);
      res.guardaConDatosSanos = dataSana();
      if (process.env.FS_MOTOR_TEST) {
        res.motor = await actualizarDatos("prueba");
        res.motorLog = motor.log.map(l => l.txt);
      }
    } catch(e) {
      res.error = e.message;
    }
    const txt = JSON.stringify(res, null, 2);
    console.log("SELFTEST " + txt);
    // el exe empaquetado no escribe en consola: dejamos el resultado en un archivo
    try { fs.writeFileSync(process.env.FS_SELFTEST_OUT || path.join(app.getPath("temp"), "fantasy-selftest.json"), txt); } catch(e) {}
    saliendo = true;
    app.exit(0);
  });
}
