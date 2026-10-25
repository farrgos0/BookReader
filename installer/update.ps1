# Обновление установленного сервера BookReader: с GitHub (по умолчанию) или из архива-обновления
# (BookReader_Server_vX.Y.Z_update.zip) / полного архива. Запуск: update.bat (или перетащите на него архив).
#
# Что делает (ничего не ломая, если что-то пошло не так):
#   1. проверяет контрольную сумму архива (файл .sha256 рядом) и каждого файла внутри (список bookreader-files.json);
#   2. сравнивает версии; если сервер запущен — предлагает его остановить;
#   3. сохраняет копию заменяемых файлов (папка данных\update-backup) — при ошибке всё возвращается обратно;
#   4. заменяет файлы программы, удаляет те, что были в прошлой версии и больше не нужны;
#      свой Python, пакеты, каталог книг, настройки и журналы не трогает;
#   5. если изменился список библиотек (requirements.txt) — доустанавливает недостающее (install.bat).
#
#   update.bat               — проверить GitHub и скачать новую версию; нет интернета — архив рядом или выбрать вручную
#   update.bat <архив.zip>   — обновить из архива (перетащить архив на update.bat)
#   update.bat -Check        — есть ли новая версия на GitHub (код выхода 10 — есть, 0 — нет)
#   update.bat -Online       — только с GitHub (не получилось — ошибка, без поиска архивов)
#   update.bat -Offline      — не ходить в интернет: только архив рядом или выбранный вручную
#   -Yes                     — без вопросов
# Откуда брать обновления на GitHub — "update_repo" в server.json (владелец/репозиторий) или $DefaultRepo ниже.

param([string]$Archive = "", [switch]$Check, [switch]$Online, [switch]$Offline, [switch]$Yes, [switch]$Bootstrapped)

$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
Add-Type -AssemblyName System.IO.Compression, System.IO.Compression.FileSystem

$DefaultRepo = "farrgos0/BookReader"   # откуда update.bat -Online берёт обновления (выпуски на GitHub)
$Root     = if ($env:BOOKREADER_ROOT) { $env:BOOKREADER_ROOT } else { Split-Path -Parent $PSScriptRoot }
$DataDir  = if ($env:BOOKREADER_HOME) { $env:BOOKREADER_HOME } else { Join-Path $env:LOCALAPPDATA "BookReader" }
$LogDir   = Join-Path $DataDir "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Log      = Join-Path $LogDir "update.log"
$Manifest = "bookreader-files.json"
# не трогаем никогда, даже если путь вдруг оказался в списке файлов
$Protected = '^(python|packages|catalog|dist|\.git)([\\/]|$)|^data[\\/]cmudict\.dict$|^BookReader\.exe$'

function Log($t) { try { "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $t" | Out-File -FilePath $Log -Append -Encoding utf8 } catch {} }
function Info($t) { Write-Host "  $t"; Log $t }
function Ok($t) { Write-Host "  √ $t" -ForegroundColor Green; Log "OK: $t" }
function Warn($t) { Write-Host "  ! $t" -ForegroundColor Yellow; Log "WARN: $t" }
function Fail($t) { Write-Host ""; Write-Host "Ошибка: $t" -ForegroundColor Red; Log "FAIL: $t"; exit 1 }
function Ask($q) { if ($Yes) { return $true }; return (Read-Host "$q (д/Н)") -match '^(д|y)' }
function Parse-Ver([string]$s) {
    $p = @(($s -replace '[^0-9.]', '').Split('.') | Where-Object { $_ -ne "" } | ForEach-Object { [int]$_ })
    while ($p.Count -lt 3) { $p += 0 }
    return [version]::new($p[0], $p[1], $p[2])
}
function Sha([byte[]]$b) { $h = [Security.Cryptography.SHA256]::Create(); try { return ([BitConverter]::ToString($h.ComputeHash($b)) -replace '-', '').ToLower() } finally { $h.Dispose() } }
function Sha-File($p) { return (Get-FileHash $p -Algorithm SHA256).Hash.ToLower() }

Log "=== обновление: $(Get-Date)"
Write-Host ""
Write-Host "Обновление сервера BookReader" -ForegroundColor Cyan

$serverPy = Join-Path $Root "server.py"
if (-not (Test-Path $serverPy)) { Fail "здесь нет установленного BookReader ($Root)" }
$curVer = if ([IO.File]::ReadAllText($serverPy) -match '(?m)^VERSION = "([^"]+)"') { $Matches[1] } else { "0.0.0" }
Info "установлена версия $curVer"

