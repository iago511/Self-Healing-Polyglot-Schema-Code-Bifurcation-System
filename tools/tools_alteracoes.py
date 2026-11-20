import ast
import difflib
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import stat
import subprocess
import tempfile
from datetime import datetime, timezone
from uuid import uuid4

from langchain.tools import tool


_modelo_codigo = None
_METADADOS = ".sh_pscbs"
_IGNORAR = {".git", ".venv", "venv", "__pycache__", "node_modules", "backups", ".pytest_cache",
            ".mypy_cache", ".ruff_cache", ".tox", "build", "dist", ".aws", ".ssh", ".azure", ".kube",
            ".docker", _METADADOS}
_EXTENSOES = {".py", ".sql", ".java", ".kt", ".dart", ".js", ".jsx", ".ts", ".tsx", ".xml"}


def configurar_modelo_codigo(modelo):
    """Recebe o mesmo modelo de código configurado no módulo principal."""
    global _modelo_codigo
    _modelo_codigo = modelo


def _erro(exc):
    return {"sucesso": False, "erro": str(exc)}


def _hash(conteudo):
    return hashlib.sha256(conteudo).hexdigest() if conteudo is not None else None


def _ignorar_nome(nome):
    nome = nome.lower()
    return (nome in _IGNORAR or nome in {".env", ".npmrc", ".pypirc", ".git-credentials"}
            or nome.startswith(".env.") or nome.endswith((".pem", ".key", ".p12", ".pfx")))


def _raizes(pasta_original, pasta_copia):
    original = Path(pasta_original).resolve(strict=True)
    copia = Path(pasta_copia).resolve(strict=True)
    if not original.is_dir() or not copia.is_dir():
        raise ValueError("A origem e a cópia devem ser pastas existentes.")
    if original == copia or original in copia.parents or copia in original.parents:
        raise ValueError("A cópia e o projeto original devem ser pastas separadas.")
    return original, copia


def _caminho(raiz, arquivo_relativo):
    relativo = Path(arquivo_relativo)
    if relativo.is_absolute() or PureWindowsPath(arquivo_relativo).drive:
        raise ValueError("Informe um caminho relativo à pasta do projeto.")
    if not relativo.parts or any(parte == ".." or _ignorar_nome(parte) for parte in relativo.parts):
        raise ValueError("O caminho informado não é permitido.")
    atual = raiz
    for parte in relativo.parts:
        atual = atual / parte
        if atual.is_symlink():
            raise ValueError("Links simbólicos não são aceitos nos caminhos das propostas.")
    if not atual.resolve().is_relative_to(raiz):
        raise ValueError("O arquivo deve permanecer dentro do projeto.")
    return atual


def _ler_bytes(arquivo):
    if not arquivo.exists():
        return None
    if not arquivo.is_file():
        raise ValueError(f"O caminho não é um arquivo: {arquivo}")
    return arquivo.read_bytes()


def _escrever_atomico(arquivo, conteudo, modo=None):
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    temporario = None
    try:
        with tempfile.NamedTemporaryFile(dir=arquivo.parent, prefix=".sh_tmp_", delete=False) as destino:
            temporario = Path(destino.name)
            destino.write(conteudo)
            destino.flush()
            os.fsync(destino.fileno())
        if modo is not None:
            os.chmod(temporario, modo)
        os.replace(temporario, arquivo)
    finally:
        if temporario is not None and temporario.exists():
            temporario.unlink()


def _salvar_manifesto(caminho, dados):
    conteudo = json.dumps(dados, ensure_ascii=False, indent=2).encode("utf-8")
    _escrever_atomico(caminho, conteudo)


def _pasta_propostas(copia):
    metadados = copia / _METADADOS
    propostas = metadados / "propostas"
    if metadados.is_symlink() or propostas.is_symlink():
        raise ValueError("A pasta de metadados não pode ser um link simbólico.")
    if not propostas.resolve().is_relative_to(copia):
        raise ValueError("A pasta de metadados deve permanecer dentro da cópia.")
    return propostas


