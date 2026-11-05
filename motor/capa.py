"""Capa do Reels (1080x1920): o melhor quadro do vídeo, uma sombra suave embaixo
e o título em letras grandes. Se a pessoa cadastrou uma logo no app, ela entra
pequena no rodapé. Sem nenhuma marca fixa."""

import re
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

BASE = Path(__file__).resolve().parent.parent
FONTES = BASE / "assets" / "fonts"
W, H = 1080, 1920


def titulo_da_pasta(nome):
    """'Visita ao Lab [agro]' -> 'VISITA AO LAB'."""
    return re.sub(r"\[[^\]]*\]", "", nome).strip(" -_").upper()


PASTAS_FONTES = []   # o app acrescenta a pasta "Fontes" da pessoa


def achar_fonte(nome):
    for pasta in [FONTES] + [Path(x) for x in PASTAS_FONTES]:
        f = pasta / Path(nome).name
        if f.exists():
            return f
    return None


def listar_fontes():
    """Fontes disponíveis: as do app e as que a pessoa colocou na pasta Fontes. [(arquivo, nome bonito)]"""
    vistos, saida = set(), []
    for pasta in [FONTES] + [Path(x) for x in PASTAS_FONTES]:
        if not pasta.is_dir():
            continue
        for f in sorted(pasta.iterdir()):
            if f.suffix.lower() in (".ttf", ".otf") and f.name not in vistos:
                vistos.add(f.name)
                try:
                    fam, estilo = ImageFont.truetype(str(f), 20).getname()
                    nome = fam if estilo.lower() in ("regular", "normal", "") else fam + " " + estilo
                except Exception:
                    nome = f.stem
                saida.append((f.name, nome))
    return saida


