"""Análise dos takes: fala (Whisper) e qualidade de imagem (OpenCV)."""

import json
import subprocess
from pathlib import Path

import cv2
import numpy as np

# Palavras de hesitação que não devem virar um trecho sozinhas.
HESITACOES = {"é", "ã", "ah", "ahn", "hum", "hmm", "eh", "uh", "tá", "né", "então", "tipo", "assim"}

_modelo = None
import threading
_TRAVA_MODELO = threading.Lock()


class ErroUsuario(Exception):
    """Erro com mensagem pronta para a pessoa ler (o que aconteceu e o que fazer)."""


def sondar(path: Path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, check=True, encoding="utf-8", errors="replace",
    ).stdout
    info = json.loads(out)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    rot = 0
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = int(sd["rotation"])
    rot = int(v.get("tags", {}).get("rotate", rot))
    w, h = int(v["width"]), int(v["height"])
    if abs(rot) in (90, 270):
        w, h = h, w
    num, den = (v.get("avg_frame_rate") or "30/1").split("/")
    fps = float(num) / float(den) if float(den) else 30.0
    hdr = v.get("color_transfer") in ("arib-std-b67", "smpte2084")  # HLG / PQ (iPhone grava assim)
    return {
        "hdr": hdr,
        "duracao": float(info["format"]["duration"]),
        "largura": w,
        "altura": h,
        "fps": fps or 30.0,
        "tem_audio": any(s["codec_type"] == "audio" for s in info["streams"]),
    }


