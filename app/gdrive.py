"""Google Drive sem bibliotecas extras: login (OAuth com PKCE) e chamadas à API v3.
Escopo drive.file: o app só enxerga o que ele mesmo criou no Drive da pessoa."""

import json
import os
import subprocess
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/drive.file", "openid", "email"]
FOLDER = "application/vnd.google-apps.folder"
DADOS = Path(".")
CREDENCIAIS = Path("credentials.json")


def configurar(dados, credenciais):
    global DADOS, CREDENCIAIS
    DADOS, CREDENCIAIS = Path(dados), Path(credenciais)



import base64
import hashlib
import http.client
import io
import secrets
import socket
import ssl

_SSL = None
_TOKEN = {}
_TRAVA_TOKEN = threading.Lock()


def contexto_ssl():
    """Certificados: o Python do python.org não usa os do Mac, então exporta do sistema."""
    global _SSL
    if _SSL is not None:
        return _SSL
    ctx = ssl.create_default_context()
    try:
        import certifi
        ctx.load_verify_locations(certifi.where())
    except Exception:
        pem = DADOS / "certificados.pem"
        if not pem.exists() or time.time() - pem.stat().st_mtime > 30 * 86400:
            try:
                if os.name == "nt":
                    raise OSError
                saida = subprocess.run(
                    ["security", "find-certificate", "-a", "-p",
                     "/System/Library/Keychains/SystemRootCertificates.keychain",
                     "/Library/Keychains/System.keychain"],
                    capture_output=True, timeout=60).stdout
                if saida:
                    pem.write_bytes(saida)
            except Exception:
                pass
        if pem.exists():
            ctx.load_verify_locations(str(pem))
    _SSL = ctx
    return ctx


def requisicao(metodo, url, corpo=None, cabecalhos=None, destino=None, seguir=3):
    """Faz a chamada HTTPS. Devolve (status, cabeçalhos, bytes). Com `destino`, grava o corpo no arquivo."""
    u = urllib.parse.urlsplit(url)
    c = http.client.HTTPSConnection(u.netloc, timeout=120, context=contexto_ssl())
    caminho = u.path + ("?" + u.query if u.query else "")
    c.request(metodo, caminho, body=corpo, headers=cabecalhos or {})
    r = c.getresponse()
    if r.status in (301, 302, 303, 307) and seguir and r.getheader("Location"):
        r.read()
        c.close()
        return requisicao("GET", r.getheader("Location"), None, None, destino, seguir - 1)
    hdr = {k.lower(): v for k, v in r.getheaders()}
    if destino is not None and 200 <= r.status < 300:
        with open(destino, "wb") as f:
            while True:
                bloco = r.read(1024 * 1024)
                if not bloco:
                    break
                f.write(bloco)
        dados = b""
    else:
        dados = r.read()
    c.close()
    return r.status, hdr, dados


def _credenciais():
    # a credencial vem da variável de ambiente GREGIO_GOOGLE_CREDENCIAIS (ou do arquivo local, que fica fora do git)
    texto = os.environ.get("GREGIO_GOOGLE_CREDENCIAIS")
    d = json.loads(texto) if texto else json.loads(CREDENCIAIS.read_text(encoding="utf-8"))
    return d.get("installed") or d.get("web")


def _salvar_token(t):
    tok = DADOS / "token.json"
    tok.write_text(json.dumps(t), encoding="utf-8")
    os.chmod(tok, 0o600)


def _trocar_token(campos):
    cred = _credenciais()
    campos.update({"client_id": cred["client_id"], "client_secret": cred["client_secret"]})
    st, _, dados = requisicao("POST", cred.get("token_uri", "https://oauth2.googleapis.com/token"),
                              urllib.parse.urlencode(campos).encode(),
                              {"Content-Type": "application/x-www-form-urlencoded"})
    r = json.loads(dados.decode("utf-8") or "{}")
    if st != 200:
        raise PermissionError(r.get("error_description") or r.get("error") or "login")
    return r


def token_acesso():
    with _TRAVA_TOKEN:
        if not _TOKEN:
            tok = DADOS / "token.json"
            if not tok.exists():
                raise PermissionError("login")
            _TOKEN.update(json.loads(tok.read_text(encoding="utf-8")))
        if not _TOKEN.get("refresh_token"):
            raise PermissionError("login")
        if not _TOKEN.get("access_token") or _TOKEN.get("expira", 0) < time.time() + 60:
            r = _trocar_token({"grant_type": "refresh_token", "refresh_token": _TOKEN["refresh_token"]})
            _TOKEN["access_token"] = r["access_token"]
            _TOKEN["expira"] = time.time() + int(r.get("expires_in", 3600))
            _salvar_token(_TOKEN)
        return _TOKEN["access_token"]


def servico():
    """Só confirma que há login válido (mantido o nome antigo)."""
    return token_acesso()


