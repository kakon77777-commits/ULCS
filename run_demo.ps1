$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
python -m unittest discover -s tests -v
python -m sos_mvp examples/error_report.sos --dry-run --emit-ir output/language_operator_graph.json
python -m sos_mvp examples/error_report.sos --yes --json
