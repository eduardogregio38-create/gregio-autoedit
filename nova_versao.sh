#!/usr/bin/env bash
# Publica uma versão nova do Gregio AutoEdit.  Uso: bash nova_versao.sh 2.1.0
# Roda a partir da pasta descompactada do pacote novo; copia por cima do repositório em ~/gregio-autoedit.
set -euo pipefail
VERSAO="${1:?Informe a versão, ex.: 2.1.0}"
ORIGEM="$(cd "$(dirname "$0")" && pwd)"
REPO="$HOME/gregio-autoedit"
cd "$REPO"
find "$REPO" -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
cp -a "$ORIGEM/." "$REPO/"
git add -A
git -c user.name="Eduardo Gregio" -c user.email="eduardogregio38@gmail.com" commit -qm "Gregio AutoEdit $VERSAO" || true
git push -q origin main
git tag -f "v$VERSAO" && git push -q -f origin "v$VERSAO"
echo "PRONTO: versão $VERSAO enviada. Instaladores em https://github.com/eduardogregio38-create/gregio-autoedit/actions"
