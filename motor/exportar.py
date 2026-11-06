"""Saídas para continuar a edição: legenda SRT, timeline XML
(Premiere e DaVinci Resolve), pacote de clipes para o CapCut e relatório."""

import csv
import shutil
from pathlib import Path
from urllib.parse import quote
from xml.sax.saxutils import escape

from render import FORMATOS


# ---------------- LEGENDAS ----------------

def _ts(t):
    t = max(0.0, t)
    h, r = divmod(t, 3600)
    m, s = divmod(r, 60)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int(round((s - int(s)) * 1000)) % 1000:03d}"


def palavras_na_timeline(roteiro, inicios):
    """Converte o tempo de cada palavra do take original para o tempo do vídeo final."""
    saida = []
    for bloco, inicio in zip(roteiro, inicios):
        rel = 0.0
        for t in bloco["trechos"]:
            for p in t["palavras"]:
                if t["ini"] <= p["s"] < t["fim"]:
                    s = inicio + rel + (p["s"] - t["ini"])
                    e = inicio + rel + (min(p["e"], t["fim"]) - t["ini"])
                    saida.append({"w": p["w"], "s": s, "e": e})
            rel += t["fim"] - t["ini"]
    return saida


def gerar_srt(roteiro, inicios, destino: Path, max_chars=32):
    return srt_de_palavras(palavras_na_timeline(roteiro, inicios), destino, max_chars)


def srt_de_palavras(palavras, destino: Path, max_chars=32):
    if not palavras:
        return None
    legendas, atual = [], []
    for i, p in enumerate(palavras):
        texto = " ".join(x["w"] for x in atual + [p])
        pausa = atual and p["s"] - atual[-1]["e"] > 0.5
        if atual and (len(texto) > max_chars or pausa or p.get("b")):  # "b": quebra de linha pedida pela pessoa
            legendas.append(atual)
            atual = []
        atual.append(p)
        if p["w"].endswith((".", "!", "?")):
            legendas.append(atual)
            atual = []
    if atual:
        legendas.append(atual)

    linhas = []
    for n, grupo in enumerate(legendas, 1):
        ini = grupo[0]["s"]
        fim = max(grupo[-1]["e"] + 0.15, ini + 0.8)
        if n < len(legendas):
            fim = min(fim, legendas[n][0]["s"] - 0.02)
        texto = " ".join(x["w"] for x in grupo).strip()
        linhas.append(f"{n}\n{_ts(ini)} --> {_ts(fim)}\n{texto}\n")
    destino.write_text("\n".join(linhas), encoding="utf-8")
    return destino


# ---------------- LEGENDA NO VÍDEO (ASS), NOME NA TELA E TEXTO FINAL ----------------

def _ass_t(t):
    t = max(0.0, t)
    h, r = divmod(t, 3600)
    m, s = divmod(r, 60)
    return "%d:%02d:%05.2f" % (h, m, s)


def _limpo(txt):
    return str(txt).replace("{", "(").replace("}", ")").replace("\\", "/").replace("\n", " ")


def _cor_ass(hexcor, padrao="&H0000D4FF"):
    """'#RRGGBB' -> '&H00BBGGRR'."""
    try:
        h = hexcor.lstrip("#")
        return "&H00%s%s%s" % (h[4:6], h[2:4], h[0:2])
    except Exception:
        return padrao


def agrupar_palavras(palavras, max_chars=24):
    grupos, atual = [], []
    for p in palavras:
        texto = " ".join(x["w"] for x in atual + [p])
        pausa = atual and p["s"] - atual[-1]["e"] > 0.5
        if atual and (len(texto) > max_chars or pausa or p.get("b")):  # "b": quebra de linha pedida pela pessoa
            grupos.append(atual)
            atual = []
        atual.append(p)
        if p["w"].endswith((".", "!", "?")):
            grupos.append(atual)
            atual = []
    if atual:
        grupos.append(atual)
    return grupos