def _fonte(nome, tamanho):
    achada = achar_fonte(nome)
    for p in [achada, FONTES / "Poppins-Bold.ttf", Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")]:
        if p is None:
            continue
        if p.exists():
            return ImageFont.truetype(str(p), tamanho)
    return ImageFont.load_default()


def _quebrar(texto, fonte, largura, draw):
    palavras, linhas, atual = texto.split(), [], ""
    for p in palavras:
        teste = (atual + " " + p).strip()
        if draw.textlength(teste, font=fonte) <= largura or not atual:
            atual = teste
        else:
            linhas.append(atual)
            atual = p
    if atual:
        linhas.append(atual)
    return linhas


def _duracao(video):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nw=1:nk=1", str(video)],
                         capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    try:
        return float(out.strip())
    except ValueError:
        return 0.0


def _cascata(nome):
    try:
        import cv2
        c = cv2.CascadeClassifier(cv2.data.haarcascades + nome)
        return None if c.empty() else c
    except Exception:
        return None


_DET = {}


def _detectores():
    if not _DET:
        for k, f in {"frontal": "haarcascade_frontalface_alt2.xml", "frontal2": "haarcascade_frontalface_default.xml",
                     "perfil": "haarcascade_profileface.xml", "olhos": "haarcascade_eye.xml",
                     "sorriso": "haarcascade_smile.xml"}.items():
            _DET[k] = _cascata(f)
    return _DET


def _achar_rosto(cinza):
    """Rosto de frente (dois detectores) ou de lado (os dois lados). Devolve (x, y, w, h) em coords de 540x960 ou None."""
    import cv2
    d = _detectores()
    pequeno = cv2.resize(cinza, (540, 960))
    achados = []
    for k in ("frontal", "frontal2"):
        if d.get(k) is not None:
            achados += list(d[k].detectMultiScale(pequeno, 1.1, 5, minSize=(60, 60)))
            if achados:
                break
    if not achados and d.get("perfil") is not None:
        achados += list(d["perfil"].detectMultiScale(pequeno, 1.1, 4, minSize=(60, 60)))
        achados += [(540 - x - w, y, w, h) for (x, y, w, h) in d["perfil"].detectMultiScale(cv2.flip(pequeno, 1), 1.1, 4, minSize=(60, 60))]
    return max(achados, key=lambda r: r[2] * r[3]) if len(achados) else None, pequeno


def _expressao(rosto, pequeno):
    """Pontos pela expressão: dois olhos abertos visíveis e, se der, sorriso. Olho fechado ou mão na cara perde pontos."""
    import cv2
    d = _detectores()
    x, y, w, h = [int(v) for v in rosto]
    pts = 0.0
    if d.get("olhos") is not None:
        olhos = d["olhos"].detectMultiScale(pequeno[y:y + int(h * 0.6), x:x + w], 1.1, 6, minSize=(w // 10, w // 10))
        pts += 1.0 if len(olhos) >= 2 else (-0.8 if len(olhos) == 0 else 0.0)
    if d.get("sorriso") is not None:
        sor = d["sorriso"].detectMultiScale(pequeno[y + int(h * 0.55):y + h, x:x + w], 1.5, 18, minSize=(w // 4, w // 8))
        pts += 0.5 if len(sor) else 0.0
    return pts


def melhor_quadro(video, destino: Path, quantas=1):
    """Quadros mais nítidos, de preferência com rosto de frente, olhos abertos e expressão natural.
    quantas=1 devolve (foto, rosto); quantas>1 devolve uma lista de (foto, rosto) de momentos diferentes."""
    import cv2
    dur = _duracao(video)
    cand = []
    N = 16
    for k in range(N):
        t = dur * (0.08 + 0.84 * k / (N - 1))
        tmp = destino.with_name(f"_q{k}.png")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", str(video),
                        "-frames:v", "1", "-vf", f"scale={W}:{H}", str(tmp)], capture_output=True)
        img = cv2.imread(str(tmp))
        if img is None:
            continue
        cinza = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        nitidez = cv2.Laplacian(cv2.resize(cinza, (270, 480)), cv2.CV_64F).var()
        rosto, pequeno = _achar_rosto(cinza)
        nota = np.log1p(nitidez)
        if rosto is not None:
            nota += 2.0 + _expressao(rosto, pequeno)
            rosto = np.array(rosto) * 2
        cand.append({"t": t, "arq": tmp, "rosto": rosto, "nota": nota})
    if not cand:
        return (None, None) if quantas == 1 else []
    cand.sort(key=lambda c: c["nota"], reverse=True)
    escolhidos = []
    for c in cand:  # momentos diferentes (pelo menos 8% da duração de distância)
        if all(abs(c["t"] - e["t"]) >= dur * 0.08 for e in escolhidos):
            escolhidos.append(c)
        if len(escolhidos) >= quantas:
            break
    saidas = []
    for i, c in enumerate(escolhidos):
        dest = destino if i == 0 else destino.with_name(destino.stem + f"_{i + 1}" + destino.suffix)
        Image.open(c["arq"]).convert("RGB").save(dest)
        saidas.append((dest, c["rosto"]))
    for f in destino.parent.glob("_q*.png"):
        try:
            f.unlink()
        except OSError:
            pass
    return saidas[0] if quantas == 1 else saidas


def _sombra(altura):
    a = np.clip(np.linspace(0, 1, altura), 0, 1) ** 1.6 * 255 * 0.75
    camada = np.zeros((altura, W, 4), dtype=np.uint8)
    camada[..., 3] = a[:, None].astype(np.uint8)
    return Image.fromarray(camada, "RGBA")


ESTILOS_CAPA = ("classico", "faixa", "cor", "caixa")


def _contraste(hexa):
    """Preto ou branco, o que ler melhor em cima da cor dada."""
    h = hexa.lstrip("#")
    if len(h) != 6:
        return "#FFFFFF"
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return "#111111" if (0.299 * r + 0.587 * g + 0.114 * b) > 150 else "#FFFFFF"


def _caixa(base, texto, fonte, cor_titulo, cor_caixa, tag, cor_tag, logo, posicao, rosto, margem_segura):
    """Estilo caixa: retângulo arredondado de cor lisa com etiqueta (tag) no topo, título e logo opcional embaixo."""
    if cor_titulo.upper() == "#FFFFFF" and _contraste(cor_caixa) == "#111111":
        cor_titulo = "#004B96"  # título branco em caixa clara não aparece: usa azul
    draw = ImageDraw.Draw(base)
    cx_larg = 780
    miolo = cx_larg - 110
    tam = 84
    while True:
        f = _fonte(fonte, tam)
        linhas = _quebrar(texto, f, miolo, draw)
        if len(linhas) <= 3 or tam <= 48:
            break
        tam -= 4
    linhas = linhas[:3]
    alt_linha = int(tam * 1.15)
    bloco = alt_linha * len(linhas)

    ft = _fonte(fonte, 38)
    tag = (tag or "").strip().upper()[:26]
    tag_alt = 70 if tag else 0
    lg = None
    lh = 0
    if logo and Path(logo).exists():
        lg = Image.open(logo).convert("RGBA")
        lw = 300
        lh = int(lw * lg.height / lg.width)
        if lh > 110:
            lh, lw = 110, int(110 * lg.width / lg.height)
        lg = lg.resize((lw, lh), Image.LANCZOS)
    topo_pad = (tag_alt // 2 + 36) if tag else 54
    alt = topo_pad + bloco + (36 + lh if lg else 0) + 54
    centro = {"baixo": 1290, "meio": 960, "cima": 640}.get(posicao, 1290)
    y0 = centro - alt // 2
    if rosto is not None and posicao == "baixo":
        _, ry, _, rh = rosto
        if ry + rh > y0 - 20:
            y0 = max(300, ry - alt - 60) if ry - alt - 60 > 300 else min(H - alt - 260, ry + rh + 40)
    y0 = max(300, min(y0, 1640 - alt)) if margem_segura else max(120, min(y0, H - alt - 160))
    x0 = (W - cx_larg) // 2

    sombra = Image.new("RGBA", base.size, (0, 0, 0, 0))
    ImageDraw.Draw(sombra).rounded_rectangle((x0, y0 + 10, x0 + cx_larg, y0 + alt + 10), radius=46, fill=(0, 0, 0, 90))
    base.alpha_composite(sombra.filter(ImageFilter.GaussianBlur(14)))
    draw = ImageDraw.Draw(base)
    draw.rounded_rectangle((x0, y0, x0 + cx_larg, y0 + alt), radius=46, fill=cor_caixa)

    if tag:
        tw = int(draw.textlength(tag, font=ft)) + 76
        tx0 = (W - tw) // 2
        draw.rounded_rectangle((tx0, y0 - tag_alt // 2, tx0 + tw, y0 + tag_alt // 2), radius=tag_alt // 2, fill=cor_tag)
        draw.text((W / 2, y0), tag, font=ft, fill=_contraste(cor_tag), anchor="mm")
    y = y0 + topo_pad
    for ln in linhas:
        draw.text((W / 2, y), ln, font=f, fill=cor_titulo, anchor="ma")
        y += alt_linha
    if lg is not None:
        base.alpha_composite(lg, ((W - lg.width) // 2, y + 36 - 8))
    return linhas


def gerar(foto: Path, nome_projeto: str, destino: Path, rosto=None, titulo=None, logo=None, cor="#FFFFFF",
          estilo="classico", fonte="Poppins-Bold.ttf", posicao="baixo", margem_segura=True,
          cor_caixa="#F1F1F1", tag="", cor_tag="#E63843"):
    """Estilos: classico (sombra), faixa (barra escura atrás do título), cor (letras na cor escolhida).
    Posição: baixo, meio ou cima. Com margem_segura o título fica dentro do recorte 3:4 da grade do perfil
    (faixa de 240 a 1680) e longe dos botões do Reels."""
    texto = (titulo or titulo_da_pasta(nome_projeto)).strip().upper()
    base = Image.open(foto).convert("RGBA").resize((W, H), Image.LANCZOS)
    draw = ImageDraw.Draw(base)
    if estilo == "caixa":
        linhas = _caixa(base, texto, fonte, cor, cor_caixa, tag, cor_tag, logo, posicao, rosto, margem_segura)
        base.convert("RGB").save(destino, quality=95)
        return destino, {"titulo": " / ".join(linhas)}
    largura_max = 820 if margem_segura else 940

    tam = 92
    while True:
        f = _fonte(fonte, tam)
        linhas = _quebrar(texto, f, largura_max, draw)
        if len(linhas) <= 3 or tam <= 54:
            break
        tam -= 4
    linhas = linhas[:3]
    alt_linha = int(tam * 1.12)
    bloco = alt_linha * len(linhas)

    centro = {"baixo": 1380, "meio": 960, "cima": 520}.get(posicao, 1380)
    topo = centro - bloco // 2
    if rosto is not None and posicao == "baixo":  # se cobrir o rosto, sobe para cima da cabeça
        _, ry, _, rh = rosto
        if ry + rh > topo - 40:
            topo = max(300, ry - bloco - 80) if ry - bloco - 80 > 300 else min(H - bloco - 260, ry + rh + 60)
    if margem_segura:
        topo = max(300, min(topo, 1560 - bloco))
    else:
        topo = max(140, min(topo, H - bloco - 200))
    cor_texto = cor
    if estilo == "cor":
        cor_texto = "#FFD400" if cor.upper() == "#FFFFFF" else cor
    if estilo == "faixa":
        faixa = Image.new("RGBA", (W, bloco + 90), (0, 0, 0, 175))
        base.alpha_composite(faixa, (0, topo - 45))
    else:
        alt_sombra = H - max(0, topo - 380)
        base.alpha_composite(_sombra(alt_sombra), (0, H - alt_sombra))
    draw = ImageDraw.Draw(base)

    y = topo
    for ln in linhas:
        draw.text((W / 2 + 3, y + 4), ln, font=f, fill=(0, 0, 0, 150), anchor="ma")
        draw.text((W / 2, y), ln, font=f, fill=cor_texto, anchor="ma")
        y += alt_linha

    if logo and Path(logo).exists():
        lg = Image.open(logo).convert("RGBA")
        lw = 260
        lh = int(lw * lg.height / lg.width)
        if lh > 140:
            lh, lw = 140, int(140 * lg.width / lg.height)
        yl = (1680 - lh - 40) if margem_segura else (H - lh - 150)
        if posicao == "baixo" and yl < topo + bloco + 20 and margem_segura:
            yl = min(H - lh - 20, topo + bloco + 60)
        base.alpha_composite(lg.resize((lw, lh), Image.LANCZOS), ((W - lw) // 2, yl))

    base.convert("RGB").save(destino, quality=95)
    return destino, {"titulo": " / ".join(linhas)}
