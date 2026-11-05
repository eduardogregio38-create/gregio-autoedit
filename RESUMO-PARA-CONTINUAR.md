# Gregio AutoEdit: resumo para continuar o projeto

Use este arquivo para continuar o projeto em outra conta do Claude. Mande ele no começo da conversa e diga "continue de onde parou".

Dono: Eduardo Gregio · eduardogregio38@gmail.com · GitHub: eduardogregio38-create
Atualizado em: 5 de novembro de 2026 (tarde)

## O que é
App de computador (Mac e Windows) que edita vídeos para Reels sozinho, no próprio computador da pessoa.
Qualquer pessoa baixa, entra com a própria conta Google e usa. Nada da Faculdade UNIGUAÇU (o projeto nasceu lá como "AutoEdit AI", mas foi totalmente desvinculado).

### Telas do app
- **Mídias**: "Vídeos para editar" (nome, título da capa, estilo da música, arrastar takes) + "Andamento" com etapa real, porcentagem e tempo que falta.
- **Entrevistas** (nova, código na v2.2.0, ainda não publicada): para takes que são todos fala. A pessoa coloca os vídeos, define a ordem (número de posição, setas e ✕) e o app respeita essa ordem, sem imagens por cima, sem descartar take repetido, sem embaralhar. Só corta pausas longas (mais de 0,9 s) e "hum" soltos. Música: "Sem música" por padrão, ou um estilo. Por dentro: o app numera os arquivos (01_, 02_...) ao enviar; `projeto.modo = "entrevista"` liga `cfg["entrevista"]`, que muda `selecao.montar_roteiro` (função `montar_entrevista`), `e_fala` e `completar_duracao`. O andamento da entrevista aparece na própria aba.
- **Cortar começo e final** (v2.3.0, código pronto): em cada vídeo da lista, nas abas Mídias e Entrevistas, o botão "✂ Cortar" abre um player. A pessoa marca "Começar daqui" e "Terminar aqui" (ou digita min:seg) para tirar um erro de fala no começo ou no final do take. O original não é mexido. Por dentro: a tela manda `cortes` em `/api/concluir` (nome do arquivo no servidor -> {ini, fim}), o servidor guarda em `projeto.cortes` (`limpar_cortes`), `analise.analisar(..., cortes=)` calcula `ini_util`/`fim_util` de cada take e descarta as palavras fora dele; `selecao` e `ritmo` respeitam essa faixa (falas, janelas de imagens do local e alongamento). Sem recodificar nada. Se o player não abrir o vídeo (ex.: formato que o computador não toca), dá para digitar os tempos.
- **Vídeos prontos**: vídeo + capa, botões Aprovar / Reprovar / Excluir. Aprovados vão para o Google Drive da pessoa e para a pasta local "Vídeos prontos".
- **Ajustes**: padrão da edição com + e −, músicas (a pessoa adiciona as dela por estilo), logo da capa e tela final opcionais, pasta no Drive, versão do app / procurar atualização, histórico do que o app aprendeu.
- Faixa no topo "Versão nova disponível · Atualizar agora" (atualiza sozinho sem perder arquivos).

### O que a edição faz
Separa fala (Whisper) de imagens sem fala; corta pausas, gaguejos e repetições; abertura e fechamento pela fala; alterna montagens com música e narração por cima;
cortes no ritmo da música (detecção de batidas); transição com desfoque entre cenas, corte seco entre imagens; vertical 1080x1920 com fundo desfocado para horizontais;
correção de HDR do iPhone; voz limpa (filtro FFT, EQ, compressão), música com ducking, -14 LUFS; capa automática neutra (melhor quadro + título, logo opcional);
legenda .srt; pacote para CapCut/Premiere/DaVinci. Mínimo 30 s.

### Aprendizado
Ao reprovar, a pessoa marca motivos (música alta/baixa, não combinou, imagens rápidas/lentas, de mais/de menos, cortou fala, pausas, longo/curto, transição lenta, capa, ordem).
Cada motivo ajusta um valor do padrão; "música não combinou" põe a faixa na lista de evitar; aprovar marca a música como boa. Tudo fica em `aprendizado.json` (local) e `_gregio_aprendizado.json` na pasta do Drive (vale em outros computadores).

