# Сборка архивов сервера для выкладки в Releases (+ .sha256, dist\CHANGELOG.txt). Два вида:
#   полный     — для первой установки: сервер + пакеты для установки без интернета (build-server-full.bat)
#   обновление — только программа (~1 МБ), когда Python, torch и модель уже стоят (build-server-update.bat)
# build-server.bat спросит, какой собрать (или оба сразу — для выпуска).
#
#   build-server.bat                   — спросит, какое это обновление
#   build-server.bat -Bump patch       — без вопроса: patch | minor | major | same
#   build-server.bat -Bump minor -Notes "Что нового" -SkipTests
#
#   build-server.bat -Version 1.0.1    — своя версия X.Y.Z (то же — пункт «5» в меню)
#   build-server.bat -Kind full|update|both — какой архив (без вопроса); -NoPackages — то же, что -Kind update
#   build-server.bat -RefreshPackages  — скачать пакеты заново (новые torch, numpy и т. д.)
#
# Части NVIDIA (~3 ГБ, «…_NVIDIA_torchX.Y.Z_k_of_N.zip») выкладываются на GitHub только когда меняется torch.
# Пока torch тот же, новые выпуски состоят из APK, _full и _update. Установщик просматривает выпуски от новых
# к старым и берёт части из самого нового, где лежит нужный torch. Новый torch (-RefreshPackages) — части
# собираются заново, и их нужно выложить в этот выпуск.
#
# Пакеты для установки без интернета (Python, pip, Visual C++, torch для процессора и для видеокарты, numpy, scipy…,
# модель Silero, словарь) собираются в папку packages\ один раз и потом берутся оттуда. Нужен установленный
# сервер (install.bat): его Python скачивает пакеты, а модель голосов берётся из его папки torch hub.
# Результат:
#   dist\BookReader_Server_vX.Y.Z_full.zip           — полный: сервер + всё для процессора (~0,6 ГБ)
#   dist\BookReader_Server_vX.Y.Z_NVIDIA_torch…_k_of_N.zip — к полному: torch для видеокарты частями (на GitHub файл — до 2 ГБ);
#                                                      распаковываются в ту же папку, установщик соберёт их сам
#   dist\BookReader_Server_vX.Y.Z_update.zip         — обновление: только программа, распаковать поверх установленной
#
# Версия — VERSION в server.py (X.Y.Z). В архив попадает только то, что нужно для установки
# (белый список ниже): без своего Python, книг каталога, журналов, кэшей и скриптов сборки.
# Путей к конкретному ПК в скрипте нет — всё считается от папки, где он лежит.

param(
    [ValidateSet("", "patch", "minor", "major", "same", "manual")] [string]$Bump = "",
    [string]$Version = "",   # своя версия X.Y.Z (например, если прошлая сборка не сохранилась)
    [string]$Notes = "",
    [switch]$SkipTests,
    [ValidateSet("", "full", "update", "both")] [string]$Kind = "",
    [switch]$NoPackages,
    [switch]$RefreshPackages,
    [string]$NvidiaRelease = ""   # выпуск, где уже лежат части NVIDIA этой версии torch (например v1.1.0); обычно не нужен — берётся сам
)
$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}

$Root   = Split-Path -Parent $PSScriptRoot
$Server = Join-Path $Root "server.py"
$Dist   = Join-Path $Root "dist"

# Windows PowerShell 5.1: строка внешней программы в потоке ошибок (тесты, dotnet, keytool пишут туда и обычный вывод)
# при $ErrorActionPreference = "Stop" становится аварийной ошибкой. Внешние программы запускаем без этого режима,
# успех проверяем по коду выхода; строки потока ошибок превращаем в обычный текст.
function Native([scriptblock]$sb) {
    $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { & $sb } finally { $ErrorActionPreference = $old }
}
function Lines { process { if ($_ -is [System.Management.Automation.ErrorRecord]) { $m = $_.Exception.Message; if ($m -and $m -ne "System.Management.Automation.RemoteException") { $m } else { "" } } else { "$_" } } }

$Log = $null   # журнал сборки: dist\build-server-last.log
function Fail($t) {
    Write-Host ""; Write-Host "Ошибка: $t" -ForegroundColor Red
    if ($Log) { Write-Host "Полный журнал сборки: $Log"; try { Stop-Transcript | Out-Null } catch {} }
    exit 1
}
function Step($t) { Write-Host ""; Write-Host "== $t" -ForegroundColor Cyan }

