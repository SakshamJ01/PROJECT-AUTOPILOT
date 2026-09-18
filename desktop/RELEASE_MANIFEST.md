# PROJECT-AUTOPILOT Release Artifact Manifest — M7 (Final)
# Generated: 2026-09-18T11:09 (UTC)
# Before release, SHA-256: 8FC628BBB19476112506358A3275384FC7B5852040B09E7702D487303E865806 (M6 build, 10:08Z)
# After M7 final regression rebuild: see below.

[autopilot-desktop.exe]
path = desktop/src-tauri/target/release/autopilot-desktop.exe
sha256 = 62F2D247230F429ED2BDD37CAFE9CD66CCFCFEB2D321C01BA3C2D3A6F3FC2F97
size_bytes = 9024512
size_mb = 8.61
file_version = 0.1.0
product_version = 0.1.0
product_name = autopilot-desktop
build_timestamp_utc = 2026-09-18T11:09:07Z
installer = NONE (bundling disabled in tauri.conf.json; standalone exe only by design)

[verify]
powershell_command = Get-FileHash <path> -Algorithm SHA256
expected = 62F2D247230F429ED2BDD37CAFE9CD66CCFCFEB2D321C01BA3C2D3A6F3FC2F97