"""Seleção dos takes e montagem da ordem da edição.

Estrutura do vídeo (baseada nos vídeos de referência):
  - abre com a fala de abertura (a que tem saudação)
  - entre as falas alterna montagem (imagens com música alta)
    e narração (a voz continua sobre imagens do local)
  - fecha com a fala de fechamento (convite ou despedida) e a tela de encerramento
"""

import random
from difflib import SequenceMatcher

from analise import HESITACOES, ponto_silencioso

ABERTURA = ["oi", "olá", "ola", "bom dia", "boa tarde", "boa noite", "bem-vindo", "bem-vinda",
            "bem-vindos", "bem vindo", "bem vindos", "sejam", "e aí", "eu sou", "meu nome",
            "tudo bem", "pessoal", "gente"]
FECHAMENTO = ["vem", "venha", "vem pra", "inscreva", "inscrição", "inscrições", "matrícula",
              "matricule", "acesse", "link", "siga", "segue", "te espero", "esperamos",
              "até mais", "até a próxima", "tchau", "obrigado", "obrigada", "não perca",
              "garanta", "vagas", "agende", "participe", "venham", "aguardo", "contato"]


def _limpa(p):
    return p["w"].lower().strip(".,!?;:\"'")


def _texto(item):
    return " " + " ".join(_limpa(p) for p in item["palavras"]) + " "


def _pontua(texto, termos, inicio=True):
    """Conta termos, com peso maior quando aparecem no começo (saudação)
    ou no fim (convite/despedida) da fala."""
    pontos = 0.0
    n = max(len(texto), 1)
    for t in termos:
        pos = texto.find(" " + t + " ")
        if pos >= 0:
            rel = pos / n
            peso = (1.5 - rel) if inicio else (0.5 + rel)
            pontos += peso
    return pontos


def tirar_recomecos(palavras, max_erro=5, janela_seg=4.0):
    """Quando a pessoa erra e recomeça a frase no mesmo take ('a escola tem... a escola tem cursos'),
    fica só a última versão. Cuidado: na fala normal as mesmas palavras voltam ('a gente vai... a gente
    também'), e isso NÃO é erro. Só tira quando o recomeço é bem claro:
    - 3 palavras iguais repetidas logo em seguida (até 5 palavras no meio), ou
    - 2 palavras iguais, coladas (até 2 palavras no meio) e com uma pausa de respiro antes do recomeço.
    Marca a primeira palavra que sobrou com x=(início, fim) do trecho removido."""
    ps = [dict(p) for p in palavras]
    norm = [_limpa(p) for p in ps]
    i, saida = 0, []
    while i < len(ps):
        removido = None
        for n, erro in ((3, max_erro), (2, 2)):
            if i + n > len(ps) or any(w in HESITACOES for w in norm[i:i + n]):
                continue
            ref = norm[i:i + n]
            for j in range(i + n, min(len(ps) - n + 1, i + n + erro + 1)):
                if ps[j]["s"] - ps[i]["s"] > janela_seg:
                    break
                if norm[j:j + n] == ref:
                    if n == 2 and ps[j]["s"] - ps[j - 1]["e"] < 0.25:
                        continue  # sem pausa: é só a pessoa falando normal
                    removido = j
                    break
            if removido:
                break
        if removido:
            ps[removido]["x"] = (ps[i]["s"], ps[removido - 1]["e"])
            i = removido
            continue
        saida.append(ps[i])
        i += 1
    return saida


