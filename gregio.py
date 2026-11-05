"""Gregio AutoEdit: ponto de entrada do app (Mac e Windows).

    gregio            abre o app
    gregio --teste PASTA   edita uma pasta de takes pela linha de comando (usado nos testes)
"""

import os
import sys
from pathlib import Path


def base():
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent


def preparar():
    b = base()
    # FFmpeg e FFprobe que vêm junto com o app
    bin_dir = b / "bin"
    if bin_dir.exists():
        os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
    for nome_modelo in ("whisper-small", "whisper-base"):  # o melhor que veio no instalador
        modelo = b / "modelos" / nome_modelo
        if (modelo / "model.bin").exists():
            os.environ.setdefault("AUTOEDIT_WHISPER", str(modelo))
            break
    rnn = b / "modelos" / "sh.rnnn"
    if rnn.exists():
        os.environ.setdefault("AUTOEDIT_RNNOISE", str(rnn))
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    sys.path.insert(0, str(b / "motor"))
    sys.path.insert(0, str(b / "app"))

    if os.name == "nt":
        # sem janelas pretas piscando a cada chamada do FFmpeg
        import subprocess
        _orig = subprocess.Popen.__init__

        def _init(self, *a, **k):
            k.setdefault("creationflags", 0x08000000)  # CREATE_NO_WINDOW
            _orig(self, *a, **k)
        subprocess.Popen.__init__ = _init

    # quando aberto pelo ícone não há terminal: guarda o registro num arquivo
    if getattr(sys, "frozen", False):
        sem_saida = sys.stdout is None or sys.stderr is None
        if sem_saida or not os.environ.get("GREGIO_CONSOLE"):
            import servidor  # noqa: F401  (cria a pasta de dados)
            log = open(servidor.DADOS / "gregio.log", "a", encoding="utf-8", buffering=1)
            if sem_saida or not sys.stdout.isatty():
                sys.stdout = sys.stderr = log


def main():
    preparar()
    if "--teste" in sys.argv:
        import editor
        i = sys.argv.index("--teste")
        return editor.main(sys.argv[i + 1:])
    if "--versao" in sys.argv:
        import servidor
        print(servidor.VERSAO)
        return 0
    import servidor
    servidor.main("--sem-navegador" not in sys.argv)
    return 0


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    sys.exit(main() or 0)
