"""Verifica a integridade e executa testes da proposta completa."""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from sh_pscbs.agentes.correcao import _validar_acoes, identificar_codigo_base
from sh_pscbs.orquestracao.estado import (
    AcaoCorrecao,
    PlanoCorrecao,
    PropostaCorrecao,
    ResultadoValidacao,
    VerificacaoValidacao,
)
from tools.tools_alteracoes import (
    _caminho,
    _diff_arquivo,
    _executar_pytest_isolado,
    _hash_projeto,
    _ler_bytes,
    _permissoes_copia_teste,
    copiar_projeto_isolado,
)


@dataclass(frozen=True)
class ArquivoProposto:
    acao: AcaoCorrecao
    antes: bytes | None
    depois: bytes | None


@dataclass(frozen=True)
class ArtefatosProposta:
    id_evento: str
    plano: PlanoCorrecao
    copia: Path
    hash_copia: str
    hash_diff: str
    diff: str
    arquivos: tuple[ArquivoProposto, ...]


def carregar_artefatos_proposta(
    proposta: PropostaCorrecao, pasta_projeto: str | Path,
) -> ArtefatosProposta:
    """Lê o artefato e confirma que só contém mudanças autorizadas pelo plano."""
    original = Path(pasta_projeto).expanduser().resolve(strict=True)
    caminho_diff = Path(proposta.referencia_diff)
    pasta = caminho_diff.parent.resolve(strict=True)
    if (
        caminho_diff.name != "mudancas.diff"
        or caminho_diff.is_symlink()
        or not caminho_diff.is_file()
        or pasta.name != proposta.id_proposta
        or pasta.is_relative_to(original)
    ):
        raise ValueError("O caminho do diff não corresponde à proposta.")
    caminho_manifesto = pasta / "manifesto.json"
    copia = pasta / "projeto"
    if (
        caminho_manifesto.is_symlink()
        or not caminho_manifesto.is_file()
        or copia.is_symlink()
        or copia.resolve(strict=True) != copia
        or not copia.is_dir()
    ):
        raise ValueError("O manifesto e a cópia precisam estar na pasta da proposta.")

    manifesto = json.loads(caminho_manifesto.read_text(encoding="utf-8"))
    if manifesto["versao"] != 1 or manifesto["copia_relativa"] != "projeto":
        raise ValueError("Formato de proposta não reconhecido.")
    if PropostaCorrecao.model_validate(manifesto["proposta"]) != proposta:
        raise ValueError("O manifesto descreve outra versão da proposta.")
    if Path(manifesto["pasta_original"]).resolve() != original:
        raise ValueError("A proposta pertence a outro projeto.")
    if identificar_codigo_base(original) != proposta.codigo_base_id:
        raise ValueError("O projeto original mudou desde a geração da proposta.")

    plano = PlanoCorrecao(acoes=manifesto["acoes"], testes_previstos=manifesto["testes_previstos"])
    acoes = _validar_acoes(plano, original)
    if [acao.arquivo for acao in acoes] != proposta.arquivos:
        raise ValueError("Os arquivos do manifesto não correspondem à proposta.")

    hash_copia = identificar_codigo_base(copia)
    if hash_copia != manifesto["hash_copia"]:
        raise ValueError("A cópia da proposta foi alterada.")
    escopo = frozenset(proposta.arquivos)
    if _hash_projeto(original, ignorar_arquivos=escopo) != _hash_projeto(copia, ignorar_arquivos=escopo):
        raise ValueError("A cópia contém alterações fora dos arquivos planejados.")

    arquivos, diffs = [], []
    for acao in acoes:
        antes = _ler_bytes(_caminho(original, acao.arquivo))
        depois = _ler_bytes(_caminho(copia, acao.arquivo))
        if antes == depois or (depois is None) != (acao.operacao == "excluir"):
            raise ValueError(f"A alteração de {acao.arquivo!r} não corresponde à operação planejada.")
        diffs.append(_diff_arquivo(
            acao.arquivo,
            antes.decode("utf-8") if antes is not None else None,
            depois.decode("utf-8") if depois is not None else None,
        ))
        arquivos.append(ArquivoProposto(acao, antes, depois))

    diff = (pasta / "mudancas.diff").read_bytes()
    hash_diff = hashlib.sha256(diff).hexdigest()
    if diff != "".join(diffs).encode("utf-8") or hash_diff != manifesto["hash_diff"]:
        raise ValueError("O diff não corresponde às alterações na cópia.")
    return ArtefatosProposta(
        manifesto["id_evento"], plano, copia, hash_copia, hash_diff, diff.decode("utf-8"), tuple(arquivos),
    )


def validar_proposta_correcao(
    proposta: PropostaCorrecao,
    pasta_projeto: str | Path,
    *,
    imagem_teste: str = "",
    timeout_segundos: int = 120,
) -> ResultadoValidacao:
    """Verifica a proposta e, com uma imagem local, testa todos os arquivos juntos."""
    if imagem_teste and not 1 <= timeout_segundos <= 1800:
        raise ValueError("O tempo limite deve estar entre 1 e 1800 segundos.")

    resultado = ResultadoValidacao(id_proposta=proposta.id_proposta)
    try:
        artefatos = carregar_artefatos_proposta(proposta, pasta_projeto)
    except (OSError, ValueError, KeyError, TypeError) as erro:
        resultado.verificacoes.append(
            VerificacaoValidacao(nome="Integridade da proposta", status="reprovada", detalhe=str(erro))
        )
        return resultado
    resultado.hash_diff = artefatos.hash_diff
    resultado.verificacoes.append(VerificacaoValidacao(nome="Integridade da proposta", status="aprovada"))

    for arquivo in artefatos.arquivos:
        nome = f"Sintaxe Python de {arquivo.acao.arquivo}"
        if arquivo.depois is None or Path(arquivo.acao.arquivo).suffix.lower() != ".py":
            resultado.verificacoes.append(VerificacaoValidacao(nome=nome, status="nao_aplicavel"))
            continue
        try:
            compile(arquivo.depois, arquivo.acao.arquivo, "exec", dont_inherit=True)
        except (SyntaxError, ValueError) as erro:
            resultado.verificacoes.append(
                VerificacaoValidacao(nome=nome, status="reprovada", detalhe=str(erro))
            )
        else:
            resultado.verificacoes.append(VerificacaoValidacao(nome=nome, status="aprovada"))

    if resultado.status == "reprovada":
        testes = {"status": "nao_executados", "detalhe": "Corrija os erros de sintaxe antes de rodar o pytest."}
    elif not imagem_teste:
        testes = {"status": "nao_executados", "detalhe": "Informe uma imagem Docker local com pytest e dependências."}
    else:
        def preparar(projeto):
            copiar_projeto_isolado(artefatos.copia, projeto)
            if identificar_codigo_base(projeto) != artefatos.hash_copia:
                raise ValueError("A cópia da proposta mudou durante a preparação dos testes.")
            _permissoes_copia_teste(projeto)

        testes = _executar_pytest_isolado(imagem_teste, timeout_segundos, preparar)

    status = {"aprovados": "aprovada", "falharam": "reprovada"}.get(testes["status"], "nao_executada")
    detalhes = "\n".join(testes[chave] for chave in ("detalhe", "saida") if testes.get(chave))
    resultado.verificacoes.append(
        VerificacaoValidacao(nome="Pytest da proposta completa", status=status, detalhe=detalhes or None)
    )
    return resultado
