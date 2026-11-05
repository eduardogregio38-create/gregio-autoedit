"""Gregio AutoEdit: o app (servidor local + janela no navegador).

Tudo roda no computador da pessoa:
  - Mídias: ela solta os takes, o app guarda em ~/Gregio AutoEdit/Projetos e edita aqui mesmo.
  - Vídeos prontos: assiste, aprova (vai para o Google Drive dela) ou reprova (o app aprende).
  - Ajustes: padrão da edição, músicas, logo da capa, tela final e o que o app aprendeu.
O Google Drive guarda os vídeos aprovados e os ajustes, numa pasta "Gregio AutoEdit".
"""

import copy
import datetime
import json
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import hmac
import secrets
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parent
sys.path.insert(0, str(AQUI))
sys.path.insert(0, str(RAIZ / "motor"))

import gdrive  # noqa: E402

NOME_APP = "Gregio AutoEdit"
VERSAO = "2.0"
PORTA = int(os.environ.get("GREGIO_PORTA", "8765"))
# Chave desta sessão: só a janela do app (que recebe a chave ao abrir) consegue falar com o motor.
# Sites abertos no navegador não sabem a chave, então não conseguem pedir nada a ele.
CHAVE = os.environ.get("GREGIO_TOKEN") or secrets.token_urlsafe(32)
LIVRES = {"/", "/index.html", "/picker", "/favicon.ico"}


def _pasta_dados():
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / NOME_APP
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", Path.home())) / NOME_APP
    return Path.home() / ".local" / "share" / NOME_APP


DADOS = Path(os.environ.get("GREGIO_DADOS", _pasta_dados()))
PASTA = Path(os.environ.get("GREGIO_PASTA", Path.home() / NOME_APP))
PROJETOS = PASTA / "Projetos"
MUSICAS = PASTA / "Músicas"
PRONTOS = PASTA / "Vídeos prontos"
for p in (DADOS, PROJETOS, MUSICAS, PRONTOS):
    p.mkdir(parents=True, exist_ok=True)
gdrive.configurar(DADOS, AQUI / "credentials.json")

ESTILOS = ["automatico", "animada", "emocionante", "inspiradora", "leve", "agro", "corporativa"]
for e in ESTILOS[1:]:
    (MUSICAS / e).mkdir(exist_ok=True)
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".mts", ".3gp", ".webm"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}

AJUSTES = {
    "audio.volume_musica_db": {"padrao": -16, "min": -30, "max": -6, "passo": 2, "nome": "Volume da música (dB)"},
    "selecao.duracao_local_seg": {"padrao": 3.0, "min": 1.5, "max": 6.0, "passo": 0.5, "nome": "Tempo de cada imagem sem fala (s)"},
    "cobertura.locais_na_montagem": {"padrao": 5, "min": 1, "max": 10, "passo": 1, "nome": "Imagens em cada montagem"},
    "selecao.pausa_max_seg": {"padrao": 0.6, "min": 0.2, "max": 1.5, "passo": 0.1, "nome": "Pausa máxima mantida na fala (s)"},
    "selecao.margem_depois_seg": {"padrao": 0.4, "min": 0.1, "max": 1.2, "passo": 0.1, "nome": "Respiro depois de cada frase (s)"},
    "selecao.duracao_min_final_seg": {"padrao": 30, "min": 10, "max": 90, "passo": 5, "nome": "Duração mínima do vídeo (s)"},
    "selecao.duracao_max_final_seg": {"padrao": 120, "min": 30, "max": 180, "passo": 10, "nome": "Duração máxima do vídeo (s)"},
    "transicoes.duracao_desfoque_seg": {"padrao": 0.35, "min": 0.1, "max": 1.0, "passo": 0.05, "nome": "Duração da transição (s)"},
}

