#!/usr/bin/env python3
"""Interfaz visual local y multiplataforma para conversation-reclaim."""

import io
import queue
import sys
import threading
import traceback
from contextlib import redirect_stdout
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import reclaim


CATEGORY_ORDER = ("claude", "codex", "opencode_files", "opencode_db",
                  "antigravity")


def _safe_scan(fn):
    try:
        return fn(), None
    except (OSError, ValueError, reclaim.sqlite3.Error) as exc:
        return None, str(exc)


def scan_categories():
    """Devuelve categorías independientes para UI, sin modificar datos."""
    claude, claude_error = _safe_scan(reclaim.scan_claude)
    codex, codex_error = _safe_scan(reclaim.scan_codex)
    opencode, opencode_error = _safe_scan(reclaim.scan_opencode)
    antigravity, antigravity_error = _safe_scan(reclaim.scan_antigravity)

    claude = claude or {}
    codex = codex or {}
    opencode = opencode or {}
    antigravity = antigravity or {}

    opencode_files = sum(reclaim.dir_size(reclaim.PATHS["opencode_dir"] / name)
                         for name in ("snapshot", "tool-output", "log"))
    codex_cache = (reclaim.dir_size(reclaim.PATHS["codex_cache"]) +
                   reclaim.dir_size(reclaim.PATHS["codex_logs"]))
    categories = [
        {
            "key": "claude",
            "title": "Claude Code",
            "bytes": (claude.get("reclaim", 0) + claude.get("subagents_bytes", 0) +
                      claude.get("workflows_bytes", 0)),
            "recommended": True,
            "detail": "Compactaciones y sidechains cerrados; conserva resumen, reciente y memory.",
            "note": f"{claude.get('subagents_n', 0)} subagentes cerrados",
            "note_es": f"{claude.get('subagents_n', 0)} subagentes cerrados",
            "note_en": f"{claude.get('subagents_n', 0)} closed subagents",
            "error": claude_error,
        },
        {
            "key": "codex",
            "title": "Codex",
            "bytes": (codex.get("reclaim", 0) + codex.get("subagents_bytes", 0) +
                      codex_cache),
            "recommended": True,
            "detail": "Sesiones compactadas, hijos cerrados y caches; omite esta tarea y archivos activos.",
            "note": (f"{codex.get('subagents_n', 0)} cerrados · "
                     f"{codex.get('active_subagents', 0)} activos protegidos"),
            "note_es": (f"{codex.get('subagents_n', 0)} cerrados · "
                        f"{codex.get('active_subagents', 0)} activos protegidos"),
            "note_en": (f"{codex.get('subagents_n', 0)} closed · "
                        f"{codex.get('active_subagents', 0)} active and protected"),
            "error": codex_error,
        },
        {
            "key": "opencode_files",
            "title": "OpenCode · archivos temporales",
            "bytes": opencode_files,
            "recommended": True,
            "detail": "Snapshots locales, tool-output y logs reconstruibles.",
            "note": "No modifica opencode.db",
            "note_es": "No modifica opencode.db",
            "note_en": "Does not modify opencode.db",
            "error": None,
        },
        {
            "key": "opencode_db",
            "title": "OpenCode · base de conversaciones",
            "bytes": (opencode.get("reclaim", 0) +
                      ((opencode.get("redundant") or (0, 0))[1] or 0)),
            "recommended": True,
            "detail": "Eventos de streaming redundantes y mensajes anteriores a compactación.",
            "note": "Requiere OpenCode cerrado; puede cerrarlo con aviso en macOS",
            "note_es": "Requiere OpenCode cerrado; puede cerrarlo con aviso en macOS",
            "note_en": "Requires OpenCode to be closed; macOS can quit it after warning",
            "error": opencode_error,
        },
        {
            "key": "antigravity",
            "title": "Antigravity / Gemini",
            "bytes": (antigravity.get("reclaim", 0) + antigravity.get("scratch", 0) +
                      antigravity.get("recordings", 0)),
            "recommended": True,
            "detail": "Compactaciones, scratch, caches, logs y capturas browser ya consumidas.",
            "note": (f"{antigravity.get('compacted', 0)} conversaciones compactadas · "
                     f"recordings {reclaim.human(antigravity.get('recordings', 0))}"),
            "note_es": (f"{antigravity.get('compacted', 0)} conversaciones compactadas · "
                        f"capturas {reclaim.human(antigravity.get('recordings', 0))}"),
            "note_en": (f"{antigravity.get('compacted', 0)} compacted conversations · "
                        f"recordings {reclaim.human(antigravity.get('recordings', 0))}"),
            "error": antigravity_error,
        },
    ]
    for category in categories:
        category["selected"] = bool(category["recommended"] and
                                    category["bytes"] > 0 and not category["error"])
    return categories


