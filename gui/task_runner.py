from __future__ import annotations

import codecs
from pathlib import Path


def split_stream_output(pending: str, chunk: str, final: bool = False) -> tuple[list[str], str]:
    text = pending + chunk
    lines: list[str] = []
    start = 0
    index = 0
    while index < len(text):
        character = text[index]
        if character == "\n":
            lines.append(text[start:index])
            start = index + 1
        elif character == "\r":
            if index + 1 == len(text) and not final:
                break
            lines.append(text[start:index])
            if index + 1 < len(text) and text[index + 1] == "\n":
                index += 1
            start = index + 1
        index += 1
    remainder = text[start:]
    if final and remainder:
        lines.append(remainder)
        remainder = ""
    return lines, remainder


def infer_stage(line: str) -> str | None:
    text = str(line or "").lower()
    if not text.strip():
        return None
    if "[error]" in text or "error" in text or "失败" in text or "failed" in text:
        return "error"
    if "[实际来源] api" in text or text.startswith("[api") or "[降级]" in text:
        return "baidu"
    if "[浏览器]" in text:
        return "login"
    if "preflight" in text or "自检" in text:
        return "preflight"
    if "login" in text or "账号" in text or "账户" in text or "登录" in text:
        return "login"
    if "fetch-baidu" in text or "baidu" in text or "百度" in text:
        return "baidu"
    if "parse-kst" in text or "kst" in text or "快商通" in text:
        return "kst"
    if "excel" in text or "写入" in text or "保存" in text:
        return "excel"
    if "完成" in text or "success" in text or "passed" in text:
        return "done"
    return None


def should_warn_no_output(
    stage: str,
    elapsed_seconds: float,
    *,
    already_warned: bool,
) -> bool:
    return (
        stage not in {"", "done", "error"}
        and elapsed_seconds >= 45
        and not already_warned
    )


def infer_pet_event(line: str) -> str | None:
    text = str(line or "").strip().lower()
    if not text:
        return None
    if any(token in text for token in ("[error]", "[错误]", "失败", "failed", "异常", "未通过", "中断")):
        return "failed"
    if "[实际来源] api" in text:
        return "baidu_ready"
    if text.startswith("[api"):
        if any(token in text for token in ("读取完成", "已读取", "成功", "通过")):
            return "baidu_ready"
        return "baidu"
    if "[降级]" in text:
        return "baidu"
    if "[浏览器]" in text:
        return "login"
    if "顶部用户名已匹配" in text or "百度账号登录完成" in text or "登录成功" in text:
        return "login_ready"
    if "已填写百度登录字段" in text or "登录提交按钮" in text or ("切换" in text and ("账号" in text or "账户" in text)):
        return "login"
    if "[1/4]" in text and "百度" in text:
        return "baidu"
    if "百度" in text and any(token in text for token in ("数据已读取", "读取完成", "自检：通过", "表格数据已稳定")):
        return "baidu_ready"
    if "[2/4]" in text or "解析快商通" in text:
        return "kst"
    if "[3/4]" in text or "数据合并" in text or "合并百度" in text:
        return "merge"
    if "[4/4]" in text or "写入 excel" in text or "excel 写入" in text:
        return "excel"
    return None


try:  # pragma: no cover - import depends on optional GUI dependencies.
    from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal
except Exception:  # pragma: no cover
    QObject = object  # type: ignore[assignment,misc]
    QProcess = None  # type: ignore[assignment]
    QProcessEnvironment = None  # type: ignore[assignment]

    class Signal:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs):
            pass


def build_process_environment():
    if QProcessEnvironment is None:  # pragma: no cover
        raise RuntimeError("PySide6 is required for process environment")
    env = QProcessEnvironment.systemEnvironment()
    env.insert("PYTHONUTF8", "1")
    env.insert("PYTHONIOENCODING", "utf-8")
    env.insert("PYTHONUNBUFFERED", "1")
    return env


class QtTaskRunner(QObject):
    output = Signal(str)
    stage_changed = Signal(str)
    started = Signal()
    finished = Signal(int)
    failed_to_start = Signal(str)

    def __init__(self, parent=None):
        if QProcess is None:  # pragma: no cover
            raise RuntimeError("PySide6 is required for QtTaskRunner")
        super().__init__(parent)
        self._process = QProcess(self)
        self._pending_output = ""
        self._output_decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._process.setProcessEnvironment(build_process_environment())
        self._process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._process.readyReadStandardOutput.connect(self._read_output)
        self._process.started.connect(self.started.emit)
        self._process.finished.connect(self._handle_finished)
        self._process.errorOccurred.connect(self._handle_error)

    def is_running(self) -> bool:
        return self._process.state() != QProcess.ProcessState.NotRunning

    def start(self, command: list[str], cwd: str | Path, extra_env: dict[str, str] | None = None) -> None:
        if self.is_running():
            self.failed_to_start.emit("已有任务正在运行，请等待当前任务结束。")
            return
        if not command:
            self.failed_to_start.emit("命令为空，无法启动任务。")
            return
        self._pending_output = ""
        self._output_decoder = codecs.getincrementaldecoder("utf-8")("replace")
        process_env = build_process_environment()
        for key, value in (extra_env or {}).items():
            process_env.insert(str(key), str(value))
        self._process.setProcessEnvironment(process_env)
        self._process.setWorkingDirectory(str(cwd))
        self._process.start(command[0], command[1:])

    def stop(self) -> None:
        if self.is_running():
            self._process.kill()

    def _read_output(self) -> None:
        QtTaskRunner._consume_output_bytes(self, bytes(self._process.readAllStandardOutput()))

    def _consume_output_bytes(self, data: bytes, final: bool = False) -> None:
        text = QtTaskRunner._decode_output(self, data, final=final)
        lines, self._pending_output = split_stream_output(self._pending_output, text, final=final)
        QtTaskRunner._emit_output_lines(self, lines)

    def _decode_output(self, data: bytes, final: bool = False) -> str:
        decoder = getattr(self, "_output_decoder", None)
        if decoder is None:
            decoder = codecs.getincrementaldecoder("utf-8")("replace")
            self._output_decoder = decoder
        return decoder.decode(data, final=final)

    def _emit_output_lines(self, lines: list[str]) -> None:
        for line in lines:
            self.output.emit(line)
            stage = infer_stage(line)
            if stage:
                self.stage_changed.emit(stage)

    def _handle_finished(self, exit_code: int, _exit_status) -> None:
        QtTaskRunner._consume_output_bytes(self, bytes(self._process.readAllStandardOutput()))
        QtTaskRunner._consume_output_bytes(self, b"", final=True)
        self.finished.emit(int(exit_code))

    def _handle_error(self, error) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self.failed_to_start.emit(str(error))
