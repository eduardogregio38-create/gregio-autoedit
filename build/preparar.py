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

# modelo de voz: Whisper "small" (~480 MB, entende bem mais que o "base"); se falhar, usa o "base" (~140 MB)
from huggingface_hub import snapshot_download
_padroes = ["config.json", "model.bin", "tokenizer.json", "vocabulary.*", "preprocessor_config.json"]
try:
    snapshot_download("Systran/faster-whisper-small", local_dir=str(MOD / "whisper-small"), allow_patterns=_padroes)
except Exception as e:
    print("sem o modelo small, usando o base:", e)
    snapshot_download("Systran/faster-whisper-base", local_dir=str(MOD / "whisper-base"), allow_patterns=_padroes)
# redução de ruído da voz
try:
    (MOD / "sh.rnnn").write_bytes(baixar(
        "https://raw.githubusercontent.com/GregorR/rnnoise-models/master/somnolent-hogwash-2018-09-01/sh.rnnn"))
except Exception as e:
    print("sem RNNoise:", e)
print("preparado")


# coletânea de fontes livres (licença OFL, Google Fonts) para título da capa e legenda.
# Se alguma não baixar, o instalador sai sem ela: as fontes Poppins continuam dentro do app.
FONTES = RAIZ / "assets" / "fonts"; FONTES.mkdir(parents=True, exist_ok=True)
GF = "https://raw.githubusercontent.com/google/fonts/main/ofl/"
FUNDO = ["anton/Anton-Regular.ttf", "bebasneue/BebasNeue-Regular.ttf", "bangers/Bangers-Regular.ttf",
         "archivoblack/ArchivoBlack-Regular.ttf", "lobster/Lobster-Regular.ttf", "pacifico/Pacifico-Regular.ttf",
         "permanentmarker/PermanentMarker-Regular.ttf", "alfaslabone/AlfaSlabOne-Regular.ttf",
         "bowlbyone/BowlbyOne-Regular.ttf", "russoone/RussoOne-Regular.ttf", "righteous/Righteous-Regular.ttf",
         # divertidas
         "luckiestguy/LuckiestGuy-Regular.ttf", "chewy/Chewy-Regular.ttf", "bungee/Bungee-Regular.ttf",
         "lilitaone/LilitaOne-Regular.ttf", "titanone/TitanOne-Regular.ttf", "sigmarone/SigmarOne-Regular.ttf",
         "shrikhand/Shrikhand-Regular.ttf", "boogaloo/Boogaloo-Regular.ttf", "caveatbrush/CaveatBrush-Regular.ttf",
         "knewave/Knewave-Regular.ttf", "rammettoone/RammettoOne-Regular.ttf", "passionone/PassionOne-Bold.ttf",
         "blackopsone/BlackOpsOne-Regular.ttf", "monoton/Monoton-Regular.ttf", "pressstart2p/PressStart2P-Regular.ttf",
         "gochihand/GochiHand-Regular.ttf", "specialelite/SpecialElite-Regular.ttf", "satisfy/Satisfy-Regular.ttf",
         "fugazone/FugazOne-Regular.ttf", "paytone one/PaytoneOne-Regular.ttf".replace(" ", ""),
         "baloo2/Baloo2-Bold.ttf", "kalam/Kalam-Bold.ttf", "bubblegumsans/BubblegumSans-Regular.ttf",
         "patrickhand/PatrickHand-Regular.ttf", "creepster/Creepster-Regular.ttf"]
for caminho in FUNDO:
    try:
        nome = Path(caminho).name.replace("%5B", "[").replace("%2C", ",").replace("%5D", "]")
        if "[" in nome:
            continue  # fontes variáveis ficam de fora (só o peso padrão apareceria)
        (FONTES / nome).write_bytes(baixar(GF + caminho))
        print("  fonte ok", nome)
    except Exception as e:
        print("  fonte não baixou:", caminho, e)
