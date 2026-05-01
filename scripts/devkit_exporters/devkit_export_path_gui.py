r"""Small local GUI for configuring the ARK DevKit defaults exporter.

Run from Windows PowerShell:

python scripts\devkit_exporters\devkit_export_path_gui.py

Paste a Blueprint reference/Object Path. The GUI writes
captures/_devkit_export_request.json, which export_current_blueprint_defaults.py
reads automatically inside ARK DevKit.
"""

from __future__ import annotations

import json
import os
import re
import tkinter as tk
from tkinter import messagebox


PROJECT_ROOT = r"C:\Users\ac\Documents\project gaming\Blueprint to Code"
CAPTURE_ROOT = os.path.join(PROJECT_ROOT, "captures")
REQUEST_PATH = os.path.join(CAPTURE_ROOT, "_devkit_export_request.json")
EXPORT_SCRIPT = os.path.join(PROJECT_ROOT, "scripts", "devkit_exporters", "export_current_blueprint_defaults.py")


def normalize_asset_path(raw_text: str) -> str:
    text = str(raw_text or "").strip().replace("\\", "/").strip("\"'")
    quoted = re.search(r"['\"](?P<path>/Game/[^'\"]+)['\"]", text)
    if quoted:
        text = quoted.group("path").strip()
    path_match = re.search(r"(?P<path>/Game/[^\s,'\"]+)", text)
    if path_match:
        text = path_match.group("path").strip()
    text = text.strip("\"'")
    if not text.startswith("/Game/"):
        return ""
    if "." in text and text.endswith("_C"):
        package, obj = text.rsplit(".", 1)
        text = package + "." + obj[:-2]
    if "." not in text:
        object_name = text.rsplit("/", 1)[-1]
        if object_name:
            text = text + "." + object_name
    return text


def write_request(asset_path: str) -> None:
    os.makedirs(CAPTURE_ROOT, exist_ok=True)
    payload = {
        "schema": "blueprint-translator.devkit-export-request.v1",
        "asset_path": asset_path,
    }
    with open(REQUEST_PATH, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def load_existing() -> str:
    if not os.path.isfile(REQUEST_PATH):
        return ""
    try:
        with open(REQUEST_PATH, "r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            return str(data.get("asset_path") or "")
    except Exception:
        return ""
    return ""


def devkit_python_console_command() -> str:
    return 'exec(open(r"{}", encoding="utf-8").read())'.format(EXPORT_SCRIPT)


def devkit_output_log_command() -> str:
    return 'py exec(open(r"{}", encoding="utf-8").read())'.format(EXPORT_SCRIPT)


def main() -> int:
    root = tk.Tk()
    root.title("ARK Blueprint Defaults Export Path")
    root.geometry("820x330")

    description = (
        "Paste the Blueprint reference or Object Path copied from ARK DevKit.\n"
        "After saving, run the Python Console command below inside ARK DevKit.\n"
        "The exporter runs in crash-safe mode: it exports defaults and component candidates only."
    )
    tk.Label(root, text=description, justify="left", anchor="w").pack(fill="x", padx=14, pady=(14, 8))

    text_box = tk.Text(root, height=5, wrap="word")
    text_box.pack(fill="both", expand=True, padx=14)
    existing = load_existing()
    if existing:
        text_box.insert("1.0", existing)

    command_var = tk.StringVar(value=devkit_python_console_command())
    tk.Label(root, text="DevKit Python Console command:", anchor="w").pack(fill="x", padx=14, pady=(10, 2))
    command_entry = tk.Entry(root, textvariable=command_var)
    command_entry.pack(fill="x", padx=14)

    output_log_var = tk.StringVar(value=devkit_output_log_command())
    tk.Label(root, text="Output Log / command mode alternative:", anchor="w").pack(fill="x", padx=14, pady=(8, 2))
    output_entry = tk.Entry(root, textvariable=output_log_var)
    output_entry.pack(fill="x", padx=14)

    status_var = tk.StringVar(value="Request file: {}".format(REQUEST_PATH))
    tk.Label(root, textvariable=status_var, anchor="w").pack(fill="x", padx=14, pady=(8, 0))

    def save() -> None:
        asset_path = normalize_asset_path(text_box.get("1.0", "end").strip())
        if not asset_path:
            messagebox.showwarning("Invalid path", "Paste a path that starts with /Game/.")
            return
        write_request(asset_path)
        status_var.set("Saved: {}".format(asset_path))
        try:
            root.clipboard_clear()
            root.clipboard_append(devkit_python_console_command())
        except Exception:
            pass
        messagebox.showinfo("Saved", "Export request saved. The Python Console command was copied to clipboard.")

    def copy_command() -> None:
        try:
            root.clipboard_clear()
            root.clipboard_append(devkit_python_console_command())
            status_var.set("Python Console command copied to clipboard.")
        except Exception as exc:
            messagebox.showerror("Copy failed", str(exc))

    def copy_output_log_command() -> None:
        try:
            root.clipboard_clear()
            root.clipboard_append(devkit_output_log_command())
            status_var.set("Output Log command copied to clipboard.")
        except Exception as exc:
            messagebox.showerror("Copy failed", str(exc))

    buttons = tk.Frame(root)
    buttons.pack(fill="x", padx=14, pady=12)
    tk.Button(buttons, text="Save Path", command=save, width=16).pack(side="left")
    tk.Button(buttons, text="Copy Python Console", command=copy_command, width=22).pack(side="left", padx=8)
    tk.Button(buttons, text="Copy Output Log", command=copy_output_log_command, width=18).pack(side="left")
    tk.Button(buttons, text="Close", command=root.destroy, width=12).pack(side="right")
    root.bind("<Control-Return>", lambda _event: save())
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
