"""Testes automáticos do motor com vários cenários (rodam antes de publicar cada versão).
Usa transcrição de mentira para ser rápido e não depender do modelo de voz.
Uso: python testes/teste_cenarios.py   (precisa de ffmpeg e ffprobe no PATH)"""
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "motor"))
import analise  # noqa: E402
import editor  # noqa: E402
import render  # noqa: E402

F = "ffmpeg"


def w(t, s, e):
    return {"w": t, "s": s, "e": e, "p": 0.9}


FALAS = {
    "fala_a.mp4": [w("gravando", 0.3, 0.8), w("oi", 1.5, 1.8), w("pessoal", 1.8, 2.4), w("a", 3.0, 3.1), w("escola", 3.1, 3.6),
                   w("tem", 3.6, 3.8), w("a", 4.6, 4.7), w("escola", 4.7, 5.2), w("tem", 5.2, 5.4), w("cursos", 5.4, 6.0),
                   w("novos", 6.0, 6.6), w("corta", 10.0, 10.4)],
    "fala_b.mp4": [w("vem", 1.0, 1.3), w("conhecer", 1.3, 2.0), w("a", 2.0, 2.1), w("gente", 2.1, 2.6), w("matricule", 2.6, 3.4),
                   w("se", 3.4, 3.6)],
    "01_ent.mp4": [w("primeira", 0.5, 1.2), w("pergunta", 1.2, 2.0)],
    "02_ent.mp4": [w("segunda", 0.5, 1.2), w("resposta", 1.2, 2.0)],
}
analise.transcrever = lambda v, cfg, cache: {"palavras": FALAS.get(v.name, []), "confianca": -0.3}


def video(destino, cor="blue", tam="1280x720", dur=12, audio=True, extra=()):
    cmd = [F, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={cor}:s={tam}:r=30:d={dur}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=f=300:d={dur}"]
    cmd += ["-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", *(["-c:a", "aac"] if audio else []), *extra, str(destino)]
    subprocess.run(cmd, check=True)


def duracao(f):
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1",
                                 str(f)], capture_output=True, text=True, check=True).stdout)


def rodar(nome, preparar, esperar_erro=False, **cfg_extra):
    base = Path(tempfile.mkdtemp(prefix="gregio_t_"))
    try:
        takes = base / "takes"
        takes.mkdir()
        musica = preparar(takes)
        cfg = editor.carregar_config()
        cfg.update(cfg_extra)
        videos, musica_pasta, enc = editor.separar_arquivos(takes)
        try:
            r = editor.editar(nome, videos, base / "saida", cfg, musica or musica_pasta, None)
        except analise.ErroUsuario as e:
            if esperar_erro:
                print("  ok (erro claro): %s" % e)
                return True
            raise
        if esperar_erro:
            raise AssertionError("era para dar erro")
        assert Path(r["video"]).exists() and duracao(r["video"]) > 3, "vídeo final ruim"
        assert r["capa"] and Path(r["capa"]).exists(), "sem capa"
        assert "resumo" in r
        print("  ok: %.1f s de vídeo" % duracao(r["video"]))
        return True
    finally:
        shutil.rmtree(base, ignore_errors=True)


def musica(takes):
    subprocess.run([F, "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=220:d=40", "-c:a", "libmp3lame", str(takes / "musica.mp3")], check=True)


def cenario_normal(takes):
    video(takes / "fala_a.mp4", "red")
    video(takes / "fala_b.mp4", "blue")
    video(takes / "local_c.mp4", "green")
    video(takes / "local_d.mp4", "yellow")
    musica(takes)


def cenario_entrevista(takes):
    video(takes / "01_ent.mp4", "red", dur=6)
    video(takes / "02_ent.mp4", "blue", dur=6)


def cenario_so_imagens(takes):
    video(takes / "local_a.mp4", "green", dur=10)
    video(takes / "local_b.mp4", "orange", dur=10)
    musica(takes)


def cenario_horizontal_e_rotacao(takes):
    video(takes / "fala_a.mp4", "red", tam="1920x1080")
    video(takes / "local_r.mp4", "purple", tam="1080x1920", extra=("-metadata:s:v", "rotate=90"))
    video(takes / "local_s.mp4", "teal", tam="1920x1080", audio=False)


def cenario_corrompido(takes):
    video(takes / "fala_a.mp4", "red")
    (takes / "quebrado.mp4").write_bytes(b"isto nao e um video" * 50)


def main():
    cenarios = [
        ("normal com música", cenario_normal, False, {}),
        ("normal com legenda simples e gancho", cenario_normal, False,
         {"legendas": {**editor.carregar_config()["legendas"], "estilo": "simples"}}),
        ("entrevista", cenario_entrevista, False, {"entrevista": True}),
        ("só imagens", cenario_so_imagens, False, {}),
        ("horizontal, rotação e sem áudio", cenario_horizontal_e_rotacao, False, {}),
        ("arquivo corrompido", cenario_corrompido, True, {}),
    ]
    falhas = 0
    for nome, prep, erro, extra in cenarios:
        print("Cenário:", nome)
        try:
            rodar("teste", prep, erro, **extra)
        except Exception:
            falhas += 1
            traceback.print_exc()
            print("  FALHOU")
    print("\n%d cenário(s), %d falha(s)" % (len(cenarios), falhas))
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