def _trechos_de_fala(item, cfg):
    """Quebra a fala em trechos, cortando as pausas longas e hesitações soltas."""
    sel = cfg["selecao"]
    pausa_max = {"natural": 0.6, "dinamico": 0.4, "seco": 0.25}.get(sel.get("ritmo_fala", "natural"),
                                                                    sel["pausa_max_seg"])
    if cfg.get("entrevista"):
        pausa_max = max(pausa_max, 0.9)  # entrevista: respiros naturais ficam, só as pausas longas saem
    energia = item.get("energia")
    palavras = tirar_recomecos(item["palavras"]) if sel.get("tirar_recomecos", True) else item["palavras"]
    grupos, atual = [], []
    for p in palavras:
        if atual and (p["s"] - atual[-1]["e"] > pausa_max or p.get("x") is not None):
            grupos.append(atual)
            atual = []
        atual.append(p)
    if atual:
        grupos.append(atual)

    trechos = []
    for g in grupos:
        if all(_limpa(p) in HESITACOES for p in g):
            continue
        ini = max(item.get("ini_util", 0.0), g[0]["s"] - sel["margem_antes_seg"])
        fim = min(item.get("fim_util", item["duracao"]), g[-1]["e"] + sel["margem_depois_seg"])
        recomeco = g[0].get("x")
        if recomeco is not None:  # tem um erro tirado logo antes: não deixa o começo dele vazar
            ini = max(ini, recomeco[1] - 0.02, g[0]["s"] - 0.15)
            if trechos:
                trechos[-1]["fim"] = min(trechos[-1]["fim"], recomeco[0])
        if energia and sel.get("cortes_precisos", True):  # cola o corte no ponto mais silencioso por perto
            ini = max(item.get("ini_util", 0.0), min(ponto_silencioso(energia, ini, 0.12, 0.06), g[0]["s"] - 0.03))
            fim = min(item.get("fim_util", item["duracao"]), max(ponto_silencioso(energia, fim, 0.06, 0.15), g[-1]["e"] + 0.03))
        if trechos and ini <= trechos[-1]["fim"] and recomeco is None:
            trechos[-1]["fim"] = fim
            trechos[-1]["palavras"] += g
        else:
            trechos.append({"ini": ini, "fim": fim, "palavras": list(g)})
    return [t for t in trechos if t["fim"] - t["ini"] >= 0.4]


def _janelas(item, duracao, max_janelas=4):
    """Os melhores pedaços (sem sobreposição) de um take sem fala.
    Um take longo pode render várias janelas diferentes."""
    base = item.get("ini_util", 0.0)  # começo do que sobrou depois dos cortes da pessoa
    dur = item.get("fim_util", item["duracao"]) - base
    if dur < 1.2:
        return []
    janela = min(duracao, dur - 0.6) if dur > 2 else dur
    ini_min = 0.3 if dur > janela + 0.6 else 0.0
    ini_max = dur - janela - 0.3 if dur > janela + 0.6 else max(0.0, dur - janela)
    notas = [(tt - base, n) for (tt, n) in item["visual"]]

    candidatos = []
    t = ini_min
    while t <= ini_max + 1e-6:
        dentro = [n for (tt, n) in notas if t <= tt <= t + janela] if notas else [0.5]
        if dentro:
            candidatos.append((sum(dentro) / len(dentro), t))
        t += 0.25
    candidatos.sort(reverse=True)

    escolhidas = []
    for nota, t in candidatos:
        if all(abs(base + t - e["ini"]) >= janela for e in escolhidas):
            escolhidas.append({"item": item, "ini": base + t, "fim": base + t + janela, "nota": max(nota, 0.0)})
        if len(escolhidas) >= max_janelas:
            break
    return escolhidas


def _remover_repeticoes(falas, cfg):
    """Quando a mesma fala foi gravada mais de uma vez, fica só o melhor take."""
    lim = cfg["selecao"]["similaridade_repeticao"]
    grupos = []
    for it in falas:
        palavras = [_limpa(p) for p in it["palavras"]]
        for g in grupos:
            if SequenceMatcher(None, palavras, g["ref"]).ratio() >= lim:
                g["itens"].append(it)
                break
        else:
            grupos.append({"ref": palavras, "itens": [it]})

    escolhidos, descartados = [], []
    for g in grupos:
        def nota(it):
            hes = sum(1 for p in it["palavras"] if _limpa(p) in HESITACOES)
            pausas = sum(1 for a, b in zip(it["palavras"], it["palavras"][1:]) if b["s"] - a["e"] > 0.8)
            nitidez = (sum(n for _, n in it["visual"]) / len(it["visual"])) if it.get("visual") else 0.5
            return (it["confianca"] - 0.04 * hes - 0.03 * pausas + 0.5 * it.get("clareza", 0.0)
                    + 0.3 * nitidez + 0.02 * it["ordem"])
        g["itens"].sort(key=nota, reverse=True)
        escolhidos.append(g["itens"][0])
        descartados += g["itens"][1:]
    return escolhidos, descartados


