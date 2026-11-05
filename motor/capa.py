"""Capa do Reels (1080x1920): o melhor quadro do vídeo, uma sombra suave embaixo
e o título em letras grandes. Se a pessoa cadastrou uma logo no app, ela entra
pequena no rodapé. Sem nenhuma marca fixa."""

import re
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

BASE = Path(__file__).resolve().parent.parent
FONTES = BASE / "assets" / "fonts"
W, H = 1080, 1920


def titulo_da_pasta(nome):
    """'Visita ao Lab [agro]' -> 'VISITA AO LAB'."""
    return re.sub(r"\[[^\]]*\]", "", nome).strip(" -_").upper()


def _fonte(nome, tamanho):
    for p in [FONTES / nome, Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")]:
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


def _detector():
    try:
        import cv2
        d = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        return None if d.empty() else d
    except Exception:
        return None


def melhor_quadro(video, destino: Path):
    """Quadro mais nítido, de preferência com um rosto visível."""
    import cv2
    dur = _duracao(video)
    detector = _detector()
    melhor, rosto_melhor, nota_melhor = None, None, -1
    for k in range(8):
        t = dur * (0.12 + 0.76 * k / 7)
        tmp = destino.with_name(f"_q{k}.png")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", str(video),
                        "-frames:v", "1", "-vf", f"scale={W}:{H}", str(tmp)], capture_output=True)
        img = cv2.imread(str(tmp))
        if img is None:
            continue
        cinza = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        nitidez = cv2.Laplacian(cv2.resize(cinza, (270, 480)), cv2.CV_64F).var()
        rostos = detector.detectMultiScale(cv2.resize(cinza, (540, 960)), 1.1, 6, minSize=(60, 60)) \
            if detector is not None else []
        rosto = max(rostos, key=lambda r: r[2] * r[3]) * 2 if len(rostos) else None
        nota = np.log1p(nitidez) + (2.0 if rosto is not None else 0)
        if nota > nota_melhor:
            melhor, rosto_melhor, nota_melhor = tmp, rosto, nota
    if melhor is None:
        return None, None
    Image.open(melhor).convert("RGB").save(destino)
    for f in destino.parent.glob("_q*.png"):
        try:
            f.unlink()
        except OSError:
            pass
    return destino, rosto_melhor


def _sombra(altura):
    a = np.clip(np.linspace(0, 1, altura), 0, 1) ** 1.6 * 255 * 0.75
    camada = np.zeros((altura, W, 4), dtype=np.uint8)
    camada[..., 3] = a[:, None].astype(np.uint8)
    return Image.fromarray(camada, "RGBA")


def gerar(foto: Path, nome_projeto: str, destino: Path, rosto=None, titulo=None, logo=None, cor="#FFFFFF"):
    texto = (titulo or titulo_da_pasta(nome_projeto)).strip().upper()
    base = Image.open(foto).convert("RGBA").resize((W, H), Image.LANCZOS)
    draw = ImageDraw.Draw(base)

    tam = 92
    while True:
        f = _fonte("Poppins-Bold.ttf", tam)
        linhas = _quebrar(texto, f, 940, draw)
        if len(linhas) <= 3 or tam <= 54:
            break
        tam -= 4
    linhas = linhas[:3]
    alt_linha = int(tam * 1.12)
    bloco = alt_linha * len(linhas)

    # título no terço de baixo; se cobrir o rosto, sobe para cima da cabeça
    topo = 1380 - bloco // 2
    if rosto is not None:
        _, ry, _, rh = rosto
        if ry + rh > topo - 40:
            topo = max(140, ry - bloco - 80) if ry - bloco - 80 > 140 else min(H - bloco - 260, ry + rh + 60)
    alt_sombra = H - max(0, topo - 380)
    base.alpha_composite(_sombra(alt_sombra), (0, H - alt_sombra))
    draw = ImageDraw.Draw(base)

    y = topo
    for ln in linhas:
        draw.text((W / 2 + 3, y + 4), ln, font=f, fill=(0, 0, 0, 120), anchor="ma")
        draw.text((W / 2, y), ln, font=f, fill=cor, anchor="ma")
        y += alt_linha

    if logo and Path(logo).exists():
        lg = Image.open(logo).convert("RGBA")
        lw = 260
        lh = int(lw * lg.height / lg.width)
        if lh > 140:
            lh, lw = 140, int(140 * lg.width / lg.height)
        base.alpha_composite(lg.resize((lw, lh), Image.LANCZOS), ((W - lw) // 2, H - lh - 150))

    base.convert("RGB").save(destino, quality=95)
    return destino, {"titulo": " / ".join(linhas)}
