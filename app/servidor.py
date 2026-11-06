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
import zipfile
import platform
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parent
sys.path.insert(0, str(AQUI))
sys.path.insert(0, str(RAIZ / "motor"))

import gdrive  # noqa: E402

NOME_APP = "Gregio AutoEdit"
VERSAO = os.environ.get("GREGIO_VERSAO") or "2.0"
AJUDA_EMAIL = "eduardogregio38@gmail.com"   # (não usado mais no botão; fica de reserva)
AJUDA_WHATSAPP = "5545988280533"             # contato do botão "Preciso de ajuda"
AJUDA_WHATSAPP_TXT = "+55 45 98828-0533"
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


# ---------------- PREFERÊNCIAS (tela de ajustes avançados) E RECEITAS ----------------

OP_CAPA_ESTILO = [("caixa", "Caixa de cor (estilo Canva)"), ("classico", "Clássico (sombra)"), ("faixa", "Faixa escura atrás do título"), ("cor", "Letras coloridas")]
PREFS = [
    {"k": "selecao.ritmo_fala", "tipo": "escolha", "grupo": "Cortes", "nome": "Ritmo da fala",
     "op": [("natural", "Natural"), ("dinamico", "Dinâmico"), ("seco", "Bem seco")]},
    {"k": "selecao.ordem_meio", "tipo": "escolha", "grupo": "Cortes", "nome": "Ordem das falas do meio",
     "op": [("sortear", "Sortear a cada edição"), ("gravacao", "Seguir a ordem da gravação")]},
    {"k": "selecao.gancho", "tipo": "liga", "grupo": "Cortes", "nome": "Abrir com a frase mais forte (gancho nos primeiros segundos)"},
    {"k": "selecao.tirar_recomecos", "tipo": "liga", "grupo": "Cortes", "nome": "Tirar erro e recomeço de frase dentro do mesmo take"},
    {"k": "selecao.tirar_claquete", "tipo": "liga", "grupo": "Cortes", "nome": "Tirar \"gravando\", \"valendo\" e \"corta\" do começo e do fim"},
    {"k": "selecao.cortes_precisos", "tipo": "liga", "grupo": "Cortes", "nome": "Cortar no ponto mais silencioso (sem comer o fim das palavras)"},
    {"k": "audio.nivel_ruido", "tipo": "escolha", "grupo": "Áudio", "nome": "Redução de ruído",
     "op": [("auto", "Automático (pelo ruído de cada take)"), ("suave", "Suave"), ("medio", "Média"), ("forte", "Forte")]},
    {"k": "audio.filtro_vento", "tipo": "liga", "grupo": "Áudio", "nome": "Filtro de vento em gravações ao ar livre"},
    {"k": "audio.igualar_volume", "tipo": "liga", "grupo": "Áudio", "nome": "Igualar o volume entre os takes"},
    {"k": "audio.musica_alta_sem_fala", "tipo": "liga", "grupo": "Áudio", "nome": "Música mais alta nas partes só com imagem"},
    {"k": "audio.efeitos_transicao", "tipo": "liga", "grupo": "Áudio", "nome": "Efeito sonoro nas transições (usa os sons da pasta Efeitos)"},
    {"k": "video.melhorar_escuro", "tipo": "liga", "grupo": "Imagem", "nome": "Clarear takes escuros e reduzir granulação"},
    {"k": "video.nitidez_final", "tipo": "liga", "grupo": "Imagem", "nome": "Nitidez leve depois de reduzir 4K para 1080p"},
    {"k": "video.estabilizar", "tipo": "liga", "grupo": "Imagem", "nome": "Estabilização leve nas imagens do local (deixa a edição mais lenta)"},
    {"k": "legendas.queimar_no_video", "tipo": "liga", "grupo": "Legenda", "nome": "Legenda dentro do vídeo"},
    {"k": "legendas.estilo", "tipo": "escolha", "grupo": "Legenda", "nome": "Estilo da legenda",
     "op": [("destaque", "Palavra por palavra, com destaque"), ("simples", "Simples")]},
    {"k": "legendas.posicao", "tipo": "escolha", "grupo": "Legenda", "nome": "Posição da legenda",
     "op": [("baixo", "Embaixo"), ("meio", "No meio")]},
    {"k": "legendas.cor_destaque", "tipo": "cor", "grupo": "Legenda", "nome": "Cor da palavra em destaque"},
    {"k": "legendas.fonte", "tipo": "escolha", "grupo": "Legenda", "nome": "Fonte da legenda", "fonte": True, "op": []},
    {"k": "legendas.tamanho_ass", "tipo": "escolha", "grupo": "Legenda", "nome": "Tamanho da legenda",
     "op": [("pequena", "Pequena"), ("media", "Média"), ("grande", "Grande")]},
    {"k": "legendas.cor_texto", "tipo": "cor", "grupo": "Legenda", "nome": "Cor do texto da legenda"},
    {"k": "legendas.maiusculas", "tipo": "liga", "grupo": "Legenda", "nome": "Legenda em letras maiúsculas"},
    {"k": "capa.tres_opcoes", "tipo": "liga", "grupo": "Capa", "nome": "Gerar 3 opções de capa para escolher"},
    {"k": "capa.estilo", "tipo": "escolha", "grupo": "Capa", "nome": "Estilo do título", "op": OP_CAPA_ESTILO},
    {"k": "capa.posicao", "tipo": "escolha", "grupo": "Capa", "nome": "Posição do título",
     "op": [("baixo", "Embaixo"), ("meio", "No meio"), ("cima", "Em cima")]},
    {"k": "capa.fonte", "tipo": "escolha", "grupo": "Capa", "nome": "Fonte do título", "fonte": True, "op": []},
    {"k": "capa.cor", "tipo": "cor", "grupo": "Capa", "nome": "Cor do título"},
    {"k": "capa.margem_segura", "tipo": "liga", "grupo": "Capa", "nome": "Manter o título na área segura (grade do perfil e botões do Reels)"},
    {"k": "encerramento.texto", "tipo": "texto", "grupo": "Tela final", "nome": "Texto sobre a tela final (ex.: Siga, Matrículas abertas)"},
    {"k": "aceleracao", "tipo": "escolha", "grupo": "Sistema", "nome": "Renderização",
     "op": [("auto", "Usar o chip de vídeo do computador, se tiver"), ("nenhuma", "Só o processador")]},
    {"k": "limpar_apos_aprovar", "tipo": "liga", "grupo": "Sistema", "nome": "Apagar arquivos de trabalho depois de aprovar (libera espaço)"},
    {"k": "limpar_takes_apos_aprovar", "tipo": "liga", "grupo": "Sistema", "nome": "Apagar também os takes copiados para o app (não dá mais para refazer)"},
    {"k": "vocabulario", "tipo": "lista", "grupo": "Transcrição", "nome": "Vocabulário: nomes, marcas e cursos que o app deve acertar (um por linha)"},
]
PADROES_EXTRA = {"limpar_apos_aprovar": True, "limpar_takes_apos_aprovar": False, "vocabulario": []}
RECEITAS = {
    "padrao": {"nome": "Padrão", "cfg": {}},
    "bastidores": {"nome": "Bastidores", "cfg": {"selecao": {"ritmo_fala": "dinamico", "gancho": True},
                                                 "audio": {"volume_musica_db": -14}}},
    "entrevista": {"nome": "Entrevista", "cfg": {"selecao": {"ritmo_fala": "natural"}, "audio": {"nivel_ruido": "medio"},
                                                  "legendas": {"queimar_no_video": True}}},
    "depoimento": {"nome": "Depoimento", "cfg": {"selecao": {"ritmo_fala": "natural", "gancho": False},
                                                 "audio": {"volume_musica_db": -22}, "capa": {"estilo": "faixa"}}},
}