def _ordenar_falas(blocos, cfg=None):
    """Saudação abre, convite/despedida fecha, o meio fica em ordem livre."""
    if len(blocos) <= 1:
        return blocos
    resto = list(blocos)
    abertura = max(resto, key=lambda b: _pontua(_texto(b["item"]), ABERTURA, True))
    if _pontua(_texto(abertura["item"]), ABERTURA, True) > 0:
        resto.remove(abertura)
    else:
        abertura = None
    fechamento = max(resto, key=lambda b: _pontua(_texto(b["item"]), FECHAMENTO, False))
    if _pontua(_texto(fechamento["item"]), FECHAMENTO, False) > 0 and len(resto) > 1:
        resto.remove(fechamento)
    else:
        fechamento = None
    if (cfg or {}).get("selecao", {}).get("ordem_meio", "sortear") != "gravacao":
        random.shuffle(resto)
    return ([abertura] if abertura else []) + resto + ([fechamento] if fechamento else [])


def _assinatura(j):
    ass = [h for (t, h) in j["item"].get("assinaturas", []) if j["ini"] <= t <= j["fim"]]
    if not ass:
        return None
    n = len(ass[0])
    return [sum(h[k] for h in ass) / len(ass) for k in range(n)]


def _distancia(a, b):
    if a is None or b is None:
        return 0.5
    return sum(abs(x - y) for x, y in zip(a, b)) / 2


def _escolher_variados(janelas, limite):
    """Escolhe imagens de lugares diferentes: cada nova escolha prefere a que mais se
    parece pouco com as já escolhidas (cores), sem esquecer a nota de qualidade."""
    if len(janelas) <= 2:
        return _embaralhar_sem_repetir(list(janelas))[:limite]
    restantes = list(janelas)
    random.shuffle(restantes)
    sig = {id(j): _assinatura(j) for j in restantes}
    escolhidas = [restantes.pop(0)]
    while restantes and len(escolhidas) < limite:
        def valor(j):
            d = min(_distancia(sig[id(j)], sig[id(e)]) for e in escolhidas)
            outro_take = 0.15 if all(j["item"] is not e["item"] for e in escolhidas[-1:]) else 0.0
            return d + 0.35 * j["nota"] + outro_take
        melhor = max(restantes, key=valor)
        restantes.remove(melhor)
        escolhidas.append(melhor)
    return escolhidas


def _embaralhar_sem_repetir(janelas):
    """Ordem aleatória, evitando o mesmo take duas vezes seguidas."""
    random.shuffle(janelas)
    for i in range(1, len(janelas)):
        if janelas[i]["item"] is janelas[i - 1]["item"]:
            for j in range(i + 1, len(janelas)):
                if janelas[j]["item"] is not janelas[i - 1]["item"]:
                    janelas[i], janelas[j] = janelas[j], janelas[i]
                    break
    return janelas


def _narracao(bloco, pool, cfg, rosto_fim=0.0):
    """Narração: a pessoa aparece no começo e depois a voz continua sobre
    imagens do local, em sequência (como no Dia de Campo)."""
    c = cfg.get("cobertura", {})
    dur_fala = sum(t["fim"] - t["ini"] for t in bloco["trechos"])
    rosto_ini = c.get("rosto_no_inicio_seg", 3.0)
    if not pool or dur_fala < rosto_ini + 2.0:
        return
    t, cobs = rosto_ini, []
    while pool and t < dur_fala - rosto_fim - 1.0:
        j = pool.pop(0)
        d = min(j["fim"] - j["ini"], dur_fala - rosto_fim - t)
        if d < 1.0:
            break
        cobs.append({"item": j["item"], "ini": j["ini"], "fim": j["ini"] + d, "t": t})
        t += d
    bloco["coberturas"] = cobs