def _carregar_proposta(caminho_manifesto):
    manifesto = Path(caminho_manifesto).resolve(strict=True)
    dados = json.loads(manifesto.read_text(encoding="utf-8"))
    if dados.get("versao") != 1:
        raise ValueError("Formato de proposta não reconhecido.")
    original, copia = _raizes(dados["pasta_original"], dados["pasta_copia"])
    esperado = _pasta_propostas(copia) / dados["id"] / "manifesto.json"
    if manifesto != esperado or manifesto.is_symlink():
        raise ValueError("O manifesto não está na pasta esperada da proposta.")
    destino = _caminho(original, dados["arquivo"])
    candidato = _caminho(copia, dados["arquivo"])
    return manifesto, dados, destino, candidato


def _conteudo_modelo(resposta):
    conteudo = resposta.content
    if isinstance(conteudo, list):
        conteudo = "".join(bloco.get("text", "") for bloco in conteudo if isinstance(bloco, dict))
    if not isinstance(conteudo, str) or not conteudo.strip():
        raise ValueError("O modelo não retornou código para a proposta.")
    conteudo = conteudo.strip()
    if conteudo.startswith("```"):
        trecho = re.fullmatch(r"```[^\n]*\n(.*?)\n?```", conteudo, re.DOTALL)
        if not trecho:
            raise ValueError("O modelo retornou uma resposta com bloco de código incompleto.")
        conteudo = trecho.group(1)
    return conteudo.rstrip("\n") + "\n"


@tool("analisar_impacto")
def analisar_impacto(pasta: str, termos: list[str]) -> dict:
    """Localiza termos de tabelas ou colunas, com arquivo, linha e contexto.

    Em Python, distingue identificadores e strings usando AST; nos demais
    casos, informa que a evidência é textual. Não altera o projeto.
    """
    try:
        raiz = Path(pasta).resolve(strict=True)
        if not raiz.is_dir():
            raise ValueError("A pasta do projeto não existe.")
        termos = list(dict.fromkeys(termo.strip() for termo in termos if termo.strip()))
        if not termos:
            raise ValueError("Informe pelo menos um nome de tabela ou coluna.")
        padroes = {termo: re.compile(r"(?<!\w)" + re.escape(termo) + r"(?!\w)") for termo in termos}
        evidencias, avisos = [], []
        for diretorio, pastas, arquivos in os.walk(raiz, followlinks=False):
            pastas[:] = [nome for nome in pastas if nome not in _IGNORAR and not (Path(diretorio) / nome).is_symlink()]
            for nome in arquivos:
                arquivo = Path(diretorio) / nome
                if arquivo.suffix.lower() not in _EXTENSOES or arquivo.is_symlink():
                    continue
                try:
                    texto = arquivo.read_text(encoding="utf-8")
                except (OSError, UnicodeError) as exc:
                    avisos.append(f"Não foi possível ler {arquivo.relative_to(raiz)}: {exc}")
                    continue
                nos, escopos = [], []
                if arquivo.suffix.lower() == ".py":
                    try:
                        arvore = ast.parse(texto, filename=str(arquivo))
                        for no in ast.walk(arvore):
                            if isinstance(no, (ast.Name, ast.Attribute, ast.arg, ast.Constant)) and hasattr(no, "lineno"):
                                nos.append(no)
                            if isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                                escopos.append((no.lineno, no.end_lineno or no.lineno, no.name))
                    except SyntaxError as exc:
                        avisos.append(f"Análise textual usada em {arquivo.relative_to(raiz)}: {exc.msg}")
                for numero, linha in enumerate(texto.splitlines(), 1):
                    encontrados = [termo for termo, padrao in padroes.items() if padrao.search(linha)]
                    if not encontrados:
                        continue
                    metodo = "texto"
                    for no in nos:
                        if not (no.lineno <= numero <= (getattr(no, "end_lineno", None) or no.lineno)):
                            continue
                        valor = getattr(no, "id", getattr(no, "attr", getattr(no, "arg", None)))
                        if valor in encontrados:
                            metodo = "identificador_python"
                            break
                        if isinstance(no, ast.Constant) and isinstance(no.value, str):
                            if any(padroes[termo].search(no.value) for termo in encontrados):
                                metodo = "string_python"
                    internos = [escopo for escopo in escopos if escopo[0] <= numero <= escopo[1]]
                    escopo = min(internos, key=lambda item: item[1] - item[0])[2] if internos else None
                    evidencias.append({"arquivo": arquivo.relative_to(raiz).as_posix(), "linha": numero,
                                       "trecho": linha.strip()[:300], "termos": encontrados,
                                       "metodo": metodo, "escopo": escopo})
        return {"sucesso": True, "evidencias": evidencias, "avisos": avisos,
                "observacao": "As ocorrências são indícios; referências dinâmicas podem não ser identificadas."}
    except (OSError, ValueError) as exc:
        return _erro(exc)


