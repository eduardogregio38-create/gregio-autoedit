"""Renderização com FFmpeg: blocos tratados, transições, música e legendas."""

import os
import re
import subprocess
from pathlib import Path

FORMATOS = {"vertical": (1080, 1920), "horizontal": (1920, 1080), "quadrado": (1080, 1080)}
BASE = Path(__file__).parent


import threading

CANCELAR = threading.Event()


class Cancelado(Exception):
    pass


def run(cmd):
    """Roda o FFmpeg; se a pessoa cancelar a edição, interrompe na hora."""
    if CANCELAR.is_set():
        raise Cancelado()
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                         errors="replace")
    saida = [""]

    def ler():
        saida[0] = p.stderr.read()
    t = threading.Thread(target=ler, daemon=True)
    t.start()
    while p.poll() is None:
        if CANCELAR.is_set():
            p.kill()
            p.wait()
            raise Cancelado()
        try:
            p.wait(timeout=0.4)
        except subprocess.TimeoutExpired:
            pass
    t.join(timeout=5)
    if p.returncode != 0:
        print(saida[0][-3000:])
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


NIVEIS_RUIDO = {"suave": 8, "medio": 14, "forte": 22}


def nivel_ruido(cfg, item=None):
    n = cfg["audio"].get("nivel_ruido", "auto")
    if n in NIVEIS_RUIDO:
        return n
    db = (item or {}).get("ruido_db", -60.0)   # auto: escolhe pelo ruído de fundo medido no take
    return "forte" if db > -38 else "medio" if db > -50 else "suave"


def filtro_voz(cfg, item=None, ganho_db=None):
    """Cadeia de tratamento da voz: tira ronco grave (e vento), ruído de fundo,
    som abafado e chiado de 's', comprime e acerta o volume.
    Com ganho_db, o volume é um ajuste fixo medido antes (igual entre os takes); sem ele, usa loudnorm."""
    modelo = _modelo_rnnoise() if cfg["audio"]["reducao_ruido"] == "rnnoise" else None
    nr = NIVEIS_RUIDO[nivel_ruido(cfg, item)]
    reducao = f"arnndn=m='{caminho_filtro(modelo)}':mix=0.9" if modelo else f"afftdn=nr={nr}:nf=-35:tn=1"
    vento = (item or {}).get("vento") and cfg["audio"].get("filtro_vento", True)
    partes = [
        "highpass=f=160,highpass=f=160" if vento else "highpass=f=85",
        reducao,
        "equalizer=f=250:t=q:w=1.2:g=-3",     # menos som de caixa/abafado
        "equalizer=f=3200:t=q:w=1.4:g=2.5",   # mais clareza na voz
        "equalizer=f=10000:t=h:w=1:g=1.5",    # um pouco de brilho
        "deesser=i=0.35",
        "acompressor=threshold=-21dB:ratio=3:attack=8:release=160:makeup=2",
    ]
    if ganho_db is None:
        partes.append("loudnorm=I=-16:TP=-1.5:LRA=9")
    else:
        partes += [f"volume={ganho_db:.2f}dB", "alimiter=limit=0.89"]
    partes += ["aresample=48000", "aformat=channel_layouts=stereo"]
    return ",".join(partes)


