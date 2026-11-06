"""O filtro de estabilização tem que funcionar no FFmpeg do computador (já falhou com rx=24). Uso: python testes/teste_filtros.py"""
import subprocess, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "motor"))
import render
import editor
cfg = editor.carregar_config({"video": {"estabilizar": True}})
cfg["video"]["estabilizar"] = True
f = render.filtro_video(cfg, 1920, 1080, "t", False, None, True)
assert "deshake" in f, "a estabilização não entrou no filtro"
r = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=1920x1080:d=1:r=30", "-vf", f, "-f", "null", "-"], capture_output=True, text=True)
assert r.returncode == 0, "FFmpeg recusou o filtro de estabilização: " + r.stderr[-300:]
print("OK: filtro de estabilização funciona")