## Onde está cada coisa
| O quê | Onde |
|---|---|
| Código (privado) | github.com/eduardogregio38-create/gregio-autoedit |
| Instaladores para download (público, só os arquivos) | github.com/eduardogregio38-create/gregio-autoedit-download (Releases) |
| Site (página inicial + privacidade + arquivo de verificação do Google) | repositório eduardogregio38-create.github.io → https://eduardogregio38-create.github.io/ |
| Login do Google | Google Cloud, projeto **gregio-autoedit** (conta eduardogregio38@gmail.com). Status: **Em produção**, escopos drive.file + email + profile. |
| Chave do seletor de pastas (Picker) | mesmo projeto; fica em `app/google.json` |
| Cliente OAuth "App para computador" | mesmo projeto; fica em `app/credentials.json` |
| Verificação do site no Google | Search Console (conta pessoal), propriedade https://eduardogregio38-create.github.io/, arquivo google4993f5c09077c18d.html |
| Terminal para publicar | Cloud Shell da conta pessoal (shell.cloud.google.com?authuser=1&project=gregio-autoedit), com `gh` logado no GitHub. Cópia do repositório em ~/gregio-autoedit |

### Estrutura do código
- `motor/` edição: analise.py (Whisper, qualidade de imagem), selecao.py (roteiro), render.py (FFmpeg), ritmo.py (batidas), capa.py, exportar.py (srt, xml, pacote), editor.py (orquestra; também linha de comando `--teste PASTA`).
- `app/` servidor.py (servidor local 127.0.0.1, fila de edição, projetos, músicas, aprendizado, Drive), gdrive.py (OAuth com PKCE e API do Drive sem bibliotecas extras), index.html (tela), picker.html (seletor de pasta do Drive, abre no navegador).
- `electron/` main.js (janela, sobe o motor), preload.js, atualizador.js (atualização automática), package.json (electron-builder: dmg + zip no Mac, NSIS no Windows).
- `build/` gregio.spec (PyInstaller do motor), preparar.py (baixa FFmpeg/FFprobe, modelo Whisper base, RNNoise), ícones, instalador.iss (não usado mais).
- `.github/workflows/instaladores.yml`: em cada tag `vX.Y.Z` gera Mac Intel (macos-15-intel), Mac Apple Silicon (macos-14) e Windows, testa a edição com takes de teste (voz sintética) e publica no repositório de download.
- `config_padrao.json`: padrão da edição.

### Pastas no computador de quem usa
- `~/Gregio AutoEdit/` → Projetos, Músicas/<estilo>, Vídeos prontos
- Dados do app (login, ajustes, logo, tela final, log): Mac `~/Library/Application Support/Gregio AutoEdit`, Windows `%APPDATA%\Gregio AutoEdit`

## Segurança (decisões)
- Código fechado (repositório privado); público só o repositório de instaladores e o site.
- Escopo `drive.file`: o app só vê a pasta "Gregio AutoEdit" que ele cria (no lugar que a pessoa escolhe pelo seletor do Google) e nada do resto do Drive. Isso evitou a revisão pesada do Google.
- Login salvo só no computador da pessoa; nenhum servidor nosso.
- Motor local escuta só em 127.0.0.1 com porta aleatória e chave nova a cada abertura; recusa pedidos sem a chave, de outros sites (Origin) ou com Host diferente.
- Electron com contextIsolation, sandbox, sem nodeIntegration, permissões bloqueadas.

## Como lançar uma versão nova
1. Alterar o código e gerar um zip com a pasta do projeto.
2. No Cloud Shell: Fazer upload do zip, descompactar e copiar por cima de ~/gregio-autoedit (sem apagar a pasta .git), depois:
   `git add -A && git commit -m "vX.Y.Z" && git push && git tag vX.Y.Z && git push origin vX.Y.Z`
3. O GitHub gera os instaladores (~30–40 min) e publica no repositório de download. Os apps instalados mostram "Atualizar agora".
(O script nova_versao.sh faz isso; o Cloud Shell não tem rsync, então ele usa find + cp.)