def _aninhar(chave, valor, d):
    partes = chave.split(".")
    for p in partes[:-1]:
        d = d.setdefault(p, {})
    d[partes[-1]] = valor


def opcoes_fonte():
    import capa
    return [(a, n) for a, n in capa.listar_fontes()]


def prefs_atuais(ap=None):
    import editor
    base = editor.carregar_config()
    ap = ap or aprendizado()
    out = []
    for m in PREFS:
        v = ap.get("prefs", {}).get(m["k"])
        if v is None:
            if "." in m["k"]:
                a, b = m["k"].split(".")
                v = base.get(a, {}).get(b)
            else:
                v = PADROES_EXTRA.get(m["k"], base.get(m["k"]))
        ops = opcoes_fonte() if m.get("fonte") else m.get("op", [])
        out.append(dict(m, valor=v, op=[list(o) for o in ops]))
    return out


TRAVA = threading.Lock()
FILA = []
EVENTO = threading.Event()
ATUAL = {"id": None}
LOG = DADOS / "app.log"


class _Tee:
    """Copia tudo que o app escreve na tela para um arquivo de registro (usado no relatório de erro)."""
    def __init__(self, orig):
        self.orig = orig

    def write(self, t):
        try:
            if self.orig:
                self.orig.write(t)
            with open(LOG, "a", encoding="utf-8", errors="replace") as f:
                f.write(t)
        except Exception:
            pass
        return len(t)

    def flush(self):
        try:
            if self.orig:
                self.orig.flush()
        except Exception:
            pass

    def __getattr__(self, k):
        return getattr(self.orig, k)


def _ligar_log():
    try:
        if LOG.exists() and LOG.stat().st_size > 2_000_000:
            dados = LOG.read_bytes()[-500_000:]
            LOG.write_bytes(dados)
    except Exception:
        pass
    sys.stdout = _Tee(sys.stdout)
    sys.stderr = _Tee(sys.stderr)


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
    texto = os.environ.get("GREGIO_GOOGLE_CFG")  # chave de API vem do ambiente (ou do arquivo local fora do git)
    if texto:
        try:
            return json.loads(texto)
        except ValueError:
            return {}
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

AP_VAZIO = {"ajustes": {}, "historico": [], "musicas_evitar": [], "musicas_boas": [], "prefs": {}}
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

META_MUS = DADOS / "musicas_meta.json"
ALVO_BPM = {"animada": 128, "emocionante": 100, "inspiradora": 92, "leve": 90, "agro": 100, "corporativa": 108}


def meta_musicas():
    return ler(META_MUS, {})


def salvar_meta_musica(estilo, nome, **campos):
    with TRAVA:
        m = meta_musicas()
        m.setdefault(estilo + "/" + nome, {}).update(campos)
        gravar(META_MUS, m)


def analisar_musica(estilo, nome):
    """Mede o volume (para igualar as faixas) e o andamento (BPM) de uma música recém-adicionada."""
    import re as _re
    f = MUSICAS / estilo / nome
    try:
        r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-t", "120", "-i", str(f), "-af",
                            "ebur128=framelog=quiet", "-f", "null", "-"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        m = _re.findall(r"I:\s*(-?\d+(?:\.\d+)?)\s*LUFS", r.stderr)
        campos = {}
        if m:
            campos["ganho_db"] = round(max(-12.0, min(12.0, -14.0 - float(m[-1]))), 1)
        import ritmo
        _, bpm = ritmo.batidas(f)
        campos["bpm"] = round(bpm) if bpm else 0
        salvar_meta_musica(estilo, nome, **campos)
    except Exception:
        traceback.print_exc()


