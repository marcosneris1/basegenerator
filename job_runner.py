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
# MAGIC aos dados). Se a SP gravasse o notebook, a run-as não conseguiria lê-lo
# MAGIC (`Unable to access the notebook … lacks the required permissions`). Gravando
# MAGIC aqui, quem cria o notebook é a **própria run-as** — então ela sempre pode
# MAGIC executá-lo. Sem depender de ACL entre identidades.
# MAGIC
# MAGIC **Concorrência:** cada run recebe um `run_id` exclusivo (usuário + timestamp)
# MAGIC e grava num caminho próprio, então runs simultâneos não colidem. Ajuste
# MAGIC `max_concurrent_runs` no Job conforme o uso.

# COMMAND ----------

import base64

dbutils.widgets.text("source_b64", "", "Código do notebook (Scala) em base64")
dbutils.widgets.text("run_id", "", "Identificador único do run")
dbutils.widgets.text("timeout_seconds", "3600", "Timeout (segundos)")

source_b64 = dbutils.widgets.get("source_b64").strip()
run_id = (dbutils.widgets.get("run_id") or "").strip() or "run"
timeout_seconds = int((dbutils.widgets.get("timeout_seconds") or "3600").strip())

if not source_b64:
    raise ValueError(
        "Parâmetro 'source_b64' é obrigatório — o app deve passá-lo no run_now."
    )

# COMMAND ----------

# Identidade que está rodando o Job (a run-as). Grava o notebook na home dela,
# então ela é dona do objeto e consegue executá-lo sem ACL extra.
current_user = spark.sql("SELECT current_user()").collect()[0][0]
nb_path = f"/Users/{current_user}/.base_generator_runs/{run_id}"
print(f"Run-as: {current_user}")
print(f"Importing generated notebook to: {nb_path} (timeout={timeout_seconds}s)")

# COMMAND ----------

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.workspace import ImportFormat, Language

w = WorkspaceClient()  # autentica como a identidade do Job (run-as)
w.workspace.mkdirs(nb_path.rsplit("/", 1)[0])
# `source_b64` já é o base64 do fonte SOURCE — é exatamente o que o import espera.
w.workspace.import_(
    path=nb_path,
    format=ImportFormat.SOURCE,
    language=Language.SCALA,
    content=source_b64,
    overwrite=True,
)

# COMMAND ----------

# Executa o notebook gerado. Ele constrói a base (com seus `%run` nativos) e grava
# o(s) CSV(s) no Volume indicado dentro do próprio notebook.
try:
    result = dbutils.notebook.run(nb_path, timeout_seconds)
finally:
    # Limpa o notebook temporário (best-effort).
    try:
        w.workspace.delete(nb_path)
    except Exception as _e:
        print(f"(cleanup) could not delete {nb_path}: {_e}")

# COMMAND ----------

# Sinaliza sucesso para o run do Job (o app confirma pelo estado do run e depois
# baixa o CSV direto do Volume).
dbutils.notebook.exit(result or "OK")