# ---------- откуда брать обновление ----------
# По умолчанию — с GitHub (последний выпуск). Нет интернета или GitHub не ответил — из архива рядом
# (папка BookReader, папка выше, «Загрузки») или из выбранного вручную. Архив новее, чем на GitHub, важнее.
$cfg = $null
try { $cfg = Get-Content (Join-Path $DataDir "server.json") -Raw -Encoding UTF8 | ConvertFrom-Json } catch {}
$Repo = if ($cfg -and $cfg.update_repo) { "$($cfg.update_repo)" } else { $DefaultRepo }

# последняя версия на GitHub: { Version, Tag, Zip, Sha, Notes } или $null
function Get-LatestRelease {
    if (-not $Repo) { return $null }
    $r = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases/latest" -UseBasicParsing `
        -Headers @{ "User-Agent" = "BookReader-updater"; "Accept" = "application/vnd.github+json" } -TimeoutSec 20
    $zip = $r.assets | Where-Object { $_.name -match '^BookReader_Server_v[\d.]+_update\.zip$' } | Select-Object -First 1
    if (-not $zip) { return $null }
    $sha = $r.assets | Where-Object { $_.name -eq "$($zip.name).sha256" } | Select-Object -First 1
    return [pscustomobject]@{
        Version = "$($r.tag_name)".TrimStart('v', 'V'); Tag = $r.tag_name; Notes = "$($r.body)"
        Zip = $zip.browser_download_url; ZipName = $zip.name; Sha = $(if ($sha) { $sha.browser_download_url } else { $null })
    }
}

# скачать файл (curl из Windows — с докачкой; нет его — средствами PowerShell); $true — скачан
function Fetch([string]$url, [string]$dest, [switch]$Quiet) {
    $curl = Join-Path $env:WINDIR "System32\curl.exe"
    if (Test-Path $curl) {
        if ($Quiet) { & $curl -L --fail --retry 3 -s -o $dest $url 2>$null } else { & $curl -L --fail --retry 3 -C - -# -o $dest $url }
        if ($LASTEXITCODE -eq 0 -and (Test-Path $dest)) { return $true }
    }
    try {
        $ProgressPreference = "SilentlyContinue"
        Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing -TimeoutSec 120 -Headers @{ "User-Agent" = "BookReader-updater" }
        return $true
    } catch { return $false } finally { $ProgressPreference = "Continue" }
}

# архив выпуска с GitHub → путь в кэше (с файлом .sha256 рядом) или $null
function Download-Release($rel) {
    $dl = Join-Path $DataDir "cache"
    New-Item -ItemType Directory -Force -Path $dl | Out-Null
    # прежние скачанные обновления больше не нужны
    Get-ChildItem $dl -Filter "BookReader_Server_v*_update.zip*" -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -notlike "$($rel.ZipName)*" } | Remove-Item -Force -ErrorAction SilentlyContinue
    $path = Join-Path $dl $rel.ZipName
    Info "скачиваю $($rel.ZipName) с GitHub…"
    if ($rel.Sha) {
        Remove-Item "$path.sha256" -Force -ErrorAction SilentlyContinue
        if (-not (Fetch $rel.Sha "$path.sha256" -Quiet)) { Warn "не скачалась контрольная сумма $($rel.ZipName).sha256"; return $null }
    } else { Warn "в выпуске нет $($rel.ZipName).sha256 — архив проверю только по списку файлов внутри него" }
    if ((Test-Path $path) -and (Test-Path "$path.sha256") -and
        (Sha-File $path) -eq ((Get-Content "$path.sha256" -Raw) -split '\s+')[0].ToLower()) { Info "уже скачан"; return $path }
    Remove-Item $path -Force -ErrorAction SilentlyContinue
    if (-not (Fetch $rel.Zip $path)) { Warn "не скачался $($rel.Zip)"; Remove-Item $path -Force -ErrorAction SilentlyContinue; return $null }
    return $path
}

# самый новый архив BookReader рядом: папка BookReader, папка выше, «Загрузки»
function Find-LocalArchive {
    $dirs = @($Root, (Split-Path $Root -Parent), (Join-Path $env:USERPROFILE "Downloads")) | Where-Object { $_ -and (Test-Path $_) }
    return $dirs | ForEach-Object { Get-ChildItem $_ -Filter "BookReader_Server_v*.zip" -File -ErrorAction SilentlyContinue } |
        Where-Object { $_.Name -match '^BookReader_Server_v([\d.]+)_(update|full)\.zip$' } |
        Sort-Object { Parse-Ver ($_.Name -replace '^BookReader_Server_v([\d.]+)_.*$', '$1') }, { $_.Name -like "*_update.zip" } -Descending |
        Select-Object -First 1
}
$verOfZip = { param($f) ($f.Name -replace '^BookReader_Server_v([\d.]+)_.*$', '$1') }