MOTIVOS = {
    "musica_nao_combinou": {"texto": "A música não combinou", "musica_evitar": True},
    "musica_alta": {"texto": "Música muito alta", "ajuste": ("audio.volume_musica_db", -1)},
    "musica_baixa": {"texto": "Música muito baixa", "ajuste": ("audio.volume_musica_db", +1)},
    "cortes_rapidos": {"texto": "Imagens passando rápido demais", "ajuste": ("selecao.duracao_local_seg", +1)},
    "cortes_lentos": {"texto": "Imagens demorando demais", "ajuste": ("selecao.duracao_local_seg", -1)},
    "imagens_demais": {"texto": "Imagens sem fala demais", "ajuste": ("cobertura.locais_na_montagem", -1)},
    "imagens_de_menos": {"texto": "Imagens sem fala de menos", "ajuste": ("cobertura.locais_na_montagem", +1)},
    "cortou_fala": {"texto": "Cortou fala no meio / ficou corrido", "ajuste": ("selecao.margem_depois_seg", +1)},
    "pausas_demais": {"texto": "Ficaram pausas e silêncios", "ajuste": ("selecao.pausa_max_seg", -1)},
    "muito_longo": {"texto": "Vídeo longo demais", "ajuste": ("selecao.duracao_max_final_seg", -1)},
    "muito_curto": {"texto": "Vídeo curto demais", "ajuste": ("selecao.duracao_min_final_seg", +1)},
    "transicao_lenta": {"texto": "Transição demorada", "ajuste": ("transicoes.duracao_desfoque_seg", -1)},
    "capa": {"texto": "Capa ruim (foto ou título)"},
    "ordem": {"texto": "Ordem das cenas"},
}

TRAVA = threading.Lock()
FILA = []
EVENTO = threading.Event()
ATUAL = {"id": None}


def agora():
    return datetime.datetime.now().strftime("%d/%m/%Y %H:%M")


def ler(f, padrao):
    try:
        return json.loads(Path(f).read_text(encoding="utf-8"))
    except Exception:
        return copy.deepcopy(padrao)


def gravar(f, dados):
    f = Path(f)
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, f)


def abrir_no_sistema(caminho, revelar=False):
    caminho = str(caminho)
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R", caminho] if revelar else ["open", caminho])
        elif os.name == "nt":
            if revelar:
                subprocess.Popen(["explorer", "/select,", caminho])
            else:
                os.startfile(caminho)  # noqa
        else:
            subprocess.Popen(["xdg-open", caminho if not revelar else str(Path(caminho).parent)])
    except Exception:
        traceback.print_exc()


# ---------------- CONTA E DRIVE ----------------

CONTA = {"email": "", "erro_login": None, "pasta_id": None}


def logado():
    try:
        gdrive.token_acesso()
        return True
    except Exception:
        return False


def google_cfg():
    return ler(AQUI / "google.json", {})


def pasta_drive():
    """Pasta 'Gregio AutoEdit' no Drive da pessoa, dentro do lugar que ela escolheu
    (ou na raiz do Drive, se ela ainda não escolheu)."""
    cfg = ler(DADOS / "conta.json", {})
    if cfg.get("pasta_id"):
        return cfg["pasta_id"]
    pid = gdrive.achar_ou_criar(cfg.get("destino_id"), NOME_APP)
    cfg["pasta_id"] = pid
    cfg.setdefault("destino_nome", "Meu Drive")
    gravar(DADOS / "conta.json", cfg)
    return pid


def escolher_destino(destino_id, destino_nome):
    cfg = ler(DADOS / "conta.json", {})
    cfg["destino_id"] = destino_id or None
    cfg["destino_nome"] = destino_nome or "Meu Drive"
    cfg.pop("pasta_id", None)
    gravar(DADOS / "conta.json", cfg)
    pid = pasta_drive()
    empurrar_aprendizado(aprendizado())
    return pid


def depois_do_login():
    try:
        conta = ler(DADOS / "conta.json", {})
        novo = gdrive.email()
        if novo != conta.get("email"):
            conta = {"email": novo}
        gravar(DADOS / "conta.json", conta)
        if conta.get("pasta_id"):
            puxar_aprendizado()
    except Exception:
        traceback.print_exc()


# ---------------- APRENDIZADO ----------------

AP_VAZIO = {"ajustes": {}, "historico": [], "musicas_evitar": [], "musicas_boas": []}
AP_ARQ = DADOS / "aprendizado.json"
AP_DRIVE = "_gregio_aprendizado.json"


def aprendizado():
    ap = ler(AP_ARQ, AP_VAZIO)
    for k, v in AP_VAZIO.items():
        ap.setdefault(k, copy.deepcopy(v))
    return ap


