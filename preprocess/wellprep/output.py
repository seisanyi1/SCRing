"""Validate output destinations using actual file writes, not access flags."""
from pathlib import Path
import os
import tempfile


def create_run_output(value: str, default: Path, run_id: str) -> tuple[Path, Path]:
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in (chr(34), chr(39)):
        text = text[1:-1].strip()
    parent = Path(os.path.expandvars(text)).expanduser() if text else default
    out = None
    created = False
    try:
        parent = parent.resolve()
        parent.mkdir(parents=True, exist_ok=True)
        if not parent.is_dir():
            raise NotADirectoryError(str(parent))
        out = parent / run_id
        out.mkdir()
        created = True
        # Check permission to create files as well as directories before queuing work.
        with tempfile.TemporaryFile(dir=out) as probe:
            probe.write(b"wellprep-output-check")
            probe.flush()
        return parent, out
    except OSError as exc:
        if created and out is not None:
            try:
                out.rmdir()  # Only our newly created, empty directory.
            except OSError:
                pass
        if isinstance(exc, PermissionError):
            reason = "系统拒绝写入（权限不足或安全软件拦截），不是中文路径或图片识别错误。请关闭旧启动窗口，用当前登录账户重新启动 start_ui.bat；若仍失败，选择可写目录，或检查该目录的写入权限及 Windows 安全中心的拦截记录。"
        elif isinstance(exc, (NotADirectoryError, FileExistsError)):
            reason = "路径被已有文件或同名目录占用，请选择其他输出文件夹。"
        else:
            reason = "请检查磁盘连接、剩余空间及目录路径。"
        raise ValueError(f"输出目录不可写：{parent}。{reason} 系统信息：{exc}") from exc
