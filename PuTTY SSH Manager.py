from __future__ import annotations

import json
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Final, Optional, Sequence, TypedDict

import tkinter as tk
import tkinter.messagebox as messagebox
from tkinter import Event, ttk

from pywinauto import Application
from pywinauto.findwindows import ElementNotFoundError

CONFIG_DIR: Final[Path] = Path.home() / ".PuTTY-SSH-Manager"
CONFIG_FILE: Final[Path] = CONFIG_DIR / "servers.json"

FIELD_DEFS: Final[Sequence[tuple[str, str]]] = (
    ("名称", "name"),
    ("主机", "host"),
    ("端口", "port"),
    ("用户", "username"),
    ("密码", "password"),
)

PUTTY_EXE: Final[str] = "putty.exe"
FOCUS_TIMEOUT: Final[float] = 5.0
FOCUS_POLL: Final[float] = 0.1


class FormData(TypedDict):
    name: str
    host: str
    port: str
    username: str
    password: str


Validator = Callable[[FormData], Optional[str]]
ServerCallback = Callable[["ServerConfig"], None]


@dataclass
class ServerConfig:
    name: str = ""
    host: str = ""
    port: int = 22
    username: str = ""
    password: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ServerConfig":
        try:
            port = int(data.get("port", 22))
        except (TypeError, ValueError):
            port = 22

        return cls(
            name=str(data.get("name", "")),
            host=str(data.get("host", "")),
            port=port,
            username=str(data.get("username", "")),
            password=str(data.get("password", "")),
        )

    @property
    def display_name(self) -> str:
        return self.name or self.host

    @property
    def endpoint(self) -> str:
        return f"{self.username}@{self.host}:{self.port}"


class ServerManager:
    def __init__(self, config_file: Path = CONFIG_FILE) -> None:
        self.config_file: Path = config_file
        self.servers: list[ServerConfig] = []
        self.load()

    def load(self) -> None:
        try:
            if not self.config_file.exists():
                self.servers = []
                return

            with self.config_file.open("r", encoding="utf-8") as f:
                items: Any = json.load(f)

            if not isinstance(items, list):
                self.servers = []
                return

            self.servers = [ServerConfig.from_dict(i) for i in items if isinstance(i, dict)]

        except (json.JSONDecodeError, OSError, TypeError):
            self.servers = []

    def save(self) -> None:
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        with self.config_file.open("w", encoding="utf-8") as f:
            json.dump([s.to_dict() for s in self.servers], f, ensure_ascii=False, indent=2)

    def add(self, server: ServerConfig) -> ServerConfig:
        self.servers.append(server)
        self.save()
        return server

    def remove(self, server: ServerConfig) -> bool:
        index = self.index_of(server)
        if index is None:
            return False

        del self.servers[index]
        self.save()
        return True

    def update(self, server: ServerConfig, replacement: ServerConfig) -> bool:
        index = self.index_of(server)
        if index is None:
            return False

        self.servers[index] = replacement
        self.save()
        return True

    def index_of(self, server: ServerConfig) -> Optional[int]:
        try:
            return self.servers.index(server)
        except ValueError:
            return None

    def list(self) -> list[ServerConfig]:
        return list(self.servers)

    def __len__(self) -> int:
        return len(self.servers)


def validate_server(data: FormData) -> Optional[str]:
    for key, label in (("name", "名称"), ("host", "主机"), ("port", "端口"), ("username", "用户"), ("password", "密码")):
        if not data.get(key):
            return f"{label}不能为空"

    try:
        port = int(data["port"])
    except ValueError:
        return "端口不是数字"

    if not (0 < port < 65536):
        return "端口超出有效范围(1-65535)"

    return None


class FormDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        title: str,
        fields: Sequence[tuple[str, str]],
        initial: Optional[dict[str, Any]] = None,
        ok_text: str = "确定",
        validator: Optional[Validator] = None,
    ) -> None:
        super().__init__(parent)
        self._parent: tk.Misc = parent
        self._validator: Optional[Validator] = validator
        self._vars: dict[str, tk.StringVar] = {}
        self._result: Optional[FormData] = None

        init = {k: str(v) for k, v in (initial or {}).items()}

        self.title(title)
        self.resizable(False, False)
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self.bind("<Escape>", lambda _: self._on_cancel())
        self.bind("<Return>", lambda _: self._on_ok())

        body = ttk.Frame(self, padding=16)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)

        first_entry: Optional[ttk.Entry] = None
        for row, (label, key) in enumerate(fields):
            ttk.Label(body, text=f"{label}：").grid(row=row, column=0, sticky="e", padx=(0, 8), pady=4)

            var = tk.StringVar(value=init.get(key, ""))
            self._vars[key] = var
            entry = ttk.Entry(body, textvariable=var, width=28, show="*" if key == "password" else "")
            entry.grid(row=row, column=1, sticky="ew", pady=4)

            if first_entry is None:
                first_entry = entry

        btns = ttk.Frame(body)
        btns.grid(row=len(fields), column=0, columnspan=2, sticky="e", pady=(16, 0))
        ttk.Button(btns, text="取消", command=self._on_cancel).pack(side="right", padx=(8, 0))
        ttk.Button(btns, text=ok_text, command=self._on_ok).pack(side="right")

        self._center_on_parent()
        self.grab_set()
        self.wait_visibility()
        if first_entry is not None:
            first_entry.focus_set()
            first_entry.select_range(0, "end")

    def _center_on_parent(self) -> None:
        self.update_idletasks()
        pw, ph = self._parent.winfo_width(), self._parent.winfo_height()
        px, py = self._parent.winfo_rootx(), self._parent.winfo_rooty()
        if pw <= 1 or ph <= 1:
            px = py = 0

        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        x = px + max(0, (pw - w) // 2)
        y = py + max(0, (ph - h) // 2)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"+{max(0, min(x, sw - w))}+{max(0, min(y, sh - h))}")

    def _on_ok(self) -> None:
        data: FormData = {k: v.get().strip() for k, v in self._vars.items()}
        if self._validator is not None:
            err = self._validator(data)

            if err:
                messagebox.showwarning("提示", err, parent=self)
                return

        self._result = data
        self.destroy()

    def _on_cancel(self) -> None:
        self._result = None
        self.destroy()

    def show(self) -> Optional[FormData]:
        self.wait_window(self)
        return self._result


class _ServerButtonGroup(ttk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        server: ServerConfig,
        *,
        on_connect: ServerCallback,
        on_edit: ServerCallback,
        on_delete: ServerCallback,
    ) -> None:
        super().__init__(parent)
        ttk.Button(self, text="连接", width=5, command=lambda: on_connect(server)).pack(side="left")
        ttk.Button(self, text="编辑", width=5, command=lambda: on_edit(server)).pack(side="left", padx=(4, 0))
        ttk.Button(self, text="删除", width=5, command=lambda: on_delete(server)).pack(side="left", padx=(4, 0))


class ServerSelector(ttk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        manager: ServerManager,
        *,
        on_connect: ServerCallback,
        on_edit: ServerCallback,
        on_delete: ServerCallback,
    ) -> None:
        super().__init__(parent)
        self._manager = manager
        self._on_connect = on_connect
        self._on_edit = on_edit
        self._on_delete = on_delete
        self._rows: list[tk.Widget] = []
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self._canvas: tk.Canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0, background="#ffffff")
        self._canvas.grid(row=0, column=0, sticky="nsew")

        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self._canvas.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self._canvas.configure(yscrollcommand=scrollbar.set)

        self._list_frame = ttk.Frame(self._canvas)
        self._list_frame.columnconfigure(0, weight=1)
        self._window_id = self._canvas.create_window((0, 0), window=self._list_frame, anchor="nw")

        self._list_frame.bind("<Configure>", lambda _: self._canvas.configure(scrollregion=self._canvas.bbox("all")))
        self._canvas.bind("<Configure>", lambda e: self._canvas.itemconfigure(self._window_id, width=e.width))
        self._canvas.bind("<Enter>", lambda _: self._canvas.bind_all("<MouseWheel>", self._on_wheel))
        self._canvas.bind("<Leave>", lambda _: self._canvas.unbind_all("<MouseWheel>"))

    def _on_wheel(self, event: Event) -> None:
        if event.delta:
            self._canvas.yview_scroll(int(-event.delta / 120), "units")

    def refresh(self) -> None:
        for row in self._rows:
            row.destroy()
        self._rows.clear()

        items = self._manager.list()
        if not items:
            empty = ttk.Label(self._list_frame, text="暂无服务器", foreground="gray", anchor="center")
            empty.grid(row=0, column=0, pady=48, sticky="ew")
            self._rows.append(empty)
            return

        for index, server in enumerate(items):
            self._rows.append(self._build_row(index, server))

    def _build_row(self, index: int, server: ServerConfig) -> tk.Widget:
        row = ttk.Frame(self._list_frame, padding=(10, 8), relief="solid", borderwidth=1)
        row.grid(row=index, column=0, sticky="ew", padx=6, pady=3)
        row.columnconfigure(0, weight=1)

        name = ttk.Label(row, text=server.display_name, anchor="w", font=("", 10, "bold"))
        name.grid(row=0, column=0, sticky="ew")
        endpoint = ttk.Label(row, text=server.endpoint, foreground="gray", anchor="w")
        endpoint.grid(row=1, column=0, sticky="ew")

        buttons = _ServerButtonGroup(row, server, on_connect=self._on_connect, on_edit=self._on_edit, on_delete=self._on_delete)
        buttons.grid(row=0, column=1, rowspan=2, sticky="e", padx=(12, 0))

        connect = lambda _=None, s=server: self._on_connect(s)
        for widget in (row, name, endpoint):
            widget.bind("<Double-Button-1>", connect)

        return row


def launch_putty(server: ServerConfig) -> None:
    proc = subprocess.Popen([PUTTY_EXE, "-ssh", "-P", str(server.port), "-l", server.username, "-pw", server.password, server.host])

    deadline = time.monotonic() + FOCUS_TIMEOUT
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return

        try:
            app = Application(backend="win32").connect(process=proc.pid)
            window = app.top_window()
            window.set_focus()
            return

        except (ElementNotFoundError, RuntimeError):
            time.sleep(FOCUS_POLL)


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("PuTTY SSH Manager")
        self.geometry("560x540")
        self.minsize(440, 380)
        self.manager: ServerManager = ServerManager()
        self._build_ui()

    def _build_ui(self) -> None:
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")

        outer = ttk.Frame(self, padding=(12, 12, 12, 8))
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)

        header = ttk.Frame(outer)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="服务器列表", font=("", 12, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Button(header, text="刷新", width=6, command=self._refresh).grid(row=0, column=1, sticky="e")

        self.selector = ServerSelector(outer, self.manager, on_connect=self._on_connect, on_edit=self._on_edit, on_delete=self._on_delete)
        self.selector.grid(row=1, column=0, sticky="nsew")

        footer = ttk.Frame(self, padding=(12, 0, 12, 10))
        footer.pack(fill="x")
        ttk.Button(footer, text="添加服务器", command=self._on_add).pack(side="right")

    def _refresh(self) -> None:
        self.selector.refresh()

    def _open_form(
        self,
        *,
        title: str,
        initial: Optional[dict[str, Any]],
        ok_text: str,
    ) -> Optional[ServerConfig]:
        data = FormDialog(self, title=title, fields=FIELD_DEFS, initial=initial, ok_text=ok_text, validator=validate_server).show()
        return ServerConfig.from_dict(data) if data is not None else None

    def _on_add(self) -> None:
        server = self._open_form(title="添加服务器", initial={"port": "22"}, ok_text="添加")
        if server is None:
            return

        self.manager.add(server)
        self._refresh()

    def _on_edit(self, server: ServerConfig) -> None:
        replacement = self._open_form(title="编辑服务器", initial=server.to_dict(), ok_text="保存")
        if replacement is None:
            return

        if not self.manager.update(server, replacement):
            self._refresh()
            return

        self._refresh()

    def _on_delete(self, server: ServerConfig) -> None:
        if not messagebox.askyesno("删除", f"确定删除 {server.display_name} 吗", parent=self):
            return

        if self.manager.remove(server):
            self._refresh()

    def _on_connect(self, server: ServerConfig) -> None:
        try:
            launch_putty(server)
        except FileNotFoundError:
            messagebox.showerror("错误", "未找到 putty.exe，请确认已安装并在 PATH 中", parent=self)


def main() -> None:
    App().mainloop()


if __name__ == "__main__":
    main()