function Parse-Ver([string]$s) {
    $p = @(($s -replace '[^0-9.]', '').Split('.') | Where-Object { $_ -ne "" } | ForEach-Object { [int]$_ })
    while ($p.Count -lt 3) { $p += 0 }
    return [version]::new($p[0], $p[1], $p[2])
}
function Ver-Text([version]$v) { "$($v.Major).$($v.Minor).$($v.Build)" }
function Bump-Ver([version]$v, [string]$kind) {
    switch ($kind) {
        "major" { [version]::new($v.Major + 1, 0, 0) }
        "minor" { [version]::new($v.Major, $v.Minor + 1, 0) }
        "patch" { [version]::new($v.Major, $v.Minor, $v.Build + 1) }
        default { $v }
    }
}

if (-not (Test-Path $Server)) { Fail "не найден server.py — build-server.bat должен лежать в папке сервера BookReader" }
if ($NoPackages -and -not $Kind) { $Kind = "update" }

# ---------- 1. что уже собрано ----------
New-Item -ItemType Directory -Force -Path $Dist | Out-Null
# весь вывод этого окна (и полный вывод тестов) дублируется в журнал — на случай, если окно закроется
try { Start-Transcript -Path (Join-Path $Dist "build-server-last.log") -Force | Out-Null; $Log = Join-Path $Dist "build-server-last.log" } catch {}
Step "Готовые архивы в $Dist"
$built = @(Get-ChildItem $Dist -Filter "BookReader_Server_v*.zip" -ErrorAction SilentlyContinue | ForEach-Object {
    # _full / _update; без суффикса — архивы прежних версий скрипта; части _NVIDIA_ — не отдельная сборка
    if ($_.BaseName -match '^BookReader_Server_v(\d+(\.\d+){1,2})(_full|_update)?$') { [pscustomobject]@{ Ver = (Parse-Ver $Matches[1]); File = $_ } }
} | Sort-Object Ver)
if ($built.Count -eq 0) { Write-Host "  пока ни одного — это будет первая сборка" }
else { foreach ($b in $built) { Write-Host ("  {0,-10} {1,6:N1} МБ  {2:dd.MM.yyyy HH:mm}" -f (Ver-Text $b.Ver), ($b.File.Length / 1MB), $b.File.LastWriteTime) } }

$src = [IO.File]::ReadAllText($Server)
if ($src -notmatch '(?m)^VERSION = "([^"]*)"') { Fail "в server.py нет строки VERSION = ""X.Y.Z""" }
$codeVer = Parse-Ver $Matches[1]
$last = if ($built.Count) { $built[-1].Ver } else { $null }
$base = if ($last -and $last -gt $codeVer) { $last } else { $codeVer }
$msg = "  версия в server.py: $(Ver-Text $codeVer)"
if ($last) { $msg += ", последний архив: $(Ver-Text $last)" }
Write-Host $msg

# ---------- 1а. какой архив ----------
if (-not $Kind) {
    Write-Host ""
    Write-Host "Какой архив собрать?"
    Write-Host "  1 — полный: для первой установки, всё без интернета (~0,6 ГБ + части NVIDIA ~3 ГБ)"
    Write-Host "  2 — обновление: только программа (~1 МБ) — поверх уже установленного сервера"
    Write-Host "  3 — оба (для выпуска на GitHub)"
    $a = Read-Host "Выбор [3]"
    $Kind = switch ($a) { "1" { "full" } "2" { "update" } "" { "both" } "3" { "both" } default { "" } }
    if (-not $Kind) { Write-Host "Отменено."; exit 0 }
}
$NoPackages = $Kind -eq "update"
$wantFull = $Kind -in @("full", "both"); $wantUpd = $Kind -in @("update", "both")