$rel = $null; $ghError = $null
if (-not $Archive -and -not $Offline) {
    if (-not $Repo) { $ghError = "не указан репозиторий для обновлений (update_repo в $(Join-Path $DataDir 'server.json'))" }
    else {
        Info "проверяю обновления на GitHub ($Repo)…"
        try { $rel = Get-LatestRelease; if (-not $rel) { $ghError = "в последнем выпуске нет архива-обновления" } }
        catch { $ghError = "GitHub не ответил: $($_.Exception.Message)" }
    }
}

if ($Check) {
    if (-not $rel) { Fail $ghError }
    if ((Parse-Ver $rel.Version) -gt (Parse-Ver $curVer)) {
        Write-Host "  Есть новая версия: $($rel.Version) (установлена $curVer)" -ForegroundColor Green
        if ($rel.Notes) { Write-Host $rel.Notes }
        exit 10
    }
    Write-Host "  Установлена последняя версия ($curVer)"; exit 0
}
if ($Online -and -not $rel) { Fail $ghError }

if (-not $Archive) {
    $local = Find-LocalArchive
    $localVer = if ($local) { Parse-Ver (& $verOfZip $local) } else { $null }
    $cur = Parse-Ver $curVer
    if ($rel) {
        $ghVer = Parse-Ver $rel.Version
        if ($local -and $localVer -ge $ghVer -and $localVer -gt $cur) {
            # свой архив не старее GitHub (например, сборка для проверки) — скачивать нечего
            if (Ask "Обновить до $(& $verOfZip $local) из $($local.FullName)?") { $Archive = $local.FullName } else { Write-Host "Отменено."; exit 0 }
        } elseif ($ghVer -gt $cur) {
            Write-Host ""
            Write-Host "  Есть новая версия: $($rel.Version) (установлена $curVer)" -ForegroundColor Green
            if ($rel.Notes) { Write-Host ""; Write-Host (($rel.Notes -split "`r?`n" | Select-Object -First 25) -join "`n"); Write-Host "" }
            if (-not (Ask "Скачать и установить $($rel.Version)?")) { Write-Host "Отменено."; exit 0 }
            $Archive = Download-Release $rel
            if (-not $Archive) {
                Warn "с GitHub скачать не получилось — можно обновить из архива, скачанного вручную"
                if ($local -and $localVer -gt $cur -and (Ask "Обновить до $(& $verOfZip $local) из $($local.FullName)?")) { $Archive = $local.FullName }
            }
        } elseif ($Online) {
            if (-not (Ask "Новее нет (на GitHub — $($rel.Version)). Всё равно переустановить?")) { exit 0 }
            $Archive = Download-Release $rel
            if (-not $Archive) { Fail "не удалось скачать обновление с GitHub" }
        } else {
            Ok "установлена последняя версия ($curVer)"
            Write-Host "  Обновить из своего архива: перетащите его на update.bat"
            exit 0
        }
    } else {
        if ($ghError -and -not $Offline) { Warn "$ghError — ищу архив обновления на этом ПК" }
        if ($local -and (Ask "Обновить из $($local.FullName)?")) { $Archive = $local.FullName }
    }
    if (-not $Archive) {
        if ($Yes) { Fail "архив обновления не найден" }
        Add-Type -AssemblyName System.Windows.Forms
        $dlg = New-Object Windows.Forms.OpenFileDialog
        $dlg.Title = "Архив обновления BookReader"; $dlg.Filter = "Архив BookReader|BookReader_Server_v*.zip|ZIP|*.zip"
        if ($dlg.ShowDialog() -ne [Windows.Forms.DialogResult]::OK) { Write-Host "Отменено."; exit 0 }
        $Archive = $dlg.FileName
    }
}
$Archive = $Archive.Trim('"')
if (-not (Test-Path $Archive)) { Fail "нет файла $Archive" }
Info "архив: $Archive"

# ---------- 2. контрольная сумма архива ----------
$shaFile = "$Archive.sha256"
if (Test-Path $shaFile) {
    $want = ((Get-Content $shaFile -Raw) -split '\s+')[0].ToLower()
    if ((Sha-File $Archive) -ne $want) { Fail "архив повреждён или подменён: контрольная сумма не совпала с $(Split-Path $shaFile -Leaf). Скачайте его заново." }
    Ok "контрольная сумма архива совпала"
} else { Warn "рядом нет файла $(Split-Path $shaFile -Leaf) — архив проверю только по списку файлов внутри него" }