def medir_ganho_voz(bloco, cfg, alvo=-16.0):
    """Mede o volume da voz tratada (LUFS) e devolve o ajuste em dB para chegar ao alvo.
    Todos os takes ficam no mesmo volume, sem o efeito de 'bombeio' do ajuste dinâmico."""
    it = bloco["item"]
    if not it.get("tem_audio") or not cfg["audio"].get("igualar_volume", True):
        return None
    try:
        cmd = ["ffmpeg", "-hide_banner", "-nostats"]
        for t in bloco["trechos"]:
            cmd += ["-ss", f"{t['ini']:.3f}", "-t", f"{t['fim'] - t['ini']:.3f}", "-i", str(it["path"])]
        n = len(bloco["trechos"])
        partes = [f"[{k}:a]aresample=48000,aformat=channel_layouts=stereo[a{k}]" for k in range(n)]
        partes.append("".join(f"[a{k}]" for k in range(n)) + f"concat=n={n}:v=0:a=1[ac]")
        partes.append("[ac]" + filtro_voz(cfg, it, ganho_db=0.0) + ",ebur128=framelog=quiet[o]")
        cmd += ["-filter_complex", ";".join(partes), "-map", "[o]", "-f", "null", "-"]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        import re
        m = re.findall(r"I:\s*(-?\d+(?:\.\d+)?)\s*LUFS", r.stderr)
        if not m:
            return None
        lufs = float(m[-1])
        if lufs < -60:
            return None
        return max(-12.0, min(18.0, alvo - lufs))
    except Exception:
        return None


# ---------------- CODEC DE VÍDEO (chip do computador, se tiver) ----------------

_HW = {}


def _testar_codec(nome, extra):
    try:
        r = subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=640x360:d=0.5:r=30",
                            "-c:v", nome, *extra, "-pix_fmt", "yuv420p", "-f", "null", "-"],
                           capture_output=True, text=True, timeout=25)
        return r.returncode == 0
    except Exception:
        return False


def codec_video(cfg):
    """Devolve (argumentos do codec, usa_chip). Tenta VideoToolbox (Mac), NVENC, QuickSync e AMF (Windows);
    se nenhum funcionar, usa o libx264 de sempre."""
    if cfg.get("aceleracao", "auto") == "nenhuma":
        return None
    if "r" not in _HW:
        import sys
        lista = (["h264_videotoolbox"] if sys.platform == "darwin" else
                 ["h264_nvenc", "h264_qsv", "h264_amf"] if sys.platform.startswith("win") else
                 ["h264_nvenc"])
        extras = {"h264_videotoolbox": ["-b:v", "14M", "-allow_sw", "1"],
                  "h264_nvenc": ["-preset", "p5", "-rc", "vbr", "-cq", "22", "-b:v", "0"],
                  "h264_qsv": ["-global_quality", "22", "-preset", "medium"],
                  "h264_amf": ["-quality", "quality", "-rc", "cqp", "-qp_i", "21", "-qp_p", "22"]}
        _HW["r"] = None
        for nome in lista:
            if _testar_codec(nome, extras[nome]):
                _HW["r"] = ["-c:v", nome, *extras[nome]]
                print("  vídeo: usando o chip do computador (%s)" % nome)
                break
    return _HW["r"]


def args_video(cfg):
    hw = codec_video(cfg)
    if hw:
        return hw
    return ["-c:v", "libx264", "-preset", cfg.get("preset_x264", "veryfast"), "-crf", str(cfg.get("crf", 20)),
            "-threads", str(cfg.get("_threads", 0))]


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