def e_fala(item, cfg):
    """Fala de verdade = alguém falando para a câmera: fala contínua, palavras claras.
    Conversa de fundo (pessoas falando longe, burburinho) costuma ter pouca fala
    no take e palavras com baixa certeza, então vira imagem do local (muda, só música).
    O nome do arquivo pode forçar: contendo 'local' vira imagem, contendo 'fala' vira fala."""
    if cfg.get("entrevista"):
        return True  # aba Entrevistas: todos os takes são fala
    sel = cfg["selecao"]
    nome = item["path"].stem.lower()
    if "local" in nome:
        return False
    if "fala" in nome:
        return True
    return (item["tempo_fala"] >= sel["min_fala_seg"]
            and item.get("n_palavras", 99) >= sel.get("fala_min_palavras", 5)
            and item.get("clareza", 1.0) >= sel.get("fala_min_clareza", 0.6)
            and item.get("densidade", 1.0) >= sel.get("fala_min_densidade", 0.3)
            and item.get("confianca", 0.0) >= sel.get("fala_min_confianca", -0.9))


def montar_entrevista(itens, cfg):
    """Aba Entrevistas: todos os takes são fala e a ordem é exatamente a que a pessoa definiu
    (os itens chegam na ordem dos nomes dos arquivos, que o app numera 01, 02, 03...).
    Só tira pausas longas e hesitações soltas. Nada é descartado, embaralhado nem coberto por imagens.
    Take em que o Whisper não achou palavras entra inteiro, porque a pessoa o colocou na lista."""
    roteiro = []
    for it in itens:
        trechos = _trechos_de_fala(it, cfg)
        if not trechos:
            trechos = [{"ini": it.get("ini_util", 0.0), "fim": it.get("fim_util", it["duracao"]), "palavras": []}]
        roteiro.append({"tipo": "fala", "item": it, "trechos": trechos, "coberturas": []})
    return roteiro, []


