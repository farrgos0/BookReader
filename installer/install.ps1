# Установщик сервера BookReader для Windows 10/11 (x64).
# Запуск: install.bat (он вызывает этот файл). Ставит всё в папку, где лежит BookReader:
#   python\        — собственный Python 3.12 (системный Python не трогается)
#   библиотеки     — torch (для видеокарты NVIDIA или процессора), numpy, omegaconf, imageio-ffmpeg
#   модель Silero  — голоса озвучки
#   BookReader.exe — запуск сервера, ярлыки на рабочем столе и в меню «Пуск»
# Повторный запуск безопасен: готовые шаги пропускаются, недостающее доустанавливается.

# Ключи: -Cpu — считать на процессоре, -Gpu — на видеокарте NVIDIA (CUDA), -Yes — без вопросов (по рекомендации).
param([switch]$Cpu, [switch]$Gpu, [switch]$Yes)

$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Root      = Split-Path -Parent $PSScriptRoot
$PyDir     = Join-Path $Root "python"
$Py        = Join-Path $PyDir "python.exe"
$Tmp       = Join-Path $env:TEMP "bookreader-setup"
# данные пользователя (настройки сервера, журналы) — не в папке программы, а в %LOCALAPPDATA%\BookReader
$DataDir   = if ($env:BOOKREADER_HOME) { $env:BOOKREADER_HOME } else { Join-Path $env:LOCALAPPDATA "BookReader" }
$LogDir    = Join-Path $DataDir "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Log       = Join-Path $LogDir "install.log"
# кэш загрузок (Python, torch): прерванная загрузка продолжается с места обрыва, повторная установка не качает заново
$Cache     = Join-Path $DataDir "cache"
New-Item -ItemType Directory -Force -Path $Cache | Out-Null
$PyVersion = "3.12.10"
$PyZipUrl  = "https://www.python.org/ftp/python/$PyVersion/python-$PyVersion-embed-amd64.zip"
$GetPipUrl = "https://bootstrap.pypa.io/get-pip.py"
$VcUrl     = "https://aka.ms/vs/17/release/vc_redist.x64.exe"
$Steps     = 9
New-Item -ItemType Directory -Force -Path $Tmp | Out-Null
# Пакеты для установки без интернета: папка packages рядом с install.bat (она есть в архивах релиза).
#   python-*.zip, get-pip.py, vc_redist.x64.exe, cmudict.dict, silero\ (модель голосов),
#   wheels\common\ (numpy, scipy, …), wheels\cpu\ (torch для процессора),
#   wheels\cu128\ (torch для видеокарты — частями *.part1, *.part2 из архивов «NVIDIA»), manifest.json
$Pkg       = Join-Path $Root "packages"
$PkgCommon = Join-Path $Pkg "wheels\common"
$PkgMan    = $null
try { if (Test-Path (Join-Path $Pkg "manifest.json")) { $PkgMan = Get-Content (Join-Path $Pkg "manifest.json") -Raw -Encoding UTF8 | ConvertFrom-Json } } catch {}
function Pkg-File([string]$rel) { $p = Join-Path $Pkg $rel; if (Test-Path $p) { $p } else { $null } }
$HaveCommon = (Test-Path $PkgCommon) -and @(Get-ChildItem $PkgCommon -Filter "*.whl" -ErrorAction SilentlyContinue).Count -gt 0
# журнал установки — в двух местах: в папке данных (сохраняется) и %TEMP%\BookReader_install.log (его проще найти и прислать)
$TempLog   = Join-Path $env:TEMP "BookReader_install.log"
"=== установка BookReader $(Get-Date) ===" | Out-File -FilePath $Log -Encoding utf8
"=== установка BookReader $(Get-Date) ===" | Out-File -FilePath $TempLog -Encoding utf8

function Log($text) {
    foreach ($f in @($Log, $TempLog)) { try { $text | Out-File -FilePath $f -Append -Encoding utf8 } catch {} }
}

function Step($n, $title) {
    $pct = [int](($n - 1) * 100 / $Steps)
    Write-Progress -Activity "Установка BookReader" -Status "Шаг $n из ${Steps}: $title" -PercentComplete $pct
    Write-Host ""
    Write-Host ("[{0}/{1}] {2}" -f $n, $Steps, $title) -ForegroundColor Cyan
    Write-Host ("      " + ("#" * [int]($pct / 4)).PadRight(25, ".") + " $pct%") -ForegroundColor DarkCyan
    Log "[$n/$Steps] $title"
}

function Ok($text)   { Write-Host "      √ $text" -ForegroundColor Green; Log "OK: $text" }
function Info($text) { Write-Host "      $text"; Log $text }
function Warn($text) { Write-Host "      ! $text" -ForegroundColor Yellow; Log "WARN: $text" }

function Fail($text) {
    Write-Progress -Activity "Установка BookReader" -Completed
    Write-Host ""
    Write-Host "Установка прервана: $text" -ForegroundColor Red
    Write-Host ""
    Write-Host "Журнал установки: $TempLog"
    Write-Host "Исправьте причину и запустите install.bat ещё раз: уже сделанное повторно не скачивается."
    Log "FAIL: $text"
    Read-Host "Нажмите Enter, чтобы закрыть"
    exit 1
}

# Любая непредвиденная ошибка — понятное сообщение, запись в журнал и пауза (окно не закрывается само).
trap { Fail ("непредвиденная ошибка: " + $_.Exception.Message + " (install.ps1, строка " + $_.InvocationInfo.ScriptLineNumber + ")") }

# Windows PowerShell 5.1: текст внешней программы в потоке ошибок при $ErrorActionPreference = "Stop" превращается
# в аварийную ошибку — даже с 2>$null (например, «No module named pip» у свежего Python). Внешние программы
# запускаем без этого режима, а успех проверяем по коду выхода ($LASTEXITCODE).
function Native([scriptblock]$sb) {
    $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { & $sb } finally { $ErrorActionPreference = $old }
}