# ---------- 2. какое это обновление ----------
if ($Version) { $Bump = "manual" }
if (-not $Bump) {
    Write-Host ""
    Write-Host "Какое это обновление?"
    Write-Host "  1 — минорное (исправления):      $(Ver-Text (Bump-Ver $base 'patch'))"
    Write-Host "  2 — среднее (новые возможности): $(Ver-Text (Bump-Ver $base 'minor'))"
    Write-Host "  3 — большое (крупные изменения): $(Ver-Text (Bump-Ver $base 'major'))"
    Write-Host "  4 — пересобрать $(Ver-Text $base) (архив заменится)"
    Write-Host "  5 — указать версию вручную (X.Y.Z)"
    Write-Host "  0 — отмена"
    $default = if ($last) { "1" } else { "4" }
    $ans = Read-Host "Выбор [$default]"
    if (-not $ans) { $ans = $default }
    $Bump = switch ($ans) { "1" { "patch" } "2" { "minor" } "3" { "major" } "4" { "same" } "5" { "manual" } default { "" } }
    if (-not $Bump) { Write-Host "Отменено."; exit 0 }
}
if ($Bump -eq "manual") {
    # своя версия: например, прошлые сборки не сохранились, а выпустить нужно следующую по счёту
    while ($Version -notmatch '^\s*\d+\.\d+\.\d+\s*$') {
        if ($Version) { Write-Host "  нужен формат X.Y.Z, например 1.0.1" -ForegroundColor Yellow }
        $Version = Read-Host "Версия (X.Y.Z, пусто — отмена)"
        if (-not $Version) { Write-Host "Отменено."; exit 0 }
    }
    $ver = Parse-Ver $Version.Trim()
    if ($last -and $ver -lt $last) {
        Write-Host "  $(Ver-Text $ver) меньше последней собранной $(Ver-Text $last)." -ForegroundColor Yellow
        Write-Host "  Номер в server.py станет меньше — у тех, у кого стоит новее, это будет выглядеть как откат." -ForegroundColor Yellow
        $ans = Read-Host "Всё равно собрать $(Ver-Text $ver)? (д/Н)"
        if ($ans -notmatch '^(д|y)') { Write-Host "Отменено."; exit 0 }
    }
} else { $ver = Bump-Ver $base $Bump }
$verText = Ver-Text $ver
$zipFull = Join-Path $Dist "BookReader_Server_v${verText}_full.zip"
$zipUpd  = Join-Path $Dist "BookReader_Server_v${verText}_update.zip"
$zip = if ($wantFull) { $zipFull } else { $zipUpd }
$exist = @(@($(if ($wantFull) { $zipFull }), $(if ($wantUpd) { $zipUpd })) | Where-Object { $_ -and (Test-Path $_) })
# полный и обновление одной версии собирают по очереди: второй — «пересобрать» (та же версия)
if ($exist.Count -and $Bump -notin @("same", "manual")) { Fail "архив $(Split-Path $exist[0] -Leaf) уже есть — выберите другое обновление (или «пересобрать»)" }
if ($exist.Count) {
    $ans = Read-Host "$((($exist | ForEach-Object { Split-Path $_ -Leaf }) -join ', ')) уже есть — заменить? (д/Н)"
    if ($ans -notmatch '^(д|y)') { Write-Host "Отменено."; exit 0 }
}
if (-not $Notes -and $Bump -ne "same") { $Notes = Read-Host "Что нового (одной строкой, можно пропустить)" }