def montar_roteiro(itens, cfg):
    """Estrutura baseada nos vídeos de referência:
      - sempre abre com a pessoa falando
      - entre uma fala e outra, um de dois estilos (alternando):
          montagem: sequência de imagens do local com a música alta (como no Vlog)
          narração: a voz continua sobre imagens do local (como no Dia de Campo)
      - a última fala fica com a pessoa na tela
      - fecha com a tela de encerramento (logo)"""
    if cfg.get("entrevista"):
        return montar_entrevista(itens, cfg)
    sel = cfg["selecao"]
    cob = cfg.get("cobertura", {})
    falas = [i for i in itens if e_fala(i, cfg)]
    locais = [i for i in itens if i not in falas]

    descartados = []
    if sel["remover_takes_repetidos"] and len(falas) > 1:
        falas, descartados = _remover_repeticoes(falas, cfg)

    blocos_fala = []
    for it in falas:
        trechos = _trechos_de_fala(it, cfg)
        if trechos:
            blocos_fala.append({"tipo": "fala", "item": it, "trechos": trechos, "coberturas": []})
    blocos_fala = _ordenar_falas(blocos_fala, cfg)

    # todas as janelas boas das imagens do local, em ordem aleatória
    pool = []
    for it in locais:
        pool += _janelas(it, sel["duracao_local_seg"])
    pool = [j for j in pool if j["nota"] >= 0.2] or pool
    pool = _escolher_variados(pool, sel["max_takes_local"])

    def bloco_local(j):
        return {"tipo": "local", "item": j["item"], "coberturas": [],
                "trechos": [{"ini": j["ini"], "fim": j["fim"], "palavras": []}]}

    if not blocos_fala:  # só imagens do local: vira um clipe de ambientação
        return [bloco_local(j) for j in pool], descartados

    usar_cobertura = cob.get("ativar", True)
    n_montagem = cob.get("locais_na_montagem", 5)

    # um estilo para cada intervalo entre falas, alternando (começo sorteado)
    intervalos = len(blocos_fala) - 1
    primeiro = random.choice(["montagem", "narracao"])
    outro = "narracao" if primeiro == "montagem" else "montagem"
    estilos = [primeiro if k % 2 == 0 else outro for k in range(intervalos)]
    if not usar_cobertura:
        estilos = ["montagem"] * intervalos

    # divide as imagens entre todos os intervalos, para nunca ficar fala colada em fala
    por_intervalo = max(1, min(n_montagem, len(pool) // max(1, intervalos))) if intervalos else 0
    montagens = {}
    for k, e in enumerate(estilos):
        if e == "montagem":
            montagens[k] = [pool.pop(0) for _ in range(min(por_intervalo, len(pool)))]
    for k, e in enumerate(estilos):
        if e == "narracao":
            parte = pool[:por_intervalo]
            del pool[:por_intervalo]
            _narracao(blocos_fala[k], parte, cfg)
            pool[:0] = parte  # devolve o que não foi usado
            if not blocos_fala[k]["coberturas"]:  # fala curta demais para narração: vira montagem
                montagens[k] = [pool.pop(0) for _ in range(min(por_intervalo, len(pool)))]
    # sobrou imagem? reforça as montagens até o limite
    for k in sorted(montagens):
        while pool and len(montagens[k]) < n_montagem:
            montagens[k].append(pool.pop(0))
    if len(blocos_fala) == 1 and usar_cobertura:
        # uma fala só: rosto no começo, narração no meio, rosto no final
        _narracao(blocos_fala[0], pool, cfg, rosto_fim=cob.get("rosto_no_fim_seg", 2.5))

    roteiro = []
    for k, bf in enumerate(blocos_fala):
        roteiro.append(bf)
        roteiro += [bloco_local(j) for j in montagens.get(k, [])]

    # respeita a duração máxima tirando imagens das montagens (falas nunca são cortadas)
    def dur(b):
        return sum(t["fim"] - t["ini"] for t in b["trechos"])
    while sum(dur(b) for b in roteiro) > sel["duracao_max_final_seg"]:
        locais_idx = [k for k, b in enumerate(roteiro) if b["tipo"] == "local"]
        if not locais_idx:
            break
        roteiro.pop(locais_idx[-1])

    if sel.get("gancho", False):
        g = _gancho(blocos_fala, roteiro, cfg)
        if g:
            roteiro.insert(0, g)
    return roteiro, descartados


PROMESSA = {"descubra", "segredo", "novidade", "novo", "nova", "grátis", "gratuito", "gratuita", "bolsa", "desconto",
            "vagas", "incrível", "imperdível", "transforma", "mudar", "primeira", "único", "única", "garanta", "veja"}


def _gancho(blocos_fala, roteiro, cfg):
    """Frase mais forte (pergunta, número ou promessa), curta, para abrir o vídeo nos primeiros segundos."""
    melhor, nota_melhor = None, 0.0
    for b in blocos_fala:
        for t in b["trechos"]:
            pals = [p for p in t["palavras"]]
            for i in range(len(pals)):
                for j in range(i + 4, min(len(pals), i + 14) + 1):
                    seg = pals[i:j]
                    d = seg[-1]["e"] - seg[0]["s"]
                    if d < 1.5 or d > 4.5:
                        continue
                    textos = [_limpa(p) for p in seg]
                    nota = 0.0
                    if seg[-1]["w"].endswith("?"):
                        nota += 2.0
                    nota += 1.5 * sum(1 for w in textos if any(c.isdigit() for c in w) or w in ("mil", "milhão", "milhões"))
                    nota += 1.0 * sum(1 for w in textos if w in PROMESSA)
                    nota += 0.5 if seg[-1]["w"].endswith((".", "!", "?")) else 0.0
                    if any(w in textos for w in ABERTURA[:8]):
                        nota -= 2.0
                    if nota > nota_melhor:
                        melhor, nota_melhor = (b, seg), nota
    if not melhor or nota_melhor < 1.5:
        return None
    b, seg = melhor
    return {"tipo": "fala", "item": b["item"], "coberturas": [], "gancho": True,
            "trechos": [{"ini": max(b["item"].get("ini_util", 0.0), seg[0]["s"] - 0.12),
                         "fim": min(b["item"].get("fim_util", b["item"]["duracao"]), seg[-1]["e"] + 0.2),
                         "palavras": [dict(p) for p in seg]}]}


def duracao_conteudo(roteiro):
    return sum(sum(t["fim"] - t["ini"] for t in b["trechos"]) for b in roteiro if b["tipo"] != "encerramento")


def completar_duracao(roteiro, itens, cfg):
    """Reels precisam de mais de 30 s. Se faltar tempo, acrescenta mais imagens
    do local nas montagens (sem repetir o mesmo trecho) e, se ainda faltar,
    alonga as imagens que já estão no vídeo."""
    if cfg.get("entrevista"):
        return 0  # não há imagens do local para completar; a duração é a das falas
    sel = cfg["selecao"]
    minimo = sel.get("duracao_min_final_seg", 30) + 1.0  # 1 s de folga pelas transições
    falta = minimo - duracao_conteudo(roteiro)
    if falta <= 0:
        return 0
    locais = [i for i in itens if not e_fala(i, cfg)]
    usados = [(b["item"]["path"], t["ini"], t["fim"]) for b in roteiro for t in b["trechos"]]
    usados += [(c["item"]["path"], c["ini"], c["fim"]) for b in roteiro for c in b.get("coberturas", [])]

    def livre(j):
        return all(not (p == j["item"]["path"] and j["ini"] < f and j["fim"] > i) for p, i, f in usados)

    extras = []
    for it in locais:
        extras += [j for j in _janelas(it, sel["duracao_local_seg"], max_janelas=8) if livre(j)]
    extras = _embaralhar_sem_repetir(extras)

    # onde inserir: depois da última imagem de cada montagem, alternando; sem montagem, após a 1ª fala
    pontos = [k for k, b in enumerate(roteiro)
              if b["tipo"] == "local" and (k + 1 >= len(roteiro) or roteiro[k + 1]["tipo"] != "local")]
    if not pontos:
        falas = [k for k, b in enumerate(roteiro) if b["tipo"] == "fala"]
        pontos = [falas[0]] if falas else [0]
    adicionados, n = 0, 0
    while falta > 0 and extras:
        j = extras.pop(0)
        alvo = pontos[n % len(pontos)]
        roteiro.insert(alvo + 1, {"tipo": "local", "item": j["item"], "coberturas": [],
                                  "trechos": [{"ini": j["ini"], "fim": j["fim"], "palavras": []}]})
        pontos = [p + 1 if p >= alvo + 1 else p for p in pontos]
        pontos[n % len(pontos)] = alvo + 1
        falta -= j["fim"] - j["ini"]
        adicionados += 1
        n += 1

    # ainda curto: alonga as imagens até 5 s cada, se o take tiver material
    if falta > 0:
        for b in roteiro:
            if falta <= 0:
                break
            if b["tipo"] == "local":
                t = b["trechos"][0]
                extra = min(5.0 - (t["fim"] - t["ini"]), b["item"].get("fim_util", b["item"]["duracao"]) - 0.05 - t["fim"], falta)
                if extra > 0:
                    t["fim"] += extra
                    falta -= extra
    if falta > 0:
        print(f"  aviso: faltou material para chegar a {minimo - 1:.0f} s (faltaram {falta:.1f} s).")
    return adicionados
