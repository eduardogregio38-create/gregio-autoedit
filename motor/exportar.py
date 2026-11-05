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
    palavras = palavras_na_timeline(roteiro, inicios)
    if not palavras:
        return None
    legendas, atual = [], []
    for i, p in enumerate(palavras):
        texto = " ".join(x["w"] for x in atual + [p])
        pausa = atual and p["s"] - atual[-1]["e"] > 0.5
        if atual and (len(texto) > max_chars or pausa):
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