@tool("propor_alteracao")
def propor_alteracao(pasta_original: str, pasta_copia: str, arquivo_relativo: str,
                     diff_schema: str, tabela: str, excluir: bool = False) -> dict:
    """Usa o modelo de código para gerar uma proposta em uma cópia e mostrar o diff.

    A cópia deve existir fora do projeto original. Trata um arquivo por vez e
    pode alterar, criar ou propor sua exclusão. Não aplica no original.
    """
    return _propor_arquivo(pasta_original, pasta_copia, arquivo_relativo, diff_schema, tabela, excluir)


def _diff_arquivo(arquivo, antes, depois):
    def linhas(conteudo):
        if not conteudo:
            return []
        *terminadas, ultima = conteudo.split("\n")
        return [linha + "\n" for linha in terminadas] + ([ultima] if ultima else [])

    origem = "/dev/null" if antes is None else f"a/{arquivo}\t"
    destino = "/dev/null" if depois is None else f"b/{arquivo}\t"
    diff = difflib.unified_diff(linhas(antes), linhas(depois), fromfile=origem, tofile=destino)
    linhas_diff = list(diff)
    if not linhas_diff and (antes is None or depois is None):
        modo = "new" if antes is None else "deleted"
        return f"diff --git a/{arquivo} b/{arquivo}\n{modo} file mode 100644\n"
    return "".join(
        linha if linha.endswith("\n") else linha + "\n\\ No newline at end of file\n"
        for linha in linhas_diff
    )


