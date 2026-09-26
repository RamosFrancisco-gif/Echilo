"""Arranque do projecto.

O `mysqlclient` compila contra as headers C do MySQL, e a Vercel não as tem: o
build da função falhava antes de o Django ser sequer importado. O `PyMySQL` é o
mesmo protocolo em Python puro, e o Django aceita-o desde que se apresente com o
nome certo.

Este import tem de acontecer antes de qualquer acesso à base de dados, e o
ficheiro mais cedo que o Django toca é o pacote de configuração — por isso vive
aqui, e não numa app.
"""

try:  # pragma: no cover - depende do que está instalado no ambiente
    import pymysql

    pymysql.install_as_MySQLdb()
except ImportError:  # pragma: no cover - ambiente sem o driver, sem MySQL
    pass