# Скачать файл. Уже скачанный целиком — не качается заново; прерванная загрузка (файл .part) продолжается с места обрыва.
# -Soft: при неудаче не прерывать установку, а вернуть $false (есть запасной путь)
function Download($url, $dest, $what, [string]$sha256 = "", [switch]$Soft) {
    if (Test-Path $dest) {
        if (-not $sha256 -or (Get-FileHash $dest -Algorithm SHA256).Hash -eq $sha256.ToUpper()) { Info "$what — уже скачан"; if ($Soft) { return $true }; return }
        Remove-Item $dest -Force
    }
    $part = "$dest.part"
    if (Test-Path $part) { Info ("Докачиваю {0} (уже есть {1:N0} МБ)…" -f $what, ((Get-Item $part).Length / 1MB)) } else { Info "Скачиваю $what…" }
    $curl = Join-Path $env:WINDIR "System32\curl.exe"
    $ok = $false
    if (Test-Path $curl) {
        for ($try = 1; $try -le 3 -and -not $ok; $try++) {
            Native { & $curl -L --fail --retry 3 --retry-delay 3 -C - -# -o $part $url }
            if ($LASTEXITCODE -eq 0 -and (Test-Path $part)) { $ok = $true }
            elseif ($LASTEXITCODE -eq 33 -or $LASTEXITCODE -eq 36) { Remove-Item $part -Force -ErrorAction SilentlyContinue }  # сервер не умеет докачку — с нуля
            elseif ($try -lt 3) { Warn "загрузка прервалась (код $LASTEXITCODE) — продолжаю с места обрыва"; Start-Sleep 3 }
        }
    }
    if (-not $ok) {
        try {
            $ProgressPreference = "SilentlyContinue"   # иначе Invoke-WebRequest в Windows PowerShell качает в разы медленнее
            Invoke-WebRequest -Uri $url -OutFile $part -UseBasicParsing
            $ok = $true
        } catch {
            if ($Soft) { Warn "не удалось скачать $what ($($_.Exception.Message))"; $ProgressPreference = "Continue"; return $false }
            Fail ("не удалось скачать $what ($($_.Exception.Message)). Проверьте интернет и запустите установку ещё раз — скачанное не пропадёт.`n`n" +
                  "Если сайт недоступен, скачайте файл вручную (через браузер, другой компьютер):`n  $url`n" +
                  "и положите его в папку`n  $(Split-Path $dest -Parent)`nпод именем`n  $(Split-Path $dest -Leaf)`nзатем снова запустите install.bat.")
        } finally { $ProgressPreference = "Continue" }
    }
    if ($sha256 -and (Get-FileHash $part -Algorithm SHA256).Hash -ne $sha256.ToUpper()) {
        Remove-Item $part -Force
        Fail "файл $what скачался с ошибкой (не совпала контрольная сумма). Запустите установку ещё раз."
    }
    Move-Item $part $dest -Force
    if ($Soft) { return $true }
}

# Окно «Продолжить / Отмена» (если окно не открывается — например, мало памяти — спрашиваем в консоли)
function Ask-Continue([string]$title, [string]$text, [switch]$Warning) {
    try {
        Add-Type -AssemblyName System.Windows.Forms, System.Drawing
        $f = New-Object Windows.Forms.Form
        $f.Text = $title; $f.StartPosition = "CenterScreen"; $f.FormBorderStyle = "FixedDialog"; $f.MaximizeBox = $false; $f.MinimizeBox = $false
        $f.Font = New-Object Drawing.Font("Segoe UI", 10); $f.TopMost = $true
        # высота окна — по тексту (список загрузок бывает длинным)
        $l = New-Object Windows.Forms.Label; $l.Text = $text; $l.Location = New-Object Drawing.Point(16, 14)
        $l.MaximumSize = New-Object Drawing.Size(512, 0); $l.AutoSize = $true
        $f.Controls.Add($l)
        $h = [math]::Max(60, $l.PreferredHeight)
        $y = 14 + $h + 18
        $ok = New-Object Windows.Forms.Button; $ok.Text = "Продолжить"; $ok.SetBounds(306, $y, 116, 34); $ok.DialogResult = "OK"
        $no = New-Object Windows.Forms.Button; $no.Text = "Отмена"; $no.SetBounds(430, $y, 100, 34); $no.DialogResult = "Cancel"
        $f.ClientSize = New-Object Drawing.Size(546, ($y + 34 + 14))
        $f.Controls.AddRange(@($ok, $no)); $f.AcceptButton = $(if ($Warning) { $no } else { $ok }); $f.CancelButton = $no
        $f.ActiveControl = $f.AcceptButton
        return $f.ShowDialog() -eq [Windows.Forms.DialogResult]::OK
    } catch {
        Write-Host ""; Write-Host $text -ForegroundColor Yellow
        $a = Read-Host "Продолжить? (д/Н)"
        return $a -match "^(д|y)"
    }
}

function Pip([string[]]$pipArgs, $what, [string]$help = "") {
    Info "Устанавливаю $what…"
    Native { & $Py -m pip install --disable-pip-version-check --no-warn-script-location --progress-bar on @pipArgs }
    if ($LASTEXITCODE -ne 0) {
        if (-not $help) { $help = "Если сайт пакетов Python (pypi.org) недоступен: скачайте нужные файлы для Windows (cp312, win_amd64) со страницы https://pypi.org/project/<имя пакета>/#files, положите их в папку`n  $Cache`nи выполните:`n  `"$Py`" -m pip install --no-index --find-links `"$Cache`" <имена пакетов>" }
        Fail "pip не смог установить $what (код $LASTEXITCODE). Частая причина — обрыв интернета или недоступен сайт пакетов; запустите установку ещё раз.`n`n$help"
    }
}

# torch из пакетов: wheels\<вариант>\torch-*.whl, или частями torch-*.whl.part1…N (собираются в один файл в кэше).
# Возвращает путь к .whl, $null — пакета нет; "missing" — части есть, но не все (не скачан какой-то архив NVIDIA).
function Local-Torch([string]$variant, [switch]$Check) {
    $dir = Join-Path $Pkg "wheels\$variant"
    if (-not (Test-Path $dir)) { return $null }
    $whl = Get-ChildItem $dir -Filter "torch-*.whl" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($whl) { return $whl.FullName }
    $info = if ($PkgMan -and $PkgMan.torch) { $PkgMan.torch.$variant } else { $null }
    $parts = @(Get-ChildItem $dir -Filter "torch-*.whl.part*" -ErrorAction SilentlyContinue |
        Sort-Object { [int]($_.Name -replace '^.*\.part(\d+)$', '$1') })
    if (-not $parts.Count) { return $null }
    $need = if ($info -and $info.parts) { [int]$info.parts } else { $parts.Count }
    if ($parts.Count -lt $need) { return "missing" }
    $name = $parts[0].Name -replace '\.part\d+$', ''
    if ($Check) { return $name }   # только проверить, что все части на месте (собирать — когда дойдёт до установки)
    $dest = Join-Path $Cache $name
    if ((Test-Path $dest) -and (-not $info -or -not $info.sha256 -or (Get-FileHash $dest -Algorithm SHA256).Hash -eq "$($info.sha256)".ToUpper())) { return $dest }
    Info "Собираю $name из $($parts.Count) частей…"
    $tmpw = "$dest.part"
    $out = [IO.File]::Create($tmpw)
    try { foreach ($pt in $parts) { $in = [IO.File]::OpenRead($pt.FullName); try { $in.CopyTo($out) } finally { $in.Dispose() } } }
    finally { $out.Dispose() }
    if ($info -and $info.sha256 -and (Get-FileHash $tmpw -Algorithm SHA256).Hash -ne "$($info.sha256)".ToUpper()) {
        Remove-Item $tmpw -Force
        Warn "части torch повреждены (не совпала контрольная сумма) — скачайте архивы NVIDIA заново"
        return "missing"
    }
    Move-Item $tmpw $dest -Force
    return $dest
}
# torch для видеокарты частями из выпуска на GitHub (архивы «…_NVIDIA_k_of_N.zip» той же версии):
# человеку достаточно скачать один полный архив — части установщик докачает сам (с докачкой после обрыва).
# Не получилось (нет интернета, репозиторий закрыт) — $false: тогда torch скачается с сайта PyTorch.
$ReleaseRepo = "farrgos0/BookReader"
function Get-NvidiaParts {
    $info = if ($PkgMan -and $PkgMan.torch) { $PkgMan.torch.cu128 } else { $null }
    if (-not $info -or -not $info.parts -or -not $info.file) { return $false }
    # части лежат в выпуске, где их выложили (torch.cu128.release: «v1.1.0»), — новые выпуски с тем же torch их не повторяют;
    # в пакетах старых сборок этой строки нет — тогда в выпуске этой же версии
    $ver = $null
    if ($info.release) { $ver = "$($info.release)".TrimStart("v") }
    if (-not $ver) { try { $ver = (Get-Content (Join-Path $Root "bookreader-files.json") -Raw -Encoding UTF8 | ConvertFrom-Json).version } catch {} }
    if (-not $ver -and ([IO.File]::ReadAllText((Join-Path $Root "server.py")) -match '(?m)^VERSION = "([^"]+)"')) { $ver = $Matches[1] }
    if (-not $ver) { return $false }
    $dir = Join-Path $Pkg "wheels\cu128"
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $n = [int]$info.parts
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    for ($k = 1; $k -le $n; $k++) {
        $partFile = Join-Path $dir "$($info.file).part$k"
        if (Test-Path $partFile) { continue }
        $name = "BookReader_Server_v${ver}_NVIDIA_${k}_of_${n}.zip"
        $zipPath = Join-Path $Cache $name
        $ok = Download "https://github.com/$ReleaseRepo/releases/download/v$ver/$name" $zipPath "PyTorch для видеокарты с GitHub, часть $k из $n" -Soft
        if (-not $ok) { return $false }
        try {
            $z = [IO.Compression.ZipFile]::OpenRead($zipPath)
            try {
                $e = $z.Entries | Where-Object { $_.FullName -like "*/wheels/cu128/*.part$k" } | Select-Object -First 1
                if (-not $e) { throw "в архиве нет части $k" }
                [IO.Compression.ZipFileExtensions]::ExtractToFile($e, $partFile, $true)
            } finally { $z.Dispose() }
            Remove-Item $zipPath -Force
        } catch { Warn "архив $name повреждён ($($_.Exception.Message))"; Remove-Item $zipPath, $partFile -Force -ErrorAction SilentlyContinue; return $false }
    }
    return $true
}

# pip из пакетов (без интернета); не вышло (каких-то файлов нет) — обычная установка из интернета
function Pip-Local([string[]]$pipArgs, $what, [string[]]$links, [string]$help = "") {
    $links = @($links | Where-Object { $_ -and (Test-Path $_) })
    if ($links.Count) {
        Info "Устанавливаю $what из пакетов установщика (без интернета)…"
        $la = @("--no-index"); foreach ($l in $links) { $la += @("--find-links", $l) }
        Native { & $Py -m pip install --disable-pip-version-check --no-warn-script-location --progress-bar on @la @pipArgs }
        if ($LASTEXITCODE -eq 0) { return }
        Warn "в пакетах установщика не нашлось всего нужного для «$what» — докачиваю из интернета"
    }
    Pip $pipArgs $what $help
}

$script:Finished = $false
try {
# ---------- 1. соглашение ----------
Step 1 "Пользовательское соглашение"
if (-not $Yes) {
    Add-Type -AssemblyName System.Windows.Forms, System.Drawing
    $text = ""
    foreach ($f in @("LICENSE.ru.txt", "LICENSE", "THIRD_PARTY_NOTICES.md")) {
        $p = Join-Path $Root $f
        if (Test-Path $p) { $text += (Get-Content $p -Raw -Encoding UTF8) + "`r`n`r`n" + ("-" * 60) + "`r`n`r`n" }
    }
    $summary = "BookReader — свободная программа (лицензия MIT-0): её можно бесплатно использовать, изменять и распространять без указания авторства. Без гарантий.`r`n`r`n" +
               "ВАЖНО: голоса озвучки — модели Silero, лицензия CC BY-NC-SA 4.0: только некоммерческое использование, с указанием авторства Silero.`r`n`r`n"
    $form = New-Object Windows.Forms.Form
    $form.Text = "BookReader — установка сервера озвучки"
    $form.Size = New-Object Drawing.Size(720, 600)
    $form.StartPosition = "CenterScreen"
    $form.Font = New-Object Drawing.Font("Segoe UI", 10)
    $form.FormBorderStyle = "FixedDialog"; $form.MaximizeBox = $false
    $head = New-Object Windows.Forms.Label
    $head.Text = "Пользовательское соглашение"; $head.Font = New-Object Drawing.Font("Segoe UI", 14, [Drawing.FontStyle]::Bold)
    $head.SetBounds(16, 12, 680, 32)
    $box = New-Object Windows.Forms.TextBox
    $box.Multiline = $true; $box.ReadOnly = $true; $box.ScrollBars = "Vertical"; $box.BackColor = [Drawing.Color]::White
    $box.Text = $summary + $text; $box.SetBounds(16, 50, 672, 400)
    $chk = New-Object Windows.Forms.CheckBox
    $chk.Text = "Я прочитал(а) и принимаю условия"; $chk.SetBounds(16, 462, 500, 28)
    $okBtn = New-Object Windows.Forms.Button
    $okBtn.Text = "Установить"; $okBtn.SetBounds(468, 505, 110, 34); $okBtn.Enabled = $false
    $okBtn.DialogResult = [Windows.Forms.DialogResult]::OK
    $noBtn = New-Object Windows.Forms.Button
    $noBtn.Text = "Отмена"; $noBtn.SetBounds(586, 505, 100, 34)
    $noBtn.DialogResult = [Windows.Forms.DialogResult]::Cancel
    $chk.Add_CheckedChanged({ $okBtn.Enabled = $chk.Checked })
    $form.Controls.AddRange(@($head, $box, $chk, $okBtn, $noBtn))
    $form.AcceptButton = $okBtn; $form.CancelButton = $noBtn
    if ($form.ShowDialog() -ne [Windows.Forms.DialogResult]::OK) {
        Write-Host "Соглашение не принято — установка отменена."
        Log "license declined"
        exit 2
    }
}
Ok "условия приняты"

# ---------- 2. проверка системы ----------
Step 2 "Проверка компьютера и выбор: видеокарта или процессор"
if (-not [Environment]::Is64BitOperatingSystem) { Fail "нужна 64-битная Windows 10 или 11" }
try { "test" | Out-File (Join-Path $Root ".write-test") -Encoding ascii; Remove-Item (Join-Path $Root ".write-test") }
catch { Fail "нет прав на запись в папку $Root. Распакуйте BookReader, например, в «Документы» или на диск D:, и запустите install.bat оттуда." }

# --- оперативная память и процессор: меньше 1 ГБ — не установится; меньше 4 ГБ — предупреждаем ---
$ramGb = $null; $cores = $null
try { $ramGb = [math]::Round((Get-CimInstance Win32_ComputerSystem -ErrorAction Stop).TotalPhysicalMemory / 1GB, 1) }
catch { try { Add-Type -AssemblyName Microsoft.VisualBasic; $ramGb = [math]::Round((New-Object Microsoft.VisualBasic.Devices.ComputerInfo).TotalPhysicalMemory / 1GB, 1) } catch {} }
try { $cores = [Environment]::ProcessorCount } catch {}
if ($ramGb -eq $null) { Warn "не удалось определить объём оперативной памяти — продолжаю" }
else {
    Info "оперативная память: $ramGb ГБ, потоков процессора: $cores"
    if ($ramGb -lt 1) {
        Fail "на этом компьютере меньше 1 ГБ оперативной памяти ($ramGb ГБ). Нейросеть озвучки на нём работать не сможет: нужно хотя бы 4 ГБ (см. «Системные требования» в README)."
    }
    if ($ramGb -lt 4 -and -not $Yes) {
        $go = Ask-Continue "BookReader — мало памяти" ("На компьютере $ramGb ГБ оперативной памяти, а BookReader нужно не меньше 4 ГБ (рекомендуется 16 ГБ " +
              "или видеокарта NVIDIA с 8 ГБ).`n`nУстановить можно, но озвучка может идти очень медленно, а при нехватке памяти сервер — закрываться.`n`nПродолжить установку?") -Warning
        if (-not $go) { Write-Host "Установка отменена."; Log "low RAM: cancelled"; exit 2 }
        Log "low RAM: continue"
    }
}

# --- видеокарты: NVIDIA (через nvidia-smi — точная видеопамять и версия CUDA в драйвере) и все остальные ---
$nv = $null   # @{ Name; VramGb; Cuda (напр. 1208); Index (cu128/cu126/cu118 или $null); Cap (вычислительные возможности, 12.0 = Blackwell) }
$smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
if (-not $smi) { $p = Join-Path $env:WINDIR "System32\nvidia-smi.exe"; if (Test-Path $p) { $smi = $p } }

# вызов nvidia-smi: без падения на тексте в stderr (Windows PowerShell 5.1 при EAP=Stop превращает его в ошибку)
function Smi([string[]]$a) {
    $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { return ((& $smi @a 2>&1) | ForEach-Object { "$_" }) -join "`n" } catch { return "" } finally { $ErrorActionPreference = $old }
}
# версия CUDA, которую поддерживает драйвер: «CUDA Version: 12.9» (шапка таблицы), «CUDA UMD Version: 13.4»
# (драйверы 6xx) или «CUDA Version : 12.9» (nvidia-smi -q)
function Cuda-From([string]$text) {
    if ($text -match 'CUDA\s+(?:UMD\s+)?Version\s*:\s*(\d+)\.(\d+)') { return [int]$Matches[1] * 100 + [int]$Matches[2] }
    return 0
}
# запасной путь: по номеру драйвера Windows (минимальные версии драйвера из release notes CUDA)
function Cuda-FromDriver([string]$drv) {
    try { $v = [version]($drv.Trim()) } catch { return 0 }
    foreach ($r in @(@("580.65", 1300), @("576.02", 1209), @("570.65", 1208), @("560.76", 1206), @("551.61", 1204), @("527.41", 1200), @("522.06", 1108))) {
        if ($v -ge [version]$r[0]) { return $r[1] }
    }
    return 0
}

if ($smi) {
    $row = (Smi @("--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits")) -split "`n" | Where-Object { $_ -match "," } | Select-Object -First 1
    if ($row) {
        $parts = $row -split ","
        $vram = 0.0; [void][double]::TryParse($parts[1].Trim(), [Globalization.NumberStyles]::Float, [Globalization.CultureInfo]::InvariantCulture, [ref]$vram)
        $drv = if ($parts.Count -ge 3) { $parts[2].Trim() } else { "" }
        $nv = @{ Name = $parts[0].Trim(); VramGb = [math]::Round($vram / 1024, 1); Cuda = 0; Index = $null; Cap = 0.0; Driver = $drv }
        $head = Smi @()
        Log "nvidia-smi: $row"
        Log (($head -split "`n" | Select-Object -First 4) -join "`n")
        $nv.Cuda = Cuda-From $head                                      # 1) шапка таблицы
        if (-not $nv.Cuda) { $nv.Cuda = Cuda-From (Smi @("-q")) }       # 2) подробный отчёт
        if (-not $nv.Cuda) { $nv.Cuda = Cuda-FromDriver $drv }          # 3) по версии драйвера
        $capText = (Smi @("--query-gpu=compute_cap", "--format=csv,noheader")) -split "`n" | Select-Object -First 1
        $cap = 0.0; if ([double]::TryParse("$capText".Trim(), [Globalization.NumberStyles]::Float, [Globalization.CultureInfo]::InvariantCulture, [ref]$cap)) { $nv.Cap = $cap }
        Log "CUDA в драйвере: $($nv.Cuda), драйвер: $drv, compute capability: $($nv.Cap)"
        # сборка torch: cu128 — CUDA 12.8+, она же обязательна для RTX 50xx (Blackwell, compute capability 12.x);
        # более ранние сборки PyTorch эти видеокарты не знают
        if ($nv.Cuda -ge 1208) { $nv.Index = "cu128" }
        elseif ($nv.Cap -ge 10) { $nv.Index = $null }
        elseif ($nv.Cuda -ge 1206) { $nv.Index = "cu126" }
        elseif ($nv.Cuda -ge 1108) { $nv.Index = "cu118" }
    } else { Log "nvidia-smi найден, но не ответил: $(Smi @('-L'))" }
}
$others = @()
try {
    $others = Get-CimInstance Win32_VideoController -ErrorAction Stop |
        Where-Object { $_.Name -notmatch "NVIDIA|Microsoft Basic|Remote|Virtual|Parsec|Meta" } | ForEach-Object { $_.Name }
} catch {}
$cpuName = "процессор"
try { $cpuName = (Get-CimInstance Win32_Processor | Select-Object -First 1).Name.Trim() } catch {}

# --- рекомендация ---
$canCuda = $nv -and $nv.Index
$recommendCuda = $canCuda -and $nv.VramGb -ge 4
if ($nv) { Info ("видеокарта NVIDIA: {0}, {1} ГБ видеопамяти, CUDA в драйвере: {2}" -f $nv.Name, $nv.VramGb, $(if ($nv.Cuda) { "{0}.{1}" -f [int]($nv.Cuda / 100), ($nv.Cuda % 100) } else { "нет" })) }
foreach ($o in $others) { Info "видеокарта: $o (только расчёт на процессоре)" }
Info "процессор: $cpuName"

$useCuda = $false
if ($Gpu) { $useCuda = $true }
elseif ($Cpu) { $useCuda = $false }
elseif ($Yes) { $useCuda = $recommendCuda }
else {
    Add-Type -AssemblyName System.Windows.Forms, System.Drawing
    $f = New-Object Windows.Forms.Form
    $f.Text = "BookReader — где считать озвучку"; $f.Size = New-Object Drawing.Size(660, 520)
    $f.StartPosition = "CenterScreen"; $f.Font = New-Object Drawing.Font("Segoe UI", 10)
    $f.FormBorderStyle = "FixedDialog"; $f.MaximizeBox = $false
    $t = New-Object Windows.Forms.Label
    $t.Text = "Где нейросеть будет озвучивать текст?"; $t.Font = New-Object Drawing.Font("Segoe UI", 13, [Drawing.FontStyle]::Bold)
    $t.SetBounds(16, 12, 620, 30)

    $gpuText = if (-not $nv) { "NVIDIA не найдена. Видеокарты AMD и Intel нейросеть Silero в Windows использовать не умеет." }
               elseif (-not $nv.Index) { "$($nv.Name): драйвер $($nv.Driver) не поддерживает нужную версию CUDA" + $(if ($nv.Cap -ge 10) { " (для RTX 50xx нужна 12.8+)" } else { "" }) + ". Обновите драйвер NVIDIA и запустите установку снова. Если уверены, что драйвер свежий, — install.bat -Gpu." }
               else { "$($nv.Name), $($nv.VramGb) ГБ видеопамяти." + $(if ($nv.VramGb -lt 4) { " Меньше 4 ГБ — видеопамяти может не хватить, если параллельно работают игры или другие нейросети." } else { "" }) }
    $lt = Local-Torch "cu128" -Check
    $gpuDl = if ($lt -and $lt -ne "missing" -and $nv -and $nv.Index -eq "cu128") { "Всё нужное — в архивах BookReader «NVIDIA», без загрузки из интернета" }
             elseif ($lt -eq "missing") { "Не все архивы «NVIDIA» распакованы в эту папку — недостающее (~3 ГБ) скачается" }
             elseif ($PkgMan -and $PkgMan.torch -and $PkgMan.torch.cu128) { "Загрузка ~3 ГБ: установщик сам скачает PyTorch для видеокарты с GitHub" }
             else { "Загрузка ~3 ГБ" }
    $lt = Local-Torch "cpu" -Check
    $cpuDl = if ($lt -and $lt -ne "missing") { "Всё нужное — в архиве BookReader, без загрузки из интернета" } else { "Загрузка ~250 МБ" }
    $rbGpu = New-Object Windows.Forms.RadioButton
    $rbGpu.Text = "Видеокарта NVIDIA (CUDA)" + $(if ($recommendCuda) { " — рекомендуется" } else { "" })
    $rbGpu.Font = New-Object Drawing.Font("Segoe UI", 10.5, [Drawing.FontStyle]::Bold); $rbGpu.SetBounds(16, 56, 620, 26)
    $rbGpu.Enabled = [bool]$canCuda
    $lGpu = New-Object Windows.Forms.Label
    $lGpu.Text = "Озвучка в десятки раз быстрее: книга целиком для телефона готовится за минуты, фразы при чтении — мгновенно. " +
                 "$gpuDl, на диске ~5 ГБ. Видеопамять во время озвучки ~1 ГБ.`r`n$gpuText"
    $lGpu.SetBounds(36, 84, 600, 92)

    $rbCpu = New-Object Windows.Forms.RadioButton
    $rbCpu.Text = "Процессор (CPU)" + $(if (-not $recommendCuda) { " — рекомендуется" } else { "" })
    $rbCpu.Font = New-Object Drawing.Font("Segoe UI", 10.5, [Drawing.FontStyle]::Bold); $rbCpu.SetBounds(16, 186, 620, 26)
    $lCpu = New-Object Windows.Forms.Label
    $lCpu.Text = "Работает на любом компьютере, в том числе с видеокартой AMD или Intel. Для чтения вслух скорости хватает, " +
                 "но подготовка всей книги для телефона займёт от получаса до нескольких часов. $cpuDl, на диске ~1 ГБ.`r`n$cpuName"
    $lCpu.SetBounds(36, 214, 600, 92)

    $note = New-Object Windows.Forms.Label
    $note.Text = "Выбор можно поменять позже: install.bat -Gpu или install.bat -Cpu. Голоса и качество озвучки в обоих вариантах одинаковые."
    $note.ForeColor = [Drawing.Color]::DimGray; $note.SetBounds(16, 320, 620, 44)

    $ok = New-Object Windows.Forms.Button; $ok.Text = "Продолжить"; $ok.SetBounds(418, 420, 110, 34)
    $ok.DialogResult = [Windows.Forms.DialogResult]::OK
    $no = New-Object Windows.Forms.Button; $no.Text = "Отмена"; $no.SetBounds(536, 420, 100, 34)
    $no.DialogResult = [Windows.Forms.DialogResult]::Cancel
    if ($recommendCuda) { $rbGpu.Checked = $true } else { $rbCpu.Checked = $true }
    $f.Controls.AddRange(@($t, $rbGpu, $lGpu, $rbCpu, $lCpu, $note, $ok, $no))
    $f.AcceptButton = $ok; $f.CancelButton = $no
    if ($f.ShowDialog() -ne [Windows.Forms.DialogResult]::OK) { Write-Host "Установка отменена."; Log "device choice cancelled"; exit 2 }
    $useCuda = $rbGpu.Checked
}
$cudaIndex = if ($useCuda) { if ($canCuda) { $nv.Index } else { "cu128" } } else { $null }
# torch из пакетов установщика: подходит ли сборка из архива (там cu128 — CUDA 12.8+) к этой видеокарте
$torchVariant = if ($cudaIndex) { $cudaIndex } else { "cpu" }
$localTorch = Local-Torch $torchVariant -Check
if ($cudaIndex -and $localTorch -eq "missing") { Warn "в папке packages не все части torch для видеокарты — скачайте и распакуйте сюда же все архивы «NVIDIA» (иначе torch скачается из интернета)"; $localTorch = $null }
elseif ($cudaIndex -and -not $localTorch -and (Test-Path (Join-Path $Pkg "wheels\cu128")) -and $cudaIndex -ne "cu128") { Warn "torch в пакетах — для CUDA 12.8+, а драйвер этой видеокарты старше: torch ($cudaIndex) скачается из интернета. Обновите драйвер NVIDIA, чтобы ставить без интернета." }
if ($localTorch) { Info "torch возьму из пакетов установщика: $(Split-Path $localTorch -Leaf)" }
if ($useCuda -and -not $canCuda) { Warn "CUDA выбрана вручную (-Gpu), но подходящая видеокарта NVIDIA не найдена — если torch не увидит её, сервер будет считать на процессоре" }

$needGb = if ($cudaIndex) { 7 } else { 3 }
$drive = (Get-Item $Root).PSDrive
$freeGb = [math]::Round((Get-PSDrive $drive.Name).Free / 1GB, 1)
if ($freeGb -lt $needGb) { Fail "на диске $($drive.Name): свободно $freeGb ГБ, нужно не меньше $needGb ГБ" }
if (-not $cudaIndex -and $cores -and $cores -lt 4) { Warn "у процессора $cores потока(ов) — на процессоре озвучка будет медленной (минимум — 4 ядра)" }
if ($cudaIndex) { Ok "выбрано: видеокарта NVIDIA (torch $cudaIndex)" }
else { Ok "выбрано: процессор" }
Ok "свободно на диске: $freeGb ГБ"

# ---------- что уже установлено и сколько придётся скачать ----------
# Уже установленное не скачивается заново; оценку трафика показываем до начала загрузки (мобильный интернет!)
$Mods = [ordered]@{ "numpy" = "numpy"; "scipy" = "scipy"; "omegaconf" = "omegaconf"; "imageio_ffmpeg" = "imageio-ffmpeg" }
function Installed-State {
    $st = @{ Py = $false; Pip = $false; Torch = $null; Missing = @($Mods.Keys) }
    if (-not (Test-Path $Py)) { return $st }
    Native { & $Py -c "import sys" 2>$null | Out-Null }
    if ($LASTEXITCODE -ne 0) { return $st }
    $st.Py = $true
    $out = Native { & $Py -c "import importlib.util as u; print(' '.join(n for n in ['pip','$(@($Mods.Keys) -join "','")'] if not u.find_spec(n)) or '-')" 2>$null }
    $miss = @("$out".Trim().Split(' ') | Where-Object { $_ -and $_ -ne '-' })
    $st.Pip = $miss -notcontains "pip"
    $st.Missing = @($miss | Where-Object { $_ -ne "pip" })
    $tb = Native { & $Py -c "import torch; print('cuda' if torch.version.cuda else 'cpu')" 2>$null }
    if ($LASTEXITCODE -eq 0) { $st.Torch = "$tb".Trim() }
    return $st
}
$state = Installed-State
$torchOk = ($cudaIndex -and $state.Torch -eq "cuda") -or (-not $cudaIndex -and $state.Torch -eq "cpu")
$hub = Join-Path $env:USERPROFILE ".cache\torch\hub\snakers4_silero-models_master"
$modelCached = (Test-Path $hub) -and (Get-ChildItem $hub -Recurse -Filter "*.pt" -ErrorAction SilentlyContinue | Select-Object -First 1)
$cmuCached = (Test-Path (Join-Path $Root "data\cmudict.dict")) -or (Test-Path (Join-Path $DataDir "data\cmudict.dict"))
$vcInstalled = $false
try { $vcInstalled = (Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64" -ErrorAction Stop).Installed -eq 1 } catch {}

$plan = New-Object System.Collections.Generic.List[object]   # что и сколько МБ (примерно)
function Need($what, $mb) { $plan.Add([pscustomobject]@{ What = $what; Mb = [double]$mb }) }
$localPy = Pkg-File "python-$PyVersion-embed-amd64.zip"
if (-not $state.Py -and -not $localPy) { Need "Python $PyVersion" 11 }
if (-not $state.Pip -and -not (Pkg-File "get-pip.py")) { Need "pip" 2 }
if (-not $vcInstalled -and -not (Pkg-File "vc_redist.x64.exe")) { Need "Visual C++" 25 }
if (-not $torchOk -and -not $localTorch) {
    $torchMb = if ($cudaIndex) { 2760 } else { 250 }
    $partMb = 0   # уже скачанная часть (прерванная загрузка) — её докачаем
    $pattern = if ($cudaIndex) { "torch-*$cudaIndex*" } else { "torch-*cpu*" }
    $fromGh = $cudaIndex -eq "cu128" -and $PkgMan -and $PkgMan.torch -and $PkgMan.torch.cu128
    if ($fromGh) {
        # с GitHub качаются части: уже лежащие в packages и недокачанные архивы в кэше — не в счёт
        $have = @(Get-ChildItem (Join-Path $Pkg "wheels\cu128") -Filter "*.part*" -ErrorAction SilentlyContinue) +
                @(Get-ChildItem $Cache -Filter "BookReader_Server_v*_NVIDIA_*" -ErrorAction SilentlyContinue)
        $partMb = [math]::Min($torchMb, (($have | Measure-Object Length -Sum).Sum / 1MB))
    } else {
        $got = Get-ChildItem $Cache -Filter $pattern -ErrorAction SilentlyContinue | Sort-Object Length -Descending | Select-Object -First 1
        if ($got) { $partMb = [math]::Min($torchMb, $got.Length / 1MB) }
    }
    Need ("PyTorch ({0})" -f $(if ($cudaIndex) { "видеокарта" } else { "процессор" }) + $(if ($fromGh) { ", с GitHub" }) + $(if ($partMb -gt 1) { ", докачка" } else { "" })) ($torchMb - $partMb)
}
$libMb = @{ numpy = 13; scipy = 41; omegaconf = 1; imageio_ffmpeg = 31 }
if (-not $HaveCommon) { foreach ($m in $state.Missing) { Need $Mods[$m] $libMb[$m] } }
$localSilero = Pkg-File "silero\snakers4_silero-models_master"
if (-not $modelCached -and -not $localSilero) { Need "модель голосов Silero" 150 }
if (-not $cmuCached -and -not (Pkg-File "cmudict.dict")) { Need "словарь английского произношения" 4 }
$totalMb = ($plan | Measure-Object Mb -Sum).Sum
if (-not $totalMb) { $totalMb = 0 }
function Size-Text($mb) { if ($mb -ge 1000) { "{0:N1} ГБ" -f ($mb / 1024) } else { "{0:N0} МБ" -f [math]::Max(1, $mb) } }
foreach ($x in $plan) { Log ("план: {0} — {1}" -f $x.What, (Size-Text $x.Mb)) }

# подключение лимитное (мобильный интернет, роуминг, лимит трафика)? — по данным Windows
$metered = $null
try {
    [void][Windows.Networking.Connectivity.NetworkInformation, Windows.Networking.Connectivity, ContentType = WindowsRuntime]
    $prof = [Windows.Networking.Connectivity.NetworkInformation]::GetInternetConnectionProfile()
    if ($prof) {
        $cost = $prof.GetConnectionCost()
        $metered = ("$($cost.NetworkCostType)" -ne "Unrestricted") -or $cost.Roaming -or $cost.ApproachingDataLimit -or $cost.OverDataLimit -or $prof.IsWwanConnectionProfile
    }
} catch {}

if ($totalMb -lt 1) { Ok "все компоненты уже установлены — скачивать ничего не нужно" }
else {
    Info ("будет скачано около {0}" -f (Size-Text $totalMb))
    if ($metered) { Warn "подключение к интернету лимитное (мобильная сеть или ограничение трафика)" }
    if (-not $Yes -and $totalMb -ge 30) {
        $lines = ($plan | ForEach-Object { "  • {0} — {1}" -f $_.What, (Size-Text $_.Mb) }) -join "`n"
        $msg = "Установка скачает из интернета около $(Size-Text $totalMb):`n`n$lines`n`n" +
               $(if ($metered) { "Похоже, подключение лимитное (мобильный интернет или ограничение трафика) — загрузка может стоить денег или съесть пакет трафика.`n`n" } else { "Если интернет мобильный или с ограничением трафика, лучше подключиться к безлимитному Wi-Fi.`n`n" }) +
               "Уже установленное повторно не скачивается, прерванная загрузка продолжится с места обрыва.`n`nПродолжить?"
        if (-not (Ask-Continue "BookReader — загрузка компонентов" ($msg -replace "`n`nПродолжить\?$", "") -Warning:([bool]$metered))) {
            Write-Host "Установка отменена — ничего не скачано."; Log "traffic declined"; exit 2
        }
    }
}

# ---------- 3. Python ----------
Step 3 "Python $PyVersion (отдельный, только для BookReader)"
# Python есть, но не запускается (установку прервали посреди распаковки) — ставим заново
if ((Test-Path $Py) -and -not $state.Py) {
    Warn "Python в папке python повреждён (видимо, установку прервали) — ставлю заново"
    Remove-Item $PyDir -Recurse -Force
}
if (-not (Test-Path $Py)) {
    $zip = if ($localPy) { $localPy } else { Join-Path $Cache "python-$PyVersion-embed-amd64.zip" }
    if (-not $localPy) { Download $PyZipUrl $zip "Python $PyVersion" } else { Info "Python — из пакетов установщика" }
    # распаковка во временную папку и переименование — прерванная распаковка не оставит «полу-Python»
    $new = "$PyDir.new"
    if (Test-Path $new) { Remove-Item $new -Recurse -Force }
    Expand-Archive -Path $zip -DestinationPath $new -Force
    # встраиваемый Python: включаем site-packages и папку BookReader (там server.py и его модули)
    $pth = Get-ChildItem $new -Filter "python*._pth" | Select-Object -First 1
    $lines = Get-Content $pth.FullName | ForEach-Object { if ($_ -match "^#\s*import site") { "import site" } else { $_ } }
    $lines += ".."
    Set-Content -Path $pth.FullName -Value $lines -Encoding ascii
    if (Test-Path $PyDir) { Remove-Item $PyDir -Recurse -Force }
    Rename-Item $new (Split-Path $PyDir -Leaf)
}
Native { & $Py -c "import sys; print(sys.version)" 2>$null | Out-Null }
if ($LASTEXITCODE -ne 0) { Fail "Python не запускается (возможно, не хватает системных библиотек — они ставятся на шаге 4; запустите установку ещё раз)" }
Native { & $Py -m pip --version 2>$null | Out-Null }
if ($LASTEXITCODE -ne 0) {
    $gp = Pkg-File "get-pip.py"
    # без интернета — только если в пакетах есть колесо pip (в старых сборках его не было: get-pip писал
    # «No matching distribution found for pip», и установка молча шла дальше через интернет)
    $pipWhl = $HaveCommon -and @(Get-ChildItem $PkgCommon -Filter "pip-*.whl" -ErrorAction SilentlyContinue).Count -gt 0
    if ($gp -and $pipWhl) { Native { & $Py $gp --no-warn-script-location --no-index "--find-links=$PkgCommon" } }
    if (-not $gp -or -not $pipWhl -or $LASTEXITCODE -ne 0) {
        $gp = Join-Path $Cache "get-pip.py"
        Download $GetPipUrl $gp "установщик пакетов pip"
        Native { & $Py $gp --no-warn-script-location }
    }
    if ($LASTEXITCODE -ne 0) { Fail "не удалось установить pip" }
}
Ok "Python готов: $Py"

# ---------- 4. системные компоненты (нужны права администратора) ----------
Step 4 "Системные компоненты: Visual C++ и разрешение в брандмауэре"
$vcOk = $false
try { $vcOk = (Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64" -ErrorAction Stop).Installed -eq 1 } catch {}
$admin = @()
if (-not $vcOk) {
    $vc = Pkg-File "vc_redist.x64.exe"
    if (-not $vc) { $vc = Join-Path $Cache "vc_redist.x64.exe"; Download $VcUrl $vc "Microsoft Visual C++ (нужен для torch)" }
    $admin += "Start-Process -FilePath '$vc' -ArgumentList '/install','/quiet','/norestart' -Wait"
} else { Ok "Visual C++ уже установлен" }
# Брандмауэр: входящие подключения с телефона — порт сервера (TCP) и поиск сервера в сети (UDP),
# только для частных сетей (дом, работа), не для общественных Wi-Fi. Удаляются в uninstall.bat.
$Port = 8765
try { $cfg = Get-Content (Join-Path $DataDir "server.json") -Raw -Encoding UTF8 | ConvertFrom-Json; if ($cfg.port -as [int]) { $Port = [int]$cfg.port } } catch {}
$RuleTcp = "BookReader (TCP $Port)"
$RuleUdp = "BookReader (UDP $Port, поиск сервера)"
$admin += "netsh advfirewall firewall delete rule name='BookReader' | Out-Null"        # правило прежних версий (на программу, все сети)
$admin += "netsh advfirewall firewall delete rule name='$RuleTcp' | Out-Null"
$admin += "netsh advfirewall firewall delete rule name='$RuleUdp' | Out-Null"
$admin += "netsh advfirewall firewall add rule name='$RuleTcp' dir=in action=allow protocol=TCP localport=$Port profile=private enable=yes | Out-Null"
$admin += "netsh advfirewall firewall add rule name='$RuleUdp' dir=in action=allow protocol=UDP localport=$Port profile=private enable=yes | Out-Null"
Info "Windows спросит разрешение администратора (установка Visual C++ и правило брандмауэра для телефона)…"
try {
    $cmd = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes(($admin -join "; ")))
    $p = Start-Process powershell -Verb RunAs -Wait -PassThru -WindowStyle Hidden -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", $cmd
    # правило действительно появилось? (проверка без прав администратора)
    Native { & netsh advfirewall firewall show rule name="$RuleTcp" 2>$null | Out-Null }
    if ($LASTEXITCODE -eq 0) { Ok "брандмауэр: открыт порт $Port для частных сетей (TCP — чтение, UDP — поиск сервера)" }
    else { Warn "правило брандмауэра не создано — телефон может не подключиться. Вручную: «Брандмауэр Защитника Windows» → «Дополнительные параметры» → «Правила для входящих подключений» → порт TCP $Port, профиль «Частный»." }
    Ok "системные компоненты готовы"
} catch {
    Warn "права администратора не выданы. Если torch не запустится — установите Visual C++: $VcUrl. Телефон сможет подключиться, когда при первом запуске сервера вы нажмёте «Разрешить доступ» в окне брандмауэра."
}
# правило — только для частных сетей: если текущая сеть отмечена как общественная, телефон не подключится
try {
    $public = @(Get-NetConnectionProfile -ErrorAction Stop | Where-Object { "$($_.NetworkCategory)" -eq "Public" })
    foreach ($n in $public) { Warn "сеть «$($n.Name)» отмечена как общественная — телефон через неё не подключится. Если это ваша домашняя сеть: Параметры → Сеть и Интернет → свойства этой сети → «Частная сеть»." }
} catch {}

# ---------- 5. torch ----------
Step 5 "PyTorch — движок нейросети (самая большая загрузка)"
$state = Installed-State   # Python мог только что появиться
$haveTorch = ($cudaIndex -and $state.Torch -eq "cuda") -or (-not $cudaIndex -and $state.Torch -eq "cpu")
if ($haveTorch) { Ok "torch уже установлен ($($state.Torch)) — не скачиваю" }
else {
    Native { & $Py -m pip show torch 2>$null | Out-Null }
    $broken = ($LASTEXITCODE -eq 0) -and -not $state.Torch   # пакет записан, но не загружается (установку прервали)
    if ($broken) { Warn "torch повреждён (видимо, установку прервали) — переустанавливаю" }
    $index = if ($cudaIndex) { "https://download.pytorch.org/whl/$cudaIndex" } else { "https://download.pytorch.org/whl/cpu" }
    $what = "torch ($(if ($cudaIndex) { 'видеокарта, ' + $cudaIndex } else { 'процессор' }))"
    # 0) есть в пакетах установщика — ставим оттуда, без интернета
    # частей NVIDIA рядом нет — сначала пробуем скачать их из выпуска на GitHub (той же версии, что этот архив)
    $ghParts = $false
    if (-not $localTorch -and $cudaIndex -eq "cu128" -and (Get-NvidiaParts)) { $localTorch = Local-Torch $torchVariant -Check; $ghParts = $true }
    if ($localTorch) { $localTorch = Local-Torch $torchVariant }   # части → один файл (в кэше)
    if ($localTorch -eq "missing") { $localTorch = $null }
    if ($localTorch) {
        if ($state.Torch -or $broken) { Pip-Local @("--force-reinstall", "--no-deps", $localTorch) $what @($PkgCommon) }
        Pip-Local @($localTorch) "torch и его зависимости" @($PkgCommon)
        # собранный из частей файл (~3 ГБ) больше не нужен: части остаются в packages
        if ($localTorch.StartsWith($Cache)) { Remove-Item $localTorch -Force -ErrorAction SilentlyContinue }
        # части, скачанные с GitHub только для этой установки, тоже не храним (ещё ~3 ГБ на диске)
        if ($ghParts) { Get-ChildItem (Join-Path $Pkg "wheels\cu128") -Filter "*.part*" -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue }
        Ok "torch установлен"
    }
    # 1) узнаём точный файл torch и качаем его сами — с докачкой после обрыва (pip начинает 2,8 ГБ с нуля)
    $whl = $null
    if (-not $localTorch) {
    $report = Join-Path $Tmp "torch-report.json"
    Remove-Item $report -ErrorAction SilentlyContinue
    Native { & $Py -m pip install torch --index-url $index --dry-run --ignore-installed --no-deps --quiet --disable-pip-version-check --report $report 2>$null | Out-Null }
    try {
        $item = (Get-Content $report -Raw -Encoding UTF8 | ConvertFrom-Json).install | Where-Object { $_.metadata.name -eq "torch" } | Select-Object -First 1
        $url = "$($item.download_info.url)"
        $sha = "$($item.download_info.archive_info.hashes.sha256)"
        if (-not $sha -and "$($item.download_info.archive_info.hash)" -match 'sha256=(\w+)') { $sha = $Matches[1] }
        if ($url) {
            $whl = Join-Path $Cache ([uri]::UnescapeDataString(([uri]$url).Segments[-1]))
            Download $url $whl $what $sha
        }
    } catch { Warn "не удалось узнать файл torch заранее ($($_.Exception.Message)) — ставлю обычным способом (без докачки)"; $whl = $null }
    # 2) установка: torch — из скачанного файла, мелкие зависимости — из интернета
    if ($whl -and (Test-Path $whl)) {
        if ($state.Torch -or $broken) { Pip @("--force-reinstall", "--no-deps", $whl) $what }   # смена CPU ⇄ CUDA или ремонт
        Pip @($whl) "torch и его зависимости"
        # в кэше оставляем только нужную сборку (на случай переустановки), старые — удаляем
        Get-ChildItem $Cache -Filter "torch-*" | Where-Object { $_.FullName -ne $whl } | Remove-Item -Force -ErrorAction SilentlyContinue
    } else {
        $force = if ($state.Torch -or $broken) { "--force-reinstall" } else { "--upgrade" }
        Pip @($force, "torch", "--index-url", $index) $what ("Если сайт PyTorch недоступен: скачайте файл для Python 3.12 (cp312, win_amd64) со страницы`n  $index/torch/`n" +
            "положите его в папку`n  $Cache`nи снова запустите install.bat — установщик возьмёт его оттуда.")
    }
    Ok "torch установлен"
    }
}

# ---------- 6. остальные библиотеки ----------
Step 6 "Библиотеки: numpy, scipy, omegaconf, imageio-ffmpeg (сжатие в Opus)"
$state = Installed-State
if ($state.Missing.Count -eq 0) { Ok "библиотеки уже установлены — не скачиваю" }
else {
    $pkgs = @($state.Missing | ForEach-Object { $Mods[$_] })
    $help = "Если сайт пакетов Python (pypi.org) недоступен: скачайте файлы для Windows (cp312, win_amd64) со страниц`n" +
            (($pkgs | ForEach-Object { "  https://pypi.org/project/$_/#files" }) -join "`n") +
            "`n(и их зависимостей), положите их в папку`n  $Cache`nи выполните:`n  `"$Py`" -m pip install --no-index --find-links `"$Cache`" $($pkgs -join ' ')"
    Pip-Local $pkgs ("библиотеки: " + ($pkgs -join ", ")) @($PkgCommon) $help
    Ok "библиотеки установлены"
}

# ---------- 7. модель и словари ----------
Step 7 "Модель голосов Silero и словарь английского произношения"
# модель из пакетов установщика — в папку torch hub пользователя (туда же её положил бы torch.hub)
if (-not $modelCached -and $localSilero) {
    Info "модель голосов — из пакетов установщика"
    New-Item -ItemType Directory -Force -Path (Split-Path $hub -Parent) | Out-Null
    if (Test-Path $hub) { Remove-Item $hub -Recurse -Force }
    Copy-Item $localSilero $hub -Recurse -Force
}
$cmuLocal = Pkg-File "cmudict.dict"
$cmuDest = Join-Path $Root "data\cmudict.dict"
if (-not $cmuCached -and $cmuLocal) {
    try { Copy-Item $cmuLocal $cmuDest -Force } catch {
        New-Item -ItemType Directory -Force -Path (Join-Path $DataDir "data") | Out-Null
        Copy-Item $cmuLocal (Join-Path $DataDir "data\cmudict.dict") -Force
    }
}
Push-Location $Root
# как сервер: из папки модели напрямую (без обращения к GitHub), если её нет — через torch.hub с GitHub
Native { & $Py -c "import server; server.load_silero(); print('модель загружена')" }
$modelOk = $LASTEXITCODE -eq 0
Native { & $Py -c "import en_translit; en_translit.preload(background=False)" }
Pop-Location
if ($modelOk) { Ok "модель Silero загружена" } else { Warn "модель не скачалась — сервер попробует скачать её при первом запуске" }

# ---------- 8. ярлык и BookReader.exe ----------
Step 8 "Ярлык BookReader на рабочем столе"
$exe = Join-Path $Root "BookReader.exe"
$csc = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
$icon = Join-Path $Root "web\favicon.ico"
$target = $null
if (Test-Path $csc) {
    Native { & $csc /nologo /codepage:65001 /target:winexe /optimize "/win32icon:$icon" "/out:$exe" /r:System.Windows.Forms.dll (Join-Path $PSScriptRoot "launcher.cs") | Out-Null }
    if ($LASTEXITCODE -eq 0 -and (Test-Path $exe)) { $target = $exe; Ok "собран BookReader.exe" }
}
if (-not $target) { $target = Join-Path $Root "BookReader.bat"; Warn "компилятор .NET не найден — ярлык будет вести на BookReader.bat" }
$shell = New-Object -ComObject WScript.Shell
foreach ($place in @([Environment]::GetFolderPath("Desktop"), (Join-Path ([Environment]::GetFolderPath("StartMenu")) "Programs"))) {
    $lnk = $shell.CreateShortcut((Join-Path $place "BookReader.lnk"))
    $lnk.TargetPath = $target
    $lnk.WorkingDirectory = $Root
    $lnk.IconLocation = "$icon,0"
    $lnk.Description = "BookReader — сервер озвучки книг"
    $lnk.Save()
}
Ok "ярлыки: рабочий стол и меню «Пуск»"

# ---------- 9. проверка ----------
Step 9 "Проверка установки"
# вывод программ: строки потока ошибок приходят как ErrorRecord — берём их текст
function Lines { process { if ($_ -is [System.Management.Automation.ErrorRecord]) { $m = $_.Exception.Message; if ($m -and $m -ne "System.Management.Automation.RemoteException") { $m } else { "" } } else { "$_" } } }
$env:PYTHONIOENCODING = "utf-8"   # русские сообщения тестов — в журнал без «кракозябр»
$check = Native { & $Py -c "import torch, numpy, scipy, omegaconf, imageio_ffmpeg; print(torch.__version__, 'GPU' if torch.cuda.is_available() else 'CPU')" 2>&1 | Lines }
if ($LASTEXITCODE -ne 0) { Fail "проверка не прошла: $check" }
Ok "torch $check"
Native { & $Py (Join-Path $Root "tests\test_server.py") 2>&1 | Lines | Out-File -FilePath $Log -Append -Encoding utf8 }
if ($LASTEXITCODE -eq 0) { Ok "самопроверка сервера пройдена" } else { Warn "самопроверка сервера нашла проблемы — подробности в install.log" }

Write-Progress -Activity "Установка BookReader" -Completed
Write-Host ""
Write-Host "Готово! BookReader установлен." -ForegroundColor Green
Write-Host "Запуск: ярлык BookReader на рабочем столе. Окно сервера покажет адрес для телефона."
Log "done"
$script:Finished = $true
$ans = Read-Host "Запустить BookReader сейчас? (Д/н)"
if ($ans -notmatch "^(н|n)") { Start-Process $target -WorkingDirectory $Root }
} finally {
    # прервали (Ctrl+C, закрыли окно) или ошибка — в журнал; повторный запуск продолжит с того же места
    if (-not $script:Finished) {
        Write-Progress -Activity "Установка BookReader" -Completed
        Log "остановлено до завершения — повторный запуск install.bat продолжит с того же места (скачанное сохранено)"
    }
}
