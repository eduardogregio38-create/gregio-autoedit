"""Duas contas no mesmo computador nao podem ver nada uma da outra. Uso: python testes/teste_contas.py"""
import os, sys, json, threading, urllib.request, tempfile, shutil
from pathlib import Path
S = Path(tempfile.mkdtemp(prefix="gregio_ct_"))
RAIZ = Path(__file__).resolve().parent.parent
os.environ.update(GREGIO_DADOS=str(S/"d"), GREGIO_PASTA=str(S/"p"), GREGIO_TOKEN="tok", GREGIO_PORTA="47998")
sys.path.insert(0, str(RAIZ/"app")); sys.path.insert(0, str(RAIZ/"motor"))
import servidor as sv
from http.server import ThreadingHTTPServer
srv = ThreadingHTTPServer(("127.0.0.1", 47998), sv.App); srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()
H = {"X-Gregio-Chave": "tok", "Host": "127.0.0.1:47998", "Content-Type": "application/json"}
def api(p, c=None):
    r = urllib.request.Request("http://127.0.0.1:47998"+p, data=json.dumps(c).encode() if c is not None else None, headers=H)
    return json.load(urllib.request.urlopen(r))
def ok(c, m):
    print(("OK   " if c else "FALHA"), m)
    if not c: sys.exit(1)
# conta A
sv.ativar_perfil("ana@gmail.com")
(sv.MUSICAS/"leve"/"minha.mp3").write_bytes(b"x"*2000)
sv.salvar_aprendizado({**sv.aprendizado(), "marca": "A"})
(sv.PROJETOS/"p1").mkdir(); (sv.PROJETOS/"p1"/"projeto.json").write_text(json.dumps({"id":"p1","nome":"da Ana","estado":"previa"}))
(sv.DADOS/"logo.png").write_bytes(b"png")
ok(len(api("/api/projetos")["projetos"]) == 1, "A ve o proprio projeto")
ok(any("minha" in json.dumps(m) for m in [api("/api/musicas")]), "A ve a propria musica")
# conta B
sv.ativar_perfil("bia@gmail.com")
ok(api("/api/projetos")["projetos"] == [], "B nao ve projeto de A")
ok("minha" not in json.dumps(api("/api/musicas")), "B nao ve musica de A")
ok(sv.aprendizado().get("marca") is None, "B nao herda aprendizado de A")
ok(not (sv.DADOS/"logo.png").exists(), "B nao herda logo de A")
# sem conta
sv.ativar_perfil(None)
ok(api("/api/projetos")["projetos"] == [] and "minha" not in json.dumps(api("/api/musicas")), "sem conta nao ve nada")
# volta A
sv.ativar_perfil("ANA@gmail.com")
ok(len(api("/api/projetos")["projetos"]) == 1 and sv.aprendizado().get("marca") == "A", "A recupera tudo (maiusculas tambem)")
# nome de pasta malicioso
sv.ativar_perfil("../../etc/x")
ok(str(sv.PASTA).startswith(str(S/"p"/"Contas")), "email estranho nao sai da pasta de contas")
# apagar conta: some do computador, guarda os prontos se pedir, e tira o acesso ao Google
sv.ativar_perfil("carla@gmail.com")
(sv.PRONTOS/"v").mkdir(); (sv.PRONTOS/"v"/"a.mp4").write_bytes(b"x"*2000)
(sv.PROJETOS/"q").mkdir(); (sv.PROJETOS/"q"/"projeto.json").write_text("{}")
pasta_c = sv.PASTA
sv.gdrive.revogar = lambda: None; sv.pasta_drive = lambda: "x"
sv.gdrive.ler_json_drive = lambda *a: (None, None)
sv.gdrive._salvar_token({"refresh_token": "R"})
guard = sv.apagar_conta(True)
ok(not pasta_c.exists() and not (sv.DADOS_BASE/"token.json").exists(), "apagar conta remove a pasta e o token")
ok(guard and (guard/"v"/"a.mp4").exists(), "vídeos prontos guardados em pasta separada")
ok(sv.PERFIL["email"] is None, "depois de apagar não há conta ativa")
# token do Google cifrado em disco
sv.gdrive._salvar_token({"refresh_token": "SEGREDO"})
ok(b"SEGREDO" not in (sv.DADOS_BASE/"token.json").read_bytes(), "token.json não fica em texto simples")
ok(sv.gdrive._ler_token_arquivo(sv.DADOS_BASE/"token.json")["refresh_token"] == "SEGREDO", "token cifrado volta igual ao ler")
print("tudo certo")
