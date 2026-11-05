// Gregio AutoEdit: janela do app (Electron). O motor de edição roda num processo
// separado (backend em Python empacotado) e a janela conversa com ele em 127.0.0.1.
const { app, BrowserWindow, shell, dialog, Menu, ipcMain } = require("electron");
const atualizador = require("./atualizador");
const { spawn } = require("child_process");
const path = require("path");
const net = require("net");
const http = require("http");
const fs = require("fs");
const crypto = require("crypto");
const CHAVE = crypto.randomBytes(32).toString("base64url");

let janela = null, backend = null, porta = 0, saindo = false;

if (!app.requestSingleInstanceLock()) { app.quit(); }
app.on("second-instance", () => { if (janela) { if (janela.isMinimized()) janela.restore(); janela.focus(); } });

function portaLivre() {
  return new Promise((ok, erro) => {
    const s = net.createServer();
    s.listen(0, "127.0.0.1", () => { const p = s.address().port; s.close(() => ok(p)); });
    s.on("error", erro);
  });
}

function caminhoBackend() {
  const nome = process.platform === "win32" ? "Gregio AutoEdit.exe" : "Gregio AutoEdit";
  if (app.isPackaged) return path.join(process.resourcesPath, "backend", nome);
  return path.join(__dirname, "..", "dist", "backend", nome);
}

function iniciarBackend() {
  const exe = caminhoBackend();
  const env = Object.assign({}, process.env, { GREGIO_PORTA: String(porta), GREGIO_TOKEN: CHAVE });
  if (!app.isPackaged && !fs.existsSync(exe)) {
    // desenvolvimento: roda o Python direto
    backend = spawn(process.env.PYTHON || "python3", [path.join(__dirname, "..", "gregio.py"), "--sem-navegador"], { env });
  } else {
    backend = spawn(exe, ["--sem-navegador"], { env, windowsHide: true });
  }
  backend.stdout && backend.stdout.on("data", d => process.stdout.write(d));
  backend.stderr && backend.stderr.on("data", d => process.stderr.write(d));
  backend.on("exit", code => {
    if (!saindo) {
      dialog.showErrorBox("Gregio AutoEdit", "O motor de edição parou (código " + code + "). Abra o app de novo.");
      app.quit();
    }
  });
}

function esperarBackend(limite = 90000) {
  const inicio = Date.now();
  return new Promise((ok, erro) => {
    const tenta = () => {
      const req = http.get({ host: "127.0.0.1", port: porta, path: "/api/estado", headers: { "X-Gregio-Chave": CHAVE }, timeout: 2000 }, res => { res.resume(); ok(); });
      req.on("error", () => { if (Date.now() - inicio > limite) erro(new Error("tempo esgotado")); else setTimeout(tenta, 400); });
      req.on("timeout", () => req.destroy());
    };
    tenta();
  });
}

async function criarJanela() {
  janela = new BrowserWindow({
    width: 1240, height: 860, minWidth: 900, minHeight: 640,
    title: "Gregio AutoEdit", backgroundColor: "#0e0e12", show: false,
    icon: path.join(__dirname, "icon.png"),
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true, preload: path.join(__dirname, "preload.js") }
  });
  janela.once("ready-to-show", () => janela.show());
  // permissões do navegador (câmera, microfone, localização...) ficam todas bloqueadas
  janela.webContents.session.setPermissionRequestHandler((wc, perm, cb) => cb(false));
  // links externos (Google Drive, login, seletor de pastas) abrem no navegador da pessoa
  janela.webContents.setWindowOpenHandler(({ url }) => { shell.openExternal(url); return { action: "deny" }; });
  janela.webContents.on("will-navigate", (e, url) => {
    if (!url.startsWith("http://127.0.0.1:" + porta)) { e.preventDefault(); shell.openExternal(url); }
  });
  janela.loadFile(path.join(__dirname, "carregando.html"));
  try {
    await esperarBackend();
    janela.loadURL("http://127.0.0.1:" + porta + "/#" + CHAVE);
  } catch (e) {
    dialog.showErrorBox("Gregio AutoEdit", "Não consegui iniciar o motor de edição. Tente abrir o app de novo.");
    app.quit();
  }
}

function menu() {
  const mac = process.platform === "darwin";
  const modelo = [
    ...(mac ? [{ role: "appMenu" }] : []),
    { label: "Arquivo", submenu: [mac ? { role: "close", label: "Fechar janela" } : { role: "quit", label: "Sair" }] },
    { role: "editMenu", label: "Editar" },
    { label: "Exibir", submenu: [{ role: "reload", label: "Recarregar" }, { role: "togglefullscreen", label: "Tela cheia" },
      { type: "separator" }, { role: "zoomIn", label: "Aumentar" }, { role: "zoomOut", label: "Diminuir" }, { role: "resetZoom", label: "Tamanho normal" }] },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(modelo));
}

// ---------- atualização ----------
let ultimaVerificacao = null;
ipcMain.handle("versao", () => app.getVersion());
ipcMain.handle("verificar-atualizacao", async () => {
  try { ultimaVerificacao = await atualizador.verificar(); return ultimaVerificacao; }
  catch (e) { return { atual: app.getVersion(), tem: false, erro: e.message }; }
});
ipcMain.handle("instalar-atualizacao", async () => {
  const info = ultimaVerificacao || await atualizador.verificar();
  if (!info.tem) return { ok: false, erro: "Você já está na versão mais nova." };
  try {
    await atualizador.instalar(info,
      p => { if (janela) janela.webContents.send("progresso-atualizacao", p); },
      () => { saindo = true; setTimeout(() => app.quit(), 300); });
    return { ok: true };
  } catch (e) { return { ok: false, erro: e.message }; }
});

app.whenReady().then(async () => {
  menu();
  porta = await portaLivre();
  iniciarBackend();
  await criarJanela();
  app.on("activate", () => { if (BrowserWindow.getAllWindows().length === 0) criarJanela(); });
});

app.on("window-all-closed", () => { app.quit(); });
app.on("before-quit", () => {
  saindo = true;
  if (backend && !backend.killed) {
    if (process.platform === "win32") spawn("taskkill", ["/pid", String(backend.pid), "/f", "/t"], { windowsHide: true });
    else backend.kill("SIGTERM");
  }
});
