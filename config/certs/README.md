# Certificados de Autoridade Certificadora

Uma CA não é um segredo. O que é secreto é a `api_key`, a senha e o
`api_secret`. O certificado vai no Git, e é por isso que esta pasta existe.

## A CA do MySQL do Aiven

O Aiven assina cada serviço com uma CA própria, e a consola dá o
certificado em **Connection settings** do serviço, num ficheiro `ca.pem`.
Não é a CA pública do sistema operativo, e não vem no pacote de CA do
Python: sem esta, a ligação fica sem verificação.

Descarrega-o para `config/certs/aiven-mysql-ca.pem` e aponta
`DJANGO_DB_SSL_CA` para o caminho. O ficheiro é versionado, com o mesmo
raciocínio dos dados do geoBoundaries: uma dependência de terceiro com
versão fixa, que não muda porque o servidor de terceiro mudou.

Quando actualizares a CA, confirma que o certificado novo continua a
servir o mesmo serviço antes de o substituir, e faz os dois commits: o
certificado novo e a variável que o aponta. Uma CA trocada sem o
`DJANGO_DB_SSL_CA` a apontar para ela é uma ligação que deixa de
verificar o servidor e continua a funcionar — que é a falha mais
perigosa deste ficheiro, porque não dá erro.

## Porque `ssl-mode=REQUIRED` não chega

O URI do Aiven traz `ssl-mode=REQUIRED`, e essa palavra é para clientes
que falam o protocolo do Aiven. O Django passa o `OPTIONS` do MySQL
tal e qual ao `connect()` do PyMySQL, e o PyMySQL não tem `ssl_mode`:
passá-la dá um `TypeError` na primeira ligação.

O mesmo `REQUIRED` existe no PyMySQL, dentro de `ssl.verify_mode`, e é
aí que vai. Ver `db_options()` em `config/settings/base.py`, e o teste
`test_as_opcoes_do_mysql_sao_todas_aceites_pelo_pymysql` em
`apps/core/test_deploy.py`, que falha se alguém reintroduzir a
palavra no sítio errado.