def lista_musicas():
    out = []
    meta = meta_musicas()
    for estilo in ESTILOS[1:]:
        for f in sorted((MUSICAS / estilo).glob("*")):
            if f.suffix.lower() in AUDIO_EXT:
                mm = meta.get(estilo + "/" + f.name, {})
                out.append({"estilo": estilo, "nome": f.name, "caminho": str(f),
                            "letra": bool(mm.get("letra")), "bpm": mm.get("bpm", 0), "ganho_db": mm.get("ganho_db", 0.0)})
    return out


def ganho_da_musica(caminho):
    if not caminho:
        return 0.0
    p = Path(caminho)
    return float(meta_musicas().get(p.parent.name + "/" + p.name, {}).get("ganho_db", 0.0))


def escolher_musica(estilo, evitar_tambem=()):
    import math
    ap = aprendizado()
    todas = lista_musicas()
    pool = [m for m in todas if m["estilo"] == estilo] if estilo and estilo != "automatico" else todas
    if not pool:
        pool = todas
    proibidas = set(ap["musicas_evitar"]) | set(evitar_tambem)
    boas = set(ap["musicas_boas"])
    candidatas = [m for m in pool if m["nome"] not in proibidas] or [m for m in pool if m["nome"] not in evitar_tambem] or pool
    sem_letra = [m for m in candidatas if not m["letra"]]   # música cantada atrapalha a fala
    candidatas = sem_letra or candidatas
    if not candidatas:
        return None

    def peso(m):
        w = 3.0 if m["nome"] in boas else 1.0
        alvo = ALVO_BPM.get(m["estilo"])
        if alvo and m["bpm"]:   # prefere o andamento que combina com o estilo
            w *= 0.4 + math.exp(-0.5 * ((m["bpm"] - alvo) / 28.0) ** 2)
        return w
    return Path(random.choices(candidatas, weights=[peso(m) for m in candidatas])[0]["caminho"])


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


def limpar_cortes(bruto):
    """Cortes de começo e fim que a pessoa marcou: {nome do arquivo: {"ini": s, "fim": s}}.
    Aceita só números sãos; qualquer outra coisa é ignorada."""
    saida = {}
    if not isinstance(bruto, dict):
        return saida
    for nome, c in bruto.items():
        if not isinstance(c, dict):
            continue
        item = {}
        for k in ("ini", "fim"):
            v = c.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v < 86400:
                item[k] = round(float(v), 2)
        if item:
            saida[Path(str(nome)).name] = item
    return saida


def nome_arquivo(nome):
    return re.sub(r'[\\/:*?"<>|]+', "-", nome).strip() or "video"


def enfileirar(pid):
    with TRAVA:
        if pid not in FILA:
            FILA.append(pid)
    EVENTO.set()


# ---------------- EDIÇÃO (fila, um vídeo por vez) ----------------

