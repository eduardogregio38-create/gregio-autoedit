# Gregio AutoEdit

Edição automática de vídeos para Reels, no computador de quem usa (Mac e Windows).

- **Mídias**: arraste os takes de um vídeo; a edição começa sozinha e mostra o andamento real.
- **Vídeos prontos**: assista, aprove (vai para a pasta "Gregio AutoEdit" no seu Google Drive) ou reprove dizendo o que não gostou.
- **Ajustes**: padrão da edição, músicas, logo da capa, tela final e o histórico do que o app aprendeu.

O que a edição faz: separa fala de imagens, corta pausas e repetições, alterna fala e montagens com música,
cortes no ritmo da música, transições com desfoque, vertical 1080x1920, voz limpa, música com ducking,
capa automática, legenda .srt e pacote para CapCut/Premiere/DaVinci.

## Gerar os instaladores
Crie uma tag `vX.Y` (ou rode o workflow "Instaladores" à mão). O GitHub gera:
`Gregio-AutoEdit-Mac-Intel.zip`, `Gregio-AutoEdit-Mac-AppleSilicon.zip` e `Gregio-AutoEdit-Windows-Instalador.exe`.

## Login do Google
`app/credentials.json` (NÃO vai para o git; no build vem dos Secrets GOOGLE_CREDENCIAIS_JSON e GOOGLE_CFG_JSON do GitHub) (cliente OAuth "App para computador") e `app/google.json` (`api_key` e `app_id` do
seletor de pastas) vêm do projeto do Google Cloud do dono do app. Escopo: `drive.file` (o app só vê o que ele criou
ou o que a pessoa escolheu no seletor).

## Segurança
- Código em repositório privado; o repositório público `gregio-autoedit-download` só tem os instaladores.
- Cada pessoa entra com a própria conta Google (OAuth com PKCE). O app pede só `drive.file`: enxerga apenas a pasta
  que ele criou ou que a pessoa escolheu no seletor. Não vê o resto do Drive.
- O login fica salvo só no computador da pessoa (pasta de dados do usuário, permissão 600). Nada passa por servidor nosso.
- O motor local escuta apenas em 127.0.0.1, numa porta aleatória, e exige uma chave nova a cada abertura do app.
  Pedidos sem a chave, de outros sites (Origin) ou com Host diferente (DNS rebinding) são recusados.
- Janela Electron com contextIsolation, sandbox, sem nodeIntegration, sem permissões de câmera/microfone/localização;
  links externos abrem no navegador da pessoa.
