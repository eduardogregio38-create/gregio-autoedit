"""Gregio AutoEdit: motor de edição que roda no computador da pessoa.

editar(pasta_takes, trabalho, cfg, ...) analisa os takes, monta o roteiro e
renderiza o vídeo em qualidade final, mais a capa, a legenda e o pacote para
CapCut/Premiere/DaVinci. As escolhas ficam num JSON para refazer depois sem
transcrever de novo.

Também pode ser usado pela linha de comando:
    python editor.py ./takes [--musica trilha.mp3] [--saida ./resultado]
"""

import argparse
import copy
import json
import os
import subprocess
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

AQUI = Path(__file__).resolve().parent
if str(AQUI) not in sys.path:
    sys.path.insert(0, str(AQUI))

import analise  # noqa: E402
import capa  # noqa: E402
import exportar  # noqa: E402
import render  # noqa: E402
import ritmo  # noqa: E402
import selecao  # noqa: E402

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".mts", ".3gp", ".webm"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}
IMG_EXT = {".png", ".jpg", ".jpeg"}
CONFIG_PADRAO = AQUI.parent / "config_padrao.json"


class Progresso:
    """Converte as etapas em uma porcentagem só, para a barra do app."""
    PESOS = [("analise", 0.45), ("montagem", 0.05), ("render", 0.35), ("final", 0.15)]

    def __init__(self, avisar=None):
        self.avisar = avisar or (lambda d: None)
        self.inicio = time.time()

    def __call__(self, fase, texto, atual=0, total=0):
        feito = 0.0
        for nome, peso in self.PESOS:
            if nome == fase:
                feito += peso * (atual / total if total else 0)
                break
            feito += peso
        d = {"fase": fase, "etapa": texto, "atual": atual, "total": total,
             "pct": round(min(99.0, feito * 100), 1), "segundos": round(time.time() - self.inicio)}
        print("  [%3.0f%%] %s %s" % (d["pct"], texto, ("%d/%d" % (atual, total)) if total else ""), flush=True)
        self.avisar(d)


def carregar_config(extra=None):
    cfg = json.loads(CONFIG_PADRAO.read_text(encoding="utf-8"))
    _mesclar(cfg, extra or {})
    return cfg


def _mesclar(base, extra):
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _mesclar(base[k], v)
        else:
            base[k] = v
    return base


def separar_arquivos(pasta: Path):
    todos = sorted(p for p in pasta.iterdir() if p.is_file() and not p.name.startswith("."))
    encerramento = next((p for p in todos if p.stem.lower().startswith("encerramento")), None)
    videos = [p for p in todos if p.suffix.lower() in VIDEO_EXT and p != encerramento]
    musica = next((p for p in todos if p.suffix.lower() in AUDIO_EXT), None)
    return videos, musica, encerramento


# ---------------- ENCERRAMENTO (opcional, a tela final da pessoa) ----------------

def preparar_encerramento(arquivo, trabalho: Path, cfg):
    if not arquivo:
        return None
    arquivo = Path(arquivo)
    if not arquivo.exists():
        return None
    if arquivo.suffix.lower() in IMG_EXT:
        seg = cfg.get("encerramento", {}).get("duracao_imagem_seg", 3)
        video = trabalho / "encerramento_imagem.mp4"
        render.run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-t", str(seg), "-i", str(arquivo),
                    "-f", "lavfi", "-t", str(seg), "-i", "anullsrc=r=48000:cl=stereo",
                    "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,"
                    "fps=30,format=yuv420p", "-c:v", "libx264", "-c:a", "aac", "-shortest", str(video)])
        arquivo = video
    info = analise.sondar(arquivo)
    item = {"path": arquivo, "ordem": 999, **info, "palavras": [], "texto": "", "tempo_fala": 0, "visual": []}
    return {"tipo": "encerramento", "item": item, "coberturas": [],
            "trechos": [{"ini": 0.0, "fim": info["duracao"], "palavras": []}]}