def run_cleanup(keys, backup_dir=None, close_opencode=True):
    """Ejecuta solo las categorías seleccionadas y devuelve el resultado."""
    chosen = set(keys)
    unknown = chosen.difference(CATEGORY_ORDER)
    if unknown:
        raise ValueError("categorías desconocidas: " + ", ".join(sorted(unknown)))
    backup_path = None
    if backup_dir:
        backup_path = reclaim.backup(backup_dir)

    freed = 0
    manifest_entries = []
    if "claude" in chosen:
        amount, entries = reclaim.apply_claude()
        freed += amount
        manifest_entries.extend(entries)
    if "codex" in chosen:
        for fn in (reclaim.apply_codex, reclaim.apply_codex_cache):
            amount, entries = fn()
            freed += amount
            manifest_entries.extend(entries)
    if "opencode_files" in chosen:
        amount, entries = reclaim.apply_opencode_files()
        freed += amount
        manifest_entries.extend(entries)
    if "antigravity" in chosen:
        amount, entries = reclaim.apply_antigravity(steps=True)
        freed += amount
        manifest_entries.extend(entries)

    manifest_path = reclaim.write_manifest(manifest_entries) if manifest_entries else None
    db_code = None
    if "opencode_db" in chosen:
        before = reclaim.PATHS["opencode_db"].stat().st_size \
            if reclaim.PATHS["opencode_db"].exists() else 0
        db_code = reclaim.prune_opencode_db(
            backup_dir=None, no_backup=True, close_opencode=close_opencode)
        after = reclaim.PATHS["opencode_db"].stat().st_size \
            if reclaim.PATHS["opencode_db"].exists() else before
        if db_code == 0:
            freed += max(0, before - after)
    return {
        "freed": freed,
        "manifest": str(manifest_path) if manifest_path else None,
        "backup": str(backup_path) if backup_path else None,
        "db_code": db_code,
        "success": db_code in (None, 0),
    }


class QueueWriter(io.TextIOBase):
    def __init__(self, events):
        self.events = events

    def write(self, text):
        if text:
            self.events.put(("log", text))
        return len(text)

    def flush(self):
        return None


