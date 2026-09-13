# SH-PSCBS

Sistema que monitora seu banco PostgreSQL e atualiza o código automaticamente quando o schema muda.

Quando você remove uma coluna, ele acha todos os arquivos que usam ela e reescreve. Quando você adiciona, ele inclui nos arquivos existentes. Quando você cria uma tabela nova, ele cria o arquivo correspondente no projeto.

---

## Antes de começar

Você vai precisar de:

- Python 3.10 ou superior
- Ollama instalado na máquina
- Uma chave da API do Gemini (gratuita)
- Um banco PostgreSQL rodando (local ou remoto)

---

## 1. Clonar e instalar dependências

```bash
git clone <url-do-repositorio>
cd sh
pip install langchain langchain-google-genai langchain-ollama langgraph psycopg2-binary python-dotenv
```

Se você usa `venv` (recomendado):

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
source .venv/bin/activate     # Linux/Mac
pip install langchain langchain-google-genai langchain-ollama langgraph psycopg2-binary python-dotenv
```

---

## 2. Instalar o Ollama e baixar o modelo

O Ollama roda o modelo de código localmente. Não fica rodando em background — só consome recurso quando está reescrevendo um arquivo.

**Windows:** baixe o instalador em https://ollama.com e instale normalmente.

**Linux/Mac:**
```bash
curl -fsSL https://ollama.com/install.sh | sh
```

Depois de instalar, baixe o modelo:

```bash
ollama pull qwen2.5-coder:3b
```

Isso baixa cerca de 2GB e só precisa ser feito uma vez. Para confirmar que funcionou:

```bash
ollama run qwen2.5-coder:3b "escreva uma função python que soma dois números"
```

Se retornar código, está pronto. Saia com `/bye`.

---

## 3. Pegar a chave do Gemini

Acesse: https://aistudio.google.com/app/apikey

Clique em "Create API key", copie a chave gerada. É gratuita.

---

## 4. Configurar o .env

Crie um arquivo chamado `.env` dentro da pasta `sh`:

```
GEMINI_API_KEY=sua_chave_aqui
DB_URL=postgresql://usuario:senha@host:5432/nome_do_banco
PASTA=C:\caminho\completo\do\seu\projeto
INTERVALO=5
```

- `DB_URL` é a URL de conexão com o seu banco PostgreSQL
- `PASTA` é o caminho da pasta com os arquivos que o sistema vai monitorar e reescrever
- `INTERVALO` é quantos segundos entre cada verificação do banco (mínimo 5)

Para banco com SSL (ex: Aiven, Supabase, Railway):

```
DB_URL=postgresql://usuario:senha@host:5432/banco?sslmode=require
```

---

## 5. Rodar

```bash
cd sh
python main.py
```

Na primeira execução, o sistema salva um snapshot do schema atual do banco e não faz nada mais. Isso serve como ponto de comparação.

A partir daí, toda vez que você alterar o banco, o sistema detecta a mudança e age automaticamente.

---

## Como funciona na prática

**Você remove uma coluna:**
O sistema acha todos os arquivos `.py`, `.java`, `.kt`, `.dart`, `.js`, `.ts` ou `.xml` que mencionam aquela coluna e reescreve cada um removendo as referências.

**Você adiciona uma coluna:**
O sistema pega todos os arquivos do projeto e adiciona a nova coluna nos parâmetros de funções, dicionários de payload e validações.

**Você cria uma tabela nova:**
O sistema lê os arquivos existentes para entender o padrão do projeto, cria um arquivo novo para aquela tabela e depois verifica se algum arquivo existente precisa importar ou referenciar o novo.

Antes de qualquer reescrita, o arquivo original é salvo em `sua-pasta/backups/` com timestamp. Se algo der errado, você tem o histórico.

---

## Estrutura do projeto

```
sh/
  main.py        - inicializa o agente e o watcher
  watcher.py     - monitora o banco e monta as mensagens pro agente
  snapshot.py    - salva e compara schemas
  tools.py       - ferramentas que o agente pode usar
  prompts.py     - instruções do agente
  .env           - suas configurações (não commitar)
  schema_snapshots/  - histórico de schemas salvos
```

---

## Parar o sistema

`Ctrl+C` no terminal.

O Ollama não precisa ser encerrado manualmente — ele para sozinho quando não está sendo usado.

---

## Dúvidas comuns

**O sistema alterou um arquivo errado.**
Vá em `sua-pasta/backups/`, encontre o `.bak` com o timestamp antes da alteração e restaure manualmente.

**Erro de conexão com o banco.**
Verifique se a URL está correta e se o banco está acessível. Para bancos remotos com SSL, lembre de adicionar `?sslmode=require` no final da URL.

**O Ollama trava ou fica lento.**
O modelo `qwen2.5-coder:3b` precisa de pelo menos 4GB de RAM livre. Se sua máquina tiver pouca memória, feche outros programas antes de rodar.

**Quer usar um venv específico.**
No `.env`, não há configuração de Python — mas se precisar, abra o `main.py` e confirme que está rodando com o Python correto usando `which python` ou `where python` antes de iniciar.