# -*- mode: python -*-
# PyInstaller: gera o app do Gregio AutoEdit (pasta única, sem janela de terminal).
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

RAIZ = Path(SPECPATH).parent
datas, binaries, hidden = [], [], []
for pacote in ("faster_whisper", "ctranslate2", "av", "onnxruntime", "tokenizers", "cv2",
               "huggingface_hub", "requests", "tqdm", "certifi", "cryptography"):
    d, b, h = collect_all(pacote)
    datas += d; binaries += b; hidden += h

datas += [
    (str(RAIZ / "motor"), "motor"),
    (str(RAIZ / "app"), "app"),
    (str(RAIZ / "assets"), "assets"),
    (str(RAIZ / "modelos"), "modelos"),
    (str(RAIZ / "config_padrao.json"), "."),
]
binaries += [(str(p), "bin") for p in (RAIZ / "bin").iterdir()]
hidden += ["numpy", "PIL", "PIL.Image", "PIL.ImageDraw", "PIL.ImageFont", "certifi", "csv", "xml.sax.saxutils",
           "concurrent.futures", "http.server", "email", "json", "ssl"]

# O motor e a tela vão como arquivos (precisam do caminho real em disco). Por isso o PyInstaller não
# enxerga os imports deles sozinho: aqui eles são lidos e incluídos um por um.
import ast
LOCAIS = {p.stem for d in ("motor", "app") for p in (RAIZ / d).glob("*.py")}
for d in ("motor", "app"):
    for arq in (RAIZ / d).glob("*.py"):
        for no in ast.walk(ast.parse(arq.read_text(encoding="utf-8"))):
            nomes = []
            if isinstance(no, ast.Import):
                nomes = [n.name for n in no.names]
            elif isinstance(no, ast.ImportFrom) and no.module and not no.level:
                nomes = [no.module]
            for n in nomes:
                if n.split(".")[0] not in LOCAIS:
                    hidden.append(n)
hidden = sorted(set(hidden))

a = Analysis([str(RAIZ / "gregio.py")], pathex=[str(RAIZ / "motor"), str(RAIZ / "app")],
             binaries=binaries, datas=datas, hiddenimports=hidden, excludes=["tkinter", "matplotlib", "torch"],
             noarchive=False)
pyz = PYZ(a.pure)
icone = str(RAIZ / "build" / ("icone.icns" if sys.platform == "darwin" else "icone.ico"))
# motor (backend) em pasta única; o Electron abre a janela e chama este executável
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Gregio AutoEdit", console=False,
          icon=icone, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name="backend", upx=False)
