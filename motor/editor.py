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
        if render.CANCELAR.is_set():
            raise render.Cancelado()
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
    if render.codec_video(cfg):   # chip de vídeo: poucas sessões ao mesmo tempo
        paralelo = min(paralelo, 2)
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
    realces, efeitos = _realces_e_efeitos(roteiro, transicoes, inicios, cfg)
    inicio_m = ritmo.ponto_inicial(musica) if musica and cfg["audio"].get("musica_ponto_certo", True) else 0.0
    ganho_m = (cfg.get("_musica_ganho_db") or 0.0)
    srt = exportar.gerar_srt(roteiro, inicios, trabalho / "legendas.srt", cfg["legendas"]["max_caracteres"]) \
        if cfg["legendas"]["gerar_srt"] else None
    palavras = exportar.palavras_na_timeline(roteiro, inicios)
    (trabalho / "legenda.json").write_text(json.dumps(palavras, ensure_ascii=False), encoding="utf-8")
    nomes_cfg = cfg.get("_nomes") or {}
    nomes = []
    for k, bl in enumerate(roteiro):
        n = nomes_cfg.get(bl["item"]["path"].name) if bl["tipo"] == "fala" and not bl.get("gancho") else None
        if n and n.get("nome"):
            nomes.append((inicios[k] + 0.3, inicios[k] + min(4.0, bl.get("duracao_render", 4.0)), n["nome"], n.get("cargo", "")))
    texto_final = (cfg.get("encerramento", {}).get("texto") or "").strip()
    inicio_final = inicios[-1] if roteiro and roteiro[-1]["tipo"] == "encerramento" else None
    total_video = render.duracao(sem_musica)
    ass = exportar.gerar_ass(palavras, trabalho / "legenda.ass", cfg, total_video, nomes,
                             texto_final or None, inicio_final)
    (trabalho / "audio.json").write_text(json.dumps({
        "inicios": inicios, "fim_musica": fim_musica, "realces": realces,
        "efeitos": [[str(a), t] for a, t in efeitos],
        "nomes": nomes, "texto_final": texto_final, "inicio_final": inicio_final}), encoding="utf-8")
    final = trabalho / f"{nome}.mp4"
    render.finalizar(sem_musica, musica, srt, final, cfg, fim_musica, realces, efeitos, inicio_m, ganho_m, ass)

    prog("final", "Criando a capa", 2, 3)
    arq_capa, capas, capas_info = None, [], []
    try:
        fonte = next((b["arquivo"] for b in roteiro if b["tipo"] == "fala"), roteiro[0]["arquivo"])
        cp = cfg.get("capa", {})
        quadros = capa.melhor_quadro(fonte, trabalho / "capa_foto.png", 3 if cp.get("tres_opcoes", True) else 1)
        if not isinstance(quadros, list):
            quadros = [quadros] if quadros and quadros[0] else []
        for i, (foto, rosto) in enumerate(quadros):
            dest = trabalho / (f"CAPA - {nome}.jpg" if i == 0 else f"CAPA - {nome} ({i + 1}).jpg")
            capa.gerar(foto, nome, dest, rosto, titulo=titulo, logo=logo, estilo=cp.get("estilo", "classico"),
                       fonte=cp.get("fonte", "Poppins-Bold.ttf"), posicao=cp.get("posicao", "baixo"),
                       cor=cp.get("cor", "#FFFFFF"), margem_segura=cp.get("margem_segura", True),
                       cor_caixa=cp.get("cor_caixa", "#F1F1F1"), tag=cp.get("tag", ""), cor_tag=cp.get("cor_tag", "#E63843"))
            capas.append(dest)
            capas_info.append({"foto": str(foto), "rosto": [int(v) for v in rosto] if rosto is not None else None})
        arq_capa = capas[0] if capas else None
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
    dur_final = render.duracao(final)
    return {"video": final, "capa": arq_capa, "capas": capas, "capas_info": capas_info, "legenda": srt, "pacote": pacote,
            "duracao": dur_final, "resumo": _resumo(itens, roteiro, dur_final, cfg)}