# ---------- 3. содержимое и список файлов ----------
$zip = [IO.Compression.ZipFile]::OpenRead($Archive)
try {
    $byName = @{}
    foreach ($e in $zip.Entries) { if ($e.FullName -like "BookReader/*" -and -not $e.FullName.EndsWith("/")) { $byName[$e.FullName.Substring(11)] = $e } }
    if (-not $byName.ContainsKey($Manifest)) { Fail "в архиве нет списка файлов ($Manifest) — это не архив BookReader или архив старой версии (до 1.1)" }
    $r = New-Object IO.StreamReader($byName[$Manifest].Open(), [Text.Encoding]::UTF8); $newMan = $r.ReadToEnd() | ConvertFrom-Json; $r.Dispose()
    $newVer = "$($newMan.version)"

    # обновлённый установщик внутри архива — запускаем его (свежая логика обновления), а не этот
    $mine = $PSCommandPath
    if (-not $Bootstrapped -and $byName.ContainsKey("installer/update.ps1")) {
        $ms = New-Object IO.MemoryStream; $s = $byName["installer/update.ps1"].Open(); $s.CopyTo($ms); $s.Dispose()
        $bytes = $ms.ToArray()
        if ((Sha $bytes) -ne (Sha-File $mine)) {
            $tmpUpd = Join-Path $env:TEMP "bookreader-update-$newVer.ps1"
            [IO.File]::WriteAllBytes($tmpUpd, $bytes)
            $zip.Dispose(); $zip = $null
            Info "запускаю установщик обновления из архива (версия $newVer)…"
            $env:BOOKREADER_ROOT = $Root
            $a = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $tmpUpd, "-Archive", $Archive, "-Bootstrapped")
            if ($Yes) { $a += "-Yes" }
            & powershell @a
            exit $LASTEXITCODE
        }
    }

    Info "в архиве версия $newVer (файлов программы: $(@($newMan.files.PSObject.Properties).Count))"
    $cmp = (Parse-Ver $newVer).CompareTo((Parse-Ver $curVer))
    if ($cmp -lt 0 -and -not (Ask "В архиве версия СТАРЕЕ установленной ($newVer < $curVer). Всё равно установить?")) { exit 0 }
    if ($cmp -eq 0 -and -not (Ask "Эта версия ($newVer) уже установлена. Переустановить файлы?")) { exit 0 }

    # ---------- 4. распаковка во временную папку и проверка каждого файла ----------
    $stage = Join-Path $env:TEMP ("bookreader-update-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Force -Path $stage | Out-Null
    $newFiles = @{}
    foreach ($p in $newMan.files.PSObject.Properties) {
        $rel = $p.Name; $want = "$($p.Value)".ToLower()
        if ($rel -match '\.\.|^[\\/]|:' ) { Fail "недопустимый путь в списке файлов: $rel" }
        if (-not $byName.ContainsKey($rel)) { Fail "в архиве не хватает файла $rel — архив неполный, скачайте заново" }
        $dst = Join-Path $stage $rel
        New-Item -ItemType Directory -Force -Path (Split-Path $dst -Parent) | Out-Null
        [IO.Compression.ZipFileExtensions]::ExtractToFile($byName[$rel], $dst, $true)
        if ((Sha-File $dst) -ne $want) { Fail "файл $rel в архиве повреждён (контрольная сумма не совпала)" }
        $newFiles[$rel] = $want
    }
    Ok "все файлы архива целы ($($newFiles.Count))"
} finally { if ($zip) { $zip.Dispose() } }

# ---------- 5. сервер запущен? ----------
$py = Join-Path $Root "python\python.exe"
$running = @()
try { $running = @(Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction Stop | Where-Object { $_.ExecutablePath -eq $py -or "$($_.CommandLine)" -like "*$Root*server.py*" }) } catch {}
$wasRunning = $running.Count -gt 0
if ($wasRunning) {
    if (-not (Ask "Сервер BookReader сейчас работает. Остановить его на время обновления?")) { Fail "обновление отменено: сначала закройте окно сервера" }
    $running | ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop } catch {} }
    Start-Sleep -Milliseconds 800
    Ok "сервер остановлен"
}