def salvar_aprendizado(ap):
    ap["atualizado"] = time.time()
    gravar(AP_ARQ, ap)
    threading.Thread(target=empurrar_aprendizado, args=(ap,), daemon=True).start()


def empurrar_aprendizado(ap):
    try:
        pid = pasta_drive()
        fid, _ = gdrive.ler_json_drive(pid, AP_DRIVE)
        gdrive.gravar_json_drive(pid, AP_DRIVE, ap, fid)
    except Exception as e:
        print("aprendizado no Drive:", e)


def puxar_aprendizado():
    """Ao entrar num computador novo, traz o padrão salvo no Drive."""
    try:
        _, nuvem = gdrive.ler_json_drive(pasta_drive(), AP_DRIVE)
        local = aprendizado()
        if nuvem and nuvem.get("atualizado", 0) > local.get("atualizado", 0):
            gravar(AP_ARQ, nuvem)
    except Exception as e:
        print("aprendizado do Drive:", e)


def _get(d, chave):
    a, b = chave.split(".")
    return d.get(a, {}).get(b)


def _set(d, chave, valor):
    a, b = chave.split(".")
    d.setdefault(a, {})[b] = valor


def valores_atuais(ap):
    out = {}
    for k, meta in AJUSTES.items():
        v = _get(ap["ajustes"], k)
        out[k] = dict(meta, valor=meta["padrao"] if v is None else v)
    return out


def registrar(ap, projeto, aprovado, motivos, nota, mudancas):
    ap["historico"].insert(0, {"data": agora(), "projeto": projeto, "aprovado": aprovado,
                               "motivos": [MOTIVOS.get(m, {}).get("texto", m) for m in motivos],
                               "nota": nota, "mudancas": mudancas})
    ap["historico"] = ap["historico"][:200]


def aprender(projeto, aprovado, motivos=(), nota=""):
    ap = aprendizado()
    atuais = valores_atuais(ap)
    mudancas = []
    musica = projeto.get("musica_nome")
    if aprovado:
        if musica and musica not in ap["musicas_boas"]:
            ap["musicas_boas"].append(musica)
    for m in motivos:
        regra = MOTIVOS.get(m, {})
        if regra.get("musica_evitar") and musica:
            if musica not in ap["musicas_evitar"]:
                ap["musicas_evitar"].append(musica)
            if musica in ap["musicas_boas"]:
                ap["musicas_boas"].remove(musica)
            mudancas.append("Não usar mais a música '%s'" % musica)
        if "ajuste" in regra:
            chave, sentido = regra["ajuste"]
            meta = atuais[chave]
            novo = round(min(meta["max"], max(meta["min"], meta["valor"] + sentido * meta["passo"])), 2)
            if novo != meta["valor"]:
                _set(ap["ajustes"], chave, novo)
                mudancas.append("%s: %s → %s" % (meta["nome"], meta["valor"], novo))
                atuais[chave]["valor"] = novo
    registrar(ap, projeto.get("nome", ""), aprovado, list(motivos), nota, mudancas)
    salvar_aprendizado(ap)
    return mudancas


# ---------------- MÚSICAS ----------------

def lista_musicas():
    out = []
    for estilo in ESTILOS[1:]:
        for f in sorted((MUSICAS / estilo).glob("*")):
            if f.suffix.lower() in AUDIO_EXT:
                out.append({"estilo": estilo, "nome": f.name, "caminho": str(f)})
    return out


def escolher_musica(estilo, evitar_tambem=()):
    ap = aprendizado()
    todas = lista_musicas()
    pool = [m for m in todas if m["estilo"] == estilo] if estilo and estilo != "automatico" else todas
    if not pool:
        pool = todas
    proibidas = set(ap["musicas_evitar"]) | set(evitar_tambem)
    boas = set(ap["musicas_boas"])
    candidatas = [m for m in pool if m["nome"] not in proibidas] or [m for m in pool if m["nome"] not in evitar_tambem] or pool
    if not candidatas:
        return None
    pesos = [3 if m["nome"] in boas else 1 for m in candidatas]
    return Path(random.choices(candidatas, weights=pesos)[0]["caminho"])


# ---------------- PROJETOS ----------------

def dir_projeto(pid):
    return PROJETOS / re.sub(r"[^a-z0-9]", "", pid)