def _resumo(itens, roteiro, dur_final, cfg):
    """Números para o aviso na tela: tamanho, quanto de fala foi cortado e se o vídeo está fora do ideal."""
    vistos, bruto = set(), 0.0
    for b in roteiro:   # tempo gravado dos takes de fala que entraram, antes de tirar pausas e erros
        if b["tipo"] == "fala" and not b.get("gancho") and id(b["item"]) not in vistos:
            vistos.add(id(b["item"]))
            bruto += b["item"].get("fim_util", b["item"]["duracao"]) - b["item"].get("ini_util", 0.0)
    mantido = sum(t["fim"] - t["ini"] for b in roteiro if b["tipo"] == "fala" and not b.get("gancho") for t in b["trechos"])
    avisos = []
    if dur_final > 90:
        avisos.append("Passou de 90 segundos. Reels mais curtos costumam render mais.")
    if dur_final < 15:
        avisos.append("Ficou muito curto (menos de 15 segundos).")
    elif dur_final < cfg["selecao"].get("duracao_min_final_seg", 30) - 1:
        avisos.append("Ficou abaixo de %d segundos: faltou material." % cfg["selecao"].get("duracao_min_final_seg", 30))
    return {"duracao": round(dur_final, 1), "fala_original": round(bruto, 1), "fala_mantida": round(mantido, 1),
            "cortado": round(max(0.0, bruto - mantido), 1), "cenas": len(roteiro), "avisos": avisos}


def _realces_e_efeitos(roteiro, transicoes, inicios, cfg):
    """Trechos só com imagem (música sobe) e instantes de transição (efeito sonoro opcional)."""
    realces = []
    for k, b in enumerate(roteiro):
        if b["tipo"] == "local" and cfg["audio"]["som_ambiente_takes_local_db"] <= -90:
            a, f = inicios[k], inicios[k] + b.get("duracao_render", 0)
            if realces and abs(realces[-1][1] - a) < 0.8:
                realces[-1][1] = f
            else:
                realces.append([a, f])
    efeitos = []
    pasta = cfg.get("_pasta_efeitos")
    if pasta and cfg["audio"].get("efeitos_transicao", False) and Path(pasta).is_dir():
        arqs = sorted(p for p in Path(pasta).iterdir() if p.suffix.lower() in AUDIO_EXT)
        if arqs:
            import random
            for k, t in enumerate(transicoes):
                if t == "desfoque":
                    efeitos.append((random.choice(arqs), max(0.0, inicios[k + 1] - 0.15)))
    return realces, efeitos


def refazer_final(nome, trabalho: Path, cfg, musica, ganho_musica_db=0.0, palavras=None):
    """Refaz só o áudio e a legenda em cima da montagem pronta (segundos, sem renderizar as cenas de novo).
    Serve para trocar a música e para corrigir uma palavra da legenda."""
    d = json.loads((trabalho / "audio.json").read_text(encoding="utf-8"))
    cfg = copy.deepcopy(cfg)
    if palavras is not None:
        (trabalho / "legenda.json").write_text(json.dumps(palavras, ensure_ascii=False), encoding="utf-8")
    else:
        pj = trabalho / "legenda.json"
        palavras = json.loads(pj.read_text(encoding="utf-8")) if pj.exists() else []
    srt = None
    if palavras and cfg["legendas"].get("gerar_srt", True):
        srt = exportar.srt_de_palavras(palavras, trabalho / "legendas.srt", cfg["legendas"]["max_caracteres"])
    total = render.duracao(trabalho / "montagem.mp4")
    ass = exportar.gerar_ass(palavras, trabalho / "legenda.ass", cfg, total,
                             [tuple(n) for n in d.get("nomes", [])], d.get("texto_final") or None, d.get("inicio_final"))
    inicio_m = ritmo.ponto_inicial(musica) if musica and cfg["audio"].get("musica_ponto_certo", True) else 0.0
    final = trabalho / f"{nome}.mp4"
    novo = trabalho / f"_novo_{nome}.mp4"
    novo.unlink(missing_ok=True)
    render.finalizar(trabalho / "montagem.mp4", musica, srt, novo, cfg, d.get("fim_musica"), d.get("realces"),
                     [(Path(a), t) for a, t in d.get("efeitos", [])], inicio_m, ganho_musica_db, ass)
    if not novo.exists() or novo.stat().st_size < 1000:
        raise RuntimeError("O vídeo novo não foi gerado. O vídeo anterior foi mantido.")
    os.replace(novo, final)  # troca de uma vez, o vídeo antigo só some quando o novo está inteiro
    return final


refazer_audio = refazer_final


def _tem_audio(arq):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_type",
                          "-of", "csv=p=0", str(arq)], capture_output=True, text=True).stdout
    return "audio" in out


