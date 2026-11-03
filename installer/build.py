"""설치 파일 만들기: PyInstaller(exe) → zip → IExpress(Setup.exe).

실행: python installer/build.py
결과: dist/MarkemSender_Setup_<버전>.exe  (그리고 dist/MarkemSender/ 폴더)
필요: pip install pyinstaller pillow  (IExpress는 Windows에 기본 포함)
"""
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from version import VERSION  # noqa: E402

BUILD = os.path.join(ROOT, "build")
DIST = os.path.join(ROOT, "dist")
APP_DIR = os.path.join(DIST, "MarkemSender")
STAGE = os.path.join(BUILD, "stage")


def make_icon():
    from PIL import Image
    src = Image.open(os.path.join(ROOT, "assets", "emblem_navy.png")).convert("RGBA")
    side = max(src.size)
    canvas = Image.new("RGBA", (side, side), (255, 255, 255, 255))
    canvas.alpha_composite(src, ((side - src.width) // 2, (side - src.height) // 2))
    ico = os.path.join(BUILD, "app.ico")
    canvas.save(ico, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    return ico


def write_ps(src, dst):
    """Windows PowerShell 5.1 이 한글을 읽도록 UTF-8(BOM)으로 저장한다."""
    with open(src, encoding="utf-8") as f:
        text = f.read()
    with open(dst, "w", encoding="utf-8-sig", newline="\r\n") as f:
        f.write(text)


def main():
    for d in (BUILD, DIST):
        shutil.rmtree(d, ignore_errors=True)
    os.makedirs(STAGE)
    ico = make_icon()

    subprocess.check_call([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
        "--name", "MarkemSender", "--icon", ico,
        "--add-data", "%s;assets" % os.path.join(ROOT, "assets"),
        "--distpath", DIST, "--workpath", os.path.join(BUILD, "pyi"), "--specpath", BUILD,
        os.path.join(ROOT, "app.py")], cwd=ROOT)
    if not os.path.exists(os.path.join(APP_DIR, "MarkemSender.exe")):
        raise SystemExit("exe 빌드 실패")

    # 제거 스크립트는 설치 폴더 안에 둔다
    write_ps(os.path.join(HERE, "uninstall.ps1"), os.path.join(APP_DIR, "uninstall.ps1"))
    shutil.copy(os.path.join(HERE, "uninstall.cmd"), APP_DIR)
    shutil.make_archive(os.path.join(STAGE, "app"), "zip", APP_DIR)

    write_ps(os.path.join(HERE, "install.ps1"), os.path.join(STAGE, "install.ps1"))
    with open(os.path.join(STAGE, "version.txt"), "w", encoding="ascii") as f:
        f.write(VERSION + "\n")

    target = os.path.join(DIST, "MarkemSender_Setup_%s.exe" % VERSION)
    QUIET = ("powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden "
             "-File install.ps1 -Silent")
    files = ["app.zip", "install.ps1", "version.txt"]
    sed = ["[Version]", "Class=IEXPRESS", "SEDVersion=3", "[Options]", "PackagePurpose=InstallApp",
           "ShowInstallProgramWindow=0", "HideExtractAnimation=1", "UseLongFileName=1",
           "InsideCompressed=0", "CAB_FixedSize=0", "CAB_ResvCodeSigning=0", "RebootMode=N",
           "InstallPrompt=%InstallPrompt%", "DisplayLicense=%DisplayLicense%",
           "FinishMessage=%FinishMessage%", "TargetName=%TargetName%",
           "FriendlyName=%FriendlyName%", "AppLaunched=%AppLaunched%",
           "PostInstallCmd=%PostInstallCmd%", "AdminQuietInstCmd=%AdminQuietInstCmd%",
           "UserQuietInstCmd=%UserQuietInstCmd%", "SourceFiles=SourceFiles", "[Strings]",
           "InstallPrompt=TASCO 프린터 데이터 전송 %s 을(를) 설치합니다. 계속할까요?" % VERSION,
           "DisplayLicense=", "FinishMessage=", "TargetName=" + target,
           "FriendlyName=TASCO 프린터 데이터 전송 설치",
           # cmd 의 현재 폴더 검색(NoDefaultCurrentDirectoryInExePath 영향)에 기대지 않고 직접 실행
           "AppLaunched=powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden "
           "-File install.ps1",
           "PostInstallCmd=<None>",
           # Setup.exe /Q (일괄 설치): 같은 스크립트를 대화상자 없이 실행
           "AdminQuietInstCmd=" + QUIET, "UserQuietInstCmd=" + QUIET]
    sed += ['FILE%d="%s"' % (i, f) for i, f in enumerate(files)]
    sed += ["[SourceFiles]", "SourceFiles0=" + STAGE + "\\", "[SourceFiles0]"]
    sed += ["%%FILE%d%%=" % i for i in range(len(files))]
    sed_path = os.path.join(BUILD, "setup.sed")
    with open(sed_path, "w", encoding="mbcs", newline="\r\n") as f:   # IExpress 는 시스템 기본 인코딩
        f.write("\n".join(sed) + "\n")

    subprocess.run(["iexpress", "/N", "/Q", sed_path], check=False)
    for _ in range(120):   # IExpress 는 바로 돌아오므로 파일이 생길 때까지 기다린다
        if os.path.exists(target) and os.path.getsize(target) > 1_000_000:
            time.sleep(1)
            break
        time.sleep(1)
    else:
        raise SystemExit("Setup.exe 생성 실패")
    print("완료:", target, "%.1f MB" % (os.path.getsize(target) / 1e6))


if __name__ == "__main__":
    main()
