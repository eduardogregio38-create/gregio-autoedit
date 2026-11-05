"""Renderização com FFmpeg: blocos tratados, transições, música e legendas."""

import os
import subprocess
from pathlib import Path

FORMATOS = {"vertical": (1080, 1920), "horizontal": (1920, 1080), "quadrado": (1080, 1080)}
BASE = Path(__file__).parent


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(r.stderr[-3000:])
        raise RuntimeError("FFmpeg falhou: " + " ".join(cmd[:6]) + " ...")


def duracao(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return float(out.strip())


def caminho_filtro(p):
    """Caminho dentro de um filtro do FFmpeg (no Windows o 'C:' precisa de escape)."""
    return str(p).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def _modelo_rnnoise():
    p = Path(os.environ.get("AUTOEDIT_RNNOISE", BASE / "modelos" / "sh.rnnn"))
    return p if p.exists() else None


def filtro_voz(cfg):
    """Cadeia de tratamento da voz: tira ronco grave, ruído de fundo,
    som abafado e chiado de 's', comprime e normaliza o volume."""
    modelo = _modelo_rnnoise() if cfg["audio"]["reducao_ruido"] == "rnnoise" else None
    reducao = f"arnndn=m='{caminho_filtro(modelo)}':mix=0.9" if modelo else "afftdn=nr=14:nf=-35:tn=1"
    return ",".join([
        "highpass=f=85",
        reducao,
        "equalizer=f=250:t=q:w=1.2:g=-3",     # menos som de caixa/abafado
        "equalizer=f=3200:t=q:w=1.4:g=2.5",   # mais clareza na voz
        "equalizer=f=10000:t=h:w=1:g=1.5",    # um pouco de brilho
        "deesser=i=0.35",
        "acompressor=threshold=-21dB:ratio=3:attack=8:release=160:makeup=2",
        "loudnorm=I=-16:TP=-1.5:LRA=9",
        "aresample=48000",
        "aformat=channel_layouts=stereo",
    ])


_TEM_ZSCALE = None


def _tonemap():
    """Converte vídeo HDR (padrão do iPhone) para cores normais (SDR).
    Sem isso o vídeo final fica com cores lavadas e acinzentadas."""
    global _TEM_ZSCALE
    if _TEM_ZSCALE is None:
        out = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace").stdout
        _TEM_ZSCALE = " zscale " in out and " tonemap " in out
    if not _TEM_ZSCALE:
        return ""
    return ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
            "tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p,")


def tamanho(cfg):
    return FORMATOS[cfg["formato"]]


def qualidade(cfg):
    """Um único render em qualidade final (a prévia já é o vídeo pronto)."""
    return ["-preset", cfg.get("preset_x264", "veryfast"), "-crf", str(cfg.get("crf", 20)),
            "-threads", str(cfg.get("_threads", 0))]


def filtro_video(cfg, larg_src, alt_src, tag="x", hdr=False):
    W, H = tamanho(cfg)
    pre = _tonemap() if hdr else ""
    fps = cfg["fps"]
    fim = f"fps={fps},format=yuv420p,settb=AVTB"
    proporcao_src = larg_src / alt_src
    proporcao_dst = W / H
    muito_diferente = max(proporcao_src, proporcao_dst) / min(proporcao_src, proporcao_dst) > 1.3
    if cfg.get("enquadramento") == "fundo_desfocado" and muito_diferente:
        return (f"{pre}split=2[bg{tag}][fg{tag}];"
                f"[bg{tag}]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=30:5[bgb{tag}];"
                f"[fg{tag}]scale={W}:{H}:force_original_aspect_ratio=decrease[fgs{tag}];"
                f"[bgb{tag}][fgs{tag}]overlay=(W-w)/2:(H-h)/2,setsar=1,{fim}")
    return f"{pre}scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,{fim}"