def ler_projeto(pid):
    return ler(dir_projeto(pid) / "projeto.json", None)


def salvar_projeto(p):
    with TRAVA:
        gravar(dir_projeto(p["id"]) / "projeto.json", p)


def atualizar(pid, **campos):
    with TRAVA:
        f = dir_projeto(pid) / "projeto.json"
        p = ler(f, None)
        if p is None:
            return None
        p.update(campos)
        gravar(f, p)
        return p


def todos_projetos():
    lista = []
    for d in PROJETOS.iterdir():
        p = ler(d / "projeto.json", None) if d.is_dir() else None
        if p:
            lista.append(p)
    return sorted(lista, key=lambda p: p.get("criado_ts", 0), reverse=True)


def nome_arquivo(nome):
    return re.sub(r'[\\/:*?"<>|]+', "-", nome).strip() or "video"


def enfileirar(pid):
    with TRAVA:
        if pid not in FILA:
            FILA.append(pid)
    EVENTO.set()


# ---------------- EDIÇÃO (fila, um vídeo por vez) ----------------

def config_edicao():
    import editor
    ap = aprendizado()
    return editor.carregar_config(ap["ajustes"])


def editar_projeto(pid):
    import editor
    p = ler_projeto(pid)
    if not p:
        return
    pasta = dir_projeto(pid)
    takes = pasta / "takes"
    trabalho = pasta / "trabalho"
    for velho in ("blocos",):
        shutil.rmtree(trabalho / velho, ignore_errors=True)
    videos, musica_projeto, encerramento_projeto = editor.separar_arquivos(takes)
    if not videos:
        atualizar(pid, estado="erro", erro="Nenhum vídeo neste projeto.")
        return
    musica = musica_projeto
    if not musica and p.get("estilo") != "nenhuma":
        evitar = p.get("musicas_recusadas", [])
        musica = escolher_musica(p.get("estilo"), evitar)
    encerramento = encerramento_projeto
    if not encerramento:
        enc = next((f for f in DADOS.glob("encerramento.*")), None)
        encerramento = enc
    logo = DADOS / "logo.png"
    atualizar(pid, estado="editando", etapa="Começando", pct=0, inicio_ts=time.time(), erro=None,
              musica_nome=musica.name if musica else None)

    def avisar(d):
        atualizar(pid, etapa=d["etapa"], pct=d["pct"], atual=d["atual"], total=d["total"])

    try:
        cfg = config_edicao()
        if p.get("modo") == "entrevista":
            cfg["entrevista"] = True
        nome = nome_arquivo(p["nome"])
        r = editor.editar(nome, videos, trabalho, cfg, musica, encerramento, avisar,
                          logo=logo if logo.exists() else None, titulo=p.get("titulo") or None)
        arquivos = {k: str(v) for k, v in r.items() if k in ("video", "capa", "legenda", "pacote") and v}
        atualizar(pid, estado="previa", etapa="Pronto para você ver", pct=100, arquivos=arquivos,
                  duracao=round(r["duracao"], 1), segundos=r["segundos"], fim_ts=time.time())
    except Exception as e:
        traceback.print_exc()
        atualizar(pid, estado="erro", erro=str(e)[:400])


def trabalhador():
    while True:
        EVENTO.wait(5)
        EVENTO.clear()
        while True:
            with TRAVA:
                pid = FILA.pop(0) if FILA else None
            if not pid:
                break
            ATUAL["id"] = pid
            try:
                editar_projeto(pid)
            finally:
                ATUAL["id"] = None


def retomar_fila():
    """Ao abrir o app, volta para a fila o que estava esperando ou foi interrompido."""
    for p in reversed(todos_projetos()):
        if p.get("estado") in ("na_fila", "editando"):
            atualizar(p["id"], estado="na_fila", etapa="Na fila")
            enfileirar(p["id"])
        elif p.get("estado") == "enviando":
            threading.Thread(target=aprovar_e_enviar, args=(p["id"],), daemon=True).start()


# ---------------- APROVAR: guarda e manda para o Drive ----------------

