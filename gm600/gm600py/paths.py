"""Where the tools write data: backups of codeplugs and flash sectors.

$GM600_BACKUP_DIR if set, else ~/.local/share/gm600 (never inside the source tree or an install
location). Subfolders per kind: codeplug/ (gm600py.codeplug.apply), flash/ (stubs.flashtool)."""
import os


def backup_dir(kind):
    root = os.environ.get("GM600_BACKUP_DIR") or os.path.join(os.path.expanduser("~"), ".local", "share", "gm600")
    path = os.path.join(root, kind)
    os.makedirs(path, exist_ok=True)
    return path