def render_bloco(bloco, indice, pasta: Path, cfg):
    """Renderiza um bloco (um take) já cortado, enquadrado e com áudio tratado."""
    it = bloco["item"]
    destino = pasta / f"{indice + 1:02d}_{bloco['tipo']}_{it['path'].stem}.mp4"
    cmd = ["ffmpeg", "-y", "-hide_banner"]
    for t in bloco["trechos"]:
        cmd += ["-ss", f"{t['ini']:.3f}", "-t", f"{t['fim'] - t['ini']:.3f}", "-i", str(it["path"])]

    n = len(bloco["trechos"])
    coberturas = bloco.get("coberturas", [])
    for c in coberturas:
        cmd += ["-ss", f"{c['ini']:.3f}", "-t", f"{c['fim'] - c['ini']:.3f}", "-i", str(c["item"]["path"])]
    m = len(coberturas)

    partes = []
    for k in range(n):
        partes.append(f"[{k}:v]{filtro_video(cfg, it['largura'], it['altura'], f't{k}', it.get('hdr', False))}[v{k}]")

    ambiente_db = cfg["audio"]["som_ambiente_takes_local_db"]
    usa_audio_original = it["tem_audio"] and (bloco["tipo"] in ("fala", "encerramento") or ambiente_db > -90)

    if usa_audio_original:
        for k in range(n):
            partes.append(f"[{k}:a]aresample=48000,aformat=channel_layouts=stereo[a{k}]")
        entrada = "".join(f"[v{k}][a{k}]" for k in range(n))
        partes.append(f"{entrada}concat=n={n}:v=1:a=1[vc][ac]")
        if bloco["tipo"] == "fala":
            partes.append(f"[ac]{filtro_voz(cfg)}[aout]")
        elif bloco["tipo"] == "encerramento":
            partes.append("[ac]aresample=48000[aout]")
        else:
            partes.append(f"[ac]afftdn=nr=10,volume={ambiente_db}dB,aresample=48000[aout]")
    else:
        total = sum(t["fim"] - t["ini"] for t in bloco["trechos"])
        cmd += ["-f", "lavfi", "-t", f"{total:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
        entrada = "".join(f"[v{k}]" for k in range(n))
        partes.append(f"{entrada}concat=n={n}:v=1:a=0[vc]")
        partes.append(f"[{n + m}:a]anull[aout]")

    # cobertura: a imagem corta para o local enquanto a voz continua
    vant = "[vc]"
    for j, c in enumerate(coberturas):
        idx = n + j
        fim = c["t"] + (c["fim"] - c["ini"])
        vfc = filtro_video(cfg, c["item"]["largura"], c["item"]["altura"], f"c{j}", c["item"].get("hdr", False))
        partes.append(f"[{idx}:v]{vfc},setpts=PTS-STARTPTS+{c['t']:.3f}/TB[cb{j}]")
        partes.append(f"{vant}[cb{j}]overlay=enable='between(t,{c['t']:.3f},{fim:.3f})':eof_action=pass[vo{j}]")
        vant = f"[vo{j}]"

    # transição "desfoque": a imagem desfoca no fim e começa desfocada (como no Dia de Campo)
    total = sum(t["fim"] - t["ini"] for t in bloco["trechos"])
    d = min(cfg["transicoes"].get("duracao_desfoque_seg", 0.35), total / 3)
    ini_blur, fim_blur = bloco.get("desfoque_inicio"), bloco.get("desfoque_fim")
    if ini_blur or fim_blur:
        sig = cfg["transicoes"].get("intensidade_desfoque", 30)
        partes.append(f"{vant}split=3[s0][s1][s2]")
        base = "[s0]"
        if ini_blur:
            partes.append(f"[s1]trim=0:{d:.3f},setpts=PTS-STARTPTS,gblur=sigma={sig},format=yuva420p,"
                          f"fade=t=out:st=0:d={d:.3f}:alpha=1[bi]")
            partes.append(f"{base}[bi]overlay=eof_action=pass[xb1]")
            base = "[xb1]"
        else:
            partes.append("[s1]nullsink")
        if fim_blur:
            partes.append(f"[s2]trim=start={total - d:.3f},setpts=PTS-STARTPTS,gblur=sigma={sig},format=yuva420p,"
                          f"fade=t=in:st=0:d={d:.3f}:alpha=1,setpts=PTS+{total - d:.3f}/TB[bo]")
            partes.append(f"{base}[bo]overlay=eof_action=pass[xb2]")
            base = "[xb2]"
        else:
            partes.append("[s2]nullsink")
        partes.append(f"{base}format=yuv420p[vblur]")
        vant = "[vblur]"

    cmd += [
        "-filter_complex", ";".join(partes),
        "-map", vant, "-map", "[aout]",
        "-c:v", "libx264", *qualidade(cfg),
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart", str(destino),
    ]
    run(cmd)

    # áudio limpo em WAV, usado na timeline do Premiere/DaVinci
    wav = destino.with_suffix(".wav")
    if cfg.get("gerar_pacote", True):
        run(["ffmpeg", "-y", "-v", "error", "-i", str(destino), "-vn", "-c:a", "pcm_s16le", str(wav)])

    bloco["arquivo"] = destino
    bloco["wav"] = wav
    bloco["duracao_render"] = duracao(destino)
    return destino


def escolher_transicoes(roteiro, cfg):
    """Como nos vídeos de referência:
    - entre imagens do local seguidas: corte seco
    - entre a pessoa e as imagens (e entre falas): desfoque
    - para a tela de encerramento: corte seco (a animação já está nela)
    Marca nos blocos onde o desfoque acontece, por isso roda antes de renderizar."""
    tr = cfg["transicoes"]
    escolhas = []
    for a, b in zip(roteiro, roteiro[1:]):
        if b["tipo"] == "encerramento":
            t = tr.get("para_encerramento", "corte")
        elif a["tipo"] == "local" and b["tipo"] == "local":
            t = tr.get("entre_imagens", "corte")
        else:
            t = tr.get("entre_cenas", "desfoque")
        if t == "desfoque":
            a["desfoque_fim"] = True
            b["desfoque_inicio"] = True
        escolhas.append(t)
    return escolhas


def juntar_com_transicoes(roteiro, tipos, destino: Path, cfg):
    """Junta os blocos: corte seco (concat) ou transição (xfade + acrossfade).
    Devolve o instante em que cada bloco começa no vídeo final."""
    d_padrao = cfg["transicoes"]["duracao_seg"]
    durs = [b["duracao_render"] for b in roteiro]

    cmd = ["ffmpeg", "-y", "-hide_banner"]
    for b in roteiro:
        cmd += ["-i", str(b["arquivo"])]

    if len(roteiro) == 1:
        partes = ["[0:v]null[v]", "[0:a]anull[a]"]
        inicios = [0.0]
        total = durs[0]
    else:
        partes = []
        # mesmo timebase em tudo, senão o xfade recusa misturar com cortes (concat)
        for k in range(len(roteiro)):
            partes.append(f"[{k}:v]fps={cfg['fps']},format=yuv420p,settb=AVTB[in{k}]")
        vant, aant = "[in0]", "[0:a]"
        acumulado = durs[0]
        inicios = [0.0]
        for k in range(1, len(roteiro)):
            tipo = tipos[k - 1]
            vout, aout = f"[vx{k}]", f"[ax{k}]"
            if tipo == "corte":
                offset = acumulado
                partes.append(f"{vant}[in{k}]concat=n=2:v=1:a=0,settb=AVTB{vout}")
                partes.append(f"{aant}[{k}:a]concat=n=2:v=0:a=1{aout}")
            else:
                base = cfg["transicoes"].get("duracao_desfoque_seg", 0.35) if tipo == "desfoque" else d_padrao
                d = round(min(base, durs[k - 1] / 3, durs[k] / 3), 3)
                offset = acumulado - d
                efeito = "fade" if tipo == "desfoque" else tipo
                partes.append(f"{vant}[in{k}]xfade=transition={efeito}:duration={d}:offset={offset:.3f},settb=AVTB{vout}")
                partes.append(f"{aant}[{k}:a]acrossfade=d={d}:c1=tri:c2=tri{aout}")
            inicios.append(offset)
            acumulado = offset + durs[k]
            vant, aant = vout, aout
        total = acumulado
        partes.append(f"{vant}null[v]")
        partes.append(f"{aant}anull[a]")

    if cfg["transicoes"].get("fade_inicio_fim", True):
        partes[-2] = partes[-2].replace("null[v]", f"fade=t=in:st=0:d=0.4,fade=t=out:st={max(0, total - 0.7):.3f}:d=0.7[v]")
        partes[-1] = partes[-1].replace("anull[a]", f"afade=t=in:st=0:d=0.3,afade=t=out:st={max(0, total - 0.7):.3f}:d=0.7[a]")

    cmd += [
        "-filter_complex", ";".join(partes),
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", *qualidade(cfg), "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart", str(destino),
    ]
    run(cmd)
    return inicios


def finalizar(video: Path, musica, srt, destino: Path, cfg, fim_musica=None):
    """Mistura a música com ducking (baixa sozinha quando alguém fala),
    normaliza o volume final e, se pedido, grava a legenda na imagem."""
    total = duracao(video)
    fim_m = min(fim_musica or total, total)
    a = cfg["audio"]
    cmd = ["ffmpeg", "-y", "-hide_banner", "-i", str(video)]
    partes = []

    if musica:
        cmd += ["-stream_loop", "-1", "-i", str(musica)]
        partes.append(
            f"[1:a]aresample=48000,aformat=channel_layouts=stereo,volume={a['volume_musica_db']}dB,"
            f"afade=t=in:st=0:d=1.0,afade=t=out:st={max(0, fim_m - 1.5):.3f}:d=1.5,"
            f"atrim=0:{fim_m:.3f},apad=whole_dur={total:.3f}[m]"
        )
        if a["ducking"]:
            partes.append("[0:a]asplit=2[voz][chave]")
            partes.append("[m][chave]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=400[md]")
            partes.append("[voz][md]amix=inputs=2:duration=first:normalize=0[mix]")
        else:
            partes.append("[0:a][m]amix=inputs=2:duration=first:normalize=0[mix]")
    else:
        partes.append("[0:a]anull[mix]")

    partes.append(f"[mix]loudnorm=I={a['loudness_final_lufs']}:TP=-1.5:LRA=11,"
                  "aresample=48000,alimiter=limit=0.95[aout]")

    queimar = srt and cfg["legendas"]["queimar_no_video"]
    if queimar:
        lg = cfg["legendas"]
        margem = lg.get("margem_inferior", 60 if cfg["formato"] == "vertical" else 25)
        caminho = caminho_filtro(srt)
        pasta_fontes = caminho_filtro(BASE.parent / "assets" / "fonts")
        estilo = (f"FontName={lg.get('fonte', 'DejaVu Sans')},FontSize={lg.get('tamanho', 13)},"
                  f"Bold={1 if lg.get('negrito', False) else 0},"
                  f"PrimaryColour={lg.get('cor', '&H00FFFFFF')},OutlineColour={lg.get('cor_contorno', '&H00000000')},"
                  f"BorderStyle=1,Outline={lg.get('contorno', 2)},Shadow={lg.get('sombra', 0)},"
                  f"Alignment=2,MarginV={margem}")
        partes.append(f"[0:v]subtitles='{caminho}':fontsdir='{pasta_fontes}':force_style='{estilo}'[vout]")
        mapa_v = ["-map", "[vout]", "-c:v", "libx264", *qualidade(cfg), "-pix_fmt", "yuv420p"]
    else:
        mapa_v = ["-map", "0:v", "-c:v", "copy"]

    cmd += ["-filter_complex", ";".join(partes), *mapa_v,
            "-map", "[aout]", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-t", f"{total:.3f}", "-movflags", "+faststart", str(destino)]
    run(cmd)
    return destino