def _estilo_valido(e):
    """Só deixa passar campos de estilo de legenda conhecidos e com valor seguro."""
    import re
    out = {}
    for k in ("cor_texto", "cor_destaque"):
        if isinstance(e.get(k), str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", e[k]):
            out[k] = e[k]
    if e.get("tamanho_ass") in ("pequena", "media", "grande"):
        out["tamanho_ass"] = e["tamanho_ass"]
    if e.get("posicao") in ("baixo", "meio"):
        out["posicao"] = e["posicao"]
    if isinstance(e.get("maiusculas"), bool):
        out["maiusculas"] = e["maiusculas"]
    if isinstance(e.get("fonte"), str) and e["fonte"] in {a for a, _ in opcoes_fonte()}:
        out["fonte"] = e["fonte"]
    return out


def config_edicao(p=None):
    import editor
    ap = aprendizado()
    cfg = editor.carregar_config(ap["ajustes"])
    prefs = {}
    for k, v in ap.get("prefs", {}).items():
        if k in {m["k"] for m in PREFS}:
            _aninhar(k, v, prefs)
    editor._mesclar(cfg, prefs)
    rec = RECEITAS.get((p or {}).get("receita") or "padrao", RECEITAS["padrao"])["cfg"]
    # a receita vale quando a pessoa não mudou aquele ajuste na tela de ajustes
    editor._mesclar(cfg, _sem_prefs(rec, prefs))
    if isinstance(cfg.get("vocabulario"), str):
        cfg["vocabulario"] = [l.strip() for l in cfg["vocabulario"].splitlines() if l.strip()]
    cfg["_pasta_efeitos"] = str(PASTA / "Efeitos")
    cfg["_nomes"] = (p or {}).get("nomes") or {}
    est = (p or {}).get("legenda_estilo")
    if isinstance(est, dict):
        cfg.setdefault("legendas", {}).update(_estilo_valido(est))
    return cfg


def _sem_prefs(rec, prefs):
    out = {}
    for a, v in rec.items():
        if isinstance(v, dict):
            sub = {k: x for k, x in v.items() if k not in prefs.get(a, {})}
            if sub:
                out[a] = sub
        else:
            out[a] = v
    return out


def mensagem_amigavel(e):
    """Traduz o erro técnico para o que a pessoa precisa saber e fazer."""
    import analise
    if isinstance(e, analise.ErroUsuario):
        return str(e)
    t = str(e)
    if isinstance(e, OSError) and getattr(e, "errno", None) == 28:
        return "O computador ficou sem espaço em disco. Libere espaço (apague vídeos antigos) e tente de novo."
    if "FFmpeg falhou" in t:
        return ("Não consegui processar um dos vídeos. Confira se ele abre em outro player. Se abrir, tente de novo; "
                "se repetir, use o botão Ajuda para me mandar o relatório.")
    if "Nenhum take aproveitável" in t:
        return "Nenhum take aproveitável. Confira se os vídeos têm imagem e som e se não foram cortados demais."
    if "faster_whisper" in t or "whisper" in t.lower():
        return "Não consegui carregar o reconhecimento de fala. Reinicie o app e tente de novo."
    return "Algo deu errado na edição. Tente de novo; se repetir, use o botão Ajuda para enviar o relatório. (%s)" % t[:160]


def espaco_suficiente(takes):
    try:
        usado = sum(f.stat().st_size for f in takes.iterdir() if f.is_file())
        livre = shutil.disk_usage(PASTA).free
        precisa = max(2 * 1024 ** 3, int(usado * 1.5))
        if livre < precisa:
            return "Pouco espaço no disco: faltam uns %s livres para editar. Apague vídeos antigos e tente de novo." % \
                   ("%.1f GB" % ((precisa - livre) / 1e9))
    except Exception:
        pass
    return None


def editar_projeto(pid):
    import editor
    import render
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
        atualizar(pid, estado="erro", erro="Nenhum vídeo neste projeto. Se você apagou os takes para liberar espaço, envie os vídeos de novo.")
        return
    aviso = espaco_suficiente(takes)
    if aviso:
        atualizar(pid, estado="erro", erro=aviso)
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
              musica_nome=musica.name if musica else None, musica_arq=str(musica) if musica else None)

    def avisar(d):
        atualizar(pid, etapa=d["etapa"], pct=d["pct"], atual=d["atual"], total=d["total"])

    try:
        cfg = config_edicao(p)
        if p.get("modo") == "entrevista":
            cfg["entrevista"] = True
        cfg["_musica_ganho_db"] = ganho_da_musica(musica)
        nome = nome_arquivo(p["nome"])
        r = editor.editar(nome, videos, trabalho, cfg, musica, encerramento, avisar,
                          logo=logo if logo.exists() else None, titulo=p.get("titulo") or None,
                          cortes=p.get("cortes") or None)
        arquivos = {k: str(v) for k, v in r.items() if k in ("video", "capa", "legenda", "pacote") and v}
        atualizar(pid, estado="previa", etapa="Pronto para você ver", pct=100, arquivos=arquivos,
                  duracao=round(r["duracao"], 1), segundos=r["segundos"], fim_ts=time.time(),
                  resumo=r.get("resumo"), capas_info=r.get("capas_info", []), rev=int(time.time()))
        notificar_pronto(p["nome"])
    except render.Cancelado:
        atualizar(pid, estado="erro", erro="Edição cancelada. Use \"Tentar de novo\" quando quiser.")
    except Exception as e:
        traceback.print_exc()
        atualizar(pid, estado="erro", erro=mensagem_amigavel(e))


def notificar_pronto(nome):
    """Sinal para a janela do app mostrar a notificação do computador."""
    with TRAVA:
        AVISOS.append({"titulo": "Vídeo pronto", "texto": "\"%s\" está esperando a sua aprovação." % nome, "ts": time.time()})
        del AVISOS[:-20]


AVISOS = []


# ---------------- CAPA, LEGENDA E MÚSICA DEPOIS DE PRONTO ----------------

def _estado_ocupado(pid, etapa):
    atualizar(pid, estado="editando", etapa=etapa, pct=40, inicio_ts=time.time(), atual=0, total=0)


def refazer_capa(pid, foto, rosto=None, titulo=None, estilo=None, posicao=None, cor=None, fonte=None,
                 cor_caixa=None, tag=None, cor_tag=None, destino=None):
    import capa
    p = ler_projeto(pid)
    cfg = config_edicao(p)
    cp = cfg.get("capa", {})
    trabalho = dir_projeto(pid) / "trabalho"
    nome = nome_arquivo(p["nome"])
    dest = Path(destino) if destino else trabalho / ("CAPA - %s.jpg" % nome)
    logo = DADOS / "logo.png"
    capa.gerar(Path(foto), nome, dest, tuple(rosto) if rosto else None, titulo=(titulo or p.get("titulo") or None),
               logo=logo if logo.exists() else None, estilo=estilo or cp.get("estilo", "classico"),
               fonte=fonte or cp.get("fonte", "Poppins-Bold.ttf"), posicao=posicao or cp.get("posicao", "baixo"),
               cor=cor or cp.get("cor", "#FFFFFF"), margem_segura=cp.get("margem_segura", True),
               cor_caixa=cor_caixa or cp.get("cor_caixa", "#F1F1F1"), tag=(tag if tag is not None else cp.get("tag", "")),
               cor_tag=cor_tag or cp.get("cor_tag", "#E63843"))
    if destino:
        return dest
    arq = dict(p.get("arquivos") or {})
    arq["capa"] = str(dest)
    atualizar(pid, arquivos=arq, rev=int(time.time() * 1000), titulo=titulo if titulo is not None else p.get("titulo"))
    return dest


