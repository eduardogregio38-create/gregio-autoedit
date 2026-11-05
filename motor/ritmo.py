"""Corte no ritmo da música: acha as batidas da trilha e ajusta a duração
das imagens do local para que cada corte caia em cima de uma batida."""

import subprocess

import numpy as np

SR = 22050
HOP = 512


def _ler_audio(path, max_seg=240):
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-t", str(max_seg), "-i", str(path),
         "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(out, dtype=np.float32)


def _forca_de_ataque(y):
    """Envelope de ataques (spectral flux): sobe quando entra uma batida ou nota."""
    n_fft = 2048
    if len(y) < n_fft * 4:
        return np.zeros(1)
    janela = np.hanning(n_fft).astype(np.float32)
    quadros = 1 + (len(y) - n_fft) // HOP
    idx = np.arange(n_fft)[None, :] + HOP * np.arange(quadros)[:, None]
    espectro = np.abs(np.fft.rfft(y[idx] * janela, axis=1))
    log = np.log1p(100 * espectro)
    fluxo = np.maximum(0, np.diff(log, axis=0)).sum(axis=1)
    fluxo = fluxo - np.convolve(fluxo, np.ones(16) / 16, mode="same")  # tira a tendência
    fluxo = np.maximum(fluxo, 0)
    return fluxo / (fluxo.max() + 1e-9)


def batidas(path):
    """Devolve (lista de tempos das batidas em segundos, BPM)."""
    try:
        env = _forca_de_ataque(_ler_audio(path))
    except Exception as e:
        print("  ritmo: não consegui ler a música:", e)
        return [], 0
    if len(env) < 200:
        return [], 0
    fps = SR / HOP

    # tempo: autocorrelação, com preferência suave por ~110 BPM
    ac = np.correlate(env, env, mode="full")[len(env) - 1:]
    lags = np.arange(len(ac))
    bpm_lag = 60 * fps / np.maximum(lags, 1)
    valido = (bpm_lag >= 70) & (bpm_lag <= 170)
    peso = np.exp(-0.5 * (np.log2(np.maximum(bpm_lag, 1) / 110) / 0.9) ** 2)
    score = np.where(valido, ac * peso, 0)
    lag = int(np.argmax(score))
    if lag <= 1:
        return [], 0
    # interpolação parabólica: período com fração de quadro (evita o ritmo "escorregar")
    a, b, c = ac[lag - 1], ac[lag], ac[lag + 1] if lag + 1 < len(ac) else ac[lag]
    den = a - 2 * b + c
    lag_f = lag + (0.5 * (a - c) / den if den != 0 else 0.0)
    bpm = 60 * fps / lag_f

    # fase: o deslocamento que mais coincide com ataques
    melhor, fase = -1, 0
    inicio_env = env[: int(12 * fps)]  # só o começo: com período fracionado não escorrega
    for off in range(lag + 1):
        idx = (off + lag_f * np.arange(int(len(inicio_env) / lag_f))).astype(int)
        idx = idx[idx < len(inicio_env)]
        s = inicio_env[idx].sum()
        if s > melhor:
            melhor, fase = s, off

    # segue as batidas: cada uma é procurada perto da anterior + período (±8%)
    tol = max(2, int(lag_f * 0.15))
    atraso = (2048 / 2 + HOP) / SR  # o pico do envelope vem um pouco antes do ataque real
    tempos = []
    pos = float(fase)
    while pos < len(env):
        a, b = max(0, int(pos) - tol), min(len(env), int(pos) + tol + 1)
        trecho = env[a:b]
        pico = a + int(np.argmax(trecho)) if trecho.max() > 0.15 else int(round(pos))
        tempos.append(pico / fps + atraso)
        pos = 0.8 * (pico + lag_f) + 0.2 * (pos + lag_f)  # puxa para o pico sem perder o andamento
    return tempos, bpm


def _overlap(transicao, cfg, dur_a, dur_b):
    if transicao == "corte":
        return 0.0
    base = cfg["transicoes"].get("duracao_desfoque_seg", 0.35) if transicao == "desfoque" \
        else cfg["transicoes"].get("duracao_seg", 0.5)
    return min(base, dur_a / 3, dur_b / 3)


def ajustar(roteiro, transicoes, tempos, cfg):
    """Ajusta o fim de cada imagem do local para cair numa batida.
    As falas nunca são mexidas. Devolve quantos cortes foram sincronizados."""
    if not tempos:
        return 0
    r = cfg.get("ritmo", {})
    dmin, dmax = r.get("duracao_min_seg", 2.0), r.get("duracao_max_seg", 4.0)
    alvo = cfg["selecao"].get("duracao_local_seg", 3.0)
    tempos = np.array(tempos)

    def dur(b):
        return sum(t["fim"] - t["ini"] for t in b["trechos"])

    inicio, sincronizados = 0.0, 0
    for k, b in enumerate(roteiro):
        if b["tipo"] == "local" and len(b["trechos"]) == 1:
            t = b["trechos"][0]
            disponivel = b["item"]["duracao"] - 0.05
            atual = t["fim"] - t["ini"]
            cand = tempos[(tempos >= inicio + dmin) & (tempos <= inicio + max(dmax, atual + 0.3))]
            if len(cand):
                fim_cena = cand[np.argmin(np.abs(cand - (inicio + max(alvo, min(atual, dmax + 1)))))]
                nova = float(fim_cena - inicio)
                # se a transição seguinte sobrepõe, o corte percebido fica no meio dela
                if k < len(transicoes) and transicoes[k] != "corte":
                    nova += _overlap(transicoes[k], cfg, nova, dur(roteiro[k + 1])) / 2
                if nova <= disponivel:
                    ini = t["ini"]
                    if ini + nova > disponivel:
                        ini = max(0.0, disponivel - nova)
                    t["ini"], t["fim"] = ini, ini + nova
                    sincronizados += 1
        d = dur(b)
        if k < len(transicoes):
            d -= _overlap(transicoes[k], cfg, d, dur(roteiro[k + 1]))
        inicio += d
    return sincronizados