def _propor_arquivo(pasta_original, pasta_copia, arquivo_relativo, diff_schema, tabela, excluir=False,
                    *, modelo=None, orientacao="", registrar_manifesto=True):
    try:
        original, copia = _raizes(pasta_original, pasta_copia)
        arquivo_original = _caminho(original, arquivo_relativo)
        arquivo_copia = _caminho(copia, arquivo_relativo)
        antes = _ler_bytes(arquivo_original)
        if _ler_bytes(arquivo_copia) != antes:
            raise ValueError("O arquivo na cópia difere do original; preserve ou revise essa alteração antes de continuar.")
        codigo_atual = antes.decode("utf-8") if antes is not None else ""
        if excluir:
            if antes is None:
                raise ValueError("Não é possível propor a exclusão de um arquivo inexistente.")
            codigo_novo, depois = None, None
        else:
            modelo = _modelo_codigo if modelo is None else modelo
            if modelo is None:
                raise ValueError("O modelo de código ainda não foi configurado.")
            prompt = (f"Atualize o arquivo {arquivo_relativo!r} para a mudança no PostgreSQL.\n"
                      f"Tabela: {tabela}\nDiff do schema:\n{diff_schema}\n\n"
                      "Preserve a lógica não relacionada. Se o arquivo não for relacionado, retorne-o sem mudanças. "
                      "Não invente regras de negócio. Retorne somente o código completo, sem explicações.\n\n"
                      f"{orientacao}"
                      f"Código atual:\n{codigo_atual}")
            codigo_novo = _conteudo_modelo(modelo.invoke(prompt)).replace("\r\n", "\n")
            codigo_normalizado = codigo_atual.replace("\r\n", "\n")
            if antes is not None and codigo_novo.rstrip("\n") == codigo_normalizado.rstrip("\n"):
                codigo_novo = codigo_atual
            elif "\r\n" in codigo_atual and "\n" not in codigo_atual.replace("\r\n", ""):
                codigo_novo = codigo_novo.replace("\n", "\r\n")
            depois = codigo_novo.encode("utf-8")
        if _ler_bytes(arquivo_original) != antes or _ler_bytes(arquivo_copia) != antes:
            raise ValueError("O arquivo original ou a cópia mudou durante a geração da proposta.")
        if depois == antes:
            return {"sucesso": True, "status": "sem_alteracoes", "diff": ""}
        arquivo = Path(arquivo_relativo).as_posix()
        diff = _diff_arquivo(arquivo, codigo_atual if antes is not None else None, codigo_novo)
        manifesto = None
        if registrar_manifesto:
            proposta_id = uuid4().hex
            pasta_proposta = _pasta_propostas(copia) / proposta_id
            manifesto = pasta_proposta / "manifesto.json"
            dados = {"versao": 1, "id": proposta_id, "pasta_original": str(original), "pasta_copia": str(copia),
                     "arquivo": arquivo, "hash_original": _hash(antes), "hash_proposta": _hash(depois),
                     "operacao": "excluir" if excluir else "criar" if antes is None else "alterar",
                     "estado": "pendente", "validacao": None, "criada_em": datetime.now(timezone.utc).isoformat()}
            pasta_proposta.mkdir(parents=True, exist_ok=False)
        modo = stat.S_IMODE(arquivo_copia.stat().st_mode) if arquivo_copia.exists() else None
        if excluir:
            arquivo_copia.unlink()
        else:
            _escrever_atomico(arquivo_copia, depois, modo)
        resultado = {"sucesso": True, "status": "pendente", "diff": diff}
        if manifesto is not None:
            _salvar_manifesto(manifesto, dados)
            resultado["manifesto"] = str(manifesto)
        return resultado
    except (OSError, ValueError, UnicodeError) as exc:
        return _erro(exc)
    except Exception as exc:
        return _erro(f"Não foi possível gerar a proposta: {exc}")


def _ignorar_copia(diretorio, nomes):
    ignorados = set()
    for nome in nomes:
        caminho = Path(diretorio) / nome
        if _ignorar_nome(nome):
            ignorados.add(nome)
            continue
        info = caminho.lstat()
        junction = getattr(info, "st_reparse_tag", None) == getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003)
        if junction or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            ignorados.add(nome)
    return ignorados


def _hash_projeto(raiz):
    digest = hashlib.sha256()
    for diretorio, pastas, arquivos in os.walk(raiz, followlinks=False):
        ignorados = _ignorar_copia(diretorio, pastas + arquivos)
        pastas[:] = sorted(nome for nome in pastas if nome not in ignorados)
        relativo = Path(diretorio).relative_to(raiz).as_posix().encode("utf-8")
        digest.update(b"D\0" + relativo + b"\0")
        for nome in sorted(nome for nome in arquivos if nome not in ignorados):
            arquivo = Path(diretorio) / nome
            relativo = arquivo.relative_to(raiz).as_posix().encode("utf-8")
            digest.update(b"F\0" + relativo + b"\0")
            conteudo = hashlib.sha256()
            with arquivo.open("rb") as entrada:
                for bloco in iter(lambda: entrada.read(1024 * 1024), b""):
                    conteudo.update(bloco)
            digest.update(conteudo.digest())
    return digest.hexdigest()


