"""Análise dos takes: fala (Whisper) e qualidade de imagem (OpenCV)."""

import json
import subprocess
from pathlib import Path

import cv2
import numpy as np

# Palavras de hesitação que não devem virar um trecho sozinhas.
HESITACOES = {"é", "ã", "ah", "ahn", "hum", "hmm", "eh", "uh", "tá", "né", "então", "tipo", "assim"}

_modelo = None


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
    cache = cache_dir / (path.stem + ".transcricao.json")
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))

    global _modelo
    if _modelo is None:
        import os
        from faster_whisper import WhisperModel
        modelo = os.environ.get("AUTOEDIT_WHISPER") or cfg["whisper_modelo"]
        _modelo = WhisperModel(modelo, device="cpu", compute_type="int8", cpu_threads=os.cpu_count() or 4)

    segs, _ = _modelo.transcribe(
        str(path),
        language=cfg["idioma"],
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 400},
        word_timestamps=True,
        condition_on_previous_text=False,
        beam_size=cfg.get("whisper_beam", 1),
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


def qualidade_visual(path: Path, amostras_por_seg=1.0):
    """Nota de imagem ao longo do tempo: nitidez, exposição e tremor.
    Retorna lista [(tempo, nota)] com nota de 0 a 1."""
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    passo = max(1, int(round(fps / amostras_por_seg)))

    dados, anterior = [], None
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
    cap.release()

    if not dados:
        return []

    nit = np.log1p([d[1] for d in dados])
    nit = (nit - nit.min()) / (np.ptp(nit) + 1e-6) if len(nit) > 1 else np.ones_like(nit)
    notas = []
    for (t, _, brilho, mov), n in zip(dados, nit):
        exposicao = 1 - min(1, abs(brilho - 0.5) * 2.2)      # foge de muito escuro/estourado
        estabilidade = 1 - min(1, max(0, mov - 0.02) * 12)    # pouco movimento ok, tremor não
        notas.append((t, 0.45 * n + 0.25 * exposicao + 0.30 * estabilidade))
    return notas


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


def analisar(videos, cfg, cache_dir: Path, progresso=None, cortes=None):
    """cortes: {nome do arquivo: {"ini": segundos, "fim": segundos}} (opcional)."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    itens = []
    for ordem, v in enumerate(videos):
        print("  analisando", v.name)
        if progresso:
            progresso("Analisando os takes", ordem, len(videos))
        info = sondar(v)
        trans = transcrever(v, cfg, cache_dir) if info["tem_audio"] else {"palavras": [], "confianca": -9.0}
        ini_util, fim_util = faixa_util(info["duracao"], (cortes or {}).get(v.name))
        todas = [p for p in trans["palavras"] if p["s"] >= ini_util - 0.1 and p["e"] <= fim_util + 0.1]
        palavras_reais = [p for p in todas if p["w"].lower().strip(".,!?") not in HESITACOES]
        tempo_fala = sum(p["e"] - p["s"] for p in palavras_reais)
        clareza = (sum(p["p"] for p in palavras_reais) / len(palavras_reais)) if palavras_reais else 0.0
        itens.append({
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
            "visual": [(t, n) for t, n in qualidade_visual(v) if ini_util <= t <= fim_util],
        })
    return itens