def foto_do_take(pid, take, tempo, destino):
    """Um quadro do take escolhido, no tamanho da capa (preenchendo a tela)."""
    import analise
    import render
    f = dir_projeto(pid) / "takes" / Path(take).name
    if not f.exists():
        raise ValueError("Take não encontrado.")
    info = analise.sondar(f)
    pre = render._tonemap() if info.get("hdr") else ""
    t = min(max(float(tempo), 0.0), max(info["duracao"] - 0.1, 0.0))
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "%.2f" % t, "-i", str(f), "-frames:v", "1", "-vf",
                    pre + "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920", str(destino)],
                   capture_output=True, check=True)
    return destino


def foto_de_imagem(origem, destino):
    from PIL import Image, ImageOps
    img = ImageOps.exif_transpose(Image.open(origem)).convert("RGB")
    ImageOps.fit(img, (1080, 1920), Image.LANCZOS).save(destino)
    return destino


def refazer_final_em_segundo_plano(pid, etapa, musica=None, palavras=None):
    """Troca a música e/ou corrige a legenda sem renderizar as cenas de novo."""
    import editor
    _estado_ocupado(pid, etapa)

    def tarefa():
        p = ler_projeto(pid)
        try:
            cfg = config_edicao(p)
            m = musica if musica is not None else (Path(p["musica_arq"]) if p.get("musica_arq") else None)
            if m is not None and not Path(m).exists():
                m = None
            editor.refazer_final(nome_arquivo(p["nome"]), dir_projeto(pid) / "trabalho", cfg, m,
                                 ganho_da_musica(m), palavras)
            campos = {"estado": "previa", "etapa": "Pronto para você ver", "pct": 100, "rev": int(time.time() * 1000)}
            if musica is not None:
                campos.update(musica_nome=Path(musica).name, musica_arq=str(musica))
            atualizar(pid, **campos)
        except Exception as e:
            traceback.print_exc()
            atualizar(pid, estado="previa", etapa="Pronto para você ver", pct=100, erro=None)
            AVISOS.append({"titulo": "Não deu certo", "texto": mensagem_amigavel(e), "ts": time.time(), "erro": True})
    threading.Thread(target=tarefa, daemon=True).start()


def limpar_trabalho(pid):
    """Depois de aprovar: apaga os arquivos grandes de trabalho (e, se a pessoa quiser, os takes copiados)."""
    try:
        ap = aprendizado()
        if not ap.get("prefs", {}).get("limpar_apos_aprovar", True):
            return
        pasta = dir_projeto(pid)
        t = pasta / "trabalho"
        shutil.rmtree(t / "blocos", ignore_errors=True)
        for nome in ("montagem.mp4",):
            (t / nome).unlink(missing_ok=True)
        if ap.get("prefs", {}).get("limpar_takes_apos_aprovar", False):
            shutil.rmtree(pasta / "takes", ignore_errors=True)
    except Exception:
        traceback.print_exc()


def estatisticas():
    ps = todos_projetos()
    prontos = [p for p in ps if p.get("estado") == "pronto"]
    ap = aprendizado()
    aprov = sum(1 for h in ap["historico"] if h.get("projeto") and h.get("aprovado"))
    reprov = sum(1 for h in ap["historico"] if h.get("projeto") and not h.get("aprovado"))
    seg = [p.get("segundos") or 0 for p in ps if p.get("segundos")]
    return {"videos": len(ps), "aprovados": len(prontos), "tempo_edicao_seg": sum(seg),
            "media_edicao_seg": round(sum(seg) / len(seg)) if seg else 0,
            "taxa_aprovacao": round(100 * aprov / (aprov + reprov)) if aprov + reprov else None,
            "reprovacoes": reprov, "segundos_de_video": round(sum(p.get("duracao") or 0 for p in prontos))}


