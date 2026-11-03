# TASCO 프린터 데이터 전송 제거. 계정·기록 데이터(ProgramData)는 지우지 않는다.
param([string]$App = "", [switch]$Silent, [switch]$NoShortcut, [switch]$NoRegistry)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms

function Say($text, $icon = 'Information') {
    if (-not $Silent) {
        [void][System.Windows.Forms.MessageBox]::Show($text, 'TASCO 프린터 데이터 전송 제거', 'OK', $icon)
    } else { Write-Host $text }
}

try {
    $App = [System.IO.Path]::GetFullPath($App)
    if ((Split-Path $App -Leaf) -ne 'MarkemSender') { throw "제거할 폴더가 올바르지 않습니다: $App" }
    if (-not $Silent) {
        $ans = [System.Windows.Forms.MessageBox]::Show(
            "TASCO 프린터 데이터 전송을 제거할까요?`n`n계정·기록 데이터(C:\ProgramData\TASCO\MarkemSender)는 지우지 않고 남깁니다.",
            'TASCO 프린터 데이터 전송 제거', 'YesNo', 'Question')
        if ($ans -ne 'Yes') { exit 0 }
    }
    Get-Process -Name MarkemSender -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep -Milliseconds 800
    if (-not $NoShortcut) {
        Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path ([Environment]::GetFolderPath('Desktop')) 'TASCO 프린터 데이터 전송.lnk')
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path ([Environment]::GetFolderPath('Programs')) 'TASCO')
    }
    if (-not $NoRegistry) {
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\TASCOMarkemSender'
    }
    if (Test-Path $App) { Remove-Item -Recurse -Force $App }
    Say "제거했습니다. 계정·기록 데이터는 C:\ProgramData\TASCO\MarkemSender 에 남아 있습니다."
} catch {
    Say "제거하지 못했습니다.`n`n$($_.Exception.Message)" 'Error'
    exit 1
}