def fazer_login():
    """Abre o navegador para a pessoa entrar com a conta Google dela."""
    cred = _credenciais()
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    porta = s.getsockname()[1]
    s.close()
    redirect = "http://127.0.0.1:%d/" % porta
    verificador = secrets.token_urlsafe(64)
    desafio = base64.urlsafe_b64encode(hashlib.sha256(verificador.encode()).digest()).decode().rstrip("=")
    estado = secrets.token_urlsafe(16)
    resultado = {}

    class Volta(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query))
            if q.get("state") == estado and ("code" in q or "error" in q):
                resultado.update(q)
            msg = "Pronto! Pode fechar esta aba e voltar ao Gregio AutoEdit." if "code" in q else "Não deu certo. Volte ao Gregio AutoEdit e tente de novo."
            corpo = ("<html><body style='font-family:-apple-system;text-align:center;padding:60px'><h2>%s</h2></body></html>" % msg).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)

    srv = ThreadingHTTPServer(("127.0.0.1", porta), Volta)
    srv.timeout = 1
    url = cred.get("auth_uri", "https://accounts.google.com/o/oauth2/auth") + "?" + urllib.parse.urlencode({
        "client_id": cred["client_id"], "redirect_uri": redirect, "response_type": "code",
        "scope": " ".join(SCOPES), "access_type": "offline", "prompt": "consent",
        "state": estado, "code_challenge": desafio, "code_challenge_method": "S256"})
    webbrowser.open(url)
    limite = time.time() + 600
    while not resultado and time.time() < limite:
        srv.handle_request()
    srv.server_close()
    if "code" not in resultado:
        raise RuntimeError(resultado.get("error") or "tempo esgotado")
    r = _trocar_token({"grant_type": "authorization_code", "code": resultado["code"],
                       "redirect_uri": redirect, "code_verifier": verificador})
    with _TRAVA_TOKEN:
        _TOKEN.clear()
        _TOKEN.update({"refresh_token": r.get("refresh_token"), "access_token": r["access_token"],
                       "expira": time.time() + int(r.get("expires_in", 3600))})
        _salvar_token(_TOKEN)


API = "https://www.googleapis.com/drive/v3"
UPLOAD = "https://www.googleapis.com/upload/drive/v3"


def drive(metodo, caminho, params=None, json_corpo=None, base=API, corpo=None, cab=None, destino=None):
    p = dict(params or {})
    p["supportsAllDrives"] = "true"
    url = base + caminho + "?" + urllib.parse.urlencode(p)
    h = {"Authorization": "Bearer " + token_acesso()}
    if json_corpo is not None:
        corpo = json.dumps(json_corpo).encode("utf-8")
        h["Content-Type"] = "application/json; charset=UTF-8"
    h.update(cab or {})
    st, hdr, dados = requisicao(metodo, url, corpo, h, destino)
    if st == 401:
        _TOKEN.pop("access_token", None)
    if st >= 400:
        try:
            msg = json.loads(dados.decode("utf-8"))["error"]["message"]
        except Exception:
            msg = dados[:200].decode("utf-8", "replace")
        raise RuntimeError("Drive %d: %s" % (st, msg))
    if destino is not None:
        return {}
    return json.loads(dados.decode("utf-8")) if dados else {}


def listar(pasta_id, extra=""):
    q = "'%s' in parents and trashed=false %s" % (pasta_id, extra)
    itens, token = [], None
    while True:
        p = {"q": q, "fields": "nextPageToken, files(id,name,mimeType,modifiedTime,size)",
             "pageSize": 200, "includeItemsFromAllDrives": "true"}
        if token:
            p["pageToken"] = token
        r = drive("GET", "/files", p)
        itens += r.get("files", [])
        token = r.get("nextPageToken")
        if not token:
            return itens


def criar_pasta(pai, nome):
    return drive("POST", "/files", {"fields": "id"}, {"name": nome, "mimeType": FOLDER, "parents": [pai]})["id"]


def subpasta(pai, nome):
    for f in listar(pai, "and mimeType='%s'" % FOLDER):
        if f["name"] == nome:
            return f["id"]
    return criar_pasta(pai, nome)


def mover(pasta_id, destino_id):
    pais = drive("GET", "/files/" + pasta_id, {"fields": "parents"}).get("parents", [])
    drive("PATCH", "/files/" + pasta_id, {"addParents": destino_id, "removeParents": ",".join(pais),
                                          "fields": "id"}, {})


def renomear(pasta_id, nome):
    drive("PATCH", "/files/" + pasta_id, {"fields": "id"}, {"name": nome})


def baixar_arquivo(file_id, alvo):
    drive("GET", "/files/" + file_id, {"alt": "media"}, destino=str(alvo))


