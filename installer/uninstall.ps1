# Удаление BookReader: свой Python, BookReader.exe, ярлыки, правило брандмауэра.
# Книги в папке catalog и сами файлы программы остаются — папку BookReader можно удалить вручную.
$ErrorActionPreference = "Continue"
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}
$Root = Split-Path -Parent $PSScriptRoot
Write-Host "Удаление BookReader из $Root" -ForegroundColor Cyan
$ans = Read-Host "Удалить установленные компоненты (Python, библиотеки, ярлыки)? (д/Н)"
if ($ans -notmatch "^(д|y)") { Write-Host "Отменено."; exit 0 }

Get-Process python -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "$Root\python\*" } | Stop-Process -Force
Remove-Item (Join-Path $env:TEMP "BookReader_install.log") -ErrorAction SilentlyContinue
foreach ($p in @("python", "BookReader.exe", "install.log")) {   # install.log — от старых версий
    $f = Join-Path $Root $p
    if (Test-Path $f) { Remove-Item $f -Recurse -Force; Write-Host "  удалено: $p" }
}
foreach ($place in @([Environment]::GetFolderPath("Desktop"), (Join-Path ([Environment]::GetFolderPath("StartMenu")) "Programs"))) {
    $l = Join-Path $place "BookReader.lnk"
    if (Test-Path $l) { Remove-Item $l -Force; Write-Host "  удалён ярлык: $l" }
}
$hub = Join-Path $env:USERPROFILE ".cache\torch\hub"
$m = Get-ChildItem $hub -Filter "snakers4_silero-models*" -ErrorAction SilentlyContinue
if ($m) {
    $ans = Read-Host "Удалить и скачанную модель голосов Silero (~100 МБ)? (д/Н)"
    if ($ans -match "^(д|y)") { $m | Remove-Item -Recurse -Force; Get-ChildItem $hub -Filter "*.pt" -Recurse -ErrorAction SilentlyContinue | Remove-Item -Force; Write-Host "  модель удалена" }
}
$data = if ($env:BOOKREADER_HOME) { $env:BOOKREADER_HOME } else { Join-Path $env:LOCALAPPDATA "BookReader" }
if (Test-Path $data) {
    $ans = Read-Host "Удалить и настройки сервера с журналами ($data)? (д/Н)"
    if ($ans -match "^(д|y)") { Remove-Item $data -Recurse -Force; Write-Host "  удалено: $data" }
}
try {
    # все правила BookReader: прежнее (на программу) и новые (порт TCP/UDP) — по любому порту
    $fw = "Get-NetFirewallRule -DisplayName 'BookReader*' -ErrorAction SilentlyContinue | Remove-NetFirewallRule -ErrorAction SilentlyContinue; " +
          "netsh advfirewall firewall delete rule name='BookReader' | Out-Null"
    $cmd = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($fw))
    Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden -ArgumentList "-NoProfile", "-EncodedCommand", $cmd
    Write-Host "  правила брандмауэра удалены"
} catch { Write-Host "  правило брандмауэра не удалено (нет прав администратора)" }
Write-Host "Готово. Папку $Root с книгами каталога можно удалить вручную." -ForegroundColor Green
Read-Host "Нажмите Enter"
