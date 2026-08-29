/* Puente seguro entre la app (index.html) y el proceso principal.
   La app solo ve estas funciones: nada de Node ni del sistema de archivos. */
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("fsDesktop", {
  version: "1.0.0",

  /* Llamada a la API oficial sin CORS. Devuelve algo con la misma forma que
     fetch() para que authFetch() no tenga que cambiar de lógica. */
  apiFetch: async (ruta, init) => {
    const r = await ipcRenderer.invoke("fs-api", ruta, init || {});
    return { status: r.status, ok: r.ok, text: async () => r.body };
  },

  /* Login en ventana propia: devuelve la parte "?code=..." de la vuelta. */
  oauth: (url, redirect) => ipcRenderer.invoke("fs-oauth", url, redirect),

  /* Motor de datos */
  motorEstado: () => ipcRenderer.invoke("fs-motor-estado"),
  motorAhora: () => ipcRenderer.invoke("fs-motor-ahora"),
  motorActivo: (v) => ipcRenderer.invoke("fs-motor-activo", v),
  onMotor: (cb) => {
    const h = (e, estado) => { try { cb(estado); } catch(err) {} };
    ipcRenderer.on("fs-motor", h);
    return () => ipcRenderer.removeListener("fs-motor", h);
  },
});
