# -*- mode: python -*-
# PyInstaller: gera o app do Gregio AutoEdit (pasta única, sem janela de terminal).
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

RAIZ = Path(SPECPATH).parent
datas, binaries, hidden = [], [], []
for pacote in ("faster_whisper", "ctranslate2", "av", "onnxruntime", "tokenizers", "cv2"):
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

a = Analysis([str(RAIZ / "gregio.py")], pathex=[str(RAIZ / "motor"), str(RAIZ / "app")],
             binaries=binaries, datas=datas, hiddenimports=hidden, excludes=["tkinter", "matplotlib", "torch"],
             noarchive=False)
pyz = PYZ(a.pure)
icone = str(RAIZ / "build" / ("icone.icns" if sys.platform == "darwin" else "icone.ico"))
# motor (backend) em pasta única; o Electron abre a janela e chama este executável
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Gregio AutoEdit", console=False,
          icon=icone, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name="backend", upx=False)