def frases(palavras, max_chars=24):
    """As linhas de legenda como aparecem no vídeo: [{n, i0, i1, ini, fim, texto}], com o índice das palavras de cada uma."""
    out, i = [], 0
    for n, g in enumerate(agrupar_palavras(palavras, max_chars)):
        out.append({"n": n, "i0": i, "i1": i + len(g) - 1, "ini": round(g[0]["s"], 2), "fim": round(g[-1]["e"], 2),
                    "texto": " ".join(x["w"] for x in g)})
        i += len(g)
    return out


def editar_frases(palavras, edicoes, max_chars=24):
    """Aplica textos novos a linhas inteiras. edicoes = {n: "texto novo"}.
    Texto vazio apaga a linha. Enter no meio do texto quebra em duas linhas. Se o número de palavras for o mesmo,
    cada palavra mantém o seu tempo; senão as palavras novas dividem o tempo da linha igualmente."""
    palavras = [dict(p) for p in palavras]
    fr = {f["n"]: f for f in frases(palavras, max_chars)}
    for n in sorted((int(k) for k in edicoes), reverse=True):
        f = fr.get(n)
        if not f:
            continue
        linhas = [l.split() for l in str(edicoes[str(n)] if str(n) in edicoes else edicoes[n]).replace("\r", "").split("\n")]
        linhas = [l for l in linhas if l]
        tokens = [t[:60] for l in linhas for t in l]
        i0, i1 = f["i0"], f["i1"]
        if not tokens:
            del palavras[i0:i1 + 1]
            continue
        velhas = palavras[i0:i1 + 1]
        if len(linhas) == 1 and len(tokens) == len(velhas):
            for p_, t in zip(velhas, tokens):
                p_["w"] = t
            continue
        t0, t1 = velhas[0]["s"], velhas[-1]["e"]
        passo = max(0.05, (t1 - t0) / len(tokens))
        novas, k = [], 0
        for li, l in enumerate(linhas):
            for ti, t in enumerate(l):
                nova = {"w": t, "s": round(t0 + k * passo, 3), "e": round(t0 + (k + 1) * passo, 3), "p": 1.0}
                if li > 0 and ti == 0:
                    nova["b"] = True
                novas.append(nova)
                k += 1
        palavras[i0:i1 + 1] = novas
    return palavras


def _familia_fonte(arquivo, pasta_trabalho):
    """Nome da família e se é negrito, lidos do arquivo da fonte; copia as fontes para uma pasta que o FFmpeg lê."""
    import shutil
    import capa
    from PIL import ImageFont
    legado = {"Poppins|1": "Poppins-Bold.ttf", "Poppins Medium|0": "Poppins-Medium.ttf", "Poppins|0": "Poppins-Regular.ttf"}
    arquivo = legado.get(arquivo, arquivo) or "Poppins-Bold.ttf"
    f = capa.achar_fonte(arquivo) or capa.achar_fonte("Poppins-Bold.ttf")
    destino = pasta_trabalho / "fontes_ass"
    destino.mkdir(exist_ok=True)
    for orig in list(capa.FONTES.glob("*.ttf")) + ([f] if f else []):
        try:
            if not (destino / orig.name).exists():
                shutil.copy2(orig, destino / orig.name)
        except OSError:
            pass
    try:
        fam, estilo = ImageFont.truetype(str(f), 20).getname()
        return fam, (1 if "bold" in estilo.lower() else 0)
    except Exception:
        return "Poppins", 1