# ---------------- PLANEJAMENTO ----------------

def planejar(videos, musica, trabalho: Path, cfg, encerramento=None, prog=None, cortes=None):
    prog = prog or Progresso()
    itens = analise.analisar(videos, cfg, trabalho / "cache",
                             progresso=lambda t, a, n: prog("analise", t, a, n), cortes=cortes)
    prog("montagem", "Montando a edição")
    for it in itens:
        tipo = "FALA " if selecao.e_fala(it, cfg) else "LOCAL"
        print(f"  {tipo} {it['path'].name}: fala {100 * it.get('densidade', 0):.0f}%, "
              f"clareza {it.get('clareza', 0):.2f}, {it.get('n_palavras', 0)} palavras")
    roteiro, descartados = selecao.montar_roteiro(itens, cfg)
    if not roteiro:
        raise RuntimeError("Nenhum take aproveitável. Confira se os vídeos têm imagem e som.")
    selecao.completar_duracao(roteiro, itens, cfg)
    final_bloco = preparar_encerramento(encerramento, trabalho, cfg)
    tempos, bpm = ritmo.batidas(musica) if musica and cfg.get("ritmo", {}).get("ativar", True) else ([], 0)
    minimo = cfg["selecao"].get("duracao_min_final_seg", 30)

    def montar_final():
        for b in roteiro:
            b.pop("desfoque_inicio", None)
            b.pop("desfoque_fim", None)
        completo = roteiro + ([final_bloco] if final_bloco else [])
        trans = render.escolher_transicoes(completo, cfg)
        n = ritmo.ajustar(completo, trans, tempos, cfg)
        return completo, trans, n

    completo, transicoes, n = montar_final()
    for _ in range(2):
        if selecao.duracao_conteudo(roteiro) >= minimo or not selecao.completar_duracao(roteiro, itens, cfg):
            break
        completo, transicoes, n = montar_final()
    if tempos:
        print(f"  ritmo: {bpm:.0f} BPM, {n} corte(s) na batida")
    return itens, completo, transicoes, descartados


CAMPOS_ITEM = ["duracao", "largura", "altura", "fps", "tem_audio", "hdr", "palavras", "confianca",
               "texto", "tempo_fala", "n_palavras", "clareza", "densidade", "ordem", "ini_util", "fim_util"]


def para_estado(itens, roteiro, transicoes, descartados, musica):
    def ref(it):
        return it["path"].name
    blocos = [{
        "tipo": b["tipo"],
        "item": ref(b["item"]) if b["tipo"] != "encerramento" else None,
        "trechos": b["trechos"],
        "coberturas": [{"item": ref(c["item"]), "ini": c["ini"], "fim": c["fim"], "t": c["t"]}
                       for c in b.get("coberturas", [])],
        "desfoque_inicio": b.get("desfoque_inicio", False),
        "desfoque_fim": b.get("desfoque_fim", False),
    } for b in roteiro]
    return {"versao": 2, "itens": [{"nome": ref(it), **{k: it.get(k) for k in CAMPOS_ITEM}} for it in itens],
            "roteiro": blocos, "transicoes": transicoes, "descartados": [ref(i) for i in descartados],
            "musica": str(musica) if musica else None}


# ---------------- RENDERIZAÇÃO ----------------