def filtro_video(cfg, larg_src, alt_src, tag="x", hdr=False, brilho=None, estabilizar=False):
    W, H = tamanho(cfg)
    pre = _tonemap() if hdr else ""
    if estabilizar and cfg.get("video", {}).get("estabilizar", False):
        pre += "deshake=rx=32:ry=32:edge=mirror,"
    v = cfg.get("video", {})
    if brilho is not None and brilho < 0.30 and v.get("melhorar_escuro", True):  # take escuro: clareia e tira granulação
        pre += "hqdn3d=3:3:5:5,eq=gamma=1.25:brightness=0.04:contrast=1.05,"
    fps = cfg["fps"]
    nitidez = ""
    if v.get("nitidez_final", True) and larg_src and max(larg_src, alt_src) > max(W, H) * 1.25:
        nitidez = ",unsharp=5:5:0.45:3:3:0.0"   # reforço leve depois de reduzir 4K para 1080p
    fim = f"{nitidez.lstrip(',') + ',' if nitidez else ''}fps={fps},format=yuv420p,settb=AVTB"
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
        partes.append(f"[{k}:v]{filtro_video(cfg, it['largura'], it['altura'], f't{k}', it.get('hdr', False), it.get('brilho'), bloco['tipo'] == 'local')}[v{k}]")

    ambiente_db = cfg["audio"]["som_ambiente_takes_local_db"]
    usa_audio_original = it["tem_audio"] and (bloco["tipo"] in ("fala", "encerramento") or ambiente_db > -90)

    ganho = medir_ganho_voz(bloco, cfg) if usa_audio_original and bloco["tipo"] == "fala" else None
    if usa_audio_original:
        for k in range(n):  # fade curtinho nas emendas: sem estalo
            dk = bloco["trechos"][k]["fim"] - bloco["trechos"][k]["ini"]
            f = min(0.025, dk / 4)
            fade = f",afade=t=in:st=0:d={f:.3f},afade=t=out:st={max(0, dk - f):.3f}:d={f:.3f}" if cfg["audio"].get("fade_emendas", True) and dk > 0.15 else ""
            partes.append(f"[{k}:a]aresample=48000,aformat=channel_layouts=stereo{fade}[a{k}]")
        entrada = "".join(f"[v{k}][a{k}]" for k in range(n))
        partes.append(f"{entrada}concat=n={n}:v=1:a=1[vc][ac]")
        if bloco["tipo"] == "fala":
            partes.append(f"[ac]{filtro_voz(cfg, it, ganho)}[aout]")
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
        vfc = filtro_video(cfg, c["item"]["largura"], c["item"]["altura"], f"c{j}", c["item"].get("hdr", False), c["item"].get("brilho"), True)
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
        *args_video(cfg),
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart", str(destino),
    ]
    try:
        run(cmd)
    except RuntimeError:
        # alguns FFmpeg recusam o filtro de estabilização; refaz sem ele em vez de derrubar a edição inteira
        i = cmd.index("-filter_complex") + 1
        sem = re.sub(r"deshake=[^,]*,", "", cmd[i])
        if sem == cmd[i]:
            raise
        print("  estabilização não funcionou neste computador; seguindo sem ela")
        cmd[i] = sem
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
        *args_video(cfg), "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart", str(destino),
    ]
    run(cmd)
    return inicios


def _expr_realce(realces, db, rampa=0.4):
    """Ganho extra da música (em linear) nos trechos só com imagem, com subida e descida suaves."""
    g = 10 ** (db / 20) - 1
    termos = []
    for (a, b) in realces:
        r = min(rampa, max((b - a) / 2, 0.05))
        termos.append(f"clip(min((t-{a:.3f})/{r:.3f},({b:.3f}-t)/{r:.3f}),0,1)")
    return f"1+{g:.4f}*(" + "+".join(termos) + ")"