def gerar_ass(palavras, destino: Path, cfg, total, nomes=None, texto_final=None, inicio_final=None):
    """Arquivo de legenda estilizado: palavra por palavra com destaque, mais nome/cargo e texto da tela final.
    Devolve None se não houver nada para desenhar."""
    from render import tamanho
    W, H = tamanho(cfg)
    lg = cfg["legendas"]
    mostrar_leg = lg.get("queimar_no_video", False) and lg.get("estilo", "destaque") == "destaque" and palavras
    if not (mostrar_leg or nomes or texto_final):
        return None
    esc = W / 1080.0
    tam = int(lg.get("ass_tamanho", 84) * esc)
    margem = int(lg.get("ass_margem_inferior", 430 if H > W else 120))
    py = lg.get("pos_y")
    if isinstance(py, (int, float)) and not isinstance(py, bool):
        # posição livre (arrastada na tela): py é onde fica o centro do texto, de 0 (topo) a 1 (base)
        py = min(0.95, max(0.08, float(py)))
        alin, margem = 2, int(max(0, (1 - py) * H - tam * 0.6))
    elif lg.get("posicao", "baixo") == "meio":
        alin, margem = 5, 0
    else:
        alin = 2
    cor_dest = _cor_ass(lg.get("cor_destaque", "#FFD400"))
    cor_txt = _cor_ass(lg.get("cor_texto", "#FFFFFF"), "&H00FFFFFF")
    fam, neg = _familia_fonte(lg.get("fonte"), destino.parent)
    tam = int(tam * {"pequena": 0.8, "media": 1.0, "grande": 1.25}.get(lg.get("tamanho_ass", "media"), 1.0))
    cab = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Leg,{fam},{tam},{cor_txt},{cor_txt},&H00000000,&H80000000,{neg},0,0,0,100,100,0,0,1,{max(3, int(5 * esc))},2,{alin},{int(60 * esc)},{int(60 * esc)},{margem},1",
        f"Style: Nome,Poppins,{int(46 * esc)},&H00FFFFFF,&H00FFFFFF,&H00000000,&HB0000000,1,0,0,0,100,100,0,0,3,{int(14 * esc)},0,1,{int(70 * esc)},{int(70 * esc)},{int(300 * esc) if H > W else int(90 * esc)},1",
        f"Style: Cargo,Poppins,{int(34 * esc)},&H00DDDDDD,&H00DDDDDD,&H00000000,&HB0000000,0,0,0,0,100,100,0,0,3,{int(12 * esc)},0,1,{int(70 * esc)},{int(70 * esc)},{int(236 * esc) if H > W else int(40 * esc)},1",
        f"Style: Final,Poppins,{int(84 * esc)},{cor_dest},{cor_dest},&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,{max(4, int(6 * esc))},3,5,{int(80 * esc)},{int(80 * esc)},{int(H * 0.12)},1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    ev = []
    if mostrar_leg:
        grupos = agrupar_palavras(palavras, lg.get("max_caracteres", 24))
        for gi, g in enumerate(grupos):
            limite = grupos[gi + 1][0]["s"] - 0.02 if gi + 1 < len(grupos) else g[-1]["e"] + 0.25
            for k, p in enumerate(g):
                ini = p["s"]
                fim = g[k + 1]["s"] if k + 1 < len(g) else min(max(g[-1]["e"] + 0.2, ini + 0.3), limite)
                fim = max(fim, ini + 0.12)
                partes = []
                for j, q in enumerate(g):
                    w = _limpo(q["w"]).upper() if lg.get("maiusculas", False) else _limpo(q["w"])
                    partes.append(("{\\c%s\\fscx108\\fscy108}%s{\\r}" % (cor_dest, w)) if j == k else w)
                ev.append(f"Dialogue: 0,{_ass_t(ini)},{_ass_t(fim)},Leg,,0,0,0,,{' '.join(partes)}")
    for (t0, t1, nome, cargo) in (nomes or []):
        ev.append(f"Dialogue: 1,{_ass_t(t0)},{_ass_t(t1)},Nome,,0,0,0,,{{\\fad(250,250)}}{_limpo(nome)}")
        if cargo:
            ev.append(f"Dialogue: 1,{_ass_t(t0)},{_ass_t(t1)},Cargo,,0,0,0,,{{\\fad(250,250)}}{_limpo(cargo)}")
    if texto_final and inicio_final is not None:
        ev.append(f"Dialogue: 2,{_ass_t(inicio_final + 0.2)},{_ass_t(total)},Final,,0,0,0,,{{\\fad(300,0)}}{_limpo(texto_final)}")
    destino.write_text("\n".join(cab + ev) + "\n", encoding="utf-8")
    return destino


