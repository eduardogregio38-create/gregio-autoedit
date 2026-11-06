"""Teste de ponta a ponta da tela Corrigir legenda: troca palavra, cor, tamanho e posição pelo servidor de verdade
e confere que o vídeo entregue mudou. Uso: python testes/teste_legenda.py (precisa de ffmpeg e Pillow)"""
import os, sys, json, time, subprocess, shutil, threading, urllib.request
from pathlib import Path
import tempfile
S=Path(tempfile.mkdtemp(prefix="gregio_leg_"))
RAIZ=Path(__file__).resolve().parent.parent
shutil.rmtree(S/"d",ignore_errors=True); shutil.rmtree(S/"p",ignore_errors=True)
os.environ.update(GREGIO_DADOS=str(S/"d"),GREGIO_PASTA=str(S/"p"),GREGIO_TOKEN="tok",GREGIO_PORTA="47999")
sys.path.insert(0,str(RAIZ/"app")); sys.path.insert(0,str(RAIZ/"motor"))
import servidor as sv
from http.server import ThreadingHTTPServer
sv.DADOS.mkdir(parents=True,exist_ok=True)
srv=ThreadingHTTPServer(("127.0.0.1",47999),sv.App); srv.daemon_threads=True
threading.Thread(target=srv.serve_forever,daemon=True).start()
pid="abc123"; pd=sv.dir_projeto(pid); T=pd/"trabalho"; T.mkdir(parents=True)
subprocess.run(["ffmpeg","-v","error","-y","-f","lavfi","-i","color=c=gray:s=1080x1920:r=30:d=4","-f","lavfi","-i","sine=f=300:d=4","-shortest","-c:v","libx264","-pix_fmt","yuv420p","-c:a","aac",str(T/"montagem.mp4")],check=True)
(T/"audio.json").write_text(json.dumps({"nomes":[],"efeitos":[]}))
pal=[{"w":"ola","s":0.5,"e":1.0,"p":.9},{"w":"mundo","s":1.0,"e":1.6,"p":.9},{"w":"teste","s":1.6,"e":2.4,"p":.9}]
(T/"legenda.json").write_text(json.dumps(pal))
sv.PROJETOS.mkdir(parents=True,exist_ok=True)
(pd/"projeto.json").write_text(json.dumps({"id":pid,"nome":"Teste","estado":"previa","arquivos":{"video":str(T/"Teste.mp4")},"rev":1}))
H={"X-Gregio-Chave":"tok","Host":"127.0.0.1:47999","Content-Type":"application/json"}
def api(path,corpo=None):
    r=urllib.request.Request("http://127.0.0.1:47999"+path,data=json.dumps(corpo).encode() if corpo is not None else None,headers=H)
    return json.load(urllib.request.urlopen(r))
def quadro(tag):
    r=urllib.request.Request(f"http://127.0.0.1:47999/api/arquivo?id={pid}&k=video",headers=H)
    (S/(tag+".mp4")).write_bytes(urllib.request.urlopen(r).read())
    subprocess.run(["ffmpeg","-v","error","-y","-ss","1.2","-i",str(S/(tag+".mp4")),"-frames:v","1",str(S/(tag+".png"))],check=True)
# 1) vídeo inicial
sv.refazer_final_em_segundo_plano(pid,"x",palavras=pal)
def esperar():
    for _ in range(90):
        time.sleep(1); p=api("/api/projetos")["projetos"][0]
        if p["estado"]=="previa": return p
p0=esperar(); quadro("antes"); print("rev antes",p0["rev"])
g=api(f"/api/legenda?id={pid}"); print("estilo lido",g["estilo"]); print("frases",[(f["n"],f["texto"]) for f in g["frases"]])
fonte=g["fontes"][0][0]
r=api("/api/legenda",{"id":pid,"frases":{g["frases"][0]["n"]:"OLA CLARA"},"estilo":{"fonte":fonte,"tamanho_ass":"grande","cor_texto":"#FFD400","cor_destaque":"#FF4F9A","posicao":"meio","maiusculas":True}})
print("post",r,"estado logo depois:",api("/api/projetos")["projetos"][0]["estado"])
p1=esperar(); quadro("depois"); print("rev depois",p1["rev"], "erro",p1.get("erro"),sv.AVISOS)
print("legenda.json",(T/"legenda.json").read_text()[:200])
from PIL import Image, ImageChops
a=Image.open(S/"antes.png").convert("RGB"); b=Image.open(S/"depois.png").convert("RGB")
print("diferença", ImageChops.difference(a,b).getbbox())

assert ImageChops.difference(a,b).getbbox(), "O vídeo não mudou depois de corrigir a legenda"
print("OK: a correção da legenda chegou ao vídeo")

# posição livre (arrastar a legenda): pos_y 0.2 leva o texto para perto do topo
def centro_y(img):
    px=img.load(); w,h=img.size; ys=[y for y in range(0,h,4) for x in range(0,w,8) if abs(px[x,y][0]-128)+abs(px[x,y][1]-128)+abs(px[x,y][2]-128)>150]
    return sum(ys)/len(ys)/h if ys else None
y_meio=centro_y(b)
api("/api/legenda",{"id":pid,"estilo":{"fonte":fonte,"tamanho_ass":"grande","cor_texto":"#FFD400","cor_destaque":"#FF4F9A","pos_y":0.2,"maiusculas":True}})
esperar(); quadro("topo")
y_topo=centro_y(Image.open(S/"topo.png").convert("RGB"))
print("centro do texto: meio",y_meio,"topo",y_topo)
assert y_meio and y_topo and y_topo < y_meio-0.15, "A legenda não foi para a posição arrastada"
print("OK: posição livre da legenda funciona")

# corte de um trecho do meio: o vídeo encurta, a legenda acompanha e dá para voltar ao original
def dur(arq):
    return float(subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=nw=1:nk=1",str(arq)],capture_output=True,text=True).stdout)
def video_atual():
    r=urllib.request.Request(f"http://127.0.0.1:47999/api/arquivo?id={pid}&k=video",headers=H)
    (S/"corte.mp4").write_bytes(urllib.request.urlopen(r).read()); return dur(S/"corte.mp4")
antes=video_atual()
api("/api/acao",{"acao":"cortar_trecho","id":pid,"de":1.0,"ate":2.0}); esperar()
depois=video_atual(); pal2=json.loads((T/"legenda.json").read_text())
print("duração antes/depois do corte",round(antes,2),round(depois,2),"palavras",[w["w"] for w in pal2])
assert abs((antes-depois)-1.0)<0.25, "O trecho não foi tirado do vídeo"
assert [w["w"] for w in pal2]==["OLA"], "A legenda não acompanhou o corte"
api("/api/acao",{"acao":"voltar_cortes","id":pid}); esperar()
volta=video_atual()
print("depois de voltar ao original",round(volta,2))
assert abs(volta-antes)<0.25, "Voltar ao original não restaurou o vídeo"
print("OK: corte de trecho e volta ao original funcionam")
