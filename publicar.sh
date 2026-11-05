#!/usr/bin/env bash
# Gregio AutoEdit: envia o código para um repositório PRIVADO (código fechado)
# e cria um repositório PÚBLICO só com os instaladores para as pessoas baixarem.
set -euo pipefail
cd "$(dirname "$0")"
DONO=eduardogregio38-create
git init -q -b main 2>/dev/null || git init -q
git add -A
git -c user.name="Eduardo Gregio" -c user.email="eduardogregio38@gmail.com" commit -qm "Gregio AutoEdit 2.0" || true

# código: privado
gh repo view $DONO/gregio-autoedit >/dev/null 2>&1 || gh repo create $DONO/gregio-autoedit --private
git remote remove origin 2>/dev/null || true
git remote add origin https://github.com/$DONO/gregio-autoedit.git
git push -q -u origin main

# downloads: público, sem código nenhum
if ! gh repo view $DONO/gregio-autoedit-download >/dev/null 2>&1; then
  gh repo create $DONO/gregio-autoedit-download --public --add-readme -d "Gregio AutoEdit: baixe aqui o app para Mac e Windows"
fi
# permissão para o robô do repositório privado publicar os instaladores no público
gh auth token | gh secret set TOKEN_DOWNLOAD -R $DONO/gregio-autoedit

git tag -f v2.0.0 && git push -q -f origin v2.0.0
echo "PRONTO: https://github.com/$DONO/gregio-autoedit/actions"