# ---------------- TIMELINE XML (Premiere / DaVinci) ----------------

def _rate(fps):
    return f"<rate><timebase>{fps}</timebase><ntsc>FALSE</ntsc></rate>"


def _url(pasta_rel, nome):
    return "file://localhost/" + quote(f"{pasta_rel}/{nome}")


def gerar_xml(roteiro, musica, destino: Path, cfg, nome_seq):
    """Timeline em FCP7 XML (xmeml), formato que o Premiere e o DaVinci importam.
    V1: takes ORIGINAIS com ponto de entrada/saída (sobra imagem para ajustar o corte).
    V2: imagens do local que cobrem a fala (a voz continua por baixo).
    A1: áudio já limpo de cada bloco. A2: música.
    Na timeline os blocos ficam em corte seco: as transições você aplica no editor."""
    fps = cfg["fps"]
    W, H = FORMATOS[cfg["formato"]]
    fr = lambda s: int(round(s * fps))

    arquivos_def = set()
    v_items, v2_items, a_items, m_items = [], [], [], []
    pos = 0
    cid = 0

    for bloco in roteiro:
        it = bloco["item"]
        nome = it["path"].name
        fid = "f_" + quote(it["path"].stem, safe="")
        rel = 0.0
        for t in bloco["trechos"]:
            cid += 1
            dur = t["fim"] - t["ini"]
            ini_tl = pos + fr(rel)
            fim_tl = ini_tl + fr(dur)
            if fid in arquivos_def:
                file_xml = f'<file id="{fid}"/>'
            else:
                arquivos_def.add(fid)
                file_xml = (
                    f'<file id="{fid}"><name>{escape(nome)}</name>'
                    f"<pathurl>{_url('Midias_Originais', nome)}</pathurl>{_rate(fps)}"
                    f"<duration>{fr(it['duracao'])}</duration>"
                    f"<media><video><samplecharacteristics><width>{it['largura']}</width>"
                    f"<height>{it['altura']}</height></samplecharacteristics></video>"
                    f"<audio><channelcount>2</channelcount></audio></media></file>"
                )
            v_items.append(
                f'<clipitem id="v{cid}"><name>{escape(nome)}</name><enabled>TRUE</enabled>'
                f"<duration>{fr(it['duracao'])}</duration>{_rate(fps)}"
                f"<start>{ini_tl}</start><end>{fim_tl}</end>"
                f"<in>{fr(t['ini'])}</in><out>{fr(t['ini']) + (fim_tl - ini_tl)}</out>"
                f"{file_xml}</clipitem>"
            )
            rel += dur

        for c in bloco.get("coberturas", []):
            ci = c["item"]
            cid += 1
            cfid = "f_" + quote(ci["path"].stem, safe="")
            cini_tl = pos + fr(c["t"])
            cfim_tl = cini_tl + fr(c["fim"] - c["ini"])
            if cfid in arquivos_def:
                cfile = f'<file id="{cfid}"/>'
            else:
                arquivos_def.add(cfid)
                cfile = (
                    f'<file id="{cfid}"><name>{escape(ci["path"].name)}</name>'
                    f"<pathurl>{_url('Midias_Originais', ci['path'].name)}</pathurl>{_rate(fps)}"
                    f"<duration>{fr(ci['duracao'])}</duration>"
                    f"<media><video><samplecharacteristics><width>{ci['largura']}</width>"
                    f"<height>{ci['altura']}</height></samplecharacteristics></video></media></file>"
                )
            v2_items.append(
                f'<clipitem id="c{cid}"><name>{escape(ci["path"].name)}</name><enabled>TRUE</enabled>'
                f"<duration>{fr(ci['duracao'])}</duration>{_rate(fps)}"
                f"<start>{cini_tl}</start><end>{cfim_tl}</end>"
                f"<in>{fr(c['ini'])}</in><out>{fr(c['ini']) + (cfim_tl - cini_tl)}</out>"
                f"{cfile}</clipitem>"
            )

        dur_bloco = fr(bloco["duracao_render"])
        if bloco["tipo"] == "fala":
            wav = bloco["wav"].name
            a_items.append(
                f'<clipitem id="a{cid}"><name>{escape(wav)}</name><enabled>TRUE</enabled>'
                f"<duration>{dur_bloco}</duration>{_rate(fps)}"
                f"<start>{pos}</start><end>{pos + dur_bloco}</end><in>0</in><out>{dur_bloco}</out>"
                f'<file id="w{cid}"><name>{escape(wav)}</name><pathurl>{_url("Audio_Limpo", wav)}</pathurl>'
                f"{_rate(fps)}<duration>{dur_bloco}</duration><media><audio><channelcount>2</channelcount>"
                f"<samplecharacteristics><depth>16</depth><samplerate>48000</samplerate></samplecharacteristics>"
                f"</audio></media></file>"
                f"<sourcetrack><mediatype>audio</mediatype><trackindex>1</trackindex></sourcetrack></clipitem>"
            )
        pos += fr(sum(t["fim"] - t["ini"] for t in bloco["trechos"]))

    total = pos
    if musica:
        from render import duracao
        dmus = fr(duracao(musica))
        t0, n = 0, 0
        while t0 < total and dmus > 0:
            n += 1
            fim = min(total, t0 + dmus)
            m_items.append(
                f'<clipitem id="m{n}"><name>{escape(musica.name)}</name><enabled>TRUE</enabled>'
                f"<duration>{dmus}</duration>{_rate(fps)}<start>{t0}</start><end>{fim}</end>"
                f"<in>0</in><out>{fim - t0}</out>"
                + (f'<file id="mus"><name>{escape(musica.name)}</name>'
                   f"<pathurl>{_url('Musica', musica.name)}</pathurl>{_rate(fps)}<duration>{dmus}</duration>"
                   f"<media><audio><channelcount>2</channelcount></audio></media></file>" if n == 1
                   else '<file id="mus"/>')
                + "<sourcetrack><mediatype>audio</mediatype><trackindex>1</trackindex></sourcetrack></clipitem>"
            )
            t0 = fim

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n<xmeml version="5">'
        f'<sequence id="seq1"><name>{escape(nome_seq)}</name><duration>{total}</duration>{_rate(fps)}'
        "<media><video><format><samplecharacteristics>"
        f"{_rate(fps)}<width>{W}</width><height>{H}</height><pixelaspectratio>square</pixelaspectratio>"
        "</samplecharacteristics></format>"
        f"<track>{''.join(v_items)}</track><track>{''.join(v2_items)}</track></video>"
        "<audio><format><samplecharacteristics><depth>16</depth><samplerate>48000</samplerate>"
        "</samplecharacteristics></format>"
        f"<track>{''.join(a_items)}</track><track>{''.join(m_items)}</track>"
        "</audio></media></sequence></xmeml>\n"
    )
    destino.write_text(xml, encoding="utf-8")
    return destino