def aprovar_e_enviar(pid):
    p = ler_projeto(pid)
    arqs = p.get("arquivos", {})
    nome = nome_arquivo(p["nome"])
    destino = PRONTOS / nome
    destino.mkdir(parents=True, exist_ok=True)
    locais = {}
    for k, caminho in arqs.items():
        if caminho and Path(caminho).exists():
            alvo = destino / Path(caminho).name
            if not alvo.exists() or alvo.stat().st_size != Path(caminho).stat().st_size:
                shutil.copy2(caminho, alvo)
            locais[k] = str(alvo)
    atualizar(pid, estado="enviando", etapa="Enviando para o seu Google Drive", pct=0, prontos=locais,
              pasta_pronta=str(destino))
    try:
        raiz = pasta_drive()
        prontos = gdrive.achar_ou_criar(raiz, "Vídeos prontos")
        pasta = gdrive.achar_ou_criar(prontos, nome)
        lista = [Path(v) for v in locais.values()]
        total = sum(f.stat().st_size for f in lista) or 1
        enviado = [0]
        ja = {f["name"] for f in gdrive.listar(pasta)}
        for f in lista:
            if f.name in ja:
                enviado[0] += f.stat().st_size
                continue
            base = enviado[0]
            gdrive.enviar_grande(str(f), f.name, pasta,
                                 lambda n, base=base: atualizar(pid, pct=round((base + n) * 100 / total, 1)))
            enviado[0] += f.stat().st_size
        atualizar(pid, estado="pronto", etapa="No seu Drive", pct=100, drive_link=gdrive.link_pasta(pasta),
                  aprovado_em=agora())
    except Exception as e:
        traceback.print_exc()
        atualizar(pid, estado="pronto", etapa="Salvo no computador", pct=100,
                  erro_drive="Não consegui enviar ao Drive: %s" % str(e)[:200])


# ---------------- SERVIDOR HTTP ----------------

TIPOS = {".mp4": "video/mp4", ".mov": "video/quicktime", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
         ".png": "image/png", ".srt": "text/plain; charset=utf-8", ".zip": "application/zip",
         ".html": "text/html; charset=utf-8", ".svg": "image/svg+xml", ".mp3": "audio/mpeg",
         ".m4a": "audio/mp4", ".wav": "audio/wav"}


def visao(p):
    """O que a tela precisa de cada projeto."""
    v = {k: p.get(k) for k in ("id", "nome", "titulo", "estilo", "estado", "etapa", "pct", "atual", "total",
                                 "erro", "erro_drive", "duracao", "segundos", "drive_link", "criado",
                                 "aprovado_em", "musica_nome", "n_takes", "modo")}
    v["tem"] = {k: True for k in (p.get("arquivos") or {})}
    if p.get("estado") == "editando" and p.get("inicio_ts"):
        dec = time.time() - p["inicio_ts"]
        v["decorrido"] = round(dec)
        pct = p.get("pct") or 0
        v["restante"] = round(dec * (100 - pct) / pct) if pct >= 8 else None
    if p.get("estado") == "na_fila":
        with TRAVA:
            v["posicao"] = FILA.index(p["id"]) + 1 if p["id"] in FILA else None
    return v