# ---------- 3. версия → server.py ----------
$newSrc = [regex]::Replace($src, '(?m)^VERSION = "[^"]*"', "VERSION = `"$verText`"")
if ($newSrc -ne $src) {
    [IO.File]::WriteAllText($Server, $newSrc, [Text.UTF8Encoding]::new($false))
    Write-Host "В server.py записана версия $verText"
}

# ---------- 4. самопроверка ----------
if (-not $SkipTests) {
    Step "Самопроверка сервера (tests/test_server.py)"
    $py = Join-Path $Root "python\python.exe"
    if (-not (Test-Path $py)) { $c = Get-Command python -ErrorAction SilentlyContinue; $py = if ($c) { $c.Source } else { $null } }
    if (-not $py) { Write-Host "  Python не найден — проверка пропущена (поставьте сервер через install.bat)" -ForegroundColor Yellow }
    else {
        $env:PYTHONIOENCODING = "utf-8"
        $out = Native { & $py (Join-Path $Root "tests\test_server.py") 2>&1 | Lines }
        $rc = $LASTEXITCODE
        $testLog = Join-Path $Dist "test-server-last.log"   # полный вывод тестов (в окне — только итог)
        $out | Out-File -FilePath $testLog -Encoding utf8
        $out | Where-Object { $_ -match "^(Ran |OK|FAILED|FAIL:|ERROR:)" } | ForEach-Object { Write-Host "  $_" }
        $global:LASTEXITCODE = $rc
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  подробности: $testLog" -ForegroundColor Yellow
            $ans = Read-Host "Самопроверка нашла проблемы. Всё равно собрать архив? (д/Н)"
            if ($ans -notmatch '^(д|y)') { Fail "сборка отменена: исправьте ошибки тестов" }
        } else { Write-Host "  пройдена" -ForegroundColor Green }
    }
}

# ---------- 5а. пакеты для установки без интернета ----------
$Pkg = Join-Path $Root "packages"
$cuParts = @()
if (-not $NoPackages) {
    Step "Пакеты для установки без интернета (папка packages)"
    $py = Join-Path $Root "python\python.exe"
    if (-not (Test-Path $py)) { Fail "нет python\python.exe — сначала установите сервер (install.bat): его Python скачивает пакеты. Или соберите без пакетов: build-server.bat -NoPackages" }
    if ($RefreshPackages -and (Test-Path $Pkg)) { Write-Host "  удаляю прежние пакеты"; Remove-Item $Pkg -Recurse -Force }
    foreach ($d in @("", "wheels\common", "wheels\cpu", "wheels\cu128", "silero")) { New-Item -ItemType Directory -Force -Path (Join-Path $Pkg $d) | Out-Null }
    $inst = [IO.File]::ReadAllText((Join-Path $PSScriptRoot "install.ps1"))
    if ($inst -notmatch '\$PyVersion = "([^"]+)"') { Fail "в install.ps1 нет строки `$PyVersion" }
    $pyVer = $Matches[1]
    $curl = Join-Path $env:WINDIR "System32\curl.exe"
    function Fetch($url, $dest) {
        if (Test-Path $dest) { Write-Host "  есть: $(Split-Path $dest -Leaf)"; return }
        Write-Host "  скачиваю $(Split-Path $dest -Leaf)…"
        Native { & $curl -L --fail --retry 3 -C - -# -o "$dest.part" $url }
        if ($LASTEXITCODE -ne 0) { Fail "не скачался $url (код $LASTEXITCODE)" }
        Move-Item "$dest.part" $dest -Force
    }
    Fetch "https://www.python.org/ftp/python/$pyVer/python-$pyVer-embed-amd64.zip" (Join-Path $Pkg "python-$pyVer-embed-amd64.zip")
    Fetch "https://bootstrap.pypa.io/get-pip.py" (Join-Path $Pkg "get-pip.py")
    Fetch "https://aka.ms/vs/17/release/vc_redist.x64.exe" (Join-Path $Pkg "vc_redist.x64.exe")
    # старые версии Python в папке (поменяли $PyVersion) — убираем
    Get-ChildItem $Pkg -Filter "python-*-embed-amd64.zip" | Where-Object { $_.Name -ne "python-$pyVer-embed-amd64.zip" } | Remove-Item -Force

    $common = Join-Path $Pkg "wheels\common"; $cpuDir = Join-Path $Pkg "wheels\cpu"; $cuDir = Join-Path $Pkg "wheels\cu128"
    $pipq = @("--disable-pip-version-check", "--progress-bar", "on")
    # Версия torch — одна для процессора и видеокарты: самая новая, что есть в сборках для CUDA 12.8
    # (на PyPI и в сборках для процессора бывают версии новее — для видеокарты их ещё нет).
    $verOf = { param($name) ($name -split '-')[1] -replace '\+.*$', '' }
    $cuInfo = $null
    $manPath = Join-Path $Pkg "manifest.json"
    if (Test-Path $manPath) { try { $cuInfo = (Get-Content $manPath -Raw -Encoding UTF8 | ConvertFrom-Json).torch.cu128 } catch {} }
    $haveParts = $cuInfo -and $cuInfo.version -and @(Get-ChildItem $cuDir -Filter "torch-*.whl.part*" -ErrorAction SilentlyContinue).Count -eq [int]$cuInfo.parts
    $tmpCu = Join-Path $env:TEMP "bookreader-cu128"
    $cuWhl = $null
    if ($haveParts) { $torchVer = "$($cuInfo.version)" }
    else {
        Get-ChildItem $cuDir -File -ErrorAction SilentlyContinue | Remove-Item -Force
        Write-Host "  torch для видеокарты (cu128, ~3 ГБ)…"
        New-Item -ItemType Directory -Force -Path $tmpCu | Out-Null
        Get-ChildItem $tmpCu -Filter "*.part" -ErrorAction SilentlyContinue | Remove-Item -Force
        Native { & $py -m pip download @pipq torch --no-deps --index-url "https://download.pytorch.org/whl/cu128" -d $tmpCu }
        if ($LASTEXITCODE -ne 0) { Fail "pip не скачал torch для видеокарты (код $LASTEXITCODE)" }
        $cuWhl = Get-ChildItem $tmpCu -Filter "torch-*cu128*.whl" | Sort-Object { [version](& $verOf $_.Name) } -Descending | Select-Object -First 1
        if (-not $cuWhl) { Fail "torch для видеокарты не скачался" }
        $torchVer = & $verOf $cuWhl.Name
    }
    Write-Host "  версия torch: $torchVer"
    # библиотеки и torch той же версии для процессора (pip wheel: пакеты без готовых файлов для Windows собираются в .whl)
    $cpuWhl = Get-ChildItem $cpuDir -Filter "torch-*.whl" -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $cpuWhl -or (& $verOf $cpuWhl.Name) -ne $torchVer) {
        Get-ChildItem $cpuDir, $common -File -ErrorAction SilentlyContinue | Remove-Item -Force   # другая версия torch — зависимости тоже заново
        Write-Host "  torch $torchVer (процессор) и библиотеки…"
        $reqs = @(Get-Content (Join-Path $Root "requirements.txt") | ForEach-Object { $_.Trim() } | Where-Object { $_ -and -not $_.StartsWith("#") } |
            ForEach-Object { if ($_ -match '^torch\b') { "torch==$torchVer" } else { $_ } })
        $reqs += "pip"   # колесо pip — чтобы get-pip.py ставил pip без интернета (--no-index)
        Native { & $py -m pip wheel @pipq @reqs --wheel-dir $common --index-url "https://download.pytorch.org/whl/cpu" --extra-index-url "https://pypi.org/simple" }
        if ($LASTEXITCODE -ne 0) { Fail "pip не скачал библиотеки (код $LASTEXITCODE)" }
        Get-ChildItem $common -Filter "torch-*.whl" | Move-Item -Destination $cpuDir -Force
    } else { Write-Host "  есть: torch $torchVer (процессор) и библиотеки" }
    if (-not (Get-ChildItem $common -Filter "pip-*.whl" -ErrorAction SilentlyContinue)) {
        Native { & $py -m pip download @pipq pip --no-deps --only-binary=:all: -d $common }
        if ($LASTEXITCODE -ne 0) { Fail "pip не скачал колесо pip (код $LASTEXITCODE)" }
    }
    $cpuWhl = Get-ChildItem $cpuDir -Filter "torch-*.whl" | Select-Object -First 1
    if (-not $cpuWhl) { Fail "torch для процессора не скачался" }
    # torch для видеокарты — частями по 1,9 ГБ (на GitHub файл выпуска — до 2 ГБ)
    if (-not $haveParts) {
        Write-Host "  делю $($cuWhl.Name) на части…"
        # размеры — [long]: в Windows PowerShell 5.1 [math]::Min(int, long) не работает (файл больше 2 ГБ)
        [long]$partSize = 1900MB; $buf = New-Object byte[] (16MB)
        $in = [IO.File]::OpenRead($cuWhl.FullName); $k = 0
        try {
            while ($in.Position -lt $in.Length) {
                $k++
                $out = [IO.File]::Create((Join-Path $cuDir "$($cuWhl.Name).part$k"))
                try {
                    [long]$left = [math]::Min([long]$partSize, [long]($in.Length - $in.Position))
                    while ($left -gt 0) {
                        [int]$want = [math]::Min([long]$buf.Length, $left)
                        $n = $in.Read($buf, 0, $want); if ($n -le 0) { break }
                        $out.Write($buf, 0, $n); $left -= $n
                    }
                } finally { $out.Dispose() }
            }
        } finally { $in.Dispose() }
        $cuInfo = [pscustomobject]@{ file = $cuWhl.Name; version = $torchVer; parts = $k; sha256 = (Get-FileHash $cuWhl.FullName -Algorithm SHA256).Hash.ToLower() }
        Remove-Item $tmpCu -Recurse -Force
    } else { Write-Host "  есть: torch $torchVer (видеокарта, $($cuInfo.parts) части)" }
    # модель голосов — из torch hub этого ПК (её скачал сервер или установщик)
    $hubSrc = Join-Path $env:USERPROFILE ".cache\torch\hub\snakers4_silero-models_master"
    if ($env:TORCH_HOME) { $hubSrc = Join-Path $env:TORCH_HOME "hub\snakers4_silero-models_master" }
    $hubDst = Join-Path $Pkg "silero\snakers4_silero-models_master"
    if (-not (Get-ChildItem $hubDst -Recurse -Filter "*.pt" -ErrorAction SilentlyContinue | Select-Object -First 1)) {
        if (-not (Get-ChildItem $hubSrc -Recurse -Filter "*.pt" -ErrorAction SilentlyContinue | Select-Object -First 1)) { Fail "модели Silero нет в $hubSrc — запустите сервер один раз (он скачает модель) и соберите снова" }
        Write-Host "  модель Silero…"
        if (Test-Path $hubDst) { Remove-Item $hubDst -Recurse -Force }
        Copy-Item $hubSrc $hubDst -Recurse -Force
        Get-ChildItem $hubDst -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
        Get-ChildItem $hubDst -Recurse -Directory -Filter ".git" -Force | Remove-Item -Recurse -Force
    } else { Write-Host "  есть: модель Silero" }
    # словарь английского произношения
    $cmuDst = Join-Path $Pkg "cmudict.dict"
    if (-not (Test-Path $cmuDst)) {
        $cmuSrc = @((Join-Path $Root "data\cmudict.dict"), (Join-Path $env:LOCALAPPDATA "BookReader\data\cmudict.dict")) | Where-Object { Test-Path $_ } | Select-Object -First 1
        if ($cmuSrc) { Copy-Item $cmuSrc $cmuDst } else { Fetch "https://raw.githubusercontent.com/cmusphinx/cmudict/master/cmudict.dict" $cmuDst }
    }
    # в каком выпуске GitHub лежат части NVIDIA: тот же torch — прежний выпуск, новый torch — этот
    $nvRel = $null
    if ($haveParts) {
        if ($NvidiaRelease) { $nvRel = "v" + $NvidiaRelease.Trim().TrimStart("v") }
        elseif ($cuInfo.release) { $nvRel = "$($cuInfo.release)" }
        else {
            # пакеты собраны прежней версией скрипта: части выкладывались с выпуском, чьи архивы _NVIDIA_ лежат в dist
            $old = Get-ChildItem $Dist -Filter "BookReader_Server_v*_NVIDIA_1_of_*.zip" -ErrorAction SilentlyContinue |
                Sort-Object LastWriteTime | Select-Object -First 1
            if ($old -and $old.Name -match '_v(\d+\.\d+\.\d+)_NVIDIA_') { $nvRel = "v" + $Matches[1] }
        }
    }
    if (-not $nvRel) { $nvRel = "v$verText" }
    $cuInfo | Add-Member -NotePropertyName release -NotePropertyValue $nvRel -Force
    $cpuInfo = [pscustomobject]@{ file = $cpuWhl.Name; version = $torchVer; sha256 = (Get-FileHash $cpuWhl.FullName -Algorithm SHA256).Hash.ToLower() }
    [pscustomobject]@{ built = (Get-Date -Format "yyyy-MM-dd"); python = $pyVer; torch = [pscustomobject]@{ version = $torchVer; cpu = $cpuInfo; cu128 = $cuInfo } } |
        ConvertTo-Json -Depth 5 | Out-File -FilePath $manPath -Encoding utf8
    $cuParts = @(Get-ChildItem $cuDir -Filter "torch-*.whl.part*" | Sort-Object { [int]($_.Name -replace '^.*\.part(\d+)$', '$1') })
    $pkgMb = [math]::Round(((Get-ChildItem $Pkg -Recurse -File | Where-Object { $_.FullName -notlike "$cuDir*" } | Measure-Object Length -Sum).Sum) / 1MB)
    Write-Host ("  пакеты готовы: для процессора ~{0} МБ, для видеокарты — {1} части" -f $pkgMb, $cuParts.Count) -ForegroundColor Green
    if ($nvRel -ne "v$verText") {
        Write-Host "  torch не менялся: части NVIDIA уже лежат в выпуске $nvRel — заново их не собираю и выкладывать не нужно" -ForegroundColor Green
        $cuParts = @()
    }
}