def ler_json_drive(pasta_id, nome):
    for f in listar(pasta_id):
        if f["name"] == nome:
            st, _, dados = requisicao("GET", API + "/files/%s?alt=media&supportsAllDrives=true" % f["id"], None,
                                      {"Authorization": "Bearer " + token_acesso()})
            if st == 200:
                return f["id"], json.loads(dados.decode("utf-8"))
    return None, None


def _multipart(meta, conteudo, tipo):
    limite = "autoedit" + secrets.token_hex(8)
    corpo = ("--%s\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n%s\r\n--%s\r\nContent-Type: %s\r\n\r\n"
             % (limite, json.dumps(meta), limite, tipo)).encode("utf-8") + conteudo + ("\r\n--%s--" % limite).encode()
    return corpo, {"Content-Type": "multipart/related; boundary=" + limite}


def gravar_json_drive(pasta_id, nome, dados, fid=None):
    conteudo = json.dumps(dados, ensure_ascii=False, indent=1).encode("utf-8")
    if fid:
        drive("PATCH", "/files/" + fid, {"uploadType": "media"}, base=UPLOAD, corpo=conteudo,
              cab={"Content-Type": "application/json"})
    else:
        corpo, cab = _multipart({"name": nome, "parents": [pasta_id]}, conteudo, "application/json")
        drive("POST", "/files", {"uploadType": "multipart", "fields": "id"}, base=UPLOAD, corpo=corpo, cab=cab)


def enviar_grande(caminho, nome, pasta, progresso):
    """Envio retomável em pedaços de 8 MB; chama progresso(bytes_enviados)."""
    tam = os.path.getsize(caminho)
    url = UPLOAD + "/files?" + urllib.parse.urlencode({"uploadType": "resumable", "supportsAllDrives": "true",
                                                       "fields": "id"})
    st, hdr, dados = requisicao("POST", url, json.dumps({"name": nome, "parents": [pasta]}).encode("utf-8"),
                                {"Authorization": "Bearer " + token_acesso(),
                                 "Content-Type": "application/json; charset=UTF-8",
                                 "X-Upload-Content-Length": str(tam)})
    if st != 200 or "location" not in hdr:
        raise RuntimeError("Drive %d ao iniciar envio: %s" % (st, dados[:200]))
    sessao = hdr["location"]
    pedaco = 8 * 1024 * 1024
    inicio = 0
    tentativas = 0
    with open(caminho, "rb") as f:
        while True:
            f.seek(inicio)
            bloco = f.read(pedaco)
            fim = inicio + len(bloco) - 1
            faixa = "bytes %d-%d/%d" % (inicio, fim, tam) if bloco else "bytes */%d" % tam
            try:
                st, hdr, dados = requisicao("PUT", sessao, bloco, {"Content-Length": str(len(bloco)),
                                                                    "Content-Range": faixa}, seguir=0)
            except (OSError, http.client.HTTPException):
                tentativas += 1
                if tentativas > 8:
                    raise
                time.sleep(min(30, 2 ** tentativas))
                st, hdr, dados = requisicao("PUT", sessao, b"", {"Content-Length": "0",
                                                                  "Content-Range": "bytes */%d" % tam}, seguir=0)
            if st in (200, 201):
                progresso(tam)
                return json.loads(dados.decode("utf-8"))
            if st == 308:
                r = hdr.get("range")
                inicio = int(r.split("-")[1]) + 1 if r else 0
                progresso(inicio)
                tentativas = 0
                continue
            if st >= 500:
                tentativas += 1
                if tentativas > 8:
                    raise RuntimeError("Drive %d no envio" % st)
                time.sleep(min(30, 2 ** tentativas))
                continue
            raise RuntimeError("Drive %d no envio: %s" % (st, dados[:200]))




def sair():
    with _TRAVA_TOKEN:
        _TOKEN.clear()
    try:
        (DADOS / "token.json").unlink()
    except OSError:
        pass


def email():
    """E-mail da conta conectada (para mostrar no app)."""
    st, _, dados = requisicao("GET", "https://openidconnect.googleapis.com/v1/userinfo", None,
                              {"Authorization": "Bearer " + token_acesso()})
    if st == 200:
        return json.loads(dados.decode("utf-8")).get("email", "")
    return ""


def achar_ou_criar(pai, nome):
    """Pasta pelo nome (só entre as que o app criou)."""
    q = "name='%s' and mimeType='%s' and trashed=false" % (nome.replace("'", "\\'"), FOLDER)
    if pai:
        q += " and '%s' in parents" % pai
    r = drive("GET", "/files", {"q": q, "fields": "files(id,name)", "pageSize": 10})
    if r.get("files"):
        return r["files"][0]["id"]
    meta = {"name": nome, "mimeType": FOLDER}
    if pai:
        meta["parents"] = [pai]
    return drive("POST", "/files", {"fields": "id"}, meta)["id"]


def link_pasta(pasta_id):
    return "https://drive.google.com/drive/folders/" + pasta_id