def renderizar(nome, itens, roteiro, transicoes, descartados, musica, trabalho: Path, cfg, prog=None,
               logo=None, titulo=None):
    prog = prog or Progresso()
    cfg = copy.deepcopy(cfg)
    blocos_dir = trabalho / "blocos"
    blocos_dir.mkdir(parents=True, exist_ok=True)
    nucleos = os.cpu_count() or 2
    paralelo = max(1, min(4, nucleos // 2))
    cfg["_threads"] = max(1, nucleos // paralelo)

    feitos = [0]
    prog("render", "Renderizando as cenas", 0, len(roteiro))

    def um(arg):
        i, bloco = arg
        render.render_bloco(bloco, i, blocos_dir, cfg)
        feitos[0] += 1
        prog("render", "Renderizando as cenas", feitos[0], len(roteiro))

    with ThreadPoolExecutor(max_workers=paralelo) as ex:
        list(ex.map(um, enumerate(roteiro)))

    cfg["_threads"] = 0
    prog("final", "Juntando as cenas e as transições", 0, 3)
    sem_musica = trabalho / "montagem.mp4"
    inicios = render.juntar_com_transicoes(roteiro, transicoes, sem_musica, cfg)
    fim_musica = inicios[-1] if roteiro and roteiro[-1]["tipo"] == "encerramento" else None

    prog("final", "Colocando a música e ajustando o volume", 1, 3)
    srt = exportar.gerar_srt(roteiro, inicios, trabalho / "legendas.srt", cfg["legendas"]["max_caracteres"]) \
        if cfg["legendas"]["gerar_srt"] else None
    final = trabalho / f"{nome}.mp4"
    render.finalizar(sem_musica, musica, srt, final, cfg, fim_musica)

    prog("final", "Criando a capa", 2, 3)
    arq_capa = None
    try:
        fonte = next((b["arquivo"] for b in roteiro if b["tipo"] == "fala"), roteiro[0]["arquivo"])
        foto, rosto = capa.melhor_quadro(fonte, trabalho / "capa_foto.png")
        if foto:
            arq_capa, _ = capa.gerar(foto, nome, trabalho / f"CAPA - {nome}.jpg", rosto, titulo=titulo, logo=logo)
    except Exception:
        traceback.print_exc()

    pacote = None
    if cfg.get("gerar_pacote", True):
        try:
            xml = exportar.gerar_xml(roteiro, musica, trabalho / "timeline_premiere_davinci.xml", cfg, nome)
            rel = exportar.gerar_relatorio(itens, roteiro, descartados, transicoes, trabalho / "relatorio.csv", cfg)
            pacote = exportar.montar_pacote(nome, trabalho, final, srt, xml, rel, roteiro, musica)
        except Exception:
            traceback.print_exc()
    return {"video": final, "capa": arq_capa, "legenda": srt, "pacote": pacote,
            "duracao": render.duracao(final)}


def editar(nome, videos, trabalho: Path, cfg, musica=None, encerramento=None, avisar=None, logo=None, titulo=None,
           cortes=None):
    """Faz tudo: análise, montagem e render. Devolve os arquivos e o estado."""
    trabalho.mkdir(parents=True, exist_ok=True)
    prog = Progresso(avisar)
    prog("analise", "Preparando", 0, len(videos))
    itens, roteiro, transicoes, descartados = planejar(videos, musica, trabalho, cfg, encerramento, prog, cortes)
    estado = para_estado(itens, roteiro, transicoes, descartados, musica)
    (trabalho / "estado.json").write_text(json.dumps(estado, ensure_ascii=False), encoding="utf-8")
    saida = renderizar(nome, itens, roteiro, transicoes, descartados, musica, trabalho, cfg, prog, logo, titulo)
    saida["segundos"] = round(time.time() - prog.inicio)
    return saida


# ---------------- LINHA DE COMANDO ----------------

def main(argv=None):
    ap = argparse.ArgumentParser(description="Gregio AutoEdit: edita uma pasta de takes")
    ap.add_argument("pasta")
    ap.add_argument("--musica")
    ap.add_argument("--saida")
    args = ap.parse_args(argv)
    pasta = Path(args.pasta)
    videos, musica_pasta, encerramento = separar_arquivos(pasta)
    if not videos:
        print("Nenhum vídeo em", pasta)
        return 1
    musica = Path(args.musica) if args.musica else musica_pasta
    saida = Path(args.saida or pasta / "_resultado")
    r = editar(pasta.name, videos, saida, carregar_config(), musica, encerramento)
    print("PRONTO em %ds: %s (%.1fs de vídeo)" % (r["segundos"], r["video"], r["duracao"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
