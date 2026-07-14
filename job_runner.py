# Databricks notebook source
# MAGIC %md
# MAGIC # Base Generator — Job Runner
# MAGIC
# MAGIC Notebook genérico usado pelo **Job** que o app "base-generator" aciona.
# MAGIC
# MAGIC Ele não contém lógica de base: apenas recebe o caminho de um notebook
# MAGIC **gerado pelo app** (parâmetro `notebook_path`) e o executa via
# MAGIC `dbutils.notebook.run`. O notebook gerado é quem constrói a base e grava
# MAGIC o(s) CSV(s) no UC Volume.
# MAGIC
# MAGIC **Por que isso existe:** o app aciona *este* Job (permissão dada à service
# MAGIC principal via *resource*, sem precisar do escopo `clusters`/`jobs`). O
# MAGIC compute e o acesso aos dados vêm da identidade em que o Job roda
# MAGIC (ex.: o cluster do grupo `bu-global-debt-resolution`).
# MAGIC
# MAGIC **Concorrência:** cada execução do Job recebe seus próprios parâmetros e um
# MAGIC `notebook_path` exclusivo (por usuário + timestamp), então múltiplos runs
# MAGIC simultâneos não colidem. Ajuste `max_concurrent_runs` no Job conforme o uso.

# COMMAND ----------

dbutils.widgets.text("notebook_path", "", "Caminho do notebook gerado pelo app")
dbutils.widgets.text("timeout_seconds", "3600", "Timeout (segundos)")

notebook_path = dbutils.widgets.get("notebook_path").strip()
timeout_seconds = int((dbutils.widgets.get("timeout_seconds") or "3600").strip())

if not notebook_path:
    raise ValueError(
        "Parâmetro 'notebook_path' é obrigatório — o app deve passá-lo no run_now."
    )

print(f"Running generated notebook: {notebook_path} (timeout={timeout_seconds}s)")

# COMMAND ----------

# Executa o notebook gerado pelo app. Ele constrói a base (com seus `%run`
# nativos) e grava o(s) CSV(s) no Volume indicado dentro do próprio notebook.
result = dbutils.notebook.run(notebook_path, timeout_seconds)

# COMMAND ----------

# Sinaliza sucesso para o run do Job (o app confirma pelo estado do run e depois
# baixa o CSV direto do Volume).
dbutils.notebook.exit(result or "OK")