# ---------- 5. состав архива (белый список) ----------
Step "Сборка архива"
$include = @(   # пути через «/» — работает и в Windows
    "server.py", "paths.py", "assemble.py", "audiocat.py", "en_translit.py", "ru_numbers.py", "ru_nouns.py", "ru_names.py", "ru_yo.py", "ru_rules.py", "requirements.txt",
    "BookReader.bat", "install.bat", "uninstall.bat", "update.bat", "start-reader.bat", "start-reader-gpu.bat",
    "README.md", "LICENSE", "LICENSE.ru.txt", "THIRD_PARTY_NOTICES.md",
    "data/en_ru.json",
    "data/third_party/nemo_ru/abbreviations.tsv", "data/third_party/nemo_ru/measurements.tsv",
    "data/third_party/nemo_ru/currency_singular.tsv", "data/third_party/nemo_ru/currency_plural.tsv",
    "data/third_party/nemo_ru/months_abbr.tsv", "data/third_party/nemo_ru/LICENSE", "data/third_party/nemo_ru/README.md",
    "installer/install.ps1", "installer/uninstall.ps1", "installer/update.ps1", "installer/launcher.cs",
    "tests/test_server.py", "tests/normalizer_cases.py", "tests/normalizer_cases.tsv", "tests/accent_check.py", "tests/yo_phrases.txt", "tests/yo_phrases_2.txt", "tests/rules_check.py", "tests/report_path.py",
    "catalog/ПРОЧТИ.txt"
)
$files = New-Object System.Collections.Generic.List[string]
foreach ($rel in $include) {
    if (Test-Path (Join-Path $Root $rel)) { $files.Add($rel) }
    elseif ($rel -notlike "start-reader*") { Fail "нет файла $rel — архив был бы неполным" }
}
# веб-интерфейс целиком, кроме дублей pdf.js (*.mjs — читалка грузит *.min.js)
Get-ChildItem (Join-Path $Root "web") -Recurse -File | Where-Object { $_.Extension -ne ".mjs" } | ForEach-Object {
    $files.Add($_.FullName.Substring($Root.Length).TrimStart('\', '/'))
}
# на всякий случай: ничего секретного и личного
$bad = $files | Where-Object { $_ -match '\.(keystore|jks|pfx|snk|env|log)$|(^|[\\/])(python|dist|__pycache__)[\\/]' }
if ($bad) { Fail "в архив попали бы лишние файлы: $($bad -join ', ')" }

# личные данные в текстах: путь к профилю этого ПК, имя пользователя Windows в путях, C:\Users\<имя>
$me = [regex]::Escape($env:USERNAME)
$pat = @('(?i)[a-z]:\\Users\\[^\\\s<>%$"''`]+', "(?i)[\\/]$me[\\/]", [regex]::Escape($env:USERPROFILE))
$hits = foreach ($rel in $files) {
    if ($rel -match '\.(ico|png|jpg|min\.js)$') { continue }
    $n = 0
    foreach ($line in [IO.File]::ReadAllLines((Join-Path $Root $rel))) {
        $n++
        foreach ($p in $pat) { if ($line -match $p) { "  ${rel}:${n}: $($line.Trim())"; break } }
    }
}
if ($hits) {
    Write-Host "Похоже на личные пути в файлах архива:" -ForegroundColor Yellow
    $hits | Select-Object -First 20 | ForEach-Object { Write-Host $_ -ForegroundColor Yellow }
    $ans = Read-Host "Всё равно собрать архив? (д/Н)"
    if ($ans -notmatch '^(д|y)') { Fail "сборка отменена: уберите личные пути" }
} else { Write-Host "  личных путей в файлах не найдено" -ForegroundColor Green }

# список файлов программы с контрольными суммами — по нему update.bat проверяет архив, заменяет файлы и убирает лишние
$fileMap = [ordered]@{}
foreach ($rel in $files) { $fileMap[($rel -replace '\\', '/')] = (Get-FileHash (Join-Path $Root $rel) -Algorithm SHA256).Hash.ToLower() }
$manRel = "bookreader-files.json"
[IO.File]::WriteAllText((Join-Path $Root $manRel),
    ([pscustomobject]@{ app = "BookReader"; version = $verText; built = (Get-Date -Format "yyyy-MM-dd"); files = [pscustomobject]$fileMap } | ConvertTo-Json -Depth 4),
    [Text.UTF8Encoding]::new($false))
$files.Add($manRel)

Add-Type -AssemblyName System.IO.Compression, System.IO.Compression.FileSystem
# архив: files — пути в папке сервера; сжимаются только тексты (колёса, zip, модель уже сжаты)
function Write-Zip($path, $list) {
    $tmp = "$path.part"
    if (Test-Path $tmp) { Remove-Item $tmp -Force }
    $fs = [IO.File]::Open($tmp, [IO.FileMode]::CreateNew)
    try {
        # имена внутри архива — UTF-8 (каталог\ПРОЧТИ.txt), папка верхнего уровня — BookReader
        $za = New-Object System.IO.Compression.ZipArchive($fs, [IO.Compression.ZipArchiveMode]::Create, $false, [Text.Encoding]::UTF8)
        try {
            foreach ($rel in $list) {
                $entry = "BookReader/" + ($rel -replace '\\', '/')
                $level = if ($rel -match '\.(whl|zip|exe|pt|part\d+)$') { [IO.Compression.CompressionLevel]::NoCompression } else { [IO.Compression.CompressionLevel]::Optimal }
                [void][IO.Compression.ZipFileExtensions]::CreateEntryFromFile($za, (Join-Path $Root $rel), $entry, $level)
            }
        } finally { $za.Dispose() }
    } finally { $fs.Dispose() }
    Move-Item $tmp $path -Force
    $h = (Get-FileHash $path -Algorithm SHA256).Hash.ToLower()
    "$h  $(Split-Path $path -Leaf)" | Out-File -FilePath "$path.sha256" -Encoding ascii
}
$relOf = { param($full) $full.Substring($Root.Length).TrimStart('\', '/') }
$mainList = New-Object System.Collections.Generic.List[string]
$files | ForEach-Object { $mainList.Add($_) }
if (-not $NoPackages) {
    Get-ChildItem $Pkg -Recurse -File | Where-Object { $_.FullName -notlike (Join-Path $Pkg "wheels\cu128\*") -and $_.Extension -ne ".part" } |
        ForEach-Object { $mainList.Add((& $relOf $_.FullName)) }
}
$made = @()
if ($wantFull) {
    # прежние части NVIDIA этой версии (пересборка) — убираем
    Get-ChildItem $Dist -Filter "BookReader_Server_v${verText}_NVIDIA_*" -ErrorAction SilentlyContinue | Remove-Item -Force
    Write-Host "  полный архив…"
    Write-Zip $zipFull $mainList
    $made += $zipFull
}
if ($wantUpd) {
    Write-Host "  архив-обновление…"
    Write-Zip $zipUpd $files
    $made += $zipUpd
}
$nvZips = @()
for ($k = 0; $wantFull -and $k -lt $cuParts.Count; $k++) {
    # версия torch в имени — по ней установщик находит на GitHub самый новый выпуск с частями нужного torch
    $nz = Join-Path $Dist ("BookReader_Server_v{0}_NVIDIA_torch{1}_{2}_of_{3}.zip" -f $verText, $torchVer, ($k + 1), $cuParts.Count)
    Write-Host "  часть для видеокарты $($k + 1) из $($cuParts.Count)…"
    Write-Zip $nz @(& $relOf $cuParts[$k].FullName)
    $nvZips += $nz
}
if ($Bump -ne "same" -or $Notes) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd')  $verText" + $(if ($Notes) { "  — $Notes" } else { "" })
    Add-Content -Path (Join-Path $Dist "CHANGELOG.txt") -Value $line -Encoding UTF8
}

Write-Host ""
Write-Host "Готово:" -ForegroundColor Green
foreach ($m in $made) { Write-Host "       $m ($([math]::Round((Get-Item $m).Length / 1MB, 1)) МБ)" -ForegroundColor Green }
foreach ($nz in $nvZips) { Write-Host "       $nz ($([math]::Round((Get-Item $nz).Length / 1MB)) МБ)" -ForegroundColor Green }
Write-Host "Контрольные суммы — рядом, в файлах .sha256"
if ($wantFull) { Write-Host "Полный — на новом ПК: распаковать в папку с правом записи (не в Program Files) и запустить install.bat." }
if ($wantUpd) { Write-Host "Обновление — распаковать поверх установленного сервера (с заменой файлов); install.bat запускать не нужно." }
if ($nvZips.Count) { Write-Host "Для видеокарты NVIDIA: архивы _NVIDIA_ распаковать в ту же папку, что и сервер (поверх), до запуска install.bat." }
if ($wantFull -and -not $NoPackages) {
    if ($nvZips.Count) { Write-Host "В выпуск GitHub: APK, _full, _update и части _NVIDIA_ (с файлами .sha256) — torch новый; старые выпуски с частями можно не трогать, установщик возьмёт самый новый." -ForegroundColor Cyan }
    else { Write-Host "В выпуск GitHub: APK, _full и _update (с файлами .sha256). Части NVIDIA не нужны — torch тот же, установщик возьмёт их из выпуска $nvRel (не удаляйте их оттуда)." -ForegroundColor Cyan }
}
if ($Log) { Write-Host "Журнал сборки: $Log"; try { Stop-Transcript | Out-Null } catch {} }
try { explorer.exe "/select,$zip" } catch {}