def _testar_em_container(original, candidato, dados, hash_base, imagem, timeout):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/@-]*", imagem):
        return {"status": "erro", "detalhe": "Nome de imagem Docker inválido."}
    if not shutil.which("docker"):
        return {"status": "nao_executados", "detalhe": "Docker não encontrado."}
    try:
        inspecao = subprocess.run(["docker", "image", "inspect", imagem], stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, timeout=10, check=False)
        if inspecao.returncode:
            return {"status": "nao_executados", "detalhe": "Docker não está acessível ou a imagem não está disponível localmente."}
        with tempfile.TemporaryDirectory(prefix="sh_pscbs_testes_") as temporario:
            projeto = (Path(temporario) / "projeto").resolve()
            shutil.copytree(original, projeto, ignore=_ignorar_copia)
            if _hash_projeto(projeto) != hash_base:
                return {"status": "erro", "detalhe": "O projeto mudou durante a preparação dos testes."}
            for diretorio, _, arquivos in os.walk(projeto):
                os.chmod(diretorio, stat.S_IMODE(os.stat(diretorio).st_mode) | 0o755)
                for nome_arquivo in arquivos:
                    caminho = Path(diretorio) / nome_arquivo
                    os.chmod(caminho, stat.S_IMODE(caminho.stat().st_mode) | 0o444)
            arquivo_teste = _caminho(projeto, dados["arquivo"])
            if dados["operacao"] == "excluir":
                arquivo_teste.unlink()
            else:
                _escrever_atomico(arquivo_teste, candidato.read_bytes(), 0o644)
            if "," in str(projeto):
                return {"status": "erro", "detalhe": "O caminho temporário contém vírgula e não pode ser montado."}
            nome = "sh-pscbs-test-" + uuid4().hex
            comando = ["docker", "run", "--rm", "--name", nome, "--pull", "never", "--network", "none",
                       "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                       "--pids-limit", "128", "--memory", "512m", "--cpus", "1", "--user", "65534:65534",
                       "--tmpfs", "/tmp:rw,nosuid,size=128m", "--mount",
                       f"type=bind,src={projeto},dst=/workspace,readonly", "--workdir", "/workspace",
                       "--env", "HOME=/tmp", "--env", "TMPDIR=/tmp", "--env", "PYTHONDONTWRITEBYTECODE=1",
                       "--env", "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1", imagem,
                       "python", "-m", "pytest", "-q", "-p", "no:cacheprovider"]
            log = Path(temporario) / "testes.log"
            try:
                with log.open("wb") as saida:
                    resultado = subprocess.run(comando, stdin=subprocess.DEVNULL, stdout=saida,
                                               stderr=subprocess.STDOUT, timeout=timeout, check=False)
            except subprocess.TimeoutExpired:
                try:
                    subprocess.run(["docker", "kill", nome], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   timeout=10, check=False)
                except (OSError, subprocess.TimeoutExpired):
                    pass
                return {"status": "erro", "detalhe": "Os testes excederam o tempo limite."}
            with log.open("rb") as saida:
                saida.seek(0, os.SEEK_END)
                saida.seek(max(0, saida.tell() - 12000))
                texto = saida.read().decode("utf-8", errors="replace")
            return {"status": "aprovados" if resultado.returncode == 0 else "falharam",
                    "codigo_saida": resultado.returncode, "saida": texto}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "erro", "detalhe": str(exc)}


