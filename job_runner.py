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
# NB: nome da pasta SEM ponto inicial — pastas ".algo" são ocultas e o
# `dbutils.notebook.run` pode não resolvê-las.
current_user = spark.sql("SELECT current_user()").collect()[0][0]
folder = f"/Users/{current_user}/base_generator_runs"
nb_path = f"{folder}/{run_id}"
print(f"Run-as: {current_user}")
print(f"Target notebook: {nb_path} (timeout={timeout_seconds}s)")

# COMMAND ----------

import time

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.workspace import ImportFormat, Language

w = WorkspaceClient()  # autentica como a identidade do Job (run-as)
w.workspace.mkdirs(folder)
# `source_b64` já é o base64 do fonte SOURCE — é exatamente o que o import espera.
w.workspace.import_(
    path=nb_path,
    format=ImportFormat.SOURCE,
    language=Language.SCALA,
    content=source_b64,
    overwrite=True,
)

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
