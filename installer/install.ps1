# TASCO 프린터 데이터 전송 설치 (관리자 권한 불필요, 현재 사용자 폴더에 설치)
param([string]$Target = "", [switch]$NoShortcut, [switch]$NoRegistry, [switch]$Silent)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms

function Say($text, $icon = 'Information') {
    if (-not $Silent) {
        [void][System.Windows.Forms.MessageBox]::Show($text, 'TASCO 프린터 데이터 전송', 'OK', $icon)
    } else { Write-Host $text }
}

# 시험용: 이 환경변수가 있으면 그 폴더에 조용히 설치(바로가기·제거 항목 없음)
if ($env:MARKEM_INSTALL_TEST_DIR) {
    $Target = Join-Path $env:MARKEM_INSTALL_TEST_DIR 'MarkemSender'
    $NoShortcut = $true; $NoRegistry = $true; $Silent = $true
}

$src = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }
if (-not $Target) { $Target = Join-Path $env:LOCALAPPDATA 'Programs\TASCO\MarkemSender' }
$Target = [System.IO.Path]::GetFullPath($Target)
$version = (Get-Content (Join-Path $src 'version.txt') -ErrorAction SilentlyContinue | Select-Object -First 1)
if (-not $version) { $version = '0.2.0' }

try {
    # 폴더 이름이 MarkemSender 로 끝날 때만 지우고 덮어쓴다(엉뚱한 폴더 삭제 방지)
    if ((Split-Path $Target -Leaf) -ne 'MarkemSender') { throw "설치 폴더 이름이 올바르지 않습니다: $Target" }
    $running = Get-Process -Name MarkemSender -ErrorAction SilentlyContinue
    if ($running) {
        Say "프로그램이 실행 중입니다. 종료한 뒤 다시 설치하세요." 'Warning'
        exit 1
    }
    if (Test-Path $Target) { Remove-Item -Recurse -Force $Target }
    New-Item -ItemType Directory -Force $Target | Out-Null
    Expand-Archive -Path (Join-Path $src 'app.zip') -DestinationPath $Target -Force
    $exe = Join-Path $Target 'MarkemSender.exe'
    if (-not (Test-Path $exe)) { throw '설치 파일이 손상되었습니다(MarkemSender.exe 없음)' }

    if (-not $NoShortcut) {
        $ws = New-Object -ComObject WScript.Shell
        $places = @((Join-Path ([Environment]::GetFolderPath('Programs')) 'TASCO'),
                    [Environment]::GetFolderPath('Desktop'))
        foreach ($dir in $places) {
            New-Item -ItemType Directory -Force $dir | Out-Null
            $lnk = $ws.CreateShortcut((Join-Path $dir 'TASCO 프린터 데이터 전송.lnk'))
            $lnk.TargetPath = $exe
            $lnk.WorkingDirectory = $Target
            $lnk.IconLocation = $exe
            $lnk.Save()
        }
    }
    if (-not $NoRegistry) {
        $key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\TASCOMarkemSender'
        New-Item -Path $key -Force | Out-Null
        $uninst = Join-Path $Target 'uninstall.cmd'
        $props = @{ DisplayName = 'TASCO 프린터 데이터 전송'; DisplayVersion = $version;
                    Publisher = 'TASCO'; InstallLocation = $Target; DisplayIcon = $exe;
                    UninstallString = "`"$uninst`""; NoModify = 1; NoRepair = 1 }
        foreach ($k in $props.Keys) { Set-ItemProperty -Path $key -Name $k -Value $props[$k] }
    }
    Say "설치가 끝났습니다.`n`n설치 위치: $Target`n바탕화면의 'TASCO 프린터 데이터 전송' 아이콘으로 실행하세요."
    if (-not $Silent) { Start-Process $exe -WorkingDirectory $Target }
} catch {
    Say "설치하지 못했습니다.`n`n$($_.Exception.Message)" 'Error'
    exit 1
}
