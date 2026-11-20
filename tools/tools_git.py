"""Operações Git explícitas para publicar uma proposta já aprovada."""

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from sh_pscbs.agentes.correcao import identificar_codigo_base
from sh_pscbs.validacao.executor import ArtefatosProposta


@dataclass(frozen=True)
class CommitProposta:
    branch: str
    commit: str


class GitProjeto:
    def __init__(self, pasta: str | Path):
        self.raiz = Path(pasta).resolve(strict=True)
        if Path(self._texto("rev-parse", "--show-toplevel")).resolve() != self.raiz:
            raise ValueError("PROJECT_PATH precisa ser a raiz do repositório Git para aprovar uma proposta.")

    def _git(self, *args, dados=None, indice=None, codigos=(0,), timeout=30):
        ambiente = {k: v for k, v in os.environ.items() if k not in {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"}}
        ambiente["GIT_TERMINAL_PROMPT"] = "0"
        ambiente["GIT_OPTIONAL_LOCKS"] = "0"
        if indice is not None:
            ambiente["GIT_INDEX_FILE"] = str(indice)
        try:
            resultado = subprocess.run(
                ["git", "--literal-pathspecs", "-C", str(self.raiz), *args],
                input=dados, env=ambiente, capture_output=True, timeout=timeout, check=False,
            )
        except FileNotFoundError:
            raise RuntimeError("Git precisa estar instalado.") from None
        if resultado.returncode not in codigos:
            erro = resultado.stderr.decode("utf-8", errors="replace").strip()
            erro = re.sub(r"(https?://)[^\s/@]+@", r"\1[oculto]@", erro)
            raise RuntimeError(f"Git {args[0]} falhou: {erro or resultado.returncode}")
        return resultado

    def _texto(self, *args, **kwargs):
        return self._git(*args, **kwargs).stdout.decode("utf-8").strip()

    def status(self) -> str:
        return self._texto("status", "--porcelain", "--untracked-files=normal")

    def criar_commit_proposta(self, id_proposta: str, artefatos: ArtefatosProposta, *, codigo_base_id: str) -> CommitProposta:
        """Cria o commit em índice temporário sem trocar branch nem editar o checkout."""
        if not re.fullmatch(r"[a-f0-9]{32}", id_proposta):
            raise ValueError("Identificador de proposta inválido para publicar.")
        base = self._texto("rev-parse", "HEAD")
        if self.status():
            raise ValueError("O checkout original deve estar limpo para criar a branch de correção.")
        if identificar_codigo_base(self.raiz) != codigo_base_id:
            raise ValueError("O projeto original mudou desde a aprovação da proposta.")
        branch = f"sh-pscbs/{id_proposta}"
        ref = f"refs/heads/{branch}"

        with TemporaryDirectory(prefix="sh-pscbs-indice-") as temporario:
            indice = Path(temporario).resolve() / "index"
            self._git("read-tree", base, indice=indice)
            for arquivo in artefatos.arquivos:
                caminho = arquivo.acao.arquivo
                existente = self._git("ls-files", "--stage", "-z", "--", caminho, indice=indice).stdout
                if arquivo.antes is not None:
                    modo = existente.split(b" ", 1)[0].decode("ascii")
                    if modo not in {"100644", "100755"}:
                        raise ValueError(f"O caminho {caminho!r} não é um arquivo versionado regular.")
                else:
                    ignorado = self._git("check-ignore", "--quiet", "--", caminho, codigos=(0, 1))
                    if existente or ignorado.returncode == 0:
                        raise ValueError(f"O caminho novo {caminho!r} está versionado ou é ignorado pelo Git.")
                    modo = "100644"

                if arquivo.depois is None:
                    self._git("update-index", "--force-remove", "--", caminho, indice=indice)
                else:
                    blob = self._texto("hash-object", "-w", f"--path={caminho}", "--stdin", dados=arquivo.depois)
                    self._git("update-index", "--add", "--cacheinfo", f"{modo},{blob},{caminho}", indice=indice)
            arvore = self._texto("write-tree", indice=indice)

        if (
            self.status() or identificar_codigo_base(self.raiz) != codigo_base_id
            or self._texto("rev-parse", "HEAD") != base
        ):
            raise ValueError("O checkout mudou durante a preparação do commit.")
        if arvore == self._texto("rev-parse", f"{base}^{{tree}}"):
            raise ValueError("O Git não encontrou mudanças para gravar após normalizar os arquivos.")
        ramo = self._git("show-ref", "--verify", "--hash", ref, codigos=(0, 1, 128))
        if ramo.returncode == 0:
            commit = ramo.stdout.decode("ascii").strip()
            if self._texto("rev-parse", f"{commit}^{{tree}}") != arvore or self._texto("rev-parse", f"{commit}^") != base:
                raise ValueError("A branch da proposta já existe com conteúdo diferente.")
            return CommitProposta(branch, commit)

        commit = self._texto("commit-tree", arvore, "-p", base, "-m", f"fix: apply SH-PSCBS proposal {id_proposta}")
        self._git("update-ref", ref, commit, "0" * len(base))
        return CommitProposta(branch, commit)

    def publicar(self, proposta: CommitProposta, remoto: str = "origin") -> None:
        if remoto.startswith("-") or remoto not in self._texto("remote").splitlines():
            raise ValueError("Informe um remoto Git configurado.")
        if not re.fullmatch(r"sh-pscbs/[a-f0-9]{32}", proposta.branch):
            raise ValueError("Somente branches de proposta do SH-PSCBS podem ser publicadas.")
        if not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", proposta.commit):
            raise ValueError("O commit aprovado não é válido.")
        ref = f"refs/heads/{proposta.branch}"
        if self._texto("rev-parse", "--verify", ref) != proposta.commit:
            raise ValueError("A branch de correção já não aponta para o commit aprovado.")
        self._git("push", remoto, f"{proposta.commit}:{ref}", timeout=120)