@tool("validar_proposta")
def validar_proposta(caminho_manifesto: str, imagem_teste: str = "", timeout_segundos: int = 120) -> dict:
    """Verifica a sintaxe Python e, se fornecida uma imagem Docker local, executa pytest isolado.

    A imagem deve conter Python, pytest e as dependências do projeto. Sem imagem,
    os testes são marcados como não executados. O projeto original não é alterado.
    """
    try:
        manifesto, dados, destino, candidato = _carregar_proposta(caminho_manifesto)
        if dados["estado"] != "pendente":
            raise ValueError("Somente propostas pendentes podem ser validadas.")
        conteudo = _ler_bytes(candidato)
        if _hash(conteudo) != dados["hash_proposta"]:
            raise ValueError("A cópia mudou depois da criação da proposta.")
        if _hash(_ler_bytes(destino)) != dados["hash_original"]:
            raise ValueError("O arquivo original mudou desde a criação da proposta.")
        original = Path(dados["pasta_original"])
        hash_base = _hash_projeto(original)
        sintaxe = {"status": "nao_verificada", "detalhe": "Validação sintática disponível apenas para Python."}
        if dados["operacao"] == "excluir":
            sintaxe = {"status": "nao_aplicavel", "detalhe": "A proposta remove o arquivo."}
        elif candidato.suffix.lower() == ".py":
            try:
                ast.parse(conteudo.decode("utf-8"), filename=dados["arquivo"])
                sintaxe = {"status": "aprovada"}
            except (SyntaxError, UnicodeError) as exc:
                sintaxe = {"status": "falhou", "detalhe": str(exc)}
        testes = {"status": "nao_executados", "detalhe": "Informe uma imagem Docker local para executar pytest."}
        if imagem_teste and sintaxe["status"] != "falhou":
            if not 1 <= timeout_segundos <= 1800:
                raise ValueError("O tempo limite deve estar entre 1 e 1800 segundos.")
            testes = _testar_em_container(original, candidato, dados, hash_base, imagem_teste, timeout_segundos)
        if sintaxe["status"] == "falhou" or testes["status"] in {"falharam", "erro"}:
            status = "reprovada"
        elif sintaxe["status"] in {"aprovada", "nao_aplicavel"} and testes["status"] == "aprovados":
            status = "aprovada"
        else:
            status = "incompleta"
        dados["validacao"] = {"status": status, "sintaxe": sintaxe, "testes": testes,
                              "hash_proposta": dados["hash_proposta"], "hash_projeto": hash_base}
        _salvar_manifesto(manifesto, dados)
        return {"sucesso": status == "aprovada", "status": status, "sintaxe": sintaxe, "testes": testes}
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        return _erro(exc)


@tool("aplicar_proposta")
def aplicar_proposta(caminho_manifesto: str, confirmar: bool = False,
                     permitir_validacao_incompleta: bool = False) -> dict:
    """Aplica uma proposta validada após confirmação, com backup e verificação das versões.

    O orquestrador deve garantir que a confirmação veio da pessoa responsável.
    Se faltarem verificações, permitir_validacao_incompleta exige autorização explícita.
    """
    try:
        if confirmar is not True:
            raise ValueError("Confirme a aplicação somente após revisar a proposta.")
        manifesto, dados, destino, candidato = _carregar_proposta(caminho_manifesto)
        if dados["estado"] in {"aplicando", "aplicada"}:
            atual = _hash(_ler_bytes(destino))
            if atual == dados["hash_proposta"]:
                if dados["hash_original"] is not None:
                    backup = _ler_bytes(manifesto.parent / "original.bak")
                    if _hash(backup) != dados["hash_original"]:
                        raise ValueError("Não foi possível confirmar a aplicação: backup ausente ou alterado.")
                dados["estado"] = "aplicada"
                _salvar_manifesto(manifesto, dados)
                return {"sucesso": True, "estado": "aplicada", "arquivo": str(destino),
                        "detalhe": "A proposta já estava aplicada."}
            if dados["estado"] == "aplicando" and atual == dados["hash_original"]:
                dados["estado"] = "pendente"
                _salvar_manifesto(manifesto, dados)
            else:
                raise ValueError("O arquivo divergiu durante ou depois da aplicação.")
        if dados["estado"] != "pendente":
            raise ValueError("Somente propostas pendentes podem ser aplicadas.")
        validacao = dados.get("validacao")
        if not validacao or validacao.get("hash_proposta") != dados["hash_proposta"]:
            raise ValueError("Valide a proposta antes de aplicá-la.")
        if validacao["sintaxe"]["status"] == "falhou" or validacao["testes"]["status"] in {"falharam", "erro"}:
            raise ValueError("A proposta possui validações com falha.")
        if validacao["status"] == "incompleta" and not permitir_validacao_incompleta:
            raise ValueError("Há verificações não executadas; revise-as antes de permitir a aplicação.")
        if _hash_projeto(Path(dados["pasta_original"])) != validacao.get("hash_projeto"):
            raise ValueError("O projeto original mudou depois da validação; valide novamente a proposta.")
        antes = _ler_bytes(destino)
        depois = _ler_bytes(candidato)
        if _hash(antes) != dados["hash_original"]:
            raise ValueError("O arquivo original mudou desde a proposta; gere uma nova proposta.")
        if _hash(depois) != dados["hash_proposta"]:
            raise ValueError("O arquivo na cópia mudou desde a proposta; gere uma nova proposta.")
        modo = stat.S_IMODE(destino.stat().st_mode) if antes is not None else None
        if antes is not None:
            _escrever_atomico(manifesto.parent / "original.bak", antes)
        dados["modo_original"] = modo
        dados["estado"] = "aplicando"
        _salvar_manifesto(manifesto, dados)
        if dados["operacao"] == "excluir":
            destino.unlink()
        else:
            _escrever_atomico(destino, depois, modo)
        dados["estado"] = "aplicada"
        dados["aplicada_em"] = datetime.now(timezone.utc).isoformat()
        _salvar_manifesto(manifesto, dados)
        return {"sucesso": True, "estado": "aplicada", "arquivo": str(destino),
                "backup": str(manifesto.parent / "original.bak") if antes is not None else None}
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        return _erro(exc)


