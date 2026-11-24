"""Окно программы: выбрать месяц, добавить файлы Airbnb / Booking, нажать «Сделать отчёт»."""
import os
import subprocess
import sys
import traceback
from datetime import date
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
import tkinter as tk

from . import settings
from .core import LUNI, SUPPORTED, detect_platform, generate, previous_month

KIND_LABEL = {"airbnb": "Airbnb", "booking": "Booking", "booking_pdf": "Booking (PDF)", None: "❌ не распознан"}


def open_folder(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def month_choices(today: date, n: int = 14) -> list[str]:
    out, y, m = [], today.year, today.month
    for _ in range(n):
        out.append(f"{y}-{m:02d}  ({LUNI[m]} {y})")
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return out


class SettingsDialog(tk.Toplevel):
    FIELDS = [("Название фирмы (SRL)", ("company", "name")), ("CIF (точно как в SAGA)", ("company", "cif")),
              ("Nr. Reg. Com.", ("company", "reg_com")), ("Адрес", ("company", "address")),
              ("Банк", ("company", "bank")), ("IBAN", ("company", "iban")),
              ("Серия документов (как в SAGA)", ("saga", "series"))]

    def __init__(self, master, cfg: dict):
        super().__init__(master)
        self.title("Данные фирмы")
        self.cfg, self.saved = cfg, False
        self.resizable(False, False)
        self.transient(master)
        f = ttk.Frame(self, padding=16)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="Эти данные попадут в документы для SAGA. Заполняются один раз.",
                  foreground="#555").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
        self.vars = {}
        for i, (label, (sec, key)) in enumerate(self.FIELDS, 1):
            ttk.Label(f, text=label).grid(row=i, column=0, sticky="w", pady=3, padx=(0, 10))
            v = tk.StringVar(value=str(cfg[sec].get(key, "")))
            ttk.Entry(f, textvariable=v, width=42).grid(row=i, column=1, sticky="we", pady=3)
            self.vars[(sec, key)] = v
        r = len(self.FIELDS) + 1
        ttk.Label(f, text="Следующий номер документа").grid(row=r, column=0, sticky="w", pady=3, padx=(0, 10))
        self.number = tk.StringVar(value=str(settings.next_number(cfg)))
        ttk.Entry(f, textvariable=self.number, width=16, font=("Segoe UI", 11, "bold")).grid(
            row=r, column=1, sticky="w", pady=3)
        ttk.Label(f, text="только число: последний номер в SAGA + 1 (VA419 → 420)", foreground="#777").grid(
            row=r + 1, column=1, sticky="w")
        ttk.Label(f, text="TVA (НДС), %").grid(row=r + 2, column=0, sticky="w", pady=3)
        self.vat = tk.StringVar(value=str(cfg.get("vat_rate", 0)))
        ttk.Combobox(f, textvariable=self.vat, values=["0", "11", "21"], width=10).grid(
            row=r + 2, column=1, sticky="w", pady=3)
        ttk.Label(f, text="0 — если фирма не плательщик TVA", foreground="#777").grid(
            row=r + 3, column=1, sticky="w")
        b = ttk.Frame(f)
        b.grid(row=r + 4, column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(b, text="Отмена", command=self.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(b, text="Сохранить", command=self.on_save).pack(side="right")
        self.grab_set()

    def on_save(self):
        try:
            number = int("".join(ch for ch in self.number.get() if ch.isdigit()))
            vat = float(self.vat.get().strip().replace(",", ".") or 0)
        except ValueError:
            messagebox.showerror("Ошибка", "Номер документа и TVA должны быть числами.", parent=self)
            return
        if not self.vars[("company", "name")].get().strip():
            messagebox.showerror("Ошибка", "Впишите название фирмы.", parent=self)
            return
        for (sec, key), v in self.vars.items():
            self.cfg[sec][key] = v.get().strip()
        self.cfg["vat_rate"] = int(vat) if vat.is_integer() else vat
        settings.save(self.cfg)
        settings.set_next_number(number)
        self.saved = True
        self.destroy()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Raport cazare — Booking + Airbnb → SAGA")
        self.minsize(640, 520)
        self.cfg = settings.load()
        self.files: list[Path] = []
        self.last_dir = str(Path.home() / "Downloads")

        f = ttk.Frame(self, padding=18)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="Отчёт для бухгалтера", font=("Segoe UI", 15, "bold")).pack(anchor="w")
        ttk.Label(f, text="Добавьте файлы из Airbnb и Booking за месяц и нажмите «Сделать отчёт».",
                  foreground="#555").pack(anchor="w", pady=(2, 14))

        row = ttk.Frame(f)
        row.pack(fill="x")
        ttk.Label(row, text="1. Месяц:", font=("Segoe UI", 10, "bold")).pack(side="left")
        choices = month_choices(date.today())
        self.month = tk.StringVar(value=next(c for c in choices if c.startswith(previous_month(date.today()))))
        ttk.Combobox(row, textvariable=self.month, values=choices, state="readonly", width=28).pack(
            side="left", padx=10)

        ttk.Label(f, text="2. Файлы (CSV из Airbnb, PDF или Excel из Booking):",
                  font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(16, 4))
        self.tree = ttk.Treeview(f, columns=("kind",), height=6, selectmode="extended")
        self.tree.heading("#0", text="Файл")
        self.tree.heading("kind", text="Что это")
        self.tree.column("#0", width=430)
        self.tree.column("kind", width=140)
        self.tree.pack(fill="both", expand=True)
        btns = ttk.Frame(f)
        btns.pack(fill="x", pady=(6, 0))
        ttk.Button(btns, text="➕ Добавить файлы…", command=self.add_files).pack(side="left")
        ttk.Button(btns, text="Убрать выбранные", command=self.remove_files).pack(side="left", padx=6)

        ttk.Label(f, text="3.", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(16, 0))
        style = ttk.Style(self)
        style.configure("Big.TButton", font=("Segoe UI", 12, "bold"), padding=10)
        ttk.Button(f, text="Сделать отчёт", style="Big.TButton", command=self.run).pack(fill="x")

        self.next_doc = ttk.Label(f, font=("Segoe UI", 10, "bold"), foreground="#1a5fb4")
        self.next_doc.pack(anchor="w", pady=(10, 0))
        self.refresh_next_doc()

        bottom = ttk.Frame(f)
        bottom.pack(fill="x", pady=(14, 0))
        ttk.Button(bottom, text="⚙ Данные фирмы…", command=self.edit_settings).pack(side="left")
        ttk.Button(bottom, text="📂 Папка с отчётами",
                   command=lambda: open_folder(settings.reports_dir())).pack(side="right")

        if not settings.is_configured(self.cfg):
            self.after(300, self.first_run)

    def refresh_next_doc(self):
        series = self.cfg.get("saga", {}).get("series", "")
        self.next_doc.config(text=f"Следующий документ для SAGA: {series}{settings.next_number(self.cfg)}"
                                  "   (изменить — «Данные фирмы»)")

    def first_run(self):
        messagebox.showinfo("Добро пожаловать",
                            "Сначала один раз впишите данные фирмы — они нужны для документов SAGA.")
        self.edit_settings()

    def edit_settings(self):
        d = SettingsDialog(self, settings.load())
        self.wait_window(d)
        self.cfg = settings.load()
        self.refresh_next_doc()

    def add_files(self):
        paths = filedialog.askopenfilenames(
            parent=self, title="Выберите файлы из Airbnb и Booking", initialdir=self.last_dir,
            filetypes=[("Airbnb / Booking", " ".join(f"*{e}" for e in SUPPORTED)), ("Все файлы", "*.*")])
        for p in map(Path, paths):
            if p in self.files:
                continue
            self.last_dir = str(p.parent)
            kind = detect_platform(p)
            self.files.append(p)
            self.tree.insert("", "end", iid=str(p), text=p.name, values=(KIND_LABEL.get(kind, "?"),))

    def remove_files(self):
        for iid in self.tree.selection():
            self.tree.delete(iid)
            self.files = [p for p in self.files if str(p) != iid]

    def run(self):
        if not settings.is_configured(self.cfg):
            self.edit_settings()
            if not settings.is_configured(self.cfg):
                return
        if not self.files:
            messagebox.showwarning("Нет файлов", "Нажмите «Добавить файлы…» и выберите выгрузки Airbnb и Booking.")
            return
        month = self.month.get()[:7]
        out_dir = settings.reports_dir() / month
        self.config(cursor="watch")
        self.update()
        try:
            res = generate(dict(self.cfg), self.files, month, out_dir, settings.state_path(),
                           settings.app_dir() / "cache", log=lambda s: None)
        except Exception as e:  # noqa: BLE001
            log = settings.app_dir() / "error.log"
            log.write_text(traceback.format_exc(), encoding="utf-8")
            messagebox.showerror("Ошибка", f"Не получилось сделать отчёт:\n\n{e}\n\nПодробности: {log}")
            return
        finally:
            self.config(cursor="")
        text = res.summary()
        if res.warnings or res.notes:
            text += "\n\nОбратите внимание:\n" + "\n".join(f"• {w}" for w in res.warnings + res.notes)
        text += f"\n\nФайлы сохранены в папку:\n{out_dir}"
        self.refresh_next_doc()
        (messagebox.showwarning if res.warnings else messagebox.showinfo)("Отчёт готов", text)
        open_folder(out_dir)


def run() -> None:
    App().mainloop()