# ---------------- RELATÓRIO ----------------

CFG_RELATORIO = {"selecao": {"min_fala_seg": 1.5}}


def gerar_relatorio(itens, roteiro, descartados, transicoes, destino: Path, cfg=None):
    global CFG_RELATORIO
    if cfg:
        CFG_RELATORIO = cfg
    usados = {id(b["item"]): b for b in roteiro}
    cobertura = {}
    for b in roteiro:
        for c in b.get("coberturas", []):
            cobertura.setdefault(id(c["item"]), []).append(f"{c['ini']:.1f}-{c['fim']:.1f}")
    repetidos = {id(i) for i in descartados}
    ordem_no_video = {id(b["item"]): n + 1 for n, b in enumerate(roteiro)}
    with open(destino, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Arquivo", "Tipo", "Situacao", "Posicao no video", "Trechos usados (s)",
                    "Duracao original (s)", "Fala (% do take)", "Clareza", "Transcricao"])
        for it in itens:
            b = usados.get(id(it))
            from selecao import e_fala
            tipo = "fala" if e_fala(it, CFG_RELATORIO) else "local (mudo, só música)"
            if b:
                situacao = "usado"
                trechos = " | ".join(f"{t['ini']:.1f}-{t['fim']:.1f}" for t in b["trechos"])
                if id(it) in cobertura:
                    situacao += " (e como cobertura)"
                    trechos += " | cobertura: " + " | ".join(cobertura[id(it)])
            elif id(it) in cobertura:
                situacao = "usado como cobertura sobre fala"
                trechos = " | ".join(cobertura[id(it)])
            elif id(it) in repetidos:
                situacao, trechos = "descartado (fala repetida em outro take melhor)", ""
            else:
                situacao, trechos = "nao usado", ""
            w.writerow([it["path"].name, tipo, situacao, ordem_no_video.get(id(it), ""),
                        trechos, round(it["duracao"], 2), round(100 * it.get("densidade", 0)),
                        round(it.get("clareza", 0), 2), it["texto"]])
        w.writerow([])
        w.writerow(["Transicoes na ordem:", " > ".join(transicoes)])
    return destino


