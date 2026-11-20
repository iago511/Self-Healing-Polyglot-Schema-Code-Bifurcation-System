# Guia de execução

## Local

Use Python 3.11 ou superior, Git, PostgreSQL, Docker e Ollama. A conta PostgreSQL precisa enxergar o schema e as colunas monitoradas. Use uma conta com acesso somente leitura.

```bash
python -m pip install -r requirements.txt
ollama pull qwen2.5-coder:3b
```

Copie `.env.example` para `.env` e preencha `PROJECT_PATH`, `DB_URL` e `GROQ_API_KEY`. Ajuste `PG_SCHEMA` quando o schema for diferente de `public`. O Ollama usa a instância local por padrão. Configure `IMAGEM_TESTE` com uma imagem Docker que tenha Python, pytest e as dependências do projeto monitorado. O executor usa a imagem local, executa sem rede e bloqueia a aprovação quando o pytest fica pendente ou falha.

```bash
python main.py monitor --uma-vez
python main.py monitor
```

O primeiro ciclo registra o schema inicial. Os próximos registram as mudanças de tabelas e colunas e avançam os eventos no grafo. O SQLite e os artefatos ficam na pasta de dados do usuário por padrão. `EVENTOS_DB_PATH` e `PROPOSTAS_DIR` permitem escolher outros locais, sempre fora do projeto monitorado.

## Revisão e publicação

```bash
python main.py pendentes
python main.py pendentes --falhas
python main.py evento ID_EVENTO
python main.py esclarecer ID_EVENTO --resposta "A coluna foi renomeada no mesmo cadastro."
python main.py reprocessar ID_EVENTO
python main.py ajustar ID_EVENTO --proposta ID_PROPOSTA --autor iago --motivo "Preserve o formato da API."
python main.py rejeitar ID_EVENTO --proposta ID_PROPOSTA --autor iago
python main.py aprovar ID_EVENTO --proposta ID_PROPOSTA --autor iago
python main.py publicar ID_EVENTO --remoto origin
```

O comando `evento` mostra a análise, o caminho do diff, os testes, o parecer e o histórico. Para aprovar, o projeto monitorado precisa estar na raiz de um repositório Git com checkout limpo e identidade configurada. A aprovação cria um commit em `sh-pscbs/ID_PROPOSTA`, preservando o checkout original. `publicar` envia só essa branch. O merge fica a cargo do repositório de destino.

```bash
python main.py git-status
python main.py copiar /caminho/para/uma/pasta-nova
```

A cópia exclui o histórico do Git, arquivos `.env`, links, ambientes virtuais e caches. Use `reprocessar` após configurar a imagem de testes ou corrigir um erro de infraestrutura. Use `ajustar` para passar instruções de correção ao agente.

## Docker

Configure `TARGET_PROJECT_PATH` no `.env` com o caminho do projeto no host. Para PostgreSQL e Ollama no host, substitua `127.0.0.1` por `host.docker.internal` nas URLs e habilite o acesso nesses serviços.

```bash
docker compose build
docker compose run --rm monitor monitor --uma-vez
docker compose up -d monitor
docker compose run --rm monitor pendentes
```

Por padrão, o Compose monta o projeto como somente leitura e persiste o SQLite e as propostas no volume `eventos`. Para rodar o pytest pelo monitor em Docker com um daemon confiável, use o arquivo adicional e informe em `DOCKER_SOCKET_GID` o grupo com acesso ao socket. Esse acesso dá ao monitor controle sobre o daemon.

```bash
docker compose -f compose.yaml -f compose.testes.yaml up -d monitor
docker compose -f compose.yaml -f compose.testes.yaml run --rm monitor reprocessar ID_EVENTO
```

Para aprovar via Compose, habilite `TARGET_PROJECT_READ_ONLY=false`, conceda ao usuário do contêiner permissão para gravar em `.git` e configure as identidades de autor e committer no Git. Para publicar, configure também as credenciais do remoto no ambiente de execução.

## Testes do SH-PSCBS

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
docker build --target test -t sh-pscbs:pytest .
```

O teste de integração usa um repositório Git temporário. Para incluir PostgreSQL e Docker reais na execução, aponte `SH_TEST_POSTGRES_DSN` para um banco descartável com permissão de criar schema e `SH_TEST_DOCKER_IMAGE` para `sh-pscbs:pytest`. Rode `python -m pytest -q testes/test_integracao_grafo.py`. No host, defina `DOCKER_TEST_STORAGE=volume` para testar o modo usado pelo Compose. Os modelos são simulados nesse teste.