def transcrever(path: Path, cfg, cache_dir: Path):
    """Retorna lista de palavras [{w, s, e, p}] e média de confiança.
    O resultado fica em cache (JSON) para não transcrever duas vezes."""
    vocab = [v.strip() for v in cfg.get("vocabulario", []) if v and v.strip()]
    import hashlib
    import os
    modelo_id = os.path.basename(os.environ.get("AUTOEDIT_WHISPER") or cfg["whisper_modelo"])
    # o cache vale só para o mesmo vocabulário E o mesmo modelo de voz (trocar de modelo refaz a transcrição)
    sufixo = "." + hashlib.md5(("|".join(vocab) + "#" + modelo_id).encode("utf-8")).hexdigest()[:6]
    cache = cache_dir / (path.stem + sufixo + ".transcricao.json")
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))

    global _modelo
    with _TRAVA_MODELO:
        if _modelo is None:
            import os
            from faster_whisper import WhisperModel
            modelo = os.environ.get("AUTOEDIT_WHISPER") or cfg["whisper_modelo"]
            w = max(1, int(cfg.get("_whisper_workers", 1)))
            _modelo = WhisperModel(modelo, device="cpu", compute_type="int8",
                                   cpu_threads=max(1, (os.cpu_count() or 4) // w), num_workers=w)

    segs, _ = _modelo.transcribe(
        str(path),
        language=cfg["idioma"],
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 400},
        word_timestamps=True,
        condition_on_previous_text=False,
        beam_size=cfg.get("whisper_beam", 1),
        initial_prompt=("Vocabulário: " + ", ".join(vocab) + ".") if vocab else None,
    )
    palavras, logprobs = [], []
    for seg in segs:
        if seg.no_speech_prob > 0.6:
            continue
        logprobs.append(seg.avg_logprob)
        for w in seg.words or []:
            palavras.append({"w": w.word.strip(), "s": round(w.start, 3),
                             "e": round(w.end, 3), "p": round(w.probability, 3)})
    resultado = {
        "palavras": palavras,
        "confianca": float(np.mean(logprobs)) if logprobs else -9.0,
    }
    cache.write_text(json.dumps(resultado, ensure_ascii=False), encoding="utf-8")
    return resultado


def qualidade_visual(path: Path, amostras_por_seg=1.0, detalhes=False):
    """Nota de imagem ao longo do tempo: nitidez, exposição e tremor.
    Retorna lista [(tempo, nota)] com nota de 0 a 1."""
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    passo = max(1, int(round(fps / amostras_por_seg)))

    dados, anterior, assin = [], None, []
    for idx in range(0, total, passo):
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if not ok:
            break
        h, w = frame.shape[:2]
        pequeno = cv2.resize(frame, (320, int(320 * h / w)))
        cinza = cv2.cvtColor(pequeno, cv2.COLOR_BGR2GRAY)

        nitidez = cv2.Laplacian(cinza, cv2.CV_64F).var()
        brilho = cinza.mean() / 255
        movimento = 0.0 if anterior is None else float(np.mean(cv2.absdiff(cinza, anterior))) / 255
        anterior = cinza
        dados.append((idx / fps, nitidez, brilho, movimento))
        if detalhes:  # assinatura de cor (para escolher imagens de lugares diferentes)
            hsv = cv2.cvtColor(cv2.resize(pequeno, (64, 64)), cv2.COLOR_BGR2HSV)
            h = cv2.calcHist([hsv], [0, 1], None, [8, 4], [0, 180, 0, 256]).flatten()
            assin.append((idx / fps, (h / (h.sum() + 1e-9)).tolist()))
    cap.release()

    if not dados:
        return ([], 0.5, []) if detalhes else []

    nit = np.log1p([d[1] for d in dados])
    nit = (nit - nit.min()) / (np.ptp(nit) + 1e-6) if len(nit) > 1 else np.ones_like(nit)
    notas = []
    for (t, _, brilho, mov), n in zip(dados, nit):
        exposicao = 1 - min(1, abs(brilho - 0.5) * 2.2)      # foge de muito escuro/estourado
        estabilidade = 1 - min(1, max(0, mov - 0.02) * 12)    # pouco movimento ok, tremor não
        notas.append((t, 0.45 * n + 0.25 * exposicao + 0.30 * estabilidade))
    if detalhes:
        return notas, float(np.mean([d[2] for d in dados])), assin
    return notas


# ---------------- ÁUDIO: energia, ruído, vento ----------------

CLAQUETE_INICIO = {"gravando", "valendo", "ação", "acao", "gravou", "câmera", "camera"}
CLAQUETE_FIM = {"corta", "cortar", "pronto", "gravou", "cortou", "parou"}
HOP_ENERGIA = 0.01


def ler_audio_mono(path, sr=16000, max_seg=900):
    try:
        out = subprocess.run(["ffmpeg", "-v", "error", "-t", str(max_seg), "-i", str(path), "-vn", "-ac", "1",
                              "-ar", str(sr), "-f", "f32le", "-"], capture_output=True, check=True).stdout
        return np.frombuffer(out, dtype=np.float32), sr
    except Exception:
        return np.zeros(0, dtype=np.float32), sr


def analisar_audio(path):
    """Devolve a energia a cada 10 ms (para cortar nos pontos mais silenciosos), o nível do ruído de fundo
    em dB e se há rajadas de vento (muita energia abaixo de 120 Hz)."""
    y, sr = ler_audio_mono(path)
    if len(y) < sr:
        return {"energia": None, "ruido_db": -80.0, "vento": False}
    passo = int(sr * HOP_ENERGIA)
    n = len(y) // passo
    quadros = y[: n * passo].reshape(n, passo)
    rms = np.sqrt((quadros ** 2).mean(axis=1) + 1e-12)
    db = 20 * np.log10(rms + 1e-9)
    ruido_db = float(np.percentile(db, 10))
    # vento: janelas de 0,5 s, energia grave contra o total
    jan = sr // 2
    m = len(y) // jan
    vento_n = 0
    if m >= 2:
        for k in range(m):
            esp = np.abs(np.fft.rfft(y[k * jan:(k + 1) * jan])) ** 2
            freq = np.fft.rfftfreq(jan, 1 / sr)
            total = esp.sum() + 1e-12
            if esp[freq < 120].sum() / total > 0.6 and total > 1e-3:
                vento_n += 1
    return {"energia": rms.tolist(), "ruido_db": ruido_db, "vento": m >= 2 and vento_n / m > 0.25}


def ponto_silencioso(energia, t, antes, depois):
    """Ponto de menor energia entre t-antes e t+depois (segundos). Corta onde não tem som, sem estalo."""
    if not energia:
        return t
    i0 = max(0, int((t - antes) / HOP_ENERGIA))
    i1 = min(len(energia) - 1, int((t + depois) / HOP_ENERGIA))
    if i1 <= i0:
        return t
    trecho = energia[i0:i1 + 1]
    menor = min(trecho)
    # entre os pontos quase tão silenciosos, o mais perto do original
    cand = [i0 + k for k, v in enumerate(trecho) if v <= menor * 1.3 + 1e-6]
    melhor = min(cand, key=lambda i: abs(i * HOP_ENERGIA - t))
    return melhor * HOP_ENERGIA


def tirar_claquete(palavras, ini_util, fim_util):
    """Tira 'gravando', 'valendo' no começo e 'corta', 'pronto' no fim do take."""
    p = list(palavras)
    novo_ini, novo_fim = ini_util, fim_util

    def lim(x):
        return x["w"].lower().strip(".,!?;:\"'")
    k = 0
    while p and k < 3 and lim(p[0]) in CLAQUETE_INICIO | HESITACOES and p[0]["s"] < ini_util + 4:
        novo_ini = max(novo_ini, p[0]["e"] + 0.05)
        p.pop(0)
        k += 1
    k = 0
    while p and k < 3 and lim(p[-1]) in CLAQUETE_FIM | HESITACOES and p[-1]["e"] > fim_util - 4:
        novo_fim = min(novo_fim, p[-1]["s"] - 0.05)
        p.pop()
        k += 1
    if novo_fim - novo_ini < 0.5:
        return list(palavras), ini_util, fim_util
    return p, novo_ini, novo_fim


def faixa_util(duracao, corte):
    """Parte do take que fica na edição, em segundos (começo, fim). O que a pessoa cortou no começo
    e no final do take (por exemplo, um erro de fala) fica de fora de tudo: fala, imagens e capa."""
    ini, fim = 0.0, float(duracao)
    if corte:
        try:
            i = float(corte.get("ini") or 0.0)
            f = float(corte.get("fim") or duracao)
        except (TypeError, ValueError):
            return ini, fim
        i, f = min(max(i, 0.0), fim), min(max(f, 0.0), fim)
        if f - i >= 0.5:
            ini, fim = i, f
    return ini, fim


def corrigir_vocabulario(palavras, vocab):
    """Troca palavras que a transcrição errou parecido com um termo cadastrado (nome, marca, curso)."""
    from difflib import get_close_matches
    termos = {}
    for v in vocab or []:
        v = (v or "").strip()
        if v and " " not in v:
            termos[v.lower()] = v
    if not termos:
        return palavras
    saida = []
    for p in palavras:
        base = p["w"].strip(".,!?;:\"'")
        cola = p["w"][len(base.rstrip()):] if p["w"].startswith(base) else ""
        m = get_close_matches(base.lower(), list(termos), n=1, cutoff=0.8) if len(base) >= 4 else []
        if m and base.lower() != termos[m[0]].lower():
            p = dict(p, w=termos[m[0]] + cola)
        saida.append(p)
    return saida


def _um_take(ordem, v, cfg, cache_dir, cortes):
    print("  analisando", v.name)
    try:
        info = sondar(v)
    except Exception:
        raise ErroUsuario("O arquivo %s não abriu como vídeo. Ele pode estar corrompido ou num formato incomum. "
                          "Tente abri-lo em outro player; se não abrir lá também, grave ou exporte de novo." % v.name)
    if info["duracao"] < 0.5:
        raise ErroUsuario("O arquivo %s é curto demais (menos de meio segundo). Tire-o da lista." % v.name)
    limite = cfg.get("duracao_max_take_seg", 1800)
    if info["duracao"] > limite:
        raise ErroUsuario("O arquivo %s tem %d minutos, e o limite é %d minutos por take. Use o botão Cortar para "
                          "deixar só o trecho que importa, ou divida o vídeo." % (v.name, info["duracao"] // 60, limite // 60))
    trans = transcrever(v, cfg, cache_dir) if info["tem_audio"] else {"palavras": [], "confianca": -9.0}
    ini_util, fim_util = faixa_util(info["duracao"], (cortes or {}).get(v.name))
    trans = dict(trans, palavras=corrigir_vocabulario(trans["palavras"], cfg.get("vocabulario", [])))
    todas = [p for p in trans["palavras"] if p["s"] >= ini_util - 0.1 and p["e"] <= fim_util + 0.1]
    if cfg.get("selecao", {}).get("tirar_claquete", True):
        todas, ini_util, fim_util = tirar_claquete(todas, ini_util, fim_util)
    palavras_reais = [p for p in todas if p["w"].lower().strip(".,!?") not in HESITACOES]
    tempo_fala = sum(p["e"] - p["s"] for p in palavras_reais)
    clareza = (sum(p["p"] for p in palavras_reais) / len(palavras_reais)) if palavras_reais else 0.0
    notas, brilho, assin = qualidade_visual(v, detalhes=True)
    aud = analisar_audio(v) if info["tem_audio"] else {"energia": None, "ruido_db": -80.0, "vento": False}
    return {
        "path": v,
        "ordem": ordem,
        **info,
        "ini_util": ini_util,
        "fim_util": fim_util,
        "palavras": todas,
        "confianca": trans["confianca"],
        "texto": " ".join(p["w"] for p in todas).strip(),
        "tempo_fala": tempo_fala,
        "n_palavras": len(palavras_reais),
        "clareza": clareza,                                   # certeza média do Whisper nas palavras
        "densidade": tempo_fala / max(fim_util - ini_util, 0.1),  # quanto do take é fala
        "visual": [(t, n) for t, n in notas if ini_util <= t <= fim_util],
        "brilho": brilho,
        "assinaturas": [(t, h) for t, h in assin if ini_util <= t <= fim_util],
        "energia": aud["energia"],
        "ruido_db": aud["ruido_db"],
        "vento": aud["vento"],
    }


def analisar(videos, cfg, cache_dir: Path, progresso=None, cortes=None):
    """cortes: {nome do arquivo: {"ini": segundos, "fim": segundos}} (opcional).
    Vários takes são analisados ao mesmo tempo."""
    import os
    from concurrent.futures import ThreadPoolExecutor
    cache_dir.mkdir(parents=True, exist_ok=True)
    n = len(videos)
    paralelo = max(1, min(cfg.get("analise_paralela", 3), n, (os.cpu_count() or 2)))
    cfg = dict(cfg)
    cfg["_whisper_workers"] = paralelo
    feitos = [0]
    if progresso:
        progresso("Analisando os takes", 0, n)

    def um(arg):
        ordem, v = arg
        r = _um_take(ordem, v, cfg, cache_dir, cortes)
        feitos[0] += 1
        if progresso:
            progresso("Analisando os takes", feitos[0], n)
        return r

    with ThreadPoolExecutor(max_workers=paralelo) as ex:
        return list(ex.map(um, enumerate(videos)))
