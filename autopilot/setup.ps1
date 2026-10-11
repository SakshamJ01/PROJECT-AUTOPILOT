# One-command setup for PROJECT AUTOPILOT (Windows / PowerShell).
# Installs all optional Round-2 dependencies and fills the BGM library.
$ErrorActionPreference = "Stop"
python -m autopilot.bootstrap
exit $LASTEXITCODE