@tool("reverter_proposta")
def reverter_proposta(caminho_manifesto: str, confirmar: bool = False) -> dict:
    """Restaura o arquivo anterior, ou remove um arquivo criado pela proposta, após confirmação.

    A reversão é recusada se o arquivo tiver sido alterado depois da aplicação.
    O orquestrador deve verificar que a confirmação veio da pessoa responsável.
    """
    try:
        if confirmar is not True:
            raise ValueError("Confirme a reversão antes de continuar.")
        manifesto, dados, destino, _ = _carregar_proposta(caminho_manifesto)
        atual = _ler_bytes(destino)
        hash_atual = _hash(atual)
        if dados["estado"] == "aplicando" and hash_atual == dados["hash_original"]:
            dados["estado"] = "pendente"
            _salvar_manifesto(manifesto, dados)
            return {"sucesso": True, "estado": "pendente", "arquivo": str(destino),
                    "detalhe": "A aplicação interrompida não havia alterado o arquivo."}
        if dados["estado"] in {"revertendo", "revertida"} and hash_atual == dados["hash_original"]:
            dados["estado"] = "revertida"
            _salvar_manifesto(manifesto, dados)
            return {"sucesso": True, "estado": "revertida", "arquivo": str(destino),
                    "detalhe": "A proposta já estava revertida."}
        if dados["estado"] not in {"aplicada", "aplicando", "revertendo"}:
            raise ValueError("A proposta não está aplicada.")
        if hash_atual != dados["hash_proposta"]:
            raise ValueError("O arquivo foi alterado depois da aplicação; a reversão automática foi cancelada.")
        backup = None
        if dados["operacao"] in {"alterar", "excluir"}:
            backup = _ler_bytes(manifesto.parent / "original.bak")
            if _hash(backup) != dados["hash_original"]:
                raise ValueError("O backup original está ausente ou foi alterado.")
        elif dados["operacao"] != "criar":
            raise ValueError("Operação da proposta não reconhecida.")
        dados["estado"] = "revertendo"
        _salvar_manifesto(manifesto, dados)
        if dados["operacao"] == "criar":
            destino.unlink()
        else:
            _escrever_atomico(destino, backup, dados.get("modo_original"))
        dados["estado"] = "revertida"
        dados["revertida_em"] = datetime.now(timezone.utc).isoformat()
        _salvar_manifesto(manifesto, dados)
        return {"sucesso": True, "estado": "revertida", "arquivo": str(destino)}
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        return _erro(exc)


TOOLS_ALTERACOES = [analisar_impacto, propor_alteracao, validar_proposta, aplicar_proposta, reverter_proposta]