# ---------- 6. что заменить и что удалить ----------
$oldMan = $null
$oldPath = Join-Path $Root $Manifest
if (Test-Path $oldPath) { try { $oldMan = Get-Content $oldPath -Raw -Encoding UTF8 | ConvertFrom-Json } catch {} }
$oldFiles = @{}
if ($oldMan) { foreach ($p in $oldMan.files.PSObject.Properties) { $oldFiles[$p.Name] = "$($p.Value)".ToLower() } }
else { Warn "у установленной версии нет списка файлов — лишние файлы прошлой версии не удаляю (только заменяю)" }
$toDelete = @($oldFiles.Keys | Where-Object { -not $newFiles.ContainsKey($_) -and $_ -notmatch $Protected -and (Test-Path (Join-Path $Root $_)) })
$toCopy = @($newFiles.Keys | Where-Object { $_ -notmatch $Protected })
# файл, который пользователь изменил сам (например, свои правила произношения data\en_ru.json), — сохраним его копию рядом
$userMod = @($toCopy | Where-Object { $oldFiles.ContainsKey($_) -and (Test-Path (Join-Path $Root $_)) -and (Sha-File (Join-Path $Root $_)) -ne $oldFiles[$_] -and (Sha-File (Join-Path $Root $_)) -ne $newFiles[$_] })

# ---------- 7. резервная копия и замена ----------
$bk = Join-Path $DataDir ("update-backup\" + $curVer + "-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
New-Item -ItemType Directory -Force -Path $bk | Out-Null
$added = New-Object System.Collections.Generic.List[string]
try {
    foreach ($rel in @($toCopy + $toDelete)) {
        $src = Join-Path $Root $rel
        if (Test-Path $src) { $d = Join-Path $bk $rel; New-Item -ItemType Directory -Force -Path (Split-Path $d -Parent) | Out-Null; Copy-Item $src $d -Force }
    }
    foreach ($rel in $userMod) {
        $keep = (Join-Path $Root $rel) + ".мой-" + (Get-Date -Format "yyyyMMdd")
        Copy-Item (Join-Path $Root $rel) $keep -Force
        Warn "вы меняли $rel — ваша версия сохранена как $(Split-Path $keep -Leaf)"
    }
    foreach ($rel in $toCopy) {
        $dst = Join-Path $Root $rel
        if (-not (Test-Path $dst)) { $added.Add($rel) }
        New-Item -ItemType Directory -Force -Path (Split-Path $dst -Parent) | Out-Null
        Copy-Item (Join-Path $stage $rel) $dst -Force
    }
    foreach ($rel in $toDelete) { Remove-Item (Join-Path $Root $rel) -Force; Log "удалён: $rel" }
} catch {
    # что-то пошло не так (файл занят и т. п.) — возвращаем всё как было
    Warn "ошибка при замене файлов: $($_.Exception.Message) — возвращаю прежнюю версию"
    Get-ChildItem $bk -Recurse -File | ForEach-Object { Copy-Item $_.FullName (Join-Path $Root $_.FullName.Substring($bk.Length).TrimStart('\', '/')) -Force }
    foreach ($rel in $added) { Remove-Item (Join-Path $Root $rel) -Force -ErrorAction SilentlyContinue }
    Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue
    Fail "обновление не установлено, прежняя версия $curVer на месте. Закройте программы, которые могут держать файлы BookReader, и запустите обновление ещё раз."
}
Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue
# пустые папки после удаления
foreach ($rel in $toDelete) {
    $d = Split-Path (Join-Path $Root $rel) -Parent
    while ($d -and $d.Length -gt $Root.Length -and (Test-Path $d) -and -not (Get-ChildItem $d -Force)) { Remove-Item $d -Force; $d = Split-Path $d -Parent }
}
# старые резервные копии: оставляем три последние
Get-ChildItem (Join-Path $DataDir "update-backup") -Directory | Sort-Object LastWriteTime -Descending | Select-Object -Skip 3 | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Ok ("файлы обновлены: заменено/добавлено {0}, удалено лишних {1}" -f $toCopy.Count, $toDelete.Count)
Info "копия прежней версии: $bk"

# ---------- 8. библиотеки ----------
$reqOld = if ($oldFiles.ContainsKey("requirements.txt")) { $oldFiles["requirements.txt"] } else { $null }
if ($newFiles.ContainsKey("requirements.txt") -and $reqOld -ne $newFiles["requirements.txt"]) {
    Info "у новой версии изменился список библиотек — проверяю и доустанавливаю недостающее"
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root "installer\install.ps1") -Yes
    if ($LASTEXITCODE -ne 0) { Warn "доустановка завершилась с ошибкой — запустите install.bat вручную" }
}

Write-Host ""
Write-Host "Готово: BookReader обновлён до версии $newVer" -ForegroundColor Green
Log "готово: $curVer → $newVer"
if ($wasRunning -and (Ask "Запустить сервер снова?")) {
    $exe = Join-Path $Root "BookReader.exe"
    if (Test-Path $exe) { Start-Process $exe } else { Start-Process (Join-Path $Root "BookReader.bat") }
}
