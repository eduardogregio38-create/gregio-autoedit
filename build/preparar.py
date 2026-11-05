"""Baixa o que vai dentro do instalador: FFmpeg/FFprobe, modelo do Whisper e o modelo RNNoise.
Uso: python build/preparar.py (roda no GitHub Actions, em cada sistema)."""
import io, os, platform, shutil, stat, sys, urllib.request, zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
BIN = RAIZ / "bin"; MOD = RAIZ / "modelos"
BIN.mkdir(exist_ok=True); MOD.mkdir(exist_ok=True)


def baixar(url):
    print("baixando", url, flush=True)
    req = urllib.request.Request(url, headers={"User-Agent": "gregio-build"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return r.read()


def extrair(dados, nomes):
    z = zipfile.ZipFile(io.BytesIO(dados))
    for info in z.infolist():
        base = Path(info.filename).name
        if base in nomes:
            alvo = BIN / base
            alvo.write_bytes(z.read(info))
            alvo.chmod(alvo.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
            print("  ok", alvo)


sistema, arq = platform.system(), platform.machine().lower()
if sistema == "Windows":
    extrair(baixar("https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"),
            {"ffmpeg.exe", "ffprobe.exe"})
elif sistema == "Darwin":
    cpu = "arm64" if arq in ("arm64", "aarch64") else "amd64"
    for prog in ("ffmpeg", "ffprobe"):
        try:
            extrair(baixar(f"https://ffmpeg.martin-riedl.de/redirect/latest/macos/{cpu}/release/{prog}.zip"), {prog})
        except Exception as e:
            print("  martin-riedl falhou:", e)
            if cpu == "amd64":
                extrair(baixar(f"https://evermeet.cx/ffmpeg/getrelease/{prog}/zip"), {prog})
            else:
                raise
else:
    for prog in ("ffmpeg", "ffprobe"):
        p = shutil.which(prog)
        if p:
            shutil.copy(p, BIN / prog)

for prog in ("ffmpeg", "ffprobe"):
    exe = BIN / (prog + (".exe" if sistema == "Windows" else ""))
    if not exe.exists():
        sys.exit("faltou " + exe.name)

# modelo de voz (Whisper base, ~140 MB)
from huggingface_hub import snapshot_download
snapshot_download("Systran/faster-whisper-base", local_dir=str(MOD / "whisper-base"),
                  allow_patterns=["config.json", "model.bin", "tokenizer.json", "vocabulary.*", "preprocessor_config.json"])
# redução de ruído da voz
try:
    (MOD / "sh.rnnn").write_bytes(baixar(
        "https://raw.githubusercontent.com/GregorR/rnnoise-models/master/somnolent-hogwash-2018-09-01/sh.rnnn"))
except Exception as e:
    print("sem RNNoise:", e)
print("preparado")