def finalizar(video: Path, musica, srt, destino: Path, cfg, fim_musica=None, realces=None, efeitos=None,
              inicio_musica=0.0, ganho_musica_db=0.0, ass=None):
    """Mistura a música com ducking (baixa sozinha quando alguém fala), sobe a música nos trechos só com
    imagem, coloca efeitos nas transições, normaliza o volume final e, se pedido, grava a legenda na imagem."""
    total = duracao(video)
    fim_m = min(fim_musica or total, total)
    a = cfg["audio"]
    cmd = ["ffmpeg", "-y", "-hide_banner", "-i", str(video)]
    partes = []
    entradas = 1

    if musica:
        dm = duracao(musica)
        util = max(dm - inicio_musica, 1.0)
        copias = 1 if util >= fim_m + 0.5 else min(8, int(fim_m // max(util - 2.0, 1.0)) + 2)
        for _ in range(copias):
            cmd += ["-ss", f"{inicio_musica:.3f}", "-i", str(musica)]
        base = "[1:a]"
        if copias > 1:  # música curta: repete emendando com fusão suave, sem corte seco
            ant = "[1:a]"
            for k in range(2, copias + 1):
                partes.append(f"{ant}[{k}:a]acrossfade=d=2:c1=tri:c2=tri[ml{k}]")
                ant = f"[ml{k}]"
            base = ant
        entradas += copias
        db = a["volume_musica_db"] + ganho_musica_db
        vol = f"volume={db}dB"
        if realces and a.get("musica_alta_sem_fala", True):
            vol = f"volume={db}dB,volume='{_expr_realce(realces[:40], a.get('realce_musica_db', 5))}':eval=frame"
        partes.append(
            f"{base}aresample=48000,aformat=channel_layouts=stereo,{vol},"
            f"afade=t=in:st=0:d=1.0,afade=t=out:st={max(0, fim_m - 1.5):.3f}:d=1.5,"
            f"atrim=0:{fim_m:.3f},apad=whole_dur={total:.3f}[m]"
        )
        if a["ducking"]:
            partes.append("[0:a]asplit=2[voz][chave]")
            partes.append("[m][chave]sidechaincompress=threshold=0.02:ratio=8:attack=20:release=1200[md]")
            ult = "[voz][md]"
        else:
            ult = "[0:a][m]"
        n_mix = 2
    else:
        ult, n_mix = "[0:a]", 1

    extras_fx = []
    for (arq, t) in (efeitos or [])[:20]:
        cmd += ["-i", str(arq)]
        partes.append(f"[{entradas}:a]aresample=48000,aformat=channel_layouts=stereo,volume=-10dB,"
                      f"adelay={int(max(0, t) * 1000)}|{int(max(0, t) * 1000)}[fx{entradas}]")
        extras_fx.append(f"[fx{entradas}]")
        entradas += 1
    if musica or extras_fx:
        partes.append(f"{ult}{''.join(extras_fx)}amix=inputs={n_mix + len(extras_fx)}:duration=first:normalize=0[mix]")
    else:
        partes.append("[0:a]anull[mix]")

    partes.append(f"[mix]loudnorm=I={a['loudness_final_lufs']}:TP=-1.5:LRA=11,"
                  "aresample=48000,alimiter=limit=0.95[aout]")

    queimar = (ass is not None) or (srt and cfg["legendas"]["queimar_no_video"] and cfg["legendas"].get("estilo", "destaque") == "simples")
    if queimar:
        lg = cfg["legendas"]
        pasta_fontes = caminho_filtro(BASE.parent / "assets" / "fonts")
        if ass is not None:
            extra = Path(ass).parent / "fontes_ass"
            if extra.is_dir():
                pasta_fontes = caminho_filtro(extra)
            partes.append(f"[0:v]ass='{caminho_filtro(ass)}':fontsdir='{pasta_fontes}'[vout]")
        else:
            margem = lg.get("margem_inferior", 60 if cfg["formato"] == "vertical" else 25)
            caminho = caminho_filtro(srt)
            estilo = (f"FontName={lg.get('fonte', 'DejaVu Sans')},FontSize={lg.get('tamanho', 13)},"
                      f"Bold={1 if lg.get('negrito', False) else 0},"
                      f"PrimaryColour={lg.get('cor', '&H00FFFFFF')},OutlineColour={lg.get('cor_contorno', '&H00000000')},"
                      f"BorderStyle=1,Outline={lg.get('contorno', 2)},Shadow={lg.get('sombra', 0)},"
                      f"Alignment=2,MarginV={margem}")
            partes.append(f"[0:v]subtitles='{caminho}':fontsdir='{pasta_fontes}':force_style='{estilo}'[vout]")
        mapa_v = ["-map", "[vout]", *args_video(cfg), "-pix_fmt", "yuv420p"]
    else:
        mapa_v = ["-map", "0:v", "-c:v", "copy"]

    cmd += ["-filter_complex", ";".join(partes), *mapa_v,
            "-map", "[aout]", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-t", f"{total:.3f}", "-movflags", "+faststart", str(destino)]
    run(cmd)
    return destino