# ---------------- PACOTE ----------------

LEIA_ME = """PACOTE DE EDIÇÃO - {nome}

{nome}_final.mp4 ............ vídeo pronto (música, transições, áudio tratado)
legendas.srt ................. legenda com o tempo do vídeo final
timeline_premiere_davinci.xml  timeline para continuar no Premiere ou DaVinci
Clipes_CapCut/ ............... cada bloco já cortado, enquadrado e com voz limpa, na ordem
Audio_Limpo/ ................. voz tratada de cada fala (usada pela timeline XML)
Musica/ ...................... trilha usada
relatorio.csv ................ o que foi usado, o que foi descartado e por quê

CAPCUT
  O CapCut não importa timeline de outros programas. Crie um projeto novo,
  arraste os arquivos de Clipes_CapCut/ (já estão numerados na ordem),
  coloque a música da pasta Musica/ e importe legendas.srt em Texto > Legendas.

PREMIERE
  Arquivo > Importar > timeline_premiere_davinci.xml. Quando pedir para
  localizar mídias, aponte para a pasta Midias_Originais (os vídeos originais,
  que ficam no Drive em _PROCESSADOS/{nome}) e para as pastas Audio_Limpo e Musica.

DAVINCI RESOLVE
  File > Import > Timeline > timeline_premiere_davinci.xml. Se as mídias
  aparecerem offline, use Relink Media apontando para as mesmas pastas.

Na timeline XML os blocos ficam em corte seco (sem transição) e o vídeo usa os
takes originais com sobra antes e depois de cada corte, para você ajustar.
"""


def montar_pacote(nome, pasta: Path, final_mp4, srt, xml, relatorio, roteiro, musica, originais=None):
    pacote = pasta / f"Pacote_{nome}"
    if pacote.exists():
        shutil.rmtree(pacote)
    (pacote / "Clipes_CapCut").mkdir(parents=True)
    (pacote / "Audio_Limpo").mkdir()
    for b in roteiro:
        shutil.copy2(b["arquivo"], pacote / "Clipes_CapCut" / b["arquivo"].name)
        if b["tipo"] == "fala":
            shutil.copy2(b["wav"], pacote / "Audio_Limpo" / b["wav"].name)
    if musica:
        (pacote / "Musica").mkdir()
        shutil.copy2(musica, pacote / "Musica" / musica.name)
    if originais:
        (pacote / "Midias_Originais").mkdir()
        for v in originais:
            shutil.copy2(v, pacote / "Midias_Originais" / v.name)
    for arq in [srt, xml, relatorio]:
        if arq:
            shutil.copy2(arq, pacote / arq.name)
    (pacote / "LEIA-ME.txt").write_text(LEIA_ME.format(nome=nome), encoding="utf-8")
    zip_path = shutil.make_archive(str(pasta / f"Pacote_{nome}"), "zip", root_dir=pacote)
    return Path(zip_path)
