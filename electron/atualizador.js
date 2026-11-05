// Atualização automática do Gregio AutoEdit.
// Confere a última versão publicada em github.com/eduardogregio38-create/gregio-autoedit-download,
// baixa o pacote certo para o sistema e troca o app sozinho. Os dados da pessoa (vídeos, músicas,
// ajustes, login) ficam fora do app e não são tocados.
const { app } = require("electron");
const https = require("https");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { spawn, execFileSync } = require("child_process");

const REPO = "eduardogregio38-create/gregio-autoedit-download";

function pedir(url, destino, aoProgresso, saltos = 6) {
  return new Promise((ok, erro) => {
    const req = https.get(url, { headers: { "User-Agent": "Gregio-AutoEdit", "Accept": destino ? "application/octet-stream" : "application/vnd.github+json" } }, res => {
      if ([301, 302, 303, 307, 308].includes(res.statusCode) && res.headers.location && saltos > 0) {
        res.resume();
        return pedir(new URL(res.headers.location, url).toString(), destino, aoProgresso, saltos - 1).then(ok, erro);
      }
      if (res.statusCode !== 200) { res.resume(); return erro(new Error("HTTP " + res.statusCode)); }
      if (!destino) {
        let dados = "";
        res.setEncoding("utf8");
        res.on("data", c => dados += c);
        res.on("end", () => { try { ok(JSON.parse(dados)); } catch (e) { erro(e); } });
        return;
      }
      const total = parseInt(res.headers["content-length"] || "0", 10);
      let feito = 0;
      const arq = fs.createWriteStream(destino);
      res.on("data", c => { feito += c.length; if (aoProgresso && total) aoProgresso(feito / total); });
      res.pipe(arq);
      arq.on("finish", () => arq.close(() => ok(destino)));
      arq.on("error", erro);
    });
    req.on("error", erro);
    req.setTimeout(60000, () => req.destroy(new Error("tempo esgotado")));
  });
}

function comparar(a, b) {
  const pa = String(a).replace(/^v/, "").split(".").map(n => parseInt(n, 10) || 0);
  const pb = String(b).replace(/^v/, "").split(".").map(n => parseInt(n, 10) || 0);
  for (let i = 0; i < 3; i++) { if ((pa[i] || 0) !== (pb[i] || 0)) return (pa[i] || 0) - (pb[i] || 0); }
  return 0;
}

function nomeDoPacote() {
  if (process.platform === "win32") return "Gregio-AutoEdit-Windows-Instalador.exe";
  if (process.platform === "darwin") return "Gregio-AutoEdit-mac-" + (process.arch === "arm64" ? "arm64" : "x64") + ".zip";
  return null;
}

async function verificar() {
  const rel = await pedir("https://api.github.com/repos/" + REPO + "/releases/latest");
  const atual = app.getVersion();
  const nova = String(rel.tag_name || "").replace(/^v/, "");
  const pacote = (rel.assets || []).find(a => a.name === nomeDoPacote());
  return {
    atual, nova, tem: !!nova && comparar(nova, atual) > 0 && !!pacote,
    notas: rel.body || "", url: pacote ? pacote.browser_download_url : null, tamanho: pacote ? pacote.size : 0
  };
}

function pastaDoApp() {
  // .../Gregio AutoEdit.app/Contents/MacOS/Gregio AutoEdit  ->  .../Gregio AutoEdit.app
  return path.resolve(process.execPath, "..", "..", "..");
}

async function instalar(info, aoProgresso, aoSair) {
  if (!app.isPackaged) throw new Error("A atualização automática só funciona no app instalado.");
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "gregio-atualizacao-"));
  const baixado = path.join(tmp, nomeDoPacote());
  await pedir(info.url, baixado, aoProgresso);

  if (process.platform === "win32") {
    // instalador em modo silencioso, na mesma pasta; abre o app de novo no fim
    spawn(baixado, ["/S", "--force-run"], { detached: true, stdio: "ignore", windowsHide: true }).unref();
    aoSair();
    return;
  }

  if (process.platform === "darwin") {
    const novo = path.join(tmp, "novo");
    fs.mkdirSync(novo);
    execFileSync("/usr/bin/ditto", ["-x", "-k", baixado, novo]);
    const appNovo = fs.readdirSync(novo).find(n => n.endsWith(".app"));
    if (!appNovo) throw new Error("Pacote de atualização inválido.");
    const destino = pastaDoApp();
    const script = path.join(tmp, "trocar.sh");
    fs.writeFileSync(script, [
      "#!/bin/bash",
      'APP="$1"; NOVO="$2"; PID="$3"',
      'for i in $(seq 1 120); do kill -0 "$PID" 2>/dev/null || break; sleep 0.5; done',
      'rm -rf "$APP.antigo"',
      'if mv "$APP" "$APP.antigo" && /usr/bin/ditto "$NOVO" "$APP"; then rm -rf "$APP.antigo"; else rm -rf "$APP"; mv "$APP.antigo" "$APP"; fi',
      '/usr/bin/xattr -cr "$APP" 2>/dev/null',
      'open "$APP"',
    ].join("\n"), { mode: 0o755 });
    spawn("/bin/bash", [script, destino, path.join(novo, appNovo), String(process.pid)], { detached: true, stdio: "ignore" }).unref();
    aoSair();
    return;
  }
  throw new Error("Sistema sem atualização automática.");
}

module.exports = { verificar, instalar };