## Situação em 5/11/2026 (manhã)
- **Versão 2.1.0 pronta e publicada.** Os 3 instaladores (Mac chip Apple, Mac Intel, Windows) passaram no GitHub, com teste de edição incluído, e a release v2.1.0 está em gregio-autoedit-download com: Gregio-AutoEdit-mac-arm64.dmg e .zip, Gregio-AutoEdit-mac-x64.dmg e .zip, Gregio-AutoEdit-Windows-Instalador.exe. Os 3 links do site (releases/latest/download/...) respondem 200.
- Correções da madrugada: incluir `requests` no pacote do motor; trocar o filtro de ruído RNNoise por FFT (gerava valor inválido no Mac com chip Apple); trocar o runner macos-13 (desativado, ficava na fila para sempre) por macos-15-intel.
- Teste real no MacBook Pro Intel do Eduardo: o app instalou, abriu e mostrou a tela de login com o logo. Ainda NÃO testado: login com o Google, escolha da pasta no Drive e edição de vídeo real (ele testa depois, e vai medir o tempo).
- Aviso do Mac ao abrir: "A Apple não pode verificar se o item está livre de malware" (app sem assinatura paga). Solução para o usuário: Ajustes do Sistema > Privacidade e Segurança > "Abrir Mesmo Assim"; se o botão não aparecer, tentar abrir o app uma vez antes, ou no Terminal: `xattr -cr /Applications/Gregio\ AutoEdit.app`. **Falta colocar esse passo no site** (seção "Primeira vez abrindo" em site/index.html, hoje só fala do botão direito > Abrir, que não funciona em macOS novos). Publicar no repositório eduardogregio38-create.github.io (o usuário roda os comandos no Cloud Shell, o classificador bloqueia publicar daqui).
- Site no ar (https://eduardogregio38-create.github.io/) e verificado no Search Console. App do Google "Em produção".
- Logo do Google (Branding): arquivo logo-gregio-120.png; o Eduardo ainda precisa escolher o arquivo e clicar em Salvar (opcional, revisão do Google leva 2 a 5 dias úteis).
- A tag v2.0.0 falhou e não foi publicada (ignorar).
- Cloud Shell: o terminal só aceita digitar pela ação de teclas (`key`), no máximo 100 teclas por chamada.

## Pendente de publicar
- Versão 2.2.0 (aba Entrevistas) já foi enviada e o GitHub estava montando os instaladores. Versão 2.3.0 (cortes de começo e final, pacote gregio-autoedit-2.3.0.zip) criada em seguida, ainda NÃO enviada; ela inclui tudo da 2.2.0. Antes: conferir se a release v2.2.0 saiu certa. Testado aqui (2.2.0 e 2.3.0): montagem com takes simulados, cortes de começo e final conferidos no vídeo final, edição completa com transcrição simulada (ordem, corte de pausa e take sem palavras conferidos no vídeo final) e a tela com servidor falso. NÃO testado: transcrição real (Whisper) e o app empacotado. Para publicar: subir o zip no Cloud Shell, `cd ~ && rm -rf gregio && unzip -oq gregio-autoedit-2.3.0.zip && bash gregio/nova_versao.sh 2.3.0` (para a 2.2.0 foi o mesmo comando com 2.2.0).
- Anotado para a revisão do código: na legenda (.srt) uma frase pode juntar as últimas palavras de um take com as primeiras do seguinte, em vez de quebrar na troca de take.
- Nome/marca: pesquisa de 5/11/2026 no INPI não achou "Gregio AutoEdit". Há "GREGIO" (classe 35, relógios, pedido em análise) e "15 Sec. Auto Editor" da LG (classe 9, em vigor). Domínio gregioautoedit.com.br livre (R$ 40/ano). Antes de vender, fazer análise com agente de propriedade industrial e registrar nas classes 9 e 42.

## Próximos passos combinados
1. O Eduardo testa editando vídeos reais (3 a 5 diferentes), anota o que não gostou (corte, música, capa, erros) e guarda os vídeos. Medir o tempo da edição (meta 5 a 7 min).
2. **Revisão completa do código** para ficar 100% funcional e com vídeos bons para postar (casos de borda: vídeo longo, arquivo estranho, internet caindo no envio, login expirado, fechar o app no meio). Fazer em conversa nova, de preferência com o Opus. Lembrar: só testes com vídeos reais garantem a qualidade.
3. (feito e no ar) Site corrigido (passo a passo do aviso do Mac, requisito macOS 11+, script publicar_site.sh apontando para eduardogregio38-create.github.io).
4. **Dropbox** como opção de login/armazenamento além do Google Drive (registrar o app em dropbox.com/developers com a conta do Eduardo; "App folder").
5. **Preparar para vender**: modelo de cobrança (licença, assinatura ou compra única), verificação de pagamento no app, termos de uso (incluir que as músicas são de responsabilidade de cada pessoa), assinatura da Apple (US$ 99/ano) e certificado do Windows para tirar os avisos.
6. Melhorar a primeira página do app (tela de login), a combinar.
7. Opcional: domínio próprio (gregioautoedit.com.br no registro.br) + Cloudflare; Microsoft Store; versão para celular (conversar antes).
8. Sistema da Faculdade UNIGUAÇU (repositório autoedit-ia, na conta da empresa): continua separado e não foi alterado. Se quiser, levar para ele as melhorias do motor novo, depois que o app novo estiver testado e com autorização de quem for responsável na empresa.

## Preferências do Eduardo
Português do Brasil natural e simples, sem travessões e sem frases feitas. Explicar passo a passo quando ele precisar fazer algo. Ele prefere que o Claude faça tudo o que puder sozinho e só peça a ele o que exige a conta dele (pagamentos, aceites de termos, publicar algo público, uploads de arquivo).

## v2.3.0 (atualizado): botão Recarregar
- Botão "Recarregar" na barra superior (app/index.html). Após login Google, o app confere /api/estado (também ao voltar o foco da janela) e dá location.reload() sozinho; a chave fica no sessionStorage "gk". Electron com backgroundThrottling:false.

## v2.3.0: tudo do checklist aprovado (14h-15h, 5/nov)
Feito (código + testes no sandbox, sem vídeo real): C1 C2 C3 C4 C6 C7 C9, A1 A2 A3, M1 M2 M3 M4 M5 M6 M7, V4 V6 V8 V9, L1 L3 L5, K1 K2 K3 K4 K5 (+ capa por take/momento ou imagem própria), E1 E3 E4 E5, T1(parcial: trocar capa/música/legenda, sem linha do tempo de cenas) T2(parcial) T3 T4 T6, R1 R2 R3 R4 R5 R6(upload já retomava; login 401 volta à tela de login) R7 R8 R9, P5, U1 U2 U3 U5 U6 U7, N2 (termos.html novo).
Não feito / depende do Eduardo: A4 e V1 (precisam de gravações reais), N1 (modelo de cobrança), N7 (Microsoft Store: conta de desenvolvedor), P4 (Dropbox/OneDrive: app key), U4 (inglês/espanhol), N6 (preços e vídeo de demonstração), U1 vídeo de exemplo, T1 linha do tempo completa, T2 mais motivos.
Pontos de atenção: fontes divertidas são baixadas no build (build/preparar.py, URLs não verificadas); AJUDA_EMAIL em app/servidor.py; testes: testes/teste_cenarios.py (CI, continue-on-error).


## v2.3.2
- Regra de recomeços (tirar_recomecos) ficou conservadora: só tira repetição clara (3 palavras, ou 2 palavras coladas com pausa). Antes apagava trechos da fala normal.
- Ducking da música: release 400 ms -> 1200 ms (música não sobe nas pausas curtas).
- Pendente: fonte 'Made Out Sender' (Eduardo vai enviar o arquivo); layout novo do app na 2.4.
- Achado: no vídeo IMG_3077 o Eduardo só cortou o começo (1,3 s) e manteve a fala contínua; app deve respeitar fala corrida.


## v2.4.0
- Capa no estilo caixa (Canva): caixa de cor lisa, etiqueta opcional com qualquer texto, título, logo da pessoa dentro da caixa. Sem nada da Faculdade UNIGUAÇU. Painel com prévia ao vivo (/api/capa_teste).
- Pendente: fonte Made Out Sender (aguardando arquivo); editor estilo CapCut (2.5); novo layout.