def gerar_relatorio():
    """Zip com versão, sistema, registro do app e resumo dos projetos (sem vídeos), para me mandar."""
    pasta = PASTA / "Relatórios"
    pasta.mkdir(parents=True, exist_ok=True)
    f = pasta / ("relatorio-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + ".zip")
    with zipfile.ZipFile(f, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("info.txt", "Gregio AutoEdit %s\nSistema: %s %s\nPython: %s\nEspaço livre: %.1f GB\nGerado em: %s\n" % (
            VERSAO, platform.system(), platform.release(), platform.python_version(),
            shutil.disk_usage(PASTA).free / 1e9, agora()))
        if LOG.exists():
            z.writestr("registro.txt", LOG.read_bytes()[-400_000:].decode("utf-8", "replace"))
        resumo = [{k: p.get(k) for k in ("nome", "estado", "erro", "modo", "n_takes", "duracao", "segundos", "criado",
                                         "etapa", "resumo")} for p in todos_projetos()[:15]]
        z.writestr("projetos.json", json.dumps(resumo, ensure_ascii=False, indent=1))
        for p in todos_projetos()[:3]:
            e = dir_projeto(p["id"]) / "trabalho" / "estado.json"
            if e.exists() and e.stat().st_size < 3_000_000:
                z.write(e, "estado-%s.json" % p["id"])
    return f


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
        limpar_trabalho(pid)
    except Exception as e:
        traceback.print_exc()
        atualizar(pid, estado="pronto", etapa="Salvo no computador", pct=100,
                  erro_drive="Não consegui enviar ao Drive: %s" % str(e)[:200])
        limpar_trabalho(pid)


# ---------------- SERVIDOR HTTP ----------------

LIMITE_UPLOAD = 40 * 1024 ** 3  # 40 GB por arquivo
EXT_PERMITIDAS = set(VIDEO_EXT) | {".mp3", ".m4a", ".wav", ".aac", ".jpg", ".jpeg", ".png", ".webp", ".heic"}

TIPOS = {".mp4": "video/mp4", ".mov": "video/quicktime", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
         ".png": "image/png", ".srt": "text/plain; charset=utf-8", ".zip": "application/zip",
         ".html": "text/html; charset=utf-8", ".svg": "image/svg+xml", ".mp3": "audio/mpeg",
         ".m4a": "audio/mp4", ".wav": "audio/wav"}


def visao(p):
    """O que a tela precisa de cada projeto."""
    v = {k: p.get(k) for k in ("id", "nome", "titulo", "estilo", "estado", "etapa", "pct", "atual", "total",
                                 "erro", "erro_drive", "duracao", "segundos", "drive_link", "criado",
                                 "aprovado_em", "musica_nome", "n_takes", "modo", "resumo", "receita", "rev")}
    v["n_capas"] = len(p.get("capas_info") or [])
    try:
        arq = (p.get("arquivos") or {}).get("video")
        if arq and Path(arq).exists():
            v["rev"] = max(int(p.get("rev") or 0), int(Path(arq).stat().st_mtime * 1000))
    except Exception:
        pass
    v["pode_ajustar"] = (dir_projeto(p["id"]) / "trabalho" / "audio.json").exists() and \
        (dir_projeto(p["id"]) / "trabalho" / "montagem.mp4").exists()
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
        if tam > LIMITE_UPLOAD:
            raise ValueError("Arquivo grande demais (limite de %d GB)." % (LIMITE_UPLOAD // 1024 ** 3))
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
        self.send_header("Referrer-Policy", "no-referrer")
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
                                   "receitas": [[k, v["nome"]] for k, v in RECEITAS.items()],
                                   "aviso_musica_ok": bool(aprendizado().get("aviso_musica_ok")),
                                   "tutorial_auto": not aprendizado().get("tutorial_visto") and int(aprendizado().get("tutorial_aberturas", 0)) < 2,
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
            if url.path == "/api/preferencias":
                return self._json({"prefs": prefs_atuais(), "fontes": [list(o) for o in opcoes_fonte()]})
            if url.path == "/api/estatisticas":
                return self._json(estatisticas())
            if url.path == "/api/avisos":
                depois = float(q.get("depois", 0) or 0)
                with TRAVA:
                    novos = [a for a in AVISOS if a["ts"] > depois]
                return self._json({"avisos": novos, "agora": time.time()})
            if url.path == "/api/takes":
                import analise
                pasta = dir_projeto(q["id"]) / "takes"
                lista = []
                for f in sorted(pasta.iterdir()) if pasta.exists() else []:
                    if f.suffix.lower() in VIDEO_EXT:
                        try:
                            lista.append({"nome": f.name, "duracao": round(analise.sondar(f)["duracao"], 1)})
                        except Exception:
                            pass
                return self._json({"takes": lista})
            if url.path == "/api/quadro":
                tmp = DADOS / "_quadro.jpg"
                with TRAVA:
                    foto_do_take(q["id"], q["take"], float(q.get("seg", 0)), tmp)
                    corpo = tmp.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(corpo)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(corpo)
                return
            if url.path == "/api/capa_previa":
                p = ler_projeto(q["id"]) or {}
                info = (p.get("capas_info") or [])[int(q.get("n", 1)) - 1]
                return self._arquivo(info["foto"])
            if url.path == "/api/legenda":
                f = dir_projeto(q["id"]) / "trabalho" / "legenda.json"
                palavras = ler(f, [])
                import exportar
                mc = int(config_edicao(ler_projeto(q["id"])).get("legendas", {}).get("max_caracteres", 24))
                lg = config_edicao(ler_projeto(q["id"])).get("legendas", {})
                return self._json({"palavras": [{"i": i, "w": w["w"]} for i, w in enumerate(palavras)],
                                   "frases": exportar.frases(palavras, mc),
                                   "estilo": {k: (lg.get(k) if lg.get(k) is not None else d) for k, d in (
                                       ("fonte", "Poppins-Bold.ttf"), ("cor_texto", "#FFFFFF"), ("cor_destaque", "#FFD400"),
                                       ("tamanho_ass", "media"), ("posicao", "baixo"), ("maiusculas", False))},
                                   "estilos_salvos": aprendizado().get("estilos_legenda", []),
                                   "fontes": [list(o) for o in opcoes_fonte()]})
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
            return self._json({"erro": mensagem_amigavel(e)}, 500)

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
                     "modo": "entrevista" if d.get("modo") == "entrevista" else "normal",
                     "receita": d.get("receita") if d.get("receita") in RECEITAS else "padrao", "estado": "recebendo", "etapa": "Recebendo os vídeos",
                     "pct": 0, "criado": agora(), "criado_ts": time.time(), "n_takes": 0}
                salvar_projeto(p)
                return self._json({"id": pid})

            if url.path == "/api/arquivo":
                pid = q["id"]
                nome = Path(urllib.parse.unquote(q["nome"])).name
                if Path(nome).suffix.lower() not in EXT_PERMITIDAS:
                    return self._json({"erro": "Esse tipo de arquivo não é aceito. Use vídeo, áudio ou imagem."}, 400)
                self._receber(dir_projeto(pid) / "takes" / nome)
                return self._json({"ok": True})

            if url.path == "/api/concluir":
                d = self._corpo_json()
                pid = d["id"]
                n = len([f for f in (dir_projeto(pid) / "takes").iterdir() if f.suffix.lower() in VIDEO_EXT])
                if not n:
                    atualizar(pid, estado="erro", erro="Nenhum vídeo recebido.")
                    return self._json({"erro": "Nenhum vídeo recebido."}, 400)
                nomes = {}
                for k, v in (d.get("nomes") or {}).items():
                    if isinstance(v, dict) and str(v.get("nome", "")).strip():
                        nomes[Path(str(k)).name] = {"nome": str(v["nome"]).strip()[:60], "cargo": str(v.get("cargo", "")).strip()[:60]}
                atualizar(pid, estado="na_fila", etapa="Na fila", n_takes=n, cortes=limpar_cortes(d.get("cortes")), nomes=nomes)
                enfileirar(pid)
                return self._json({"ok": True})

            if url.path == "/api/preferencias":
                d = self._corpo_json()
                validas = {m["k"]: m for m in PREFS}
                ap = aprendizado()
                if d.get("restaurar"):
                    ap["prefs"] = {}
                for k, v in (d.get("prefs") or {}).items():
                    m = validas.get(k)
                    if not m:
                        continue
                    if m["tipo"] == "liga":
                        v = bool(v)
                    elif m["tipo"] == "escolha":
                        if v not in [o[0] for o in (opcoes_fonte() if m.get("fonte") else m["op"])]:
                            continue
                    elif m["tipo"] == "cor":
                        if not re.fullmatch(r"#[0-9a-fA-F]{6}", str(v)):
                            continue
                    else:
                        v = str(v)[:2000]
                    ap["prefs"][k] = v
                salvar_aprendizado(ap)
                return self._json({"ok": True})

            if url.path in ("/api/capa", "/api/capa_teste"):
                d = self._corpo_json()
                pid = d["id"]
                p = ler_projeto(pid)
                if not p or p.get("estado") != "previa":
                    return self._json({"erro": "Só dá para mudar a capa de um vídeo que está esperando aprovação."}, 400)
                trabalho = dir_projeto(pid) / "trabalho"
                origem = d.get("origem")
                rosto = None
                if origem == "opcao":
                    info = (p.get("capas_info") or [])[int(d.get("n", 1)) - 1]
                    foto, rosto = info["foto"], info.get("rosto")
                elif origem == "take":
                    foto = foto_do_take(pid, d["take"], d.get("tempo", 0), trabalho / "capa_escolhida.png")
                elif origem == "imagem":
                    foto = trabalho / "capa_imagem.png"
                    if not foto.exists():
                        return self._json({"erro": "Envie a imagem primeiro."}, 400)
                else:
                    return self._json({"erro": "Origem de capa inválida."}, 400)
                hexa = lambda v: v if isinstance(v, str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", v) else None
                extra = dict(titulo=d.get("titulo"), estilo=d.get("estilo") if d.get("estilo") in ("classico", "faixa", "cor", "caixa") else None,
                             posicao=d.get("posicao") if d.get("posicao") in ("baixo", "meio", "cima") else None,
                             cor=hexa(d.get("cor")), cor_caixa=hexa(d.get("cor_caixa")), cor_tag=hexa(d.get("cor_tag")),
                             tag=str(d["tag"])[:26] if d.get("tag") is not None else None,
                             fonte=d.get("fonte") if d.get("fonte") in [a for a, _ in opcoes_fonte()] else None)
                if url.path == "/api/capa_teste":  # só a prévia, não muda o vídeo
                    tmp = trabalho / "_previa_capa.jpg"
                    refazer_capa(pid, foto, rosto, destino=tmp, **extra)
                    corpo = tmp.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", str(len(corpo)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(corpo)
                    return
                refazer_capa(pid, foto, rosto, **extra)
                return self._json({"ok": True})

            if url.path == "/api/capa_imagem":
                pid = q["id"]
                if not ler_projeto(pid):
                    return self._json({"erro": "Projeto não encontrado."}, 404)
                trabalho = dir_projeto(pid) / "trabalho"
                trabalho.mkdir(parents=True, exist_ok=True)
                tmp = trabalho / "_capa_envio"
                self._receber(tmp)
                try:
                    foto_de_imagem(tmp, trabalho / "capa_imagem.png")
                except Exception:
                    return self._json({"erro": "Não consegui abrir essa imagem. Use JPG ou PNG."}, 400)
                finally:
                    tmp.unlink(missing_ok=True)
                return self._json({"ok": True})

            if url.path == "/api/legenda":
                d = self._corpo_json()
                pid = d["id"]
                p = ler_projeto(pid)
                f = dir_projeto(pid) / "trabalho" / "legenda.json"
                if not p or not f.exists() or p.get("estado") != "previa":
                    return self._json({"erro": "Não dá para corrigir a legenda deste vídeo agora."}, 400)
                palavras = ler(f, [])
                if d.get("frases"):
                    import exportar
                    mc = int(config_edicao(p).get("legendas", {}).get("max_caracteres", 24))
                    palavras = exportar.editar_frases(palavras, d["frases"], mc)
                for i, t in (d.get("edicoes") or {}).items():
                    i = int(i)
                    if 0 <= i < len(palavras) and str(t).strip():
                        palavras[i]["w"] = str(t).strip()[:60]
                if isinstance(d.get("estilo"), dict):
                    est = _estilo_valido(d["estilo"])
                    atualizar(pid, legenda_estilo=est)
                    if d.get("para_todos"):
                        ap = aprendizado()
                        ap.setdefault("prefs", {}).update({"legendas." + k: v for k, v in est.items()})
                        salvar_aprendizado(ap)
                    if d.get("salvar_como"):
                        ap = aprendizado()
                        lista = [x for x in ap.get("estilos_legenda", []) if x.get("nome") != str(d["salvar_como"])[:24]]
                        lista.append({"nome": str(d["salvar_como"])[:24], "estilo": est})
                        ap["estilos_legenda"] = lista[-12:]
                        salvar_aprendizado(ap)
                refazer_final_em_segundo_plano(pid, "Aplicando a correção da legenda", palavras=palavras)
                return self._json({"ok": True})

            if url.path == "/api/ajuda":
                import urllib.parse as up
                corpo = "Versão: %s\nSistema: %s %s\n\nConte o que aconteceu:\n" % (VERSAO, platform.system(), platform.release())
                webbrowser.open("https://wa.me/%s?text=%s" % (AJUDA_WHATSAPP, up.quote("Preciso de ajuda com o Gregio AutoEdit.\n" + corpo)))
                return self._json({"ok": True, "email": AJUDA_WHATSAPP_TXT})

            if url.path == "/api/relatorio":
                f = gerar_relatorio()
                abrir_no_sistema(f, revelar=True)
                return self._json({"ok": True, "arquivo": str(f), "email": AJUDA_WHATSAPP_TXT})

            if url.path == "/api/tutorial":
                d = self._corpo_json()
                ap = aprendizado()
                if d.get("acao") == "aberto":
                    ap["tutorial_aberturas"] = int(ap.get("tutorial_aberturas", 0)) + 1
                elif d.get("acao") == "fechado":
                    ap["tutorial_visto"] = True
                salvar_aprendizado(ap)
                return self._json({"ok": True})

            if url.path == "/api/aviso_musica":
                ap = aprendizado()
                ap["aviso_musica_ok"] = True
                salvar_aprendizado(ap)
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
                    if not any(f.suffix.lower() in VIDEO_EXT for f in (dir_projeto(pid) / "takes").glob("*")):
                        return self._json({"erro": "Os takes originais foram apagados para liberar espaço. Envie os vídeos de novo."}, 400)
                    motivos = d.get("motivos", [])
                    mudancas = aprender(p, False, motivos, d.get("nota", ""))
                    recusadas = p.get("musicas_recusadas", [])
                    if "musica_nao_combinou" in motivos and p.get("musica_nome"):
                        recusadas.append(p["musica_nome"])
                    atualizar(pid, estado="na_fila", etapa="Na fila para refazer", pct=0,
                              musicas_recusadas=recusadas, erro=None)
                    enfileirar(pid)
                elif acao == "cancelar":
                    import render
                    if ATUAL["id"] == pid:
                        render.CANCELAR.set()
                    else:
                        with TRAVA:
                            if pid in FILA:
                                FILA.remove(pid)
                        atualizar(pid, estado="erro", erro="Edição cancelada. Use \"Tentar de novo\" quando quiser.")
                elif acao in ("subir", "descer"):
                    with TRAVA:
                        if pid in FILA:
                            i = FILA.index(pid)
                            j = i - 1 if acao == "subir" else i + 1
                            if 0 <= j < len(FILA):
                                FILA[i], FILA[j] = FILA[j], FILA[i]
                elif acao == "trocar_musica":
                    if p.get("estado") != "previa":
                        return self._json({"erro": "Só dá para trocar a música de um vídeo esperando aprovação."}, 400)
                    if not (dir_projeto(pid) / "trabalho" / "audio.json").exists() or not (dir_projeto(pid) / "trabalho" / "montagem.mp4").exists():
                        return self._json({"erro": "Este vídeo foi feito numa versão antiga. Use Reprovar para refazer com a música nova."}, 400)
                    escolhida = None
                    if d.get("musica"):
                        escolhida = next((Path(m["caminho"]) for m in lista_musicas() if m["nome"] == d["musica"]), None)
                    if escolhida is None:
                        evitar = list(p.get("musicas_recusadas", [])) + ([p["musica_nome"]] if p.get("musica_nome") else [])
                        escolhida = escolher_musica(p.get("estilo"), evitar)
                    if escolhida is None:
                        return self._json({"erro": "Não há outra música para trocar. Adicione músicas em Ajustes."}, 400)
                    refazer_final_em_segundo_plano(pid, "Trocando a música", musica=escolhida)
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
                elif d.get("onde") == "fontes":
                    (PASTA / "Fontes").mkdir(exist_ok=True)
                    abrir_no_sistema(PASTA / "Fontes")
                elif d.get("onde") == "efeitos":
                    (PASTA / "Efeitos").mkdir(exist_ok=True)
                    abrir_no_sistema(PASTA / "Efeitos")
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
                if d.get("letra") is not None and d.get("estilo") and d.get("nome"):
                    salvar_meta_musica(Path(d["estilo"]).name, Path(d["nome"]).name, letra=bool(d["letra"]))
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
                threading.Thread(target=analisar_musica, args=(estilo, nome), daemon=True).start()
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
            return self._json({"erro": mensagem_amigavel(e)}, 500)


def main(abrir=True):
    _ligar_log()
    (PASTA / "Efeitos").mkdir(exist_ok=True)
    (PASTA / "Fontes").mkdir(exist_ok=True)
    import capa
    capa.PASTAS_FONTES.append(str(PASTA / "Fontes"))
    try:
        class Servidor(ThreadingHTTPServer):
            def handle_error(self, request, client_address):  # o navegador fecha conexões o tempo todo: não é erro
                import sys as _s
                if isinstance(_s.exc_info()[1], (ConnectionResetError, BrokenPipeError, ConnectionAbortedError)):
                    return
                super().handle_error(request, client_address)
        srv = Servidor(("127.0.0.1", PORTA), App)
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
