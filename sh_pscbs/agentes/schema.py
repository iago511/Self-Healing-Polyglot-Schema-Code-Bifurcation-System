"""Interpreta mudanças já detectadas no schema usando regras fixas."""

from collections import Counter, defaultdict

from pydantic import JsonValue

from sh_pscbs.orquestracao.estado import AnaliseSchema, EventoSchema, HipoteseRenomeacao, MudancaSchema


def _tipo_coluna(metadados: dict[str, JsonValue]) -> str | None:
    tipo = metadados.get("tipo", metadados.get("data_type"))
    return tipo.strip() if isinstance(tipo, str) and tipo.strip() else None


def _aceita_nulo(metadados: dict[str, JsonValue]) -> bool | None:
    valor = metadados.get("aceita_nulo", metadados.get("is_nullable", metadados.get("nullable")))
    if isinstance(valor, bool):
        return valor
    if isinstance(valor, str):
        return {"YES": True, "NO": False}.get(valor.strip().upper())
    return None


def _risco_coluna(mudanca: MudancaSchema, alvo: str) -> list[str]:
    antes = mudanca.valores_anteriores
    depois = mudanca.valores_posteriores
    riscos = []

    tipo_anterior = _tipo_coluna(antes)
    tipo_atual = _tipo_coluna(depois)
    if tipo_anterior and tipo_atual and tipo_anterior != tipo_atual:
        riscos.append(
            f"{alvo} mudou de {tipo_anterior} para {tipo_atual}. "
            "Verifique conversões de dados e tipos esperados pelo código."
        )

    if _aceita_nulo(depois) is False:
        if _aceita_nulo(antes) is True:
            riscos.append(f"{alvo} passou a ser obrigatória. Verifique onde o código envia valores nulos.")
        elif mudanca.tipo == "coluna_adicionada":
            valor_padrao = depois.get("valor_padrao", depois.get("column_default"))
            if valor_padrao is None:
                riscos.append(
                    f"{alvo} foi adicionada como obrigatória e pode afetar inserções que omitem a coluna."
                )
    return riscos


def analisar_schema(evento: EventoSchema) -> AnaliseSchema:
    """Resume o evento, aponta riscos e sugere renomeações para confirmação."""
    contagem = Counter(mudanca.tipo for mudanca in evento.mudancas)
    resumo = ", ".join(
        f"{tipo.replace('_', ' ')} ({total})" for tipo, total in sorted(contagem.items())
    )
    analise = AnaliseSchema(resumo=f"Alterações no schema {evento.nome_schema}: {resumo}.")
    adicionadas: dict[str, list[MudancaSchema]] = defaultdict(list)
    removidas: dict[str, list[MudancaSchema]] = defaultdict(list)

    for mudanca in evento.mudancas:
        tabela = f"{evento.nome_schema}.{mudanca.tabela}"
        alvo = f"{tabela}.{mudanca.coluna}" if mudanca.coluna is not None else tabela

        if mudanca.tipo == "tabela_removida":
            analise.riscos.append(f"A tabela {alvo} foi removida. Consultas e modelos que a usam podem falhar.")
        elif mudanca.tipo == "coluna_removida":
            removidas[mudanca.tabela].append(mudanca)
            analise.riscos.append(f"A coluna {alvo} foi removida. Referências antigas podem falhar.")
        elif mudanca.tipo in {"coluna_adicionada", "coluna_alterada"}:
            analise.riscos.extend(_risco_coluna(mudanca, alvo))
            if mudanca.tipo == "coluna_adicionada":
                adicionadas[mudanca.tabela].append(mudanca)
            elif not mudanca.valores_anteriores or not mudanca.valores_posteriores:
                analise.duvidas.append(f"Quais propriedades de {alvo} mudaram? Os metadados estão incompletos.")
        elif mudanca.tipo == "tabela_alterada":
            analise.duvidas.append(f"Quais regras ou dependências do código mudam com a alteração de {alvo}?")

    for nome_tabela in sorted(removidas.keys() & adicionadas.keys()):
        tabela = f"{evento.nome_schema}.{nome_tabela}"
        if len(removidas[nome_tabela]) != 1 or len(adicionadas[nome_tabela]) != 1:
            analise.duvidas.append(
                f"{tabela} tem várias colunas adicionadas ou removidas. Confirme as correspondências na migração."
            )
            continue

        removida = removidas[nome_tabela][0]
        adicionada = adicionadas[nome_tabela][0]
        coluna_anterior = removida.coluna
        coluna_nova = adicionada.coluna
        if coluna_anterior is None or coluna_nova is None or coluna_anterior == coluna_nova:
            continue
        tipo_anterior = _tipo_coluna(removida.valores_anteriores)
        tipo_atual = _tipo_coluna(adicionada.valores_posteriores)
        if tipo_anterior and tipo_atual and tipo_anterior != tipo_atual:
            continue

        analise.hipoteses_renomeacao.append(HipoteseRenomeacao(
            tabela=nome_tabela, coluna_anterior=coluna_anterior, coluna_nova=coluna_nova,
        ))
        analise.duvidas.append(
            f"Confirme na migração se {tabela}.{coluna_anterior} foi renomeada para {coluna_nova}."
        )

    return analise