def cortar_trecho(trabalho: Path, a: float, b: float, cfg):
    """Tira o trecho [a, b] (em segundos) da montagem. A legenda, os efeitos, os nomes e a música acompanham.
    Guarda a montagem original na primeira vez, para dar para voltar atrás. Devolve os segundos tirados."""
    mont = trabalho / "montagem.mp4"
    total = render.duracao(mont)
    a, b = max(0.0, float(a)), min(total, float(b))
    if b - a < 0.2:
        raise ValueError("O trecho escolhido é curto demais.")
    if a <= 0.05 and b >= total - 0.05:
        raise ValueError("Não dá para cortar o vídeo inteiro.")
    for nome in ("montagem.mp4", "legenda.json", "audio.json"):
        orig = trabalho / (Path(nome).stem + "_original" + Path(nome).suffix)
        if not orig.exists() and (trabalho / nome).exists():
            (orig).write_bytes((trabalho / nome).read_bytes())
    audio = _tem_audio(mont)
    partes = []
    if a > 0.05:
        partes.append((0.0, a))
    if b < total - 0.05:
        partes.append((b, total))
    fc, rotulos = [], ""
    for i, (x, y) in enumerate(partes):
        fc.append(f"[0:v]trim={x:.3f}:{y:.3f},setpts=PTS-STARTPTS[v{i}]")
        rotulos += f"[v{i}]"
        if audio:
            fc.append(f"[0:a]atrim={x:.3f}:{y:.3f},asetpts=PTS-STARTPTS[a{i}]")
            rotulos += f"[a{i}]"
    fc.append(f"{rotulos}concat=n={len(partes)}:v=1:a={1 if audio else 0}[v]" + ("[a]" if audio else ""))
    novo = trabalho / "_montagem_nova.mp4"
    novo.unlink(missing_ok=True)
    cmd = ["ffmpeg", "-y", "-hide_banner", "-v", "error", "-i", str(mont), "-filter_complex", ";".join(fc), "-map", "[v]"]
    if audio:
        cmd += ["-map", "[a]", "-c:a", "aac", "-b:a", "192k"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "17", "-pix_fmt", "yuv420p", str(novo)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not novo.exists() or novo.stat().st_size < 1000:
        raise RuntimeError("Não consegui cortar o trecho. O vídeo anterior foi mantido.")
    dt = b - a

    def mover(t):  # tempo antigo -> novo
        return t if t <= a else (a if t < b else t - dt)

    pj = trabalho / "legenda.json"
    if pj.exists():
        novas = []
        for w in json.loads(pj.read_text(encoding="utf-8")):
            meio = (w["s"] + w["e"]) / 2
            if a <= meio <= b:
                continue  # a palavra estava no trecho tirado
            w = dict(w)
            w["s"], w["e"] = round(mover(w["s"]), 3), round(max(mover(w["e"]), mover(w["s"]) + 0.05), 3)
            novas.append(w)
        pj.write_text(json.dumps(novas, ensure_ascii=False), encoding="utf-8")
    pa = trabalho / "audio.json"
    d = json.loads(pa.read_text(encoding="utf-8"))
    if d.get("efeitos"):
        d["efeitos"] = [[arq, round(mover(t), 3)] for arq, t in d["efeitos"] if not (a <= t <= b)]
    if d.get("realces"):
        d["realces"] = [[round(mover(x), 3), round(mover(y), 3)] for x, y in d["realces"] if mover(y) - mover(x) > 0.05]
    if d.get("nomes"):
        d["nomes"] = [[round(mover(x), 3), round(mover(y), 3), *r] for x, y, *r in d["nomes"] if mover(y) - mover(x) > 0.3]
    if d.get("inicios"):
        d["inicios"] = [round(mover(t), 3) for t in d["inicios"]]
    if d.get("inicio_final") is not None:
        d["inicio_final"] = round(mover(d["inicio_final"]), 3)
    if d.get("fim_musica") is not None:
        d["fim_musica"] = round(max(0.5, d["fim_musica"] - dt), 3)
    pa.write_text(json.dumps(d), encoding="utf-8")
    os.replace(novo, mont)
    return dt


def voltar_cortes(trabalho: Path):
    """Desfaz todos os cortes de trecho: volta a montagem, a legenda e o áudio de antes do primeiro corte."""
    ok = False
    for nome in ("montagem.mp4", "legenda.json", "audio.json"):
        orig = trabalho / (Path(nome).stem + "_original" + Path(nome).suffix)
        if orig.exists():
            os.replace(orig, trabalho / nome)
            ok = True
    return ok


def editar(nome, videos, trabalho: Path, cfg, musica=None, encerramento=None, avisar=None, logo=None, titulo=None,
           cortes=None):
    """Faz tudo: análise, montagem e render. Devolve os arquivos e o estado."""
    trabalho.mkdir(parents=True, exist_ok=True)
    render.CANCELAR.clear()
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