class ReclaimApp:
    BG = "#F4F6F8"
    CARD = "#FFFFFF"
    TEXT = "#17202A"
    MUTED = "#667085"
    GREEN = "#197A52"
    GREEN_LIGHT = "#E8F5EE"
    BLUE = "#276EF1"
    BORDER = "#D9E0E7"

    def __init__(self, root):
        self.root = root
        self.events = queue.Queue()
        self.categories = []
        self.variables = {}
        self.busy = False
        self._configure_window()
        self._build_ui()
        self.root.after(100, self._drain_events)
        self.scan()

    def _configure_window(self):
        self.root.title("Conversation Reclaim")
        self.root.geometry("980x760")
        self.root.minsize(820, 650)
        self.root.configure(bg=self.BG)
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Primary.TButton", font=("TkDefaultFont", 11, "bold"),
                        padding=(18, 10), foreground="white", background=self.BLUE)
        style.map("Primary.TButton", background=[("active", "#1758C7"),
                                                  ("disabled", "#AAB8D0")])
        style.configure("Secondary.TButton", padding=(12, 8))
        style.configure("Clean.Horizontal.TProgressbar", background=self.BLUE,
                        troughcolor="#E2E8F0")

    def _build_ui(self):
        outer = tk.Frame(self.root, bg=self.BG, padx=28, pady=22)
        outer.pack(fill="both", expand=True)

        header = tk.Frame(outer, bg=self.BG)
        header.pack(fill="x")
        tk.Label(header, text="Conversation Reclaim", bg=self.BG, fg=self.TEXT,
                 font=("TkDefaultFont", 23, "bold")).pack(anchor="w")
        tk.Label(header, text="Revisa, selecciona y libera espacio sin tocar datos activos.",
                 bg=self.BG, fg=self.MUTED, font=("TkDefaultFont", 11)).pack(anchor="w", pady=(4, 0))

        summary = tk.Frame(outer, bg=self.CARD, highlightbackground=self.BORDER,
                           highlightthickness=1, padx=18, pady=14)
        summary.pack(fill="x", pady=(18, 14))
        self.summary_value = tk.Label(summary, text="Escaneando…", bg=self.CARD,
                                      fg=self.TEXT, font=("TkDefaultFont", 20, "bold"))
        self.summary_value.pack(side="left")
        self.summary_label = tk.Label(summary, text=" recuperables con la selección actual",
                                      bg=self.CARD, fg=self.MUTED,
                                      font=("TkDefaultFont", 10))
        self.summary_label.pack(side="left", padx=(6, 0), pady=(7, 0))
        ttk.Button(summary, text="Escanear de nuevo", style="Secondary.TButton",
                   command=self.scan).pack(side="right")

        controls = tk.Frame(outer, bg=self.BG)
        controls.pack(fill="x", pady=(0, 8))
        ttk.Button(controls, text="Marcar recomendado", command=self.select_recommended,
                   style="Secondary.TButton").pack(side="left")
        ttk.Button(controls, text="Desmarcar todo", command=self.clear_selection,
                   style="Secondary.TButton").pack(side="left", padx=(8, 0))

        self.category_frame = tk.Frame(outer, bg=self.BG)
        self.category_frame.pack(fill="x")

        options = tk.Frame(outer, bg=self.CARD, highlightbackground=self.BORDER,
                           highlightthickness=1, padx=16, pady=12)
        options.pack(fill="x", pady=(14, 10))
        tk.Label(options, text="Respaldo externo opcional", bg=self.CARD, fg=self.TEXT,
                 font=("TkDefaultFont", 10, "bold")).grid(row=0, column=0, sticky="w")
        self.backup_var = tk.StringVar()
        ttk.Entry(options, textvariable=self.backup_var).grid(row=1, column=0, sticky="ew", pady=(6, 0))
        ttk.Button(options, text="Elegir…", command=self.choose_backup).grid(row=1, column=1,
                                                                             padx=(8, 0), pady=(6, 0))
        self.close_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(options, variable=self.close_var,
                        text="Avisar y cerrar OpenCode normalmente si bloquea su base").grid(
                            row=2, column=0, columnspan=2, sticky="w", pady=(10, 0))
        options.columnconfigure(0, weight=1)

        log_box = tk.Frame(outer, bg=self.CARD, highlightbackground=self.BORDER,
                           highlightthickness=1)
        log_box.pack(fill="both", expand=True, pady=(0, 12))
        self.log = tk.Text(log_box, height=7, wrap="word", relief="flat", padx=12,
                           pady=10, bg="#FBFCFD", fg="#344054",
                           font=("Menlo" if self.root.tk.call("tk", "windowingsystem") == "aqua"
                                 else "TkFixedFont", 9), state="disabled")
        self.log.pack(fill="both", expand=True)

        footer = tk.Frame(outer, bg=self.BG)
        footer.pack(fill="x")
        self.progress = ttk.Progressbar(footer, mode="indeterminate",
                                        style="Clean.Horizontal.TProgressbar", length=180)
        self.progress.pack(side="left")
        self.status = tk.Label(footer, text="Preparando escaneo…", bg=self.BG,
                               fg=self.MUTED)
        self.status.pack(side="left", padx=(10, 0))
        self.apply_button = ttk.Button(footer, text="Liberar espacio seleccionado",
                                       style="Primary.TButton", command=self.confirm_apply)
        self.apply_button.pack(side="right")

    def _render_categories(self):
        for child in self.category_frame.winfo_children():
            child.destroy()
        self.variables.clear()
        for index, category in enumerate(self.categories):
            card = tk.Frame(self.category_frame, bg=self.CARD,
                            highlightbackground=self.BORDER, highlightthickness=1,
                            padx=14, pady=11)
            card.pack(fill="x", pady=(0 if index == 0 else 7, 0))
            var = tk.BooleanVar(value=category["selected"])
            self.variables[category["key"]] = var
            check = ttk.Checkbutton(card, variable=var, command=self._update_summary)
            check.grid(row=0, column=0, rowspan=2, sticky="n", padx=(0, 9))
            tk.Label(card, text=category["title"], bg=self.CARD, fg=self.TEXT,
                     font=("TkDefaultFont", 11, "bold")).grid(row=0, column=1, sticky="w")
            badge_text = "RECOMENDADO" if category["recommended"] else "REVISAR"
            badge_bg = self.GREEN_LIGHT if category["recommended"] else "#FFF4E5"
            badge_fg = self.GREEN if category["recommended"] else "#9A5B00"
            tk.Label(card, text=badge_text, bg=badge_bg, fg=badge_fg,
                     font=("TkDefaultFont", 8, "bold"), padx=7, pady=2).grid(
                         row=0, column=2, sticky="w", padx=(10, 0))
            tk.Label(card, text=reclaim.human(category["bytes"]), bg=self.CARD,
                     fg=self.TEXT, font=("TkDefaultFont", 13, "bold")).grid(
                         row=0, column=3, rowspan=2, sticky="e", padx=(15, 0))
            detail = category["error"] or category["detail"]
            detail_color = "#B42318" if category["error"] else self.MUTED
            tk.Label(card, text=detail, bg=self.CARD, fg=detail_color,
                     anchor="w", justify="left", wraplength=620).grid(
                         row=1, column=1, columnspan=2, sticky="w", pady=(4, 0))
            tk.Label(card, text=category["note"], bg=self.CARD, fg="#98A2B3",
                     font=("TkDefaultFont", 8)).grid(row=2, column=1, columnspan=3,
                                                     sticky="w", pady=(4, 0))
            card.columnconfigure(1, weight=1)
        self._update_summary()

    def _selected_keys(self):
        return [key for key in CATEGORY_ORDER
                if key in self.variables and self.variables[key].get()]

    def _update_summary(self):
        selected = set(self._selected_keys())
        total = sum(c["bytes"] for c in self.categories if c["key"] in selected)
        self.summary_value.configure(text=reclaim.human(total))

    def select_recommended(self):
        for category in self.categories:
            if category["key"] in self.variables:
                self.variables[category["key"]].set(
                    bool(category["recommended"] and category["bytes"] > 0 and
                         not category["error"]))
        self._update_summary()

    def clear_selection(self):
        for var in self.variables.values():
            var.set(False)
        self._update_summary()

    def choose_backup(self):
        path = filedialog.askdirectory(title="Destino del respaldo")
        if path:
            self.backup_var.set(path)

    def _set_busy(self, busy, text):
        self.busy = busy
        self.status.configure(text=text)
        self.apply_button.configure(state="disabled" if busy else "normal")
        if busy:
            self.progress.start(12)
        else:
            self.progress.stop()

    def scan(self):
        if self.busy:
            return
        self._set_busy(True, "Escaneando sin modificar archivos…")
        threading.Thread(target=self._scan_worker, daemon=True).start()

    def _scan_worker(self):
        try:
            self.events.put(("scan_done", scan_categories()))
        except Exception:
            self.events.put(("error", traceback.format_exc()))

    def confirm_apply(self):
        if self.busy:
            return
        keys = self._selected_keys()
        if not keys:
            messagebox.showinfo("Nada seleccionado", "Marca al menos una categoría.")
            return
        selected = [c for c in self.categories if c["key"] in keys]
        lines = "\n".join(f"• {c['title']}: {reclaim.human(c['bytes'])}" for c in selected)
        backup = self.backup_var.get().strip()
        warning = ("\n\nNo se creó un respaldo externo. Los cambios quedarán en el manifiesto."
                   if not backup else f"\n\nRespaldo previo en: {backup}")
        if not messagebox.askyesno(
                "Confirmar limpieza",
                f"Se aplicarán estas categorías:\n\n{lines}{warning}\n\n¿Continuar?"):
            return
        self._set_busy(True, "Aplicando selección…")
        self._append_log("\n=== Limpieza iniciada ===\n")
        threading.Thread(target=self._apply_worker,
                         args=(keys, backup or None, self.close_var.get()),
                         daemon=True).start()

    def _apply_worker(self, keys, backup, close_opencode):
        writer = QueueWriter(self.events)
        try:
            with redirect_stdout(writer):
                result = run_cleanup(keys, backup, close_opencode)
            self.events.put(("apply_done", result))
        except Exception:
            self.events.put(("error", traceback.format_exc()))

    def _append_log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _drain_events(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self._append_log(payload)
                elif kind == "scan_done":
                    self.categories = payload
                    self._render_categories()
                    self._set_busy(False, "Escaneo terminado. Revisa la selección.")
                elif kind == "apply_done":
                    self._set_busy(False, "Limpieza terminada." if payload["success"]
                                   else "Limpieza parcial; revisa el registro.")
                    message = f"Espacio liberado: {reclaim.human(payload['freed'])}"
                    if payload["manifest"]:
                        message += f"\nManifiesto: {payload['manifest']}"
                    if payload["backup"]:
                        message += f"\nRespaldo: {payload['backup']}"
                    if payload["db_code"] not in (None, 0):
                        message += f"\nOpenCode DB terminó con código {payload['db_code']}."
                    messagebox.showinfo("Resultado", message)
                    self.scan()
                elif kind == "error":
                    self._append_log(payload)
                    self._set_busy(False, "Ocurrió un error; revisa el registro.")
                    messagebox.showerror("Error", "La operación falló sin continuar. Revisa el registro.")
        except queue.Empty:
            pass
        self.root.after(100, self._drain_events)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--smoke-test"]:
        return 0 if len(scan_categories()) == len(CATEGORY_ORDER) else 2
    if args:
        print("Uso: gui.py [--smoke-test]")
        return 2
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print(f"No se pudo abrir la interfaz gráfica: {exc}")
        return 1
    ReclaimApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