class App(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _json(self, dados, status=200):
        corpo = json.dumps(dados, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(corpo)

    def _corpo_json(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        return json.loads(self.rfile.read(n).decode("utf-8") or "{}") if n else {}

    def _arquivo(self, f, tipo=None, baixar=False):
        f = Path(f)
        if not f.exists():
            return self._json({"erro": "arquivo não encontrado"}, 404)
        tipo = tipo or TIPOS.get(f.suffix.lower(), "application/octet-stream")
        tam = f.stat().st_size
        ini, fim = 0, tam - 1
        faixa = self.headers.get("Range")
        if faixa:
            m = re.match(r"bytes=(\d*)-(\d*)", faixa)
            if m:
                if m.group(1):
                    ini = int(m.group(1))
                    fim = int(m.group(2)) if m.group(2) else tam - 1
                else:
                    ini = max(0, tam - int(m.group(2)))
            fim = min(fim, tam - 1)
            self.send_response(206)
            self.send_header("Content-Range", "bytes %d-%d/%d" % (ini, fim, tam))
        else:
            self.send_response(200)
        self.send_header("Content-Type", tipo)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(fim - ini + 1))
        if baixar:
            self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + urllib.parse.quote(f.name))
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(f, "rb") as h:
            h.seek(ini)
            falta = fim - ini + 1
            while falta > 0:
                bloco = h.read(min(1024 * 1024, falta))
                if not bloco:
                    break
                try:
                    self.wfile.write(bloco)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    return
                falta -= len(bloco)

    def _receber(self, alvo):
        tam = int(self.headers.get("Content-Length", 0) or 0)
        alvo.parent.mkdir(parents=True, exist_ok=True)
        tmp = alvo.with_name(alvo.name + ".part")
        with open(tmp, "wb") as f:
            falta = tam
            while falta > 0:
                bloco = self.rfile.read(min(4 * 1024 * 1024, falta))
                if not bloco:
                    break
                f.write(bloco)
                falta -= len(bloco)
        os.replace(tmp, alvo)
        return tam

    def _autorizado(self, url, q):
        """Bloqueia pedidos que não vêm da janela do app:
        - o endereço tem que ser exatamente 127.0.0.1:PORTA (contra 'DNS rebinding');
        - pedidos vindos de outro site (cabeçalho Origin diferente) são recusados;
        - toda chamada /api precisa da chave desta sessão."""
        host = (self.headers.get("Host") or "").lower()
        if host not in ("127.0.0.1:%d" % PORTA, "localhost:%d" % PORTA):
            return False
        origem = self.headers.get("Origin")
        if origem and origem not in ("http://127.0.0.1:%d" % PORTA, "http://localhost:%d" % PORTA):
            return False
        if url.path in LIVRES or url.path.startswith("/img/"):
            return True
        chave = self.headers.get("X-Gregio-Chave") or q.get("t") or ""
        return hmac.compare_digest(chave, CHAVE)

    def _negar(self):
        corpo = b'{"erro": "acesso negado"}'
        self.send_response(403)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        super().end_headers()

    def do_HEAD(self):
        return self.do_GET()

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(url.query))
        if not self._autorizado(url, q):
            return self._negar()
        try:
            if url.path in ("/", "/index.html"):
                return self._arquivo(AQUI / "index.html", "text/html; charset=utf-8")
            if url.path == "/picker":
                return self._arquivo(AQUI / "picker.html", "text/html; charset=utf-8")
            if url.path.startswith("/img/"):
                return self._arquivo(AQUI / "img" / Path(url.path).name)
            if url.path == "/api/estado":
                conta = ler(DADOS / "conta.json", {})
                ok = logado()
                return self._json({"logado": ok, "email": conta.get("email", "") if ok else "",
                                   "erro_login": CONTA["erro_login"], "versao": VERSAO,
                                   "estilos": ESTILOS, "motivos": {k: v["texto"] for k, v in MOTIVOS.items()},
                                   "pasta": str(PASTA), "n_musicas": len(lista_musicas()),
                                   "drive": gdrive.link_pasta(conta["pasta_id"]) if ok and conta.get("pasta_id") else None,
                                   "destino": conta.get("destino_nome") if conta.get("pasta_id") else None})
            if url.path == "/api/picker":
                g = google_cfg()
                return self._json({"token": gdrive.token_acesso(), "chave": g.get("api_key", ""),
                                   "app_id": g.get("app_id", "")})
            if url.path == "/api/projetos":
                return self._json({"projetos": [visao(p) for p in todos_projetos()]})
            if url.path == "/api/arquivo":
                p = ler_projeto(q["id"])
                caminho = ((p or {}).get("prontos") or {}).get(q["k"]) or ((p or {}).get("arquivos") or {}).get(q["k"])
                return self._arquivo(caminho, baixar=q.get("baixar") == "1") if caminho else self._json({}, 404)
            if url.path == "/api/aprendizado":
                ap = aprendizado()
                return self._json({"ajustes": valores_atuais(ap), "historico": ap["historico"][:60],
                                   "evitar": ap["musicas_evitar"], "boas": ap["musicas_boas"],
                                   "logo": (DADOS / "logo.png").exists(),
                                   "encerramento": next((f.name for f in DADOS.glob("encerramento.*")), None)})
            if url.path == "/api/musicas":
                return self._json({"musicas": lista_musicas()})
            if url.path == "/api/ouvir":
                f = MUSICAS / Path(q["estilo"]).name / Path(q["nome"]).name
                return self._arquivo(f)
            if url.path == "/api/logo":
                return self._arquivo(DADOS / "logo.png")
            return self._json({"erro": "não encontrado"}, 404)
        except Exception as e:
            traceback.print_exc()
            return self._json({"erro": str(e)}, 500)

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(url.query))
        if not self._autorizado(url, q) or url.path in LIVRES:
            return self._negar()
        try:
            if url.path == "/api/login":
                def tarefa():
                    try:
                        CONTA["erro_login"] = None
                        gdrive.fazer_login()
                        depois_do_login()
                    except Exception as e:
                        traceback.print_exc()
                        CONTA["erro_login"] = str(e)
                threading.Thread(target=tarefa, daemon=True).start()
                return self._json({"ok": True})

            if url.path == "/api/abrir_picker":
                webbrowser.open("http://127.0.0.1:%d/picker#%s" % (PORTA, CHAVE))
                return self._json({"ok": True})

            if url.path == "/api/destino":
                d = self._corpo_json()
                pid = escolher_destino(d.get("id"), d.get("nome"))
                return self._json({"ok": True, "link": gdrive.link_pasta(pid)})

            if url.path == "/api/sair":
                gdrive.sair()
                conta = ler(DADOS / "conta.json", {})
                gravar(DADOS / "conta.json", {})
                return self._json({"ok": True})

            if url.path == "/api/novo":
                d = self._corpo_json()
                nome = (d.get("nome") or "").strip()
                if not nome:
                    return self._json({"erro": "Dê um nome ao vídeo."}, 400)
                pid = uuid.uuid4().hex[:12]
                (dir_projeto(pid) / "takes").mkdir(parents=True)
                p = {"id": pid, "nome": nome, "titulo": (d.get("titulo") or "").strip(),
                     "estilo": d.get("estilo") or "automatico",
                     "modo": "entrevista" if d.get("modo") == "entrevista" else "normal", "estado": "recebendo", "etapa": "Recebendo os vídeos",
                     "pct": 0, "criado": agora(), "criado_ts": time.time(), "n_takes": 0}
                salvar_projeto(p)
                return self._json({"id": pid})

            if url.path == "/api/arquivo":
                pid = q["id"]
                nome = Path(urllib.parse.unquote(q["nome"])).name
                self._receber(dir_projeto(pid) / "takes" / nome)
                return self._json({"ok": True})

            if url.path == "/api/concluir":
                d = self._corpo_json()
                pid = d["id"]
                n = len([f for f in (dir_projeto(pid) / "takes").iterdir() if f.suffix.lower() in VIDEO_EXT])
                if not n:
                    atualizar(pid, estado="erro", erro="Nenhum vídeo recebido.")
                    return self._json({"erro": "Nenhum vídeo recebido."}, 400)
                atualizar(pid, estado="na_fila", etapa="Na fila", n_takes=n)
                enfileirar(pid)
                return self._json({"ok": True})

            if url.path == "/api/acao":
                d = self._corpo_json()
                acao, pid = d["acao"], d["id"]
                p = ler_projeto(pid)
                if not p:
                    return self._json({"erro": "Projeto não encontrado."}, 404)
                mudancas = []
                if acao == "aprovar":
                    mudancas = aprender(p, True)
                    threading.Thread(target=aprovar_e_enviar, args=(pid,), daemon=True).start()
                elif acao in ("reprovar", "refazer"):
                    motivos = d.get("motivos", [])
                    mudancas = aprender(p, False, motivos, d.get("nota", ""))
                    recusadas = p.get("musicas_recusadas", [])
                    if "musica_nao_combinou" in motivos and p.get("musica_nome"):
                        recusadas.append(p["musica_nome"])
                    atualizar(pid, estado="na_fila", etapa="Na fila para refazer", pct=0,
                              musicas_recusadas=recusadas, erro=None)
                    enfileirar(pid)
                elif acao == "tentar_de_novo":
                    atualizar(pid, estado="na_fila", etapa="Na fila", pct=0, erro=None)
                    enfileirar(pid)
                elif acao == "reenviar_drive":
                    threading.Thread(target=aprovar_e_enviar, args=(pid,), daemon=True).start()
                elif acao == "excluir":
                    if ATUAL["id"] == pid:
                        return self._json({"erro": "Este vídeo está sendo editado agora. Espere terminar."}, 400)
                    with TRAVA:
                        if pid in FILA:
                            FILA.remove(pid)
                    shutil.rmtree(dir_projeto(pid), ignore_errors=True)
                return self._json({"ok": True, "mudancas": mudancas})

            if url.path == "/api/abrir":
                d = self._corpo_json()
                if d.get("id"):
                    p = ler_projeto(d["id"]) or {}
                    alvo = (p.get("prontos") or {}).get(d.get("k")) or (p.get("arquivos") or {}).get(d.get("k"))
                    if alvo:
                        abrir_no_sistema(alvo, revelar=True)
                    elif p.get("pasta_pronta"):
                        abrir_no_sistema(p["pasta_pronta"])
                elif d.get("onde") == "musicas":
                    abrir_no_sistema(MUSICAS)
                else:
                    abrir_no_sistema(PRONTOS)
                return self._json({"ok": True})

            if url.path == "/api/ajuste":
                d = self._corpo_json()
                ap = aprendizado()
                if d.get("restaurar"):
                    ap["ajustes"] = {}
                    registrar(ap, "", True, [], "", ["Todos os ajustes voltaram ao padrão"])
                else:
                    chave = d["chave"]
                    meta = AJUSTES[chave]
                    valor = round(min(meta["max"], max(meta["min"], float(d["valor"]))), 2)
                    antes = valores_atuais(ap)[chave]["valor"]
                    _set(ap["ajustes"], chave, valor)
                    registrar(ap, "", True, [], "Ajuste manual", ["%s: %s → %s" % (meta["nome"], antes, valor)])
                salvar_aprendizado(ap)
                return self._json({"ok": True})

            if url.path == "/api/musica":
                d = self._corpo_json()
                ap = aprendizado()
                if d.get("liberar") in ap["musicas_evitar"]:
                    ap["musicas_evitar"].remove(d["liberar"])
                    salvar_aprendizado(ap)
                if d.get("apagar"):
                    f = MUSICAS / Path(d["estilo"]).name / Path(d["apagar"]).name
                    if f.exists():
                        f.unlink()
                return self._json({"ok": True})

            if url.path == "/api/enviar_musica":
                estilo = q.get("estilo") if q.get("estilo") in ESTILOS[1:] else "animada"
                nome = Path(urllib.parse.unquote(q["nome"])).name
                if Path(nome).suffix.lower() not in AUDIO_EXT:
                    return self._json({"erro": "Formato de música não aceito."}, 400)
                self._receber(MUSICAS / estilo / nome)
                return self._json({"ok": True})

            if url.path == "/api/marca":
                tipo = q.get("tipo")
                if q.get("apagar"):
                    for f in (DADOS.glob("encerramento.*") if tipo == "encerramento" else [DADOS / "logo.png"]):
                        f.unlink()
                    return self._json({"ok": True})
                nome = Path(urllib.parse.unquote(q.get("nome", "x.png"))).name
                if tipo == "logo":
                    tmp = DADOS / ("_logo" + Path(nome).suffix.lower())
                    self._receber(tmp)
                    from PIL import Image
                    Image.open(tmp).convert("RGBA").save(DADOS / "logo.png")
                    tmp.unlink()
                else:
                    for f in DADOS.glob("encerramento.*"):
                        f.unlink()
                    self._receber(DADOS / ("encerramento" + Path(nome).suffix.lower()))
                return self._json({"ok": True})

            return self._json({"erro": "não encontrado"}, 404)
        except PermissionError:
            return self._json({"erro": "login"}, 401)
        except Exception as e:
            traceback.print_exc()
            return self._json({"erro": str(e)}, 500)


def main(abrir=True):
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", PORTA), App)
    except OSError:
        print("O app já está aberto em outra janela.")
        return
    srv.daemon_threads = True
    threading.Thread(target=trabalhador, daemon=True).start()
    retomar_fila()
    print("%s rodando em http://127.0.0.1:%d/" % (NOME_APP, PORTA), flush=True)
    if abrir:
        threading.Timer(0.8, lambda: webbrowser.open("http://127.0.0.1:%d/#%s" % (PORTA, CHAVE))).start()
    srv.serve_forever()


if __name__ == "__main__":
    main("--sem-navegador" not in sys.argv)
