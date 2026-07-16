# Databricks notebook source
# MAGIC %md
# MAGIC # Base Generator — Job Runner
# MAGIC
# MAGIC Notebook genérico usado pelo **Job** que o app "base-generator" aciona.
# MAGIC
# MAGIC Ele não contém lógica de base. Recebe o **código do notebook gerado pelo
# MAGIC app** (parâmetro `source_b64`, o fonte Scala em base64), **grava esse
# MAGIC notebook na home de quem roda o Job** e o executa via `dbutils.notebook.run`.
# MAGIC O notebook gerado é quem constrói a base e grava o(s) CSV(s) no UC Volume.
# MAGIC
# MAGIC **Por que gravar aqui (e não no app):** o app roda como a *service
# MAGIC principal*, mas o Job roda como **outra identidade** (a run-as, com acesso
# MAGIC aos dados). Gravando aqui, quem cria o notebook é a **própria run-as** —
# MAGIC então ela sempre pode executá-lo. Sem depender de ACL entre identidades.
# MAGIC
# MAGIC **Concorrência:** cada run recebe um `run_id` exclusivo (usuário + timestamp)
# MAGIC e grava num caminho próprio, então runs simultâneos não colidem. Ajuste
# MAGIC `max_concurrent_runs` no Job conforme o uso.

# COMMAND ----------

dbutils.widgets.text("source_b64", "", "Código do notebook (Scala) em base64")
dbutils.widgets.text("run_id", "", "Identificador único do run")
dbutils.widgets.text("timeout_seconds", "3600", "Timeout (segundos)")
dbutils.widgets.text(
    "notebook_dir", "", "Pasta base p/ o notebook (default /Shared/base_generator/runs)"
)

source_b64 = dbutils.widgets.get("source_b64").strip()
run_id = (dbutils.widgets.get("run_id") or "").strip() or "run"
timeout_seconds = int((dbutils.widgets.get("timeout_seconds") or "3600").strip())
notebook_dir_param = (dbutils.widgets.get("notebook_dir") or "").strip()

if not source_b64:
    raise ValueError(
        "Parâmetro 'source_b64' é obrigatório — o app deve passá-lo no run_now."
    )


def _normalize_ws_path(p):
    """Caminho de objeto do workspace (raiz `/`), não o mount `/Workspace`.

    `dbutils.notebook.run` e a Workspace API endereçam notebooks em `/Shared/…`,
    `/Users/…` — um prefixo `/Workspace` quebra a resolução. Também garante a
    barra inicial. NB: nada de pastas ".algo" (ocultas não resolvem no run).
    """
    p = (p or "").strip()
    if p.startswith("/Workspace/"):
        p = p[len("/Workspace"):]
    elif p == "/Workspace":
        p = "/"
    if p and not p.startswith("/"):
        p = "/" + p
    return p.rstrip("/")


# Pasta base: por padrão a compartilhada /Shared/base_generator/runs; se o app
# passar `notebook_dir`, usa ele. A run-as grava e roda (mesma identidade), então
# não há ACL cruzada — só precisa de write na pasta.
shared_dir = _normalize_ws_path(notebook_dir_param) or "/Shared/base_generator/runs"

# Identidade em que o Job roda (usada no fallback e no diagnóstico).
current_user = spark.sql("SELECT current_user()").collect()[0][0]
home_dir = f"/Users/{current_user}/base_generator_runs"
print(f"Run-as: {current_user}")
print(f"Preferred notebook dir: {shared_dir} (timeout={timeout_seconds}s)")

# COMMAND ----------

import time

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.workspace import ImportFormat, Language

w = WorkspaceClient()  # autentica como a identidade do Job (run-as)


def _import_to(folder):
    """Grava o notebook em `<folder>/<run_id>` e retorna o path. `source_b64` já é
    o base64 do fonte SOURCE — exatamente o que o import espera."""
    path = f"{folder}/{run_id}"
    w.workspace.mkdirs(folder)
    w.workspace.import_(
        path=path,
        format=ImportFormat.SOURCE,
        language=Language.SCALA,
        content=source_b64,
        overwrite=True,
    )
    return path


# Tenta a pasta compartilhada; se a run-as não tiver write nela, cai pra home.
try:
    folder = shared_dir
    nb_path = _import_to(folder)
except Exception as e:
    print(f"(fallback) sem acesso a {shared_dir}: {e} — usando a home do run-as")
    folder = home_dir
    nb_path = _import_to(folder)

print(f"Target notebook: {nb_path}")

# Confirma que o objeto foi criado e é um NOTEBOOK antes de rodar. O import é
# assíncrono o suficiente pra que um `dbutils.notebook.run` imediato às vezes veja
# "does not exist" — então esperamos o get_status estabilizar.
status = None
for attempt in range(15):
    try:
        status = w.workspace.get_status(nb_path)
        if status is not None:
            break
    except Exception as e:
        last = e
    time.sleep(2)

if status is None:
    raise RuntimeError(
        f"Notebook não apareceu em {nb_path} após o import (get_status falhou). "
        f"Último erro: {locals().get('last')}"
    )

obj_type = getattr(getattr(status, "object_type", None), "value", None) or getattr(
    status, "object_type", None
)
print(f"Imported OK: object_type={obj_type} object_id={getattr(status, 'object_id', None)}")

try:
    listing = list(w.workspace.list(folder))
    print(f"Folder {folder} has {len(listing)} item(s).")
except Exception as e:
    print(f"(diagnostic) could not list {folder}: {e}")

# COMMAND ----------

# Executa o notebook gerado. Ele constrói a base (com seus `%run` nativos) e grava
# o(s) CSV(s) no Volume indicado dentro do próprio notebook.
#
# IMPORTANTE: só apagamos o notebook em caso de SUCESSO. Se o run falhar, ele é
# mantido em `nb_path` para troubleshooting (abra-o no workspace e rode célula a
# célula para ver a exceção real). Runs bem-sucedidos limpam sozinhos.
result = dbutils.notebook.run(nb_path, timeout_seconds)

try:
    w.workspace.delete(nb_path)
except Exception as _e:
    print(f"(cleanup) could not delete {nb_path}: {_e}")

# COMMAND ----------

# Sinaliza sucesso para o run do Job (o app confirma pelo estado do run e depois
# baixa o CSV direto do Volume).
dbutils.notebook.exit(result or "OK")
