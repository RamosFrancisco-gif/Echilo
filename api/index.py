"""Ponto de entrada da função Python na Vercel.

A Vercel procura uma WSGI em `api/index.py` e usa a variável `app`. A imagem
não tem servidor web nem `runserver`: cada pedido é uma invocação desta função,
que morre no fim. É a razão de o `MEDIA_ROOT` não servir para ficheiros e de o
cache por memória não servir para contar tentativas de login.

O `setdefault` e não `[]=` porque `DJANGO_SETTINGS_MODULE` continua a mandar,
que é o que permite correr `vercel dev` com as settings de desenvolvimento. Sem
esta linha, a Vercel arrancava o projecto com `development.py` e publicava um
sítio com `DEBUG=True`, cookie sem `secure` e ficheiros apontados para um disco
que desaparece no fim da invocação.
"""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")

from config.wsgi import application as app  # noqa: E402 — a variável tem de existir antes
