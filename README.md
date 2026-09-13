# SH-PSCBS

**Self-Healing Polyglot Schema-Code Bifurcation System**

---

## O problema

Todo desenvolvedor que trabalha com banco de dados conhece esse ciclo: você altera uma tabela, remove uma coluna, renomeia um campo — e de repente a aplicação quebra. Telas dão erro, APIs retornam exceções, formulários travam. O motivo é sempre o mesmo: o código ainda referencia algo que o banco não tem mais.

Consertar isso manualmente é tedioso. Em projetos grandes, uma única alteração de schema pode afetar dezenas de arquivos espalhados por módulos diferentes, linguagens diferentes, camadas diferentes.

O SH-PSCBS resolve isso automaticamente.

---

## O que é

Um agente de IA que fica monitorando o banco de dados em background. Quando detecta uma mudança no schema, ele varre o projeto, identifica os arquivos afetados e os reescreve — sem você precisar fazer nada.

O sistema é chamado de "polyglot" porque funciona com qualquer linguagem: Python, Java, Kotlin, Dart, JavaScript, TypeScript. É chamado de "self-healing" porque o projeto se conserta sozinho. E "bifurcation" porque o schema do banco e o código da aplicação vivem em mundos separados — esse sistema é a ponte que os mantém sincronizados.

---

## Como funciona

O sistema roda em três camadas:

**Watcher**
Conecta ao banco PostgreSQL e salva um snapshot do schema atual em JSON. A cada intervalo configurado, compara o schema atual com o snapshot anterior. Se detectar diferença, classifica o que mudou: colunas removidas, colunas adicionadas, tabelas novas.

**Orquestrador**
Um agente LLM (Google Gemini) que recebe o diff do watcher e decide o que fazer: quais arquivos buscar, qual ferramenta chamar, em que ordem executar as operações.

**Motor de reescrita**
Um modelo especializado em código (Qwen 2.5 Coder, rodando localmente via Ollama) que recebe o arquivo original e o diff e reescreve o código refletindo as mudanças do banco.

---

## Comportamento por tipo de mudança

**Coluna removida**
O sistema busca todos os arquivos que mencionam aquela coluna e reescreve cada um removendo parâmetros, chaves de dicionário, validações e comentários relacionados.

**Coluna adicionada**
O sistema percorre os arquivos do projeto e inclui a nova coluna nos parâmetros de funções, nos payloads de API e nas validações existentes.

**Tabela nova**
O sistema lê os arquivos existentes para entender o padrão e o estilo do projeto, cria um arquivo novo para aquela tabela com modelo, funções CRUD e validações básicas, e depois verifica se algum arquivo existente precisa importar ou referenciar o novo módulo.

---

## Segurança

Antes de reescrever qualquer arquivo, o sistema salva uma cópia do original em uma pasta `backups/` com timestamp. Nada é perdido — se a reescrita não ficar boa, o arquivo anterior está disponível para restauração.

---

## Arquitetura

```
banco de dados
      |
   watcher          <- detecta mudanças no schema
      |
  orquestrador      <- agente Gemini decide o que fazer
      |
  ferramentas       <- busca arquivos, lê estrutura, chama reescrita
      |
motor de reescrita  <- Qwen 2.5 Coder (local, via Ollama)
      |
  código atualizado
```

---

## Stack

- **LangChain + LangGraph** — orquestração do agente
- **Google Gemini** — modelo de linguagem para raciocínio e tomada de decisão
- **Qwen 2.5 Coder via Ollama** — modelo especializado em código, roda localmente
- **PostgreSQL** — banco de dados monitorado
- **Python** — runtime do sistema

---

## Estado atual

O sistema está em fase de MVP. Funciona com projetos Python e está sendo validado com casos reais de alteração de schema. As próximas frentes de desenvolvimento são suporte a mais linguagens, interface visual como extensão de VS Code e geração de testes automatizados junto com a reescrita do código.

---

## Motivação

A ideia central é que o banco de dados é a fonte de verdade de uma aplicação. Se o schema muda, o código deveria acompanhar automaticamente — assim como um compilador aponta erros de tipo, esse sistema aponta e corrige desalinhamentos entre o modelo de dados e o código que o consome.

O nome "bifurcação" vem justamente disso: schema e código são dois trilhos que precisam andar juntos. O SH-PSCBS é o mecanismo que os mantém paralelos.
