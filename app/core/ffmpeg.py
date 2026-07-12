"""FFmpeg discovery and PATH injection."""
import glob
import os
import shutil


def _find_winget_ffmpeg() -> str | None:
    """Locate the ffmpeg bin dir installed by `winget install FFmpeg`.

    winget unpacks ffmpeg under the *current user's* LOCALAPPDATA, which is
    not always added to PATH. Match it dynamically so it works for any user.
    """
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return None
    pattern = os.path.join(
        local_app_data,
        "Microsoft", "WinGet", "Packages",
        "Gyan.FFmpeg_*", "ffmpeg-*-full_build", "bin",
    )
    matches = [p for p in glob.glob(pattern) if os.path.isfile(os.path.join(p, "ffmpeg.exe"))]
    # Prefer the highest version if several are installed.
    return sorted(matches)[-1] if matches else None


FFMPEG_LOCATION: str | None = shutil.which("ffmpeg")
if FFMPEG_LOCATION:
    FFMPEG_LOCATION = os.path.dirname(FFMPEG_LOCATION)
else:
    FFMPEG_LOCATION = _find_winget_ffmpeg()

if FFMPEG_LOCATION and FFMPEG_LOCATION not in os.environ.get("PATH", ""):
    os.environ["PATH"] = FFMPEG_LOCATION + os.pathsep + os.environ.get("PATH", "")
