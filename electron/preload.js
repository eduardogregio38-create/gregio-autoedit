// Ponte segura entre a tela e o app: só expõe versão e atualização, nada mais.
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("gregioApp", {
  versao: () => ipcRenderer.invoke("versao"),
  verificar: () => ipcRenderer.invoke("verificar-atualizacao"),
  atualizar: () => ipcRenderer.invoke("instalar-atualizacao"),
  aoProgresso: fn => ipcRenderer.on("progresso-atualizacao", (_e, p) => fn(p)),
});
