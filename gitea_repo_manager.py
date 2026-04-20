#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gitea Repository Manager v1.5
==============================
Verwaltung von Gitea-Repositories inkl.:
  - git clone mit Live-Ausgabe und Editor-Auswahl
  - Vorhandene-Repos-Dialog mit git pull Option
  - Push-Assistent: Dateivergleich, LINT, PUSH, PUSH2BRANCH, AUTOcommit
  - Bulk-Clone: gesamte Organisation oder gesamtes Gitea mit allen Branches

Verwendung:        python3 gitea_manager.py
Abhaengigkeiten:   pip install requests
System:            sudo apt install git meld
"""

import tkinter as tk
from tkinter import ttk, messagebox, simpledialog, filedialog
import json
import os
import shutil
import subprocess
import threading
import time
import requests
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, urlunparse

# ---------------------------------------------------------------------------
# Konstanten
# ---------------------------------------------------------------------------
CONFIG_DIR          = Path.home() / ".config" / "gitea-manager"
CONFIG_FILE         = CONFIG_DIR / "config.json"
CLONE_ZIEL          = Path("/home/guest/code")
DIFF_PROGRAMM_STD   = "meld"
TMP_VERGLEICH_BASE  = Path("/tmp/repocompare")

GITIGNORE_TEMPLATES = [
    "", "Python", "Node", "Go", "Java", "Rust", "C", "C++",
    "Ruby", "PHP", "Swift", "Kotlin", "Dart", "Unity",
]
LIZENZEN = [
    "", "MIT", "Apache-2.0", "GPL-3.0", "LGPL-3.0",
    "BSD-2-Clause", "BSD-3-Clause", "MPL-2.0", "AGPL-3.0",
]
EDITOREN = [
    ("VS Code", "code"),
    ("Geany",   "geany"),
    ("Pulsar",  "pulsar"),
]
DATEIMANAGER = [
     "caja", "thunar", "nautilus", "nemo", "pcmanfm", "dolphin", "xdg-open",
]
# Terminals werden der Reihe nach probiert; jedes Element: (befehl, [extra_args_vor_cmd])
TERMINALS = [
    ("x-terminal-emulator", ["-e"]),
    ("gnome-terminal",      ["--"]),
    ("xfce4-terminal",      ["-e"]),
    ("konsole",             ["-e"]),
    ("xterm",               ["-e"]),
]

C = {
    "bg":      "#1e1e2e",
    "bg2":     "#2a2a3e",
    "bg3":     "#313145",
    "accent":  "#89b4fa",
    "accent2": "#cba6f7",
    "success": "#a6e3a1",
    "warning": "#f9e2af",
    "danger":  "#f38ba8",
    "fg":      "#cdd6f4",
    "fg_dim":  "#6c7086",
    "border":  "#45475a",
    "priv":    "#f38ba8",
    "pub":     "#a6e3a1",
}


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------
def lade_config():
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def speichere_config(cfg):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)


def format_datum(r):
    raw = r.get("updated") or r.get("updated_at") or r.get("modified") or ""
    if not raw:
        return "-"
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.strftime("%d.%m.%Y %H:%M")
    except Exception:
        return str(raw)


def http_fehler(exc):
    code = exc.response.status_code if exc.response is not None else "?"
    meldungen = {
        401: "Authentifizierung fehlgeschlagen.\nBitte API-Token pruefen.",
        403: "Zugriff verweigert.",
        404: "Ressource nicht gefunden.",
        409: "Konflikt - Repository existiert moeglicherweise bereits.",
        422: "Ungueltige Eingabe.",
    }
    return meldungen.get(code, "HTTP-Fehler {}: {}".format(code, exc))


def zentriere(fenster, parent):
    fenster.update_idletasks()
    x = parent.winfo_rootx() + (parent.winfo_width()  - fenster.winfo_width())  // 2
    y = parent.winfo_rooty() + (parent.winfo_height() - fenster.winfo_height()) // 2
    fenster.geometry("+{}+{}".format(x, y))


def flat_btn(parent, text, cmd, bg, fg, bold=False, **kw):
    font = ("Monospace", 10, "bold") if bold else ("Monospace", 10)
    return tk.Button(parent, text=text, command=cmd,
                     bg=bg, fg=fg,
                     activebackground=C["accent2"], activeforeground=C["bg"],
                     relief="flat", font=font, padx=10, pady=4, **kw)


def finde_dateimanager():
    for dm in DATEIMANAGER:
        if shutil.which(dm):
            return dm
    return None


def finde_terminal():
    """Gibt (befehl, [extra_args]) des ersten verfuegbaren Terminals zurueck."""
    for cmd, args in TERMINALS:
        if shutil.which(cmd):
            return cmd, args
    return None, None


def starte_terminal(shell_cmd: str, parent=None):
    """Oeffnet ein Terminal und fuehrt shell_cmd aus. Wartet auf ENTER am Ende."""
    tcmd, targs = finde_terminal()
    if not tcmd:
        if parent:
            messagebox.showwarning(
                "Kein Terminal gefunden",
                "Kein unterstuetztes Terminal gefunden.\n"
                "Bitte installieren: sudo apt install xterm",
                parent=parent)
        return False
    vollstaendig = shell_cmd + '; echo ""; echo "--- Fertig. ENTER druecken ---"; read'
    try:
        subprocess.Popen(
            [tcmd] + targs + ["bash", "-c", vollstaendig],
            start_new_session=True,
            env={**os.environ})
        return True
    except Exception as e:
        if parent:
            messagebox.showerror("Fehler", str(e), parent=parent)
        return False


def oeffne_verzeichnis(pfad, parent=None):
    dm = finde_dateimanager()
    if dm:
        subprocess.Popen([dm, str(pfad)],
                         start_new_session=True, env={**os.environ})
    elif parent:
        messagebox.showwarning(
            "Kein Dateimanager",
            "Kein Dateimanager gefunden.\nBitte installieren: sudo apt install nautilus",
            parent=parent)


def frage_push_assistent(parent, repo_pfad: Path, repo_name: str):
    """Fragt ob der Push-Assistent geoeffnet werden soll; oeffnet ihn ggf."""
    if messagebox.askyesno(
        "Push Assistent",
        "Push-Assistent fuer '{}' oeffnen?".format(repo_name),
        parent=parent):
        PushAssistant(parent, repo_pfad, repo_name)


def git_run(repo_pfad: Path, args: list, timeout=30) -> subprocess.CompletedProcess:
    """Hilfsmethode: fuehrt git-Befehl in repo_pfad aus."""
    return subprocess.run(
        ["git", "-C", str(repo_pfad)] + args,
        capture_output=True, text=True,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        timeout=timeout)


# ---------------------------------------------------------------------------
# Gitea API-Client
# ---------------------------------------------------------------------------
class GiteaClient:

    def __init__(self, base_url, token):
        self.base_url = base_url.rstrip("/")
        self.token    = token
        self.session  = requests.Session()
        self.session.headers.update({
            "Authorization": "token " + token,
            "Content-Type":  "application/json",
            "Accept":        "application/json",
        })

    def _raw_get(self, path, params=None):
        resp = self.session.get(self.base_url + "/api/v1" + path,
                                params=params, timeout=15)
        resp.raise_for_status()
        return resp

    def _get(self, path, params=None):
        return self._raw_get(path, params).json()

    def _post(self, path, payload):
        resp = self.session.post(self.base_url + "/api/v1" + path,
                                 json=payload, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def _delete(self, path):
        self.session.delete(self.base_url + "/api/v1" + path,
                            timeout=15).raise_for_status()

    def _total_count(self, path):
        try:
            resp = self._raw_get(path, {"limit": 1, "page": 1})
            hdr  = resp.headers.get("X-Total-Count", "")
            return int(hdr) if hdr else len(resp.json())
        except Exception:
            return -1

    def _alle_seiten(self, path, params=None):
        result, page = [], 1
        bp = dict(params or {})
        bp["limit"] = 50
        while True:
            bp["page"] = page
            batch = self._get(path, bp)
            if not batch:
                break
            result.extend(batch)
            if len(batch) < 50:
                break
            page += 1
        return result

    def test_connection(self):   return self._get("/user")
    def get_orgs(self):          return self._alle_seiten("/user/orgs")
    def get_repos(self, org):    return self._alle_seiten("/orgs/{}/repos".format(org))
    def get_branch_count(self, org, repo):
        return self._total_count("/repos/{}/{}/branches".format(org, repo))
    def get_commit_count(self, org, repo):
        return self._total_count("/repos/{}/{}/commits".format(org, repo))
    def get_branches(self, org, repo):
        return self._alle_seiten("/repos/{}/{}/branches".format(org, repo))
    def create_repo(self, org, payload):
        return self._post("/orgs/{}/repos".format(org), payload)
    def delete_repo(self, org, repo):
        self._delete("/repos/{}/{}".format(org, repo))

    def get_alle_repos(self):
        orgs  = self.get_orgs()
        repos = []
        vorh  = set()
        for org in orgs:
            try:
                for r in self.get_repos(org["username"]):
                    fn = r.get("full_name", "")
                    if fn not in vorh:
                        vorh.add(fn)
                        repos.append(r)
            except Exception:
                pass
        try:
            for r in self._alle_seiten("/repos/search", {"limit": 50}):
                fn = r.get("full_name", "")
                if fn not in vorh:
                    vorh.add(fn)
                    repos.append(r)
        except Exception:
            pass
        return repos


# ---------------------------------------------------------------------------
# Dialog: Verbindungseinstellungen
# ---------------------------------------------------------------------------
class KonfigDialog(tk.Toplevel):

    def __init__(self, parent, cfg, callback):
        super().__init__(parent)
        self.title("Verbindungseinstellungen")
        self.resizable(False, False)
        self.configure(bg=C["bg2"])
        self.grab_set()
        self._cb = callback
        self._baue_ui(cfg)
        self.protocol("WM_DELETE_WINDOW", self._abbrechen)
        zentriere(self, parent)

    def _baue_ui(self, cfg):
        PAD = dict(padx=16, pady=8)
        tk.Label(self, text="Gitea Verbindung konfigurieren",
                 font=("Monospace", 13, "bold"),
                 bg=C["bg2"], fg=C["accent"]).grid(
            row=0, column=0, columnspan=2, pady=(16, 4), padx=16, sticky="w")
        tk.Label(self, text="Gitea-URL:", bg=C["bg2"],
                 fg=C["fg"], font=("Monospace", 10)).grid(
            row=1, column=0, sticky="w", **PAD)
        self.url_var = tk.StringVar(value=cfg.get("url", "https://"))
        tk.Entry(self, textvariable=self.url_var, width=44,
                 bg=C["bg3"], fg=C["fg"], insertbackground=C["fg"],
                 relief="flat", font=("Monospace", 10)).grid(row=1, column=1, **PAD)
        tk.Label(self, text="API-Token:", bg=C["bg2"],
                 fg=C["fg"], font=("Monospace", 10)).grid(
            row=2, column=0, sticky="w", **PAD)
        self._tv = tk.StringVar(value=cfg.get("token", ""))
        self._te = tk.Entry(self, textvariable=self._tv, width=44, show="\u2022",
                            bg=C["bg3"], fg=C["fg"], insertbackground=C["fg"],
                            relief="flat", font=("Monospace", 10))
        self._te.grid(row=2, column=1, **PAD)
        sv = tk.BooleanVar()
        tk.Checkbutton(self, text="Token anzeigen", variable=sv,
                       command=lambda: self._te.config(show="" if sv.get() else "\u2022"),
                       bg=C["bg2"], fg=C["fg_dim"], selectcolor=C["bg3"],
                       activebackground=C["bg2"], font=("Monospace", 9)).grid(
            row=3, column=1, sticky="w", padx=16)
        tk.Label(self,
                 text="Token: Gitea > Einstellungen > Anwendungen > Token generieren",
                 bg=C["bg2"], fg=C["fg_dim"], font=("Monospace", 8)).grid(
            row=4, column=0, columnspan=2, padx=16, pady=(0, 4), sticky="w")

        tk.Label(self, text="Diff-Launcher:", bg=C["bg2"],
                 fg=C["fg"], font=("Monospace", 10)).grid(
            row=5, column=0, sticky="w", padx=16, pady=4)
        self._dl_var = tk.StringVar(value=cfg.get("diff_launcher", "/usr/bin/meldlauncher"))
        tk.Entry(self, textvariable=self._dl_var, width=44,
                 bg=C["bg3"], fg=C["fg"], insertbackground=C["fg"],
                 relief="flat", font=("Monospace", 10)).grid(row=5, column=1, padx=16, pady=4)

        f = tk.Frame(self, bg=C["bg2"])
        f.grid(row=6, column=0, columnspan=2, pady=(4, 16), padx=16, sticky="e")
        flat_btn(f, "Abbrechen", self._abbrechen, C["bg3"], C["fg_dim"]).pack(
            side="left", padx=(0, 8))
        flat_btn(f, "Verbinden & Speichern", self._speichern,
                 C["accent"], C["bg"], bold=True).pack(side="left")

    def _speichern(self):
        url   = self.url_var.get().strip()
        token = self._tv.get().strip()
        if not url or not token:
            messagebox.showwarning("Eingabe fehlt", "Bitte URL und Token angeben.",
                                   parent=self)
            return
        speichere_config({"url": url, "token": token,
                          "diff_launcher": self._dl_var.get().strip()})
        self.destroy()
        self._cb({"url": url, "token": token})

    def _abbrechen(self):
        self.destroy()
        self._cb(None)


# ---------------------------------------------------------------------------
# Dialog: Neues Repository anlegen
# ---------------------------------------------------------------------------
class NeuesRepoDialog(tk.Toplevel):

    def __init__(self, parent, org, client, on_success):
        super().__init__(parent)
        self.title("Neues Repository in '{}'".format(org))
        self.resizable(False, False)
        self.configure(bg=C["bg2"])
        self.grab_set()
        self.org        = org
        self.client     = client
        self.on_success = on_success
        self._baue_ui()
        zentriere(self, parent)

    def _baue_ui(self):
        tk.Label(self, text="Neues Repository - {}".format(self.org),
                 font=("Monospace", 12, "bold"),
                 bg=C["bg2"], fg=C["accent"]).pack(padx=16, pady=(14, 8), anchor="w")
        form = tk.Frame(self, bg=C["bg2"])
        form.pack(padx=16, fill="x")

        def lbl(row, text, req=False):
            tk.Label(form, text=text + (" *" if req else ""),
                     bg=C["bg2"], fg=C["danger"] if req else C["fg_dim"],
                     font=("Monospace", 9)).grid(
                row=row, column=0, sticky="w", padx=12, pady=4)

        def entry(row, var):
            tk.Entry(form, textvariable=var, width=38,
                     bg=C["bg3"], fg=C["fg"], insertbackground=C["fg"],
                     relief="flat", font=("Monospace", 10)).grid(
                row=row, column=1, padx=8, pady=4, sticky="w")

        self.name_var = tk.StringVar()
        lbl(0, "Repository-Name", req=True); entry(0, self.name_var)
        self.desc_var = tk.StringVar()
        lbl(1, "Beschreibung"); entry(1, self.desc_var)
        lbl(2, "Sichtbarkeit")
        self.privat_var = tk.BooleanVar(value=False)
        vf = tk.Frame(form, bg=C["bg2"])
        vf.grid(row=2, column=1, padx=8, pady=4, sticky="w")
        for txt, val in [("Public", False), ("Private", True)]:
            tk.Radiobutton(vf, text=txt, variable=self.privat_var, value=val,
                           bg=C["bg2"], fg=C["fg"], selectcolor=C["bg3"],
                           activebackground=C["bg2"],
                           font=("Monospace", 10)).pack(side="left", padx=(0, 12))
        lbl(3, "Initialisieren")
        self.init_var = tk.BooleanVar(value=True)
        tk.Checkbutton(form, text="README.md anlegen", variable=self.init_var,
                       bg=C["bg2"], fg=C["fg"], selectcolor=C["bg3"],
                       activebackground=C["bg2"],
                       font=("Monospace", 10)).grid(row=3, column=1, padx=8, pady=4, sticky="w")
        lbl(4, ".gitignore-Template")
        self.gitignore_var = tk.StringVar(value="")
        ttk.Combobox(form, textvariable=self.gitignore_var,
                     values=GITIGNORE_TEMPLATES, state="readonly", width=20,
                     font=("Monospace", 10)).grid(row=4, column=1, padx=8, pady=4, sticky="w")
        lbl(5, "Lizenz")
        self.lizenz_var = tk.StringVar(value="")
        ttk.Combobox(form, textvariable=self.lizenz_var,
                     values=LIZENZEN, state="readonly", width=20,
                     font=("Monospace", 10)).grid(row=5, column=1, padx=8, pady=4, sticky="w")
        tk.Label(self, text="* Pflichtfeld", bg=C["bg2"], fg=C["fg_dim"],
                 font=("Monospace", 8)).pack(anchor="w", padx=16)
        tk.Frame(self, bg=C["border"], height=1).pack(fill="x", padx=16, pady=8)
        bf = tk.Frame(self, bg=C["bg2"])
        bf.pack(padx=16, pady=(0, 14), anchor="e")
        flat_btn(bf, "Abbrechen", self.destroy, C["bg3"], C["fg_dim"]).pack(
            side="left", padx=(0, 8))
        self._ok = flat_btn(bf, "Repository erstellen", self._erstellen,
                            C["success"], C["bg"], bold=True)
        self._ok.pack(side="left")

    def _erstellen(self):
        name = self.name_var.get().strip()
        if not name:
            messagebox.showwarning("Pflichtfeld",
                                   "Bitte einen Repository-Namen eingeben.", parent=self)
            return
        payload = {"name": name, "description": self.desc_var.get().strip(),
                   "private": self.privat_var.get(), "auto_init": self.init_var.get()}
        gi = self.gitignore_var.get().strip()
        lz = self.lizenz_var.get().strip()
        if gi: payload["gitignores"] = gi
        if lz: payload["license"]    = lz
        self._ok.config(state="disabled", text="Erstelle ...")

        def run():
            try:
                repo = self.client.create_repo(self.org, payload)
                self.after(0, lambda: self._ok_msg(repo["full_name"]))
            except requests.HTTPError as e:
                msg = http_fehler(e)
                self.after(0, lambda: self._err(msg))
            except Exception as e:
                self.after(0, lambda: self._err(str(e)))
        threading.Thread(target=run, daemon=True).start()

    def _ok_msg(self, full_name):
        messagebox.showinfo("Erstellt",
                            "Repository '{}' wurde angelegt.".format(full_name),
                            parent=self)
        self.destroy()
        self.on_success()

    def _err(self, msg):
        messagebox.showerror("Fehler beim Erstellen", msg, parent=self)
        self._ok.config(state="normal", text="Repository erstellen")


# ---------------------------------------------------------------------------
# Dialog: Editor & Dateimanager nach Clone
# ---------------------------------------------------------------------------
class OeffnenDialog(tk.Toplevel):

    def __init__(self, parent, repo_pfad: Path, repo_name: str):
        super().__init__(parent)
        self.title("Geoeffnet: {}".format(repo_name))
        self.resizable(False, False)
        self.configure(bg=C["bg2"])
        self.grab_set()
        self.repo_pfad = repo_pfad
        self.repo_name = repo_name
        self._baue_ui()
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        zentriere(self, parent)

    def _baue_ui(self):
        tk.Label(self, text="Repository erfolgreich geklont",
                 font=("Monospace", 12, "bold"),
                 bg=C["bg2"], fg=C["success"]).pack(padx=20, pady=(16, 2), anchor="w")
        tk.Label(self, text=str(self.repo_pfad),
                 font=("Monospace", 9), bg=C["bg2"], fg=C["fg_dim"]).pack(
            padx=20, pady=(0, 12), anchor="w")
        tk.Frame(self, bg=C["border"], height=1).pack(fill="x", padx=20, pady=(0, 10))
        tk.Label(self, text="README.md oeffnen mit:",
                 font=("Monospace", 10, "bold"),
                 bg=C["bg2"], fg=C["accent"]).pack(padx=20, pady=(0, 6), anchor="w")
        self._editor_var = tk.StringVar(value="")
        ed_frame = tk.Frame(self, bg=C["bg2"])
        ed_frame.pack(padx=20, fill="x")
        for anzeige, befehl in EDITOREN:
            verfuegbar = shutil.which(befehl) is not None
            tk.Radiobutton(
                ed_frame,
                text=anzeige + ("" if verfuegbar else "  (nicht installiert)"),
                variable=self._editor_var, value=befehl,
                state="normal" if verfuegbar else "disabled",
                bg=C["bg2"], fg=C["fg"] if verfuegbar else C["fg_dim"],
                selectcolor=C["bg3"], activebackground=C["bg2"],
                disabledforeground=C["fg_dim"],
                font=("Monospace", 10)).pack(anchor="w", padx=8, pady=2)
            if verfuegbar and not self._editor_var.get():
                self._editor_var.set(befehl)
        tk.Frame(self, bg=C["border"], height=1).pack(fill="x", padx=20, pady=(10, 6))
        self._fm_var = tk.BooleanVar(value=False)
        dm = finde_dateimanager()
        dm_lbl = "Dateimanager parallel oeffnen" + (
            "  ({})".format(dm) if dm else "  (kein Dateimanager gefunden)")
        tk.Checkbutton(self, text=dm_lbl, variable=self._fm_var,
                       state="normal" if dm else "disabled",
                       bg=C["bg2"], fg=C["fg"], selectcolor=C["bg3"],
                       activebackground=C["bg2"], disabledforeground=C["fg_dim"],
                       font=("Monospace", 10)).pack(padx=20, pady=(0, 6), anchor="w")
        tk.Frame(self, bg=C["border"], height=1).pack(fill="x", padx=20, pady=(4, 8))
        bf = tk.Frame(self, bg=C["bg2"])
        bf.pack(fill="x", padx=20, pady=(0, 16))
        flat_btn(bf, "Nur Dateimanager", self._nur_fm,
                 C["bg3"], C["accent2"]).pack(side="left")
        flat_btn(bf, "Schliessen", self.destroy,
                 C["bg3"], C["fg_dim"]).pack(side="left", padx=(8, 0))
        flat_btn(bf, "README oeffnen", self._oeffne,
                 C["accent"], C["bg"], bold=True).pack(side="right")

    def _stelle_readme_sicher(self) -> Path:
        readme = self.repo_pfad / "README.md"
        if not readme.exists():
            with open(readme, "w", encoding="utf-8") as fh:
                fh.write("# {}\n\n".format(self.repo_name))
        return readme

    def _oeffne(self):
        editor = self._editor_var.get()
        if not editor:
            messagebox.showwarning("Kein Editor",
                                   "Bitte einen Editor auswaehlen.", parent=self)
            return
        readme = self._stelle_readme_sicher()
        try:
            subprocess.Popen([editor, str(readme)],
                             start_new_session=True, env={**os.environ})
        except Exception as e:
            messagebox.showerror("Fehler",
                                 "{} konnte nicht gestartet werden:\n{}".format(editor, e),
                                 parent=self)
            return
        if self._fm_var.get():
            oeffne_verzeichnis(self.repo_pfad, parent=self)
        self.destroy()

    def _nur_fm(self):
        oeffne_verzeichnis(self.repo_pfad, parent=self)
        self.destroy()


# ---------------------------------------------------------------------------
# Dialog: Vorhandene Repositories (nach Clone-Versuch)
# ---------------------------------------------------------------------------
class ExistierendDialog(tk.Toplevel):
    """
    Zeigt Repositories, deren Zielordner bereits existieren.
    Erlaubt git pull auf ausgewaehlten Repos.
    on_complete([(pfad, name, pull_ok)]) wird nach dem Schliessen aufgerufen.
    """

    def __init__(self, parent, vorhandene: list, on_complete=None):
        super().__init__(parent)
        self.title("Bereits vorhandene Repositories")
        self.resizable(True, True)
        self.minsize(600, 380)
        self.configure(bg=C["bg2"])
        self.grab_set()
        # vorhandene = [(repo_name, repo_pfad), ...]
        self._vorhandene  = vorhandene
        self._on_complete = on_complete
        self._ergebnisse  = []
        self._checkboxen  = []
        self._laufend     = False
        self._baue_ui()
        self.protocol("WM_DELETE_WINDOW", self._schliessen)
        zentriere(self, parent)

    def _baue_ui(self):
        tk.Label(self,
                 text="{} vorhandene Verzeichnisse gefunden".format(
                     len(self._vorhandene)),
                 font=("Monospace", 12, "bold"),
                 bg=C["bg2"], fg=C["warning"]).pack(padx=16, pady=(14, 4), anchor="w")
        tk.Label(self,
                 text="Soll in diesen git pull versucht werden?",
                 font=("Monospace", 10), bg=C["bg2"],
                 fg=C["fg_dim"]).pack(padx=16, pady=(0, 8), anchor="w")

        # Checkbox-Liste
        list_frame = tk.Frame(self, bg=C["bg3"])
        list_frame.pack(fill="x", padx=16, pady=(0, 8))
        alle_var = tk.BooleanVar(value=True)

        def toggle_alle():
            for v in self._checkboxen:
                v.set(alle_var.get())

        tk.Checkbutton(list_frame, text="Alle auswaehlen / abwaehlen",
                       variable=alle_var, command=toggle_alle,
                       bg=C["bg3"], fg=C["accent"], selectcolor=C["bg2"],
                       activebackground=C["bg3"],
                       font=("Monospace", 9, "bold")).pack(
            anchor="w", padx=8, pady=(6, 2))
        tk.Frame(list_frame, bg=C["border"], height=1).pack(fill="x", padx=8)

        for name, pfad in self._vorhandene:
            var = tk.BooleanVar(value=True)
            self._checkboxen.append(var)
            row = tk.Frame(list_frame, bg=C["bg3"])
            row.pack(fill="x", padx=4, pady=1)
            tk.Checkbutton(row, variable=var,
                           bg=C["bg3"], selectcolor=C["bg2"],
                           activebackground=C["bg3"]).pack(side="left")
            tk.Label(row, text=str(pfad),
                     bg=C["bg3"], fg=C["fg"], font=("Monospace", 9),
                     anchor="w").pack(side="left", fill="x")

        # Log-Ausgabe
        lf = tk.Frame(self, bg=C["bg"])
        lf.pack(fill="both", expand=True, padx=16, pady=(0, 6))
        self._out = tk.Text(lf, bg="#0d0d1a", fg="#d0d0d0",
                            font=("Monospace", 9), relief="flat",
                            state="disabled", wrap="word", pady=4, padx=6,
                            height=8)
        self._out.tag_configure("ok",   foreground=C["success"])
        self._out.tag_configure("err",  foreground=C["danger"])
        self._out.tag_configure("info", foreground=C["accent"])
        sb = ttk.Scrollbar(lf, orient="vertical", command=self._out.yview)
        self._out.configure(yscrollcommand=sb.set)
        self._out.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        self._pb = ttk.Progressbar(self, mode="indeterminate")
        self._pb.pack(fill="x", padx=16, pady=(0, 6))

        bf = tk.Frame(self, bg=C["bg2"])
        bf.pack(fill="x", padx=16, pady=(0, 14))
        flat_btn(bf, "Ueberspringen", self._schliessen,
                 C["bg3"], C["fg_dim"]).pack(side="left")
        self._pull_btn = flat_btn(bf, "git pull starten", self._starte_pull,
                                  C["accent"], C["bg"], bold=True)
        self._pull_btn.pack(side="right")

    def _log(self, text, tag=""):
        self._out.config(state="normal")
        self._out.insert("end", text, tag)
        self._out.see("end")
        self._out.config(state="disabled")

    def _starte_pull(self):
        if self._laufend:
            return
        ausgewaehlt = [(name, pfad)
                       for (name, pfad), var in zip(self._vorhandene, self._checkboxen)
                       if var.get()]
        if not ausgewaehlt:
            messagebox.showinfo("Keine Auswahl",
                                "Bitte mindestens ein Repository auswaehlen.",
                                parent=self)
            return

        self._laufend = True
        self._pull_btn.config(state="disabled", text="Fuehre pull durch ...")
        self._pb.start(10)

        def run():
            for name, pfad in ausgewaehlt:
                self.after(0, lambda n=name: self._log(
                    "\ngit pull  {}\n".format(n), "info"))
                try:
                    result = git_run(pfad, ["pull"], timeout=60)
                    if result.returncode == 0:
                        output = result.stdout or result.stderr or "OK"
                        self._ergebnisse.append((pfad, name, True))
                        self.after(0, lambda o=output: self._log(o.strip() + "\n", "ok"))
                    else:
                        output = result.stderr or result.stdout or "Unbekannter Fehler"
                        self._ergebnisse.append((pfad, name, False))
                        self.after(0, lambda o=output: self._log(o.strip() + "\n", "err"))
                except Exception as e:
                    self._ergebnisse.append((pfad, name, False))
                    self.after(0, lambda e2=str(e): self._log(
                        "Fehler: {}\n".format(e2), "err"))
            self.after(0, self._pull_fertig)

        threading.Thread(target=run, daemon=True).start()

    def _pull_fertig(self):
        self._laufend = False
        self._pb.stop()
        ok  = sum(1 for _, _, rc in self._ergebnisse if rc)
        err = len(self._ergebnisse) - ok
        self._log("\nFertig: {} OK, {} Fehler\n".format(ok, err),
                  "ok" if err == 0 else "err")
        self._pull_btn.config(state="normal", text="Fertig - Schliessen",
                              bg=C["success"], fg=C["bg"],
                              command=self._schliessen)

    def _schliessen(self):
        self.destroy()
        if self._on_complete:
            self._on_complete(self._ergebnisse)


# ---------------------------------------------------------------------------
# Branch-Auswahl-Dialog (fuer Push-Assistent)
# ---------------------------------------------------------------------------
class BranchWahlDialog(tk.Toplevel):

    def __init__(self, parent, branches: list, callback):
        super().__init__(parent)
        self.title("Branch auswaehlen")
        self.resizable(False, False)
        self.configure(bg=C["bg2"])
        self.grab_set()
        self._cb = callback
        self._var = tk.StringVar(value=branches[0] if branches else "")
        tk.Label(self, text="Branch fuer Vergleich:",
                 font=("Monospace", 11, "bold"),
                 bg=C["bg2"], fg=C["accent"]).pack(padx=20, pady=(14, 8))
        ttk.Combobox(self, textvariable=self._var,
                     values=branches, state="readonly", width=30,
                     font=("Monospace", 10)).pack(padx=20, pady=(0, 12))
        bf = tk.Frame(self, bg=C["bg2"])
        bf.pack(padx=20, pady=(0, 14))
        flat_btn(bf, "Abbrechen", self.destroy, C["bg3"], C["fg_dim"]).pack(
            side="left", padx=(0, 8))
        flat_btn(bf, "OK", self._ok, C["accent"], C["bg"], bold=True).pack(side="left")
        zentriere(self, parent)

    def _ok(self):
        branch = self._var.get().strip()
        self.destroy()
        if branch:
            self._cb(branch)


# ---------------------------------------------------------------------------
# Datei-Zeile im Push-Assistenten
# ---------------------------------------------------------------------------
class DateiZeile(tk.Frame):
    """
    Eine Zeile fuer eine geaenderte Datei im Push-Assistenten.
    Zeigt Status, Dateiname, 'compare changes'-Label und 4 Aktions-Buttons.
    """

    STATUS_FARBEN = {
        "M": "#89b4fa",   # blau  – modified
        "A": "#a6e3a1",   # gruen – added
        "D": "#f38ba8",   # rot   – deleted
        "R": "#cba6f7",   # lila  – renamed
        "?": "#f9e2af",   # gelb  – untracked
    }

    def __init__(self, parent, rel_pfad: Path, status_code: str,
                 repo_pfad: Path, repo_name: str, diff_prog: str):
        super().__init__(parent, bg=C["bg2"])
        self.rel_pfad   = rel_pfad
        self.status_code = status_code
        self.repo_pfad  = repo_pfad
        self.repo_name  = repo_name
        self.diff_prog  = diff_prog
        self._baue_ui()

    def _baue_ui(self):
        farbe = self.STATUS_FARBEN.get(self.status_code, C["fg"])

        # Status-Badge
        tk.Label(self, text=" {} ".format(self.status_code),
                 bg=farbe, fg=C["bg"],
                 font=("Monospace", 8, "bold")).pack(side="left", padx=(4, 6))

        # Dateiname
        tk.Label(self, text=str(self.rel_pfad),
                 bg=C["bg2"], fg=C["fg"],
                 font=("Monospace", 9),
                 anchor="w").pack(side="left", fill="x", expand=True)

        # Aktions-Buttons (nur fuer vorhandene Dateien sinnvoll)
        if self.status_code not in ("D",):
            tk.Label(self, text="compare changes:",
                     bg=C["bg2"], fg=C["fg_dim"],
                     font=("Monospace", 8)).pack(side="left", padx=(8, 4))

            for lbl, cmd, bg, fg in [
                ("CLI",    self._cli_diff,    C["bg3"],    C["accent"]),
                ("GUI",    self._gui_diff,    C["bg3"],    C["accent2"]),
                ("Branch", self._branch_diff, C["bg3"],    C["warning"]),
                ("LINT",   self._lint,        C["bg3"],    C["danger"]),
            ]:
                tk.Button(self, text=lbl, command=cmd,
                          bg=bg, fg=fg,
                          activebackground=C["border"],
                          relief="flat",
                          font=("Monospace", 8, "bold"),
                          padx=6, pady=2).pack(side="left", padx=2)

        tk.Frame(self, bg=C["border"], width=1).pack(side="right", fill="y")

    def _cli_diff(self):
        cmd = "git -C {} diff -- {}".format(
            str(self.repo_pfad), str(self.rel_pfad))
        starte_terminal(cmd, parent=self)

    def _gui_diff(self):
        self._vergleich_mit_ref("HEAD")

    def _branch_diff(self):
        # Branches ermitteln
        result = git_run(self.repo_pfad, ["branch", "-a"])
        branches = []
        for line in result.stdout.splitlines():
            b = line.strip().lstrip("* ").strip()
            if b and not b.startswith("(HEAD"):
                branches.append(b)
        if not branches:
            messagebox.showinfo("Keine Branches", "Keine Branches gefunden.",
                                parent=self)
            return
        BranchWahlDialog(self, branches,
                         callback=lambda b: self._vergleich_mit_ref(b))

    def _vergleich_mit_ref(self, ref: str):
        tmp_dir = TMP_VERGLEICH_BASE / self.repo_name
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp_datei = tmp_dir / self.rel_pfad.name

        # Datei-Inhalt aus git-Referenz lesen
        result = git_run(
            self.repo_pfad,
            ["show", "{}:{}".format(ref, str(self.rel_pfad).replace("\\", "/"))])
        if result.returncode != 0:
            messagebox.showerror(
                "Fehler",
                "Datei nicht in '{}' gefunden.\n{}".format(ref, result.stderr),
                parent=self)
            return

        try:
            tmp_datei.write_text(result.stdout, encoding="utf-8")
        except Exception as e:
            messagebox.showerror("Fehler",
                                 "Konnte Vergleichsdatei nicht schreiben:\n{}".format(e),
                                 parent=self)
            return

        aktuell = self.repo_pfad / self.rel_pfad
        try:
            subprocess.Popen([self.diff_prog, str(aktuell), str(tmp_datei)],
                             start_new_session=True, env={**os.environ})
        except FileNotFoundError:
            messagebox.showerror(
                "Diff-Programm nicht gefunden",
                "'{}' ist nicht installiert.\nInstallation: sudo apt install {}".format(
                    self.diff_prog, self.diff_prog),
                parent=self)

    def _lint(self):
        cmd = "lint-handler {}".format(str(self.repo_pfad / self.rel_pfad))
        starte_terminal(cmd, parent=self)


# ---------------------------------------------------------------------------
# Push-Assistent
# ---------------------------------------------------------------------------
class PushAssistant(tk.Toplevel):
    """
    Zeigt geaenderte Dateien eines Repositories mit Diff/LINT-Optionen.
    Ermoeglicht Commit, Push, Push2Branch und automatisches periodisches Committen.
    """

    def __init__(self, parent, repo_pfad: Path, repo_name: str,
                 diff_prog: str = DIFF_PROGRAMM_STD):
        super().__init__(parent)
        self.title("Push-Assistent: {}".format(repo_name))
        self.resizable(True, True)
        self.minsize(820, 560)
        self.configure(bg=C["bg"])
        self.repo_pfad  = repo_pfad
        self.repo_name  = repo_name
        self.diff_prog  = diff_prog
        self._auto_id   = None     # after()-ID fuer AUTOcommit-Timer
        self._zeilen    = []       # aktuell angezeigte DateiZeile-Widgets
        self._baue_ui()
        self.protocol("WM_DELETE_WINDOW", self._schliessen)
        self._aktualisiere()
        zentriere(self, parent)

    # ── UI ────────────────────────────────────────────────────────────────

    def _baue_ui(self):
        # Header
        hdr = tk.Frame(self, bg=C["bg2"], pady=8)
        hdr.pack(fill="x")
        tk.Label(hdr, text="  Push-Assistent: {}".format(self.repo_name),
                 font=("Monospace", 12, "bold"),
                 bg=C["bg2"], fg=C["accent"]).pack(side="left", padx=10)
        tk.Label(hdr, text=str(self.repo_pfad),
                 font=("Monospace", 8), bg=C["bg2"],
                 fg=C["fg_dim"]).pack(side="left", padx=(0, 10))

        # Toolbar
        tb = tk.Frame(self, bg=C["bg3"], pady=5)
        tb.pack(fill="x", padx=8, pady=(4, 0))

        flat_btn(tb, "Aktualisieren", self._aktualisiere,
                 C["bg3"], C["accent"]).pack(side="left", padx=4)

        # Diff-Programm
        tk.Label(tb, text="Diff:", bg=C["bg3"],
                 fg=C["fg_dim"], font=("Monospace", 9)).pack(side="left", padx=(12, 2))
        self._diff_var = tk.StringVar(value=self.diff_prog)
        tk.Entry(tb, textvariable=self._diff_var, width=10,
                 bg=C["bg2"], fg=C["fg"], insertbackground=C["fg"],
                 relief="flat", font=("Monospace", 9)).pack(side="left")

        # AUTOcommit-Toggle + Intervall
        self._auto_var = tk.BooleanVar(value=False)
        tk.Label(tb, text="  AUTOcommit alle",
                 bg=C["bg3"], fg=C["fg_dim"],
                 font=("Monospace", 9)).pack(side="left", padx=(16, 2))
        self._interval_var = tk.IntVar(value=60)
        ttk.Spinbox(tb, textvariable=self._interval_var,
                    from_=10, to=3600, width=5,
                    font=("Monospace", 9)).pack(side="left")
        tk.Label(tb, text="s", bg=C["bg3"], fg=C["fg_dim"],
                 font=("Monospace", 9)).pack(side="left", padx=(2, 4))
        self._auto_cb = tk.Checkbutton(
            tb, text="EIN", variable=self._auto_var,
            command=self._toggle_auto,
            bg=C["bg3"], fg=C["warning"],
            selectcolor=C["bg2"], activebackground=C["bg3"],
            font=("Monospace", 9, "bold"))
        self._auto_cb.pack(side="left")

        tk.Frame(self, bg=C["border"], height=1).pack(fill="x")

        # ── Datei-Liste (scrollbar via Canvas) ───────────────────────────
        list_outer = tk.Frame(self, bg=C["bg2"])
        list_outer.pack(fill="both", expand=True, padx=8, pady=(4, 0))

        self._canvas = tk.Canvas(list_outer, bg=C["bg2"], highlightthickness=0)
        self._list_sb = ttk.Scrollbar(list_outer, orient="vertical",
                                      command=self._canvas.yview)
        self._liste_frame = tk.Frame(self._canvas, bg=C["bg2"])
        self._canvas_win  = self._canvas.create_window(
            (0, 0), window=self._liste_frame, anchor="nw")
        self._liste_frame.bind(
            "<Configure>",
            lambda e: self._canvas.configure(
                scrollregion=self._canvas.bbox("all")))
        self._canvas.bind(
            "<Configure>",
            lambda e: self._canvas.itemconfig(
                self._canvas_win, width=e.width))
        self._canvas.configure(yscrollcommand=self._list_sb.set)
        self._canvas.pack(side="left", fill="both", expand=True)
        self._list_sb.pack(side="right", fill="y")

        # Leer-Label
        self._leer_lbl = tk.Label(self._liste_frame,
                                  text="Keine Aenderungen gefunden.",
                                  bg=C["bg2"], fg=C["fg_dim"],
                                  font=("Monospace", 10))

        # ── Statuszeile (Statistik) ───────────────────────────────────────
        self._stats_lbl = tk.Label(
            self, text="", bg=C["bg3"], fg=C["fg_dim"],
            font=("Monospace", 9), anchor="w", padx=8)
        self._stats_lbl.pack(fill="x")

        # ── Commit-Zeile ──────────────────────────────────────────────────
        commit_row = tk.Frame(self, bg=C["bg2"], pady=6)
        commit_row.pack(fill="x", padx=8)

        tk.Label(commit_row, text="Commit-Nachricht:",
                 bg=C["bg2"], fg=C["fg_dim"],
                 font=("Monospace", 9)).pack(side="left", padx=(4, 6))
        self._msg_var = tk.StringVar()
        tk.Entry(commit_row, textvariable=self._msg_var,
                 bg=C["bg3"], fg=C["fg"], insertbackground=C["fg"],
                 relief="flat", font=("Monospace", 10)).pack(
            side="left", fill="x", expand=True, padx=(0, 8))

        # Buttons: PUSH, PUSH2BRANCH
        flat_btn(commit_row, "PUSH2BRANCH", self._push2branch,
                 C["bg3"], C["accent2"], bold=True).pack(side="right", padx=(4, 0))
        flat_btn(commit_row, "PUSH", self._push,
                 C["accent"], C["bg"], bold=True).pack(side="right", padx=4)

    # ── Dateiliste aktualisieren ───────────────────────────────────────────

    def _aktualisiere(self):
        self.diff_prog = self._diff_var.get().strip() or DIFF_PROGRAMM_STD

        # git status --porcelain
        result = git_run(self.repo_pfad, ["status", "--porcelain"])
        zeilen = []
        for line in result.stdout.splitlines():
            if len(line) < 3:
                continue
            xy   = line[:2].strip() or "?"
            code = xy[0]
            pfad = line[3:].strip()
            # Umbenennungen: "old -> new" - nur neuen Pfad nehmen
            if " -> " in pfad:
                pfad = pfad.split(" -> ")[-1].strip()
            zeilen.append((code, Path(pfad)))

        # Alte Zeilen entfernen
        for w in self._zeilen:
            w.destroy()
        self._zeilen = []
        self._leer_lbl.pack_forget()

        if not zeilen:
            self._leer_lbl.pack(pady=20)
            self._stats_lbl.config(text="Keine Aenderungen.")
            return

        for code, rel_pfad in zeilen:
            zeile = DateiZeile(
                self._liste_frame,
                rel_pfad, code,
                self.repo_pfad, self.repo_name,
                self.diff_prog)
            zeile.pack(fill="x", pady=1)
            self._zeilen.append(zeile)

        # Statistik
        self._aktualisiere_stats()

    def _aktualisiere_stats(self):
        result = git_run(self.repo_pfad, ["diff", "--stat", "HEAD"])
        stat   = result.stdout.strip()
        if stat:
            letzte = stat.splitlines()[-1] if stat.splitlines() else ""
            self._stats_lbl.config(
                text="Aenderungen seit letztem Commit:  {}".format(letzte))
        else:
            self._stats_lbl.config(
                text="{} geaenderte Datei(en)".format(len(self._zeilen)))

    # ── AUTOcommit ────────────────────────────────────────────────────────

    def _toggle_auto(self):
        if self._auto_var.get():
            self._auto_cb.config(fg=C["success"])
            self._auto_tick()
        else:
            self._auto_cb.config(fg=C["warning"])
            if self._auto_id:
                self.after_cancel(self._auto_id)
                self._auto_id = None

    def _auto_tick(self):
        if not self._auto_var.get():
            return
        self._auto_commit_ausfuehren()
        ms = max(10, self._interval_var.get()) * 1000
        self._auto_id = self.after(ms, self._auto_tick)

    def _auto_commit_ausfuehren(self):
        result = git_run(self.repo_pfad, ["status", "--porcelain"])
        if not result.stdout.strip():
            return  # Nichts geaendert

        geaenderte = []
        for line in result.stdout.splitlines():
            if len(line) >= 3:
                geaenderte.append(line[3:].strip())

        ts  = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H.%M.%S")
        msg = "autocommit {} UTC - {}".format(ts, " ".join(geaenderte))

        git_run(self.repo_pfad, ["add", "-A"])
        git_run(self.repo_pfad, ["commit", "-m", msg])
        self.after(0, self._aktualisiere)

    # ── Push-Aktionen ────────────────────────────────────────────────────

    def _commit_und_push(self, extra_args: list = None):
        """Fuehrt add + commit + push aus. extra_args werden an push angehaengt."""
        msg = self._msg_var.get().strip()
        if not msg:
            if not messagebox.askyesno(
                "Leere Commit-Nachricht",
                "Die Commit-Nachricht ist leer.\nTrotzdem fortfahren?",
                parent=self):
                return False
            msg = "commit {}".format(
                datetime.now(timezone.utc).strftime("%Y-%m-%d_%H.%M.%S"))

        # Stage + Commit
        git_run(self.repo_pfad, ["add", "-A"])
        c_result = git_run(self.repo_pfad, ["commit", "-m", msg])
        if c_result.returncode != 0 and "nothing to commit" not in c_result.stdout:
            messagebox.showerror("Commit fehlgeschlagen",
                                 c_result.stderr or c_result.stdout,
                                 parent=self)
            return False

        # Push
        push_args = ["push"] + (extra_args or [])
        p_result  = git_run(self.repo_pfad, push_args, timeout=60)
        if p_result.returncode != 0:
            messagebox.showerror("Push fehlgeschlagen",
                                 p_result.stderr or p_result.stdout,
                                 parent=self)
            return False

        self._msg_var.set("")
        messagebox.showinfo("Erfolgreich",
                            "Commit und Push abgeschlossen.", parent=self)
        self._aktualisiere()
        return True

    def _push(self):
        self._commit_und_push()

    def _push2branch(self):
        branch = simpledialog.askstring(
            "Push in Branch",
            "Ziel-Branch-Name:",
            parent=self)
        if not branch or not branch.strip():
            return
        branch = branch.strip()
        self._commit_und_push(
            extra_args=["origin", "HEAD:{}".format(branch)])

    def _schliessen(self):
        if self._auto_id:
            self.after_cancel(self._auto_id)
        self.destroy()


# ---------------------------------------------------------------------------
# Dialog: Repository klonen (Einzel)
# ---------------------------------------------------------------------------
class CloneDialog(tk.Toplevel):

    def __init__(self, parent, org, repo_name, clone_url, token):
        super().__init__(parent)
        self.title("Klonen: {}/{}".format(org, repo_name))
        self.resizable(True, True)
        self.minsize(640, 460)
        self.configure(bg=C["bg2"])
        self.grab_set()
        self.org            = org
        self.repo_name      = repo_name
        self.clone_url      = clone_url
        self.token          = token
        self._proc          = None
        self._running       = False
        self._fertig_pfad   = None
        self._baue_ui()
        self.protocol("WM_DELETE_WINDOW", self._schliessen)
        zentriere(self, parent)

    def _baue_ui(self):
        tk.Label(self,
                 text="Repository klonen: {}/{}".format(self.org, self.repo_name),
                 font=("Monospace", 12, "bold"),
                 bg=C["bg2"], fg=C["accent"]).pack(padx=16, pady=(14, 6), anchor="w")

        opt = tk.Frame(self, bg=C["bg2"])
        opt.pack(fill="x", padx=16, pady=(0, 4))
        tk.Label(opt, text="Protokoll:", bg=C["bg2"],
                 fg=C["fg_dim"], font=("Monospace", 9)).grid(
            row=0, column=0, sticky="w", padx=(0, 6))
        self._proto = tk.StringVar(value="HTTPS (Token)")
        pcb = ttk.Combobox(opt, textvariable=self._proto,
                           values=["HTTPS (Token)", "HTTPS (anonym)", "SSH"],
                           state="readonly", width=18, font=("Monospace", 9))
        pcb.grid(row=0, column=1, padx=(0, 20))
        pcb.bind("<<ComboboxSelected>>", lambda _: self._upd_prev())
        tk.Label(opt, text="Zielverzeichnis:", bg=C["bg2"],
                 fg=C["fg_dim"], font=("Monospace", 9)).grid(
            row=0, column=2, sticky="w", padx=(0, 6))
        self._ziel = tk.StringVar(value=str(CLONE_ZIEL))
        tk.Entry(opt, textvariable=self._ziel, width=26,
                 bg=C["bg3"], fg=C["fg"], insertbackground=C["fg"],
                 relief="flat", font=("Monospace", 9)).grid(
            row=0, column=3, padx=(0, 4))
        tk.Button(opt, text="...", command=self._waehle_ziel,
                  bg=C["bg3"], fg=C["accent"],
                  relief="flat", font=("Monospace", 9), padx=4).grid(row=0, column=4)

        pf = tk.Frame(self, bg=C["bg3"])
        pf.pack(fill="x", padx=16, pady=(0, 8))
        tk.Label(pf, text=" git clone ", bg=C["bg3"],
                 fg=C["fg_dim"], font=("Monospace", 9)).pack(side="left")
        self._prev_var = tk.StringVar()
        tk.Label(pf, textvariable=self._prev_var, bg=C["bg3"],
                 fg=C["warning"], font=("Monospace", 9)).pack(side="left")
        self._upd_prev()

        tf = tk.Frame(self, bg=C["bg"])
        tf.pack(fill="both", expand=True, padx=16, pady=(0, 6))
        self._out = tk.Text(tf, bg="#0d0d1a", fg="#d0d0d0",
                            font=("Monospace", 9), relief="flat",
                            state="disabled", wrap="word", pady=6, padx=8)
        self._out.tag_configure("ok",   foreground=C["success"])
        self._out.tag_configure("err",  foreground=C["danger"])
        self._out.tag_configure("info", foreground=C["accent"])
        self._out.tag_configure("dim",  foreground=C["fg_dim"])
        sb = ttk.Scrollbar(tf, orient="vertical", command=self._out.yview)
        self._out.configure(yscrollcommand=sb.set)
        self._out.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self._log("Bereit - {}/{}\n".format(self.org, self.repo_name), "info")
        self._log("Ziel: {}/{}\n".format(self._ziel.get(), self.repo_name), "dim")

        self._pb = ttk.Progressbar(self, mode="indeterminate")
        self._pb.pack(fill="x", padx=16, pady=(0, 6))

        bf = tk.Frame(self, bg=C["bg2"])
        bf.pack(fill="x", padx=16, pady=(0, 14))
        self._cancel_btn = flat_btn(bf, "Abbrechen", self._schliessen,
                                    C["bg3"], C["fg_dim"])
        self._cancel_btn.pack(side="left")
        self._clone_btn = flat_btn(bf, "Jetzt klonen", self._starte,
                                   C["accent"], C["bg"], bold=True)
        self._clone_btn.pack(side="right")

    def _build_url(self):
        proto = self._proto.get()
        base  = self.clone_url
        if proto == "HTTPS (Token)":
            p = urlparse(base)
            return urlunparse(p._replace(
                netloc="{}@{}".format(self.token, p.netloc)))
        if proto == "HTTPS (anonym)":
            return base
        p = urlparse(base)
        return "git@{}:{}".format(p.netloc, p.path.lstrip("/"))

    def _upd_prev(self):
        url     = self._build_url()
        anzeige = url.replace(self.token, "***") if self.token in url else url
        self._prev_var.set(anzeige)

    def _waehle_ziel(self):
        v = filedialog.askdirectory(title="Zielverzeichnis",
                                    initialdir=self._ziel.get(), parent=self)
        if v:
            self._ziel.set(v)
            self._log("Ziel geaendert: {}\n".format(v), "dim")

    def _starte(self):
        if self._running:
            return
        ziel = Path(self._ziel.get())
        try:
            ziel.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            messagebox.showerror("Fehler",
                                 "Verzeichnis nicht erstellbar:\n" + str(e),
                                 parent=self)
            return

        repo_pfad = ziel / self.repo_name

        # Verzeichnis existiert bereits → ExistierendDialog
        if repo_pfad.exists():
            self._log(
                "\nVerzeichnis '{}' existiert bereits.\n".format(repo_pfad), "dim")

            def nach_pull(ergebnisse):
                # Nach pull: push-assistent anbieten falls pull erfolgreich
                ok_ergebnisse = [(p, n) for p, n, rc in ergebnisse if rc]
                if ok_ergebnisse:
                    pfad, name = ok_ergebnisse[0]
                    self.after(200, lambda: frage_push_assistent(
                        self.master, pfad, name))

            ExistierendDialog(
                self,
                [(self.repo_name, repo_pfad)],
                on_complete=nach_pull)
            return

        url           = self._build_url()
        self._running = True
        self._clone_btn.config(state="disabled", text="Klone ...")
        self._pb.start(12)
        self._log("\n$ git clone [url] {}\n".format(self.repo_name), "info")

        def run():
            try:
                proc = subprocess.Popen(
                    ["git", "clone", "--progress", url, str(repo_pfad)],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1,
                    env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
                self._proc = proc
                for line in proc.stdout:
                    self.after(0, lambda z=line: self._log(z))
                proc.wait()
                rc = proc.returncode
                self.after(0, lambda: self._fertig(rc, repo_pfad))
            except FileNotFoundError:
                self.after(0, lambda: self._fehler(
                    "git nicht gefunden.\nInstallation: sudo apt install git"))
            except Exception as e:
                self.after(0, lambda: self._fehler(str(e)))

        threading.Thread(target=run, daemon=True).start()

    def _fertig(self, rc, repo_pfad):
        self._running = False
        self._pb.stop()
        self._proc    = None
        if rc == 0:
            self._fertig_pfad = repo_pfad
            self._log("\nErfolgreich geklont nach:\n  {}\n".format(repo_pfad), "ok")
            self._clone_btn.config(
                state="normal", text="In Editor oeffnen ...",
                bg=C["success"], fg=C["bg"],
                command=self._nach_clone)
            self._cancel_btn.config(text="Schliessen", command=self.destroy)
        else:
            self._log("\ngit clone fehlgeschlagen (Exit-Code {})\n".format(rc), "err")
            self._clone_btn.config(
                state="normal", text="Erneut versuchen",
                bg=C["warning"], fg=C["bg"], command=self._starte)

    def _nach_clone(self):
        """OeffnenDialog, dann Push-Assistent anbieten."""
        pfad = self._fertig_pfad
        self.destroy()
        od = OeffnenDialog(self.master, pfad, self.repo_name)
        # Push-Assistent-Frage nach Schluss des OeffnenDialogs
        od.protocol("WM_DELETE_WINDOW",
                    lambda: (od.destroy(),
                             frage_push_assistent(self.master, pfad, self.repo_name)))

        def nach_oeffnen_btn(orig_cmd, p=pfad, n=self.repo_name):
            orig_cmd()
            frage_push_assistent(self.master, p, n)

        for widget in [getattr(od, "_ok_btn_ref", None)]:
            pass  # OeffnenDialog schliesst sich selbst; wir fangen WM_DELETE ab

    def _fehler(self, msg):
        self._running = False
        self._pb.stop()
        self._log("\nFehler: {}\n".format(msg), "err")
        self._clone_btn.config(state="normal", text="Erneut versuchen",
                               bg=C["warning"], fg=C["bg"], command=self._starte)

    def _log(self, text, tag=""):
        self._out.config(state="normal")
        self._out.insert("end", text, tag)
        self._out.see("end")
        self._out.config(state="disabled")

    def _schliessen(self):
        if self._running and self._proc:
            if not messagebox.askyesno("Abbrechen?",
                                       "Clone laeuft noch. Wirklich abbrechen?",
                                       parent=self):
                return
            try:
                self._proc.terminate()
            except Exception:
                pass
        self.destroy()


# ---------------------------------------------------------------------------
# Dialog: Bulk-Clone
# ---------------------------------------------------------------------------
class BulkCloneDialog(tk.Toplevel):

    def __init__(self, parent, client: GiteaClient, orgs: list, aktuell_org: str):
        super().__init__(parent)
        self.title("Bulk-Clone")
        self.resizable(True, True)
        self.minsize(700, 580)
        self.configure(bg=C["bg2"])
        self.grab_set()
        self.client      = client
        self.orgs        = orgs
        self._stopp      = threading.Event()
        self._laufend    = False
        self._zaehler    = {"ok": 0, "err": 0, "gesamt": 0}
        self._lock       = threading.Lock()
        # Vorhandene Verzeichnisse sammeln
        self._vorhandene: list = []   # [(repo_name, repo_pfad)]
        self._baue_ui(aktuell_org)
        self.protocol("WM_DELETE_WINDOW", self._schliessen)
        zentriere(self, parent)

    def _baue_ui(self, aktuell_org):
        tk.Label(self, text="Bulk-Clone",
                 font=("Monospace", 13, "bold"),
                 bg=C["bg2"], fg=C["accent"]).pack(padx=16, pady=(14, 6), anchor="w")

        opt = tk.Frame(self, bg=C["bg2"])
        opt.pack(fill="x", padx=16, pady=(0, 8))
        tk.Label(opt, text="Umfang:", bg=C["bg2"],
                 fg=C["fg_dim"], font=("Monospace", 10)).grid(
            row=0, column=0, sticky="w", padx=(0, 10), pady=4)
        self._umfang_var = tk.StringVar(value="org")
        tk.Radiobutton(opt, text="Aktuelle Organisation",
                       variable=self._umfang_var, value="org",
                       command=self._umfang_geaendert,
                       bg=C["bg2"], fg=C["fg"], selectcolor=C["bg3"],
                       activebackground=C["bg2"],
                       font=("Monospace", 10)).grid(row=0, column=1, sticky="w")
        tk.Radiobutton(opt, text="Gesamtes Gitea",
                       variable=self._umfang_var, value="alle",
                       command=self._umfang_geaendert,
                       bg=C["bg2"], fg=C["fg"], selectcolor=C["bg3"],
                       activebackground=C["bg2"],
                       font=("Monospace", 10)).grid(row=0, column=2, sticky="w", padx=(16, 0))
        tk.Label(opt, text="Organisation:", bg=C["bg2"],
                 fg=C["fg_dim"], font=("Monospace", 10)).grid(
            row=1, column=0, sticky="w", padx=(0, 10), pady=4)
        self._org_var = tk.StringVar(value=aktuell_org)
        self._org_cb  = ttk.Combobox(opt, textvariable=self._org_var,
                                     values=[o["username"] for o in self.orgs],
                                     state="readonly", width=24, font=("Monospace", 10))
        self._org_cb.grid(row=1, column=1, columnspan=2, sticky="w")
        tk.Label(opt, text="Zielverzeichnis:", bg=C["bg2"],
                 fg=C["fg_dim"], font=("Monospace", 10)).grid(
            row=2, column=0, sticky="w", padx=(0, 10), pady=4)
        self._ziel_var = tk.StringVar(value=str(CLONE_ZIEL))
        zf = tk.Frame(opt, bg=C["bg2"])
        zf.grid(row=2, column=1, columnspan=2, sticky="w")
        tk.Entry(zf, textvariable=self._ziel_var, width=30,
                 bg=C["bg3"], fg=C["fg"], insertbackground=C["fg"],
                 relief="flat", font=("Monospace", 10)).pack(side="left")
        tk.Button(zf, text="...", command=self._waehle_ziel,
                  bg=C["bg3"], fg=C["accent"],
                  relief="flat", font=("Monospace", 9), padx=4).pack(side="left", padx=(4, 0))
        tk.Label(opt, text="Protokoll:", bg=C["bg2"],
                 fg=C["fg_dim"], font=("Monospace", 10)).grid(
            row=3, column=0, sticky="w", padx=(0, 10), pady=4)
        self._proto_var = tk.StringVar(value="HTTPS (Token)")
        ttk.Combobox(opt, textvariable=self._proto_var,
                     values=["HTTPS (Token)", "HTTPS (anonym)", "SSH"],
                     state="readonly", width=18, font=("Monospace", 10)).grid(
            row=3, column=1, sticky="w")
        self._branches_var = tk.BooleanVar(value=True)
        tk.Checkbutton(opt, text="Alle Branches klonen (--mirror)",
                       variable=self._branches_var,
                       bg=C["bg2"], fg=C["fg"], selectcolor=C["bg3"],
                       activebackground=C["bg2"],
                       font=("Monospace", 10)).grid(
            row=4, column=0, columnspan=3, sticky="w", pady=(4, 0))
        tk.Label(opt, text="Parallele Verbindungen:", bg=C["bg2"],
                 fg=C["fg_dim"], font=("Monospace", 10)).grid(
            row=5, column=0, sticky="w", padx=(0, 10), pady=4)
        self._worker_var = tk.IntVar(value=3)
        ttk.Spinbox(opt, textvariable=self._worker_var, from_=1, to=10, width=5,
                    font=("Monospace", 10)).grid(row=5, column=1, sticky="w")

        tk.Frame(self, bg=C["border"], height=1).pack(fill="x", padx=16, pady=(4, 8))
        self._prog_lbl = tk.Label(self, text="Bereit.", bg=C["bg2"],
                                  fg=C["fg_dim"], font=("Monospace", 9))
        self._prog_lbl.pack(padx=16, anchor="w")
        self._pb = ttk.Progressbar(self, mode="determinate")
        self._pb.pack(fill="x", padx=16, pady=(2, 6))

        lf = tk.Frame(self, bg=C["bg"])
        lf.pack(fill="both", expand=True, padx=16, pady=(0, 6))
        self._out = tk.Text(lf, bg="#0d0d1a", fg="#d0d0d0",
                            font=("Monospace", 8), relief="flat",
                            state="disabled", wrap="word", pady=4, padx=6)
        self._out.tag_configure("ok",   foreground=C["success"])
        self._out.tag_configure("err",  foreground=C["danger"])
        self._out.tag_configure("info", foreground=C["accent"])
        self._out.tag_configure("dim",  foreground=C["fg_dim"])
        sb2 = ttk.Scrollbar(lf, orient="vertical", command=self._out.yview)
        self._out.configure(yscrollcommand=sb2.set)
        self._out.pack(side="left", fill="both", expand=True)
        sb2.pack(side="right", fill="y")

        bf = tk.Frame(self, bg=C["bg2"])
        bf.pack(fill="x", padx=16, pady=(0, 14))
        self._stop_btn = flat_btn(bf, "Stopp", self._stoppe, C["danger"], C["bg"])
        self._stop_btn.pack(side="left")
        self._stop_btn.config(state="disabled")
        flat_btn(bf, "Schliessen", self._schliessen,
                 C["bg3"], C["fg_dim"]).pack(side="left", padx=(8, 0))
        self._start_btn = flat_btn(bf, "Clone starten", self._starte,
                                   C["accent"], C["bg"], bold=True)
        self._start_btn.pack(side="right")
        self._umfang_geaendert()

    def _umfang_geaendert(self):
        self._org_cb.config(
            state="readonly" if self._umfang_var.get() == "org" else "disabled")

    def _waehle_ziel(self):
        v = filedialog.askdirectory(title="Zielverzeichnis",
                                    initialdir=self._ziel_var.get(), parent=self)
        if v:
            self._ziel_var.set(v)

    def _log(self, text, tag=""):
        self._out.config(state="normal")
        self._out.insert("end", text, tag)
        self._out.see("end")
        self._out.config(state="disabled")

    def _upd_prog(self):
        with self._lock:
            ok  = self._zaehler["ok"]
            err = self._zaehler["err"]
            ges = self._zaehler["gesamt"]
        done = ok + err
        self._pb["value"] = int(done / ges * 100) if ges else 0
        self._prog_lbl.config(
            text="Fortschritt: {}/{} — OK: {}  Fehler: {}".format(done, ges, ok, err))

    def _build_clone_url(self, repo_data):
        base  = repo_data.get("clone_url", "")
        proto = self._proto_var.get()
        token = self.client.token
        if proto == "HTTPS (Token)":
            p = urlparse(base)
            return urlunparse(p._replace(
                netloc="{}@{}".format(token, p.netloc)))
        if proto == "HTTPS (anonym)":
            return base
        p = urlparse(base)
        return "git@{}:{}".format(p.netloc, p.path.lstrip("/"))

    def _starte(self):
        self._laufend = True
        self._vorhandene = []
        self._stopp.clear()
        self._zaehler = {"ok": 0, "err": 0, "gesamt": 0}
        self._start_btn.config(state="disabled")
        self._stop_btn.config(state="normal")

        def run_all():
            self.after(0, lambda: self._log("Lade Repository-Liste ...\n", "info"))
            try:
                if self._umfang_var.get() == "org":
                    repos = self.client.get_repos(self._org_var.get())
                else:
                    repos = self.client.get_alle_repos()
            except Exception as e:
                self.after(0, lambda: self._log(
                    "Fehler beim Laden: {}\n".format(e), "err"))
                self.after(0, self._abgeschlossen)
                return

            n = len(repos)
            with self._lock:
                self._zaehler["gesamt"] = n
            self.after(0, lambda: self._log(
                "{} Repositories. Starte Clone ...\n\n".format(n), "info"))
            self.after(0, self._upd_prog)

            ziel_basis    = Path(self._ziel_var.get())
            alle_branches = self._branches_var.get()
            sem           = threading.Semaphore(self._worker_var.get())

            def clone_one(rd):
                if self._stopp.is_set():
                    return
                full  = rd.get("full_name", rd.get("name", "?"))
                name  = rd.get("name", "unbekannt")
                owner = (rd.get("owner", {}) or {}).get("login") or \
                        (rd.get("owner", {}) or {}).get("username") or ""
                repo_ziel = ziel_basis / owner / name if owner else ziel_basis / name

                # Verzeichnis existiert → merken, nicht klonen
                if repo_ziel.exists():
                    with self._lock:
                        self._zaehler["err"] += 1
                    self._vorhandene.append((name, repo_ziel))
                    self.after(0, lambda f=full, p=str(repo_ziel): self._log(
                        "  SKIP {}: Verzeichnis existiert ({})\n".format(f, p), "dim"))
                    self.after(0, self._upd_prog)
                    return

                with sem:
                    if self._stopp.is_set():
                        return
                    url = self._build_clone_url(rd)
                    try:
                        repo_ziel.parent.mkdir(parents=True, exist_ok=True)
                        git_args = ["git", "clone"]
                        if alle_branches:
                            git_args += ["--mirror"]
                            ziel_str = str(repo_ziel) + ".git"
                        else:
                            ziel_str = str(repo_ziel)
                        git_args += [url, ziel_str]
                        proc = subprocess.run(
                            git_args,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True,
                            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
                            timeout=300)
                        if proc.returncode == 0:
                            with self._lock:
                                self._zaehler["ok"] += 1
                            self.after(0, lambda f=full: self._log(
                                "  OK  {}\n".format(f), "ok"))
                        else:
                            with self._lock:
                                self._zaehler["err"] += 1
                            out = proc.stdout.strip()[:120] if proc.stdout else ""
                            self.after(0, lambda f=full, o=out: self._log(
                                "  ERR {}: {}\n".format(f, o), "err"))
                    except subprocess.TimeoutExpired:
                        with self._lock:
                            self._zaehler["err"] += 1
                        self.after(0, lambda f=full: self._log(
                            "  TIMEOUT {}\n".format(f), "err"))
                    except Exception as e:
                        with self._lock:
                            self._zaehler["err"] += 1
                        self.after(0, lambda f=full, e2=str(e): self._log(
                            "  ERR {}: {}\n".format(f, e2), "err"))
                    self.after(0, self._upd_prog)

            threads = [threading.Thread(target=clone_one, args=(rd,), daemon=True)
                       for rd in repos]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.after(0, self._abgeschlossen)

        threading.Thread(target=run_all, daemon=True).start()

    def _abgeschlossen(self):
        self._laufend = False
        ok  = self._zaehler["ok"]
        err = self._zaehler["err"]
        ges = self._zaehler["gesamt"]
        msg = "Fertig: {}/{} geklont, {} Fehler / Vorhandene.\n".format(ok, ges, err)
        self._log("\n" + msg, "ok" if err == 0 else "dim")
        self._stop_btn.config(state="disabled")
        self._start_btn.config(state="normal", text="Erneut starten")

        # Wenn vorhandene Verzeichnisse existieren → Dialog anzeigen
        if self._vorhandene:
            self.after(300, self._zeige_vorhandene)

    def _zeige_vorhandene(self):
        def nach_pull(ergebnisse):
            # Nach erfolgreichem Pull Push-Assistent anbieten
            for pfad, name, rc in ergebnisse:
                if rc:
                    frage_push_assistent(self.master, pfad, name)
                    break  # Nur das erste anbieten; User kann weitere manuell oeffnen

        ExistierendDialog(self, self._vorhandene, on_complete=nach_pull)

    def _stoppe(self):
        self._stopp.set()
        self._log("\nStopp angefordert ...\n", "dim")
        self._stop_btn.config(state="disabled")

    def _schliessen(self):
        if self._laufend:
            if not messagebox.askyesno("Laufend",
                                       "Bulk-Clone laeuft noch.\nWirklich schliessen?",
                                       parent=self):
                return
            self._stoppe()
        self.destroy()


# ---------------------------------------------------------------------------
# Cherrypicker – Zwei-Commit-Vergleich
# ---------------------------------------------------------------------------
class CherrypickerDialog(tk.Toplevel):
    """
    Waehlt zwei Commits aus einem lokalen Git-Repo, checkt sie in separate
    Verzeichnisse aus, versteckt die .git-Ordner und oeffnet das Diff-Programm.

    Verzeichnisstruktur:
      ~/code_by_commit/REPONAME/COMMITHASH     (ausgecheckte Kopie)
      ~/code_by_commit/_DOTgitORIG/REPONAME/COMMITHASH  (gesichertes .git)
    """

    BASE_DIR     = Path.home() / "code_by_commit"
    DOTGIT_BASE  = Path.home() / "code_by_commit" / "_DOTgitORIG"

    def __init__(self, parent, diff_launcher: str = "/usr/bin/meldlauncher"):
        super().__init__(parent)
        self.title("Cherrypicker – Commit-Vergleich")
        self.resizable(True, True)
        self.minsize(860, 640)
        self.configure(bg=C["bg"])
        self.grab_set()

        self._diff_launcher = diff_launcher
        self._repo_pfad: "Path | None" = None
        self._repo_name: str = ""
        self._branches: list = []
        self._commits: list  = []   # [(hash, subject, date), ...]
        self._page: int      = 0    # pagination: page * 50 commits geladen
        self._commit1: "tuple | None" = None
        self._commit2: "tuple | None" = None

        self._baue_ui()
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        zentriere(self, parent)

    # ── UI ────────────────────────────────────────────────────────────────

    def _baue_ui(self):
        # ── Kopfzeile ─────────────────────────────────────────────────────
        tk.Label(self, text="Cherrypicker – Commit-Vergleich",
                 font=("Monospace", 12, "bold"),
                 bg=C["bg"], fg=C["accent"]).pack(padx=16, pady=(12, 4), anchor="w")

        head = tk.Frame(self, bg=C["bg2"], pady=8)
        head.pack(fill="x", padx=12)

        # Repository-Verzeichnis
        tk.Label(head, text="Git-Verzeichnis:",
                 bg=C["bg2"], fg=C["fg_dim"],
                 font=("Monospace", 9)).grid(row=0, column=0, sticky="w", padx=(8, 6))
        self._dir_var = tk.StringVar()
        tk.Entry(head, textvariable=self._dir_var, width=38,
                 bg=C["bg3"], fg=C["fg"], insertbackground=C["fg"],
                 relief="flat", font=("Monospace", 9)).grid(row=0, column=1, padx=(0, 4))
        tk.Button(head, text="...", command=self._waehle_verzeichnis,
                  bg=C["bg3"], fg=C["accent"],
                  relief="flat", font=("Monospace", 9), padx=4).grid(row=0, column=2)

        # Branch
        tk.Label(head, text="Branch:",
                 bg=C["bg2"], fg=C["fg_dim"],
                 font=("Monospace", 9)).grid(row=0, column=3, sticky="w", padx=(16, 6))
        self._branch_var = tk.StringVar()
        self._branch_cb  = ttk.Combobox(head, textvariable=self._branch_var,
                                         state="disabled", width=20,
                                         font=("Monospace", 9))
        self._branch_cb.grid(row=0, column=4, padx=(0, 8))
        self._branch_cb.bind("<<ComboboxSelected>>", lambda _: self._branch_gewaehlt())

        # Load Commits Button (initial ausgegraut)
        self._load_btn = tk.Button(head, text="Load Commits",
                                   command=self._lade_commits,
                                   state="disabled",
                                   bg=C["bg3"], fg=C["fg_dim"],
                                   activebackground=C["accent"],
                                   activeforeground=C["bg"],
                                   relief="flat",
                                   font=("Monospace", 9, "bold"), padx=8, pady=3)
        self._load_btn.grid(row=0, column=5, padx=(0, 8))

        # Diff-Launcher
        tk.Label(head, text="Diff-Prog:",
                 bg=C["bg2"], fg=C["fg_dim"],
                 font=("Monospace", 9)).grid(row=0, column=6, sticky="w", padx=(8, 4))
        self._dl_var = tk.StringVar(value=self._diff_launcher)
        tk.Entry(head, textvariable=self._dl_var, width=18,
                 bg=C["bg3"], fg=C["fg"], insertbackground=C["fg"],
                 relief="flat", font=("Monospace", 9)).grid(row=0, column=7, padx=(0, 8))

        tk.Frame(self, bg=C["border"], height=1).pack(fill="x", padx=12)

        # ── Haupt-Bereich: zwei Commit-Listen nebeneinander ────────────────
        panes = tk.Frame(self, bg=C["bg"])
        panes.pack(fill="both", expand=True, padx=12, pady=(6, 0))
        panes.columnconfigure(0, weight=1)
        panes.columnconfigure(1, weight=1)
        panes.rowconfigure(1, weight=1)

        # Linke Liste: Commit 1
        tk.Label(panes, text="Commit 1 auswaehlen:",
                 bg=C["bg"], fg=C["accent"],
                 font=("Monospace", 10, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 2))

        l_frame = tk.Frame(panes, bg=C["bg"])
        l_frame.grid(row=1, column=0, sticky="nsew", padx=(0, 6))
        l_frame.rowconfigure(0, weight=1)
        l_frame.columnconfigure(0, weight=1)

        self._lb1 = tk.Listbox(l_frame,
                               bg=C["bg2"], fg=C["fg"],
                               selectbackground=C["accent"],
                               selectforeground=C["bg"],
                               font=("Monospace", 8),
                               activestyle="none")
        sb1 = ttk.Scrollbar(l_frame, orient="vertical", command=self._lb1.yview)
        self._lb1.configure(yscrollcommand=sb1.set)
        self._lb1.grid(row=0, column=0, sticky="nsew")
        sb1.grid(row=0, column=1, sticky="ns")
        self._lb1.bind("<<ListboxSelect>>", self._commit1_gewaehlt)

        self._mehr_btn = flat_btn(l_frame, "Weitere Commits laden",
                                  self._mehr_commits,
                                  C["bg3"], C["fg_dim"])
        self._mehr_btn.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        self._mehr_btn.config(state="disabled")

        # Rechte Liste: Commit 2 (initial leer / ausgegraut)
        self._lbl2 = tk.Label(panes, text="Commit 2 (Ziel) auswaehlen:",
                              bg=C["bg"], fg=C["fg_dim"],
                              font=("Monospace", 10, "bold"))
        self._lbl2.grid(row=0, column=1, sticky="w", pady=(0, 2))

        r_frame = tk.Frame(panes, bg=C["bg"])
        r_frame.grid(row=1, column=1, sticky="nsew", padx=(6, 0))
        r_frame.rowconfigure(0, weight=1)
        r_frame.columnconfigure(0, weight=1)

        self._lb2 = tk.Listbox(r_frame,
                               bg=C["bg2"], fg=C["fg"],
                               selectbackground=C["accent2"],
                               selectforeground=C["bg"],
                               font=("Monospace", 8),
                               activestyle="none",
                               state="disabled")
        sb2 = ttk.Scrollbar(r_frame, orient="vertical", command=self._lb2.yview)
        self._lb2.configure(yscrollcommand=sb2.set)
        self._lb2.grid(row=0, column=0, sticky="nsew")
        sb2.grid(row=0, column=1, sticky="ns")
        self._lb2.bind("<<ListboxSelect>>", self._commit2_gewaehlt)

        # ── Unterer Bereich ────────────────────────────────────────────────
        bottom = tk.Frame(self, bg=C["bg2"], pady=8)
        bottom.pack(fill="x", padx=12, pady=(6, 0))

        # Hinweis-Text
        self._hinweis_lbl = tk.Label(
            bottom,
            text="Hinweis: Es werden 2 Kopien des Repository angelegt und\n"
                 "die .git-Verzeichnisse zum Vergleichen daraus entfernt.",
            bg=C["bg2"], fg=C["warning"],
            font=("Monospace", 8), justify="left")
        self._hinweis_lbl.pack(side="left", padx=(8, 16))

        # Compare Button
        self._compare_btn = flat_btn(bottom, "Compare",
                                     self._starte_vergleich,
                                     C["accent"], C["bg"], bold=True)
        self._compare_btn.pack(side="right", padx=8)
        self._compare_btn.config(state="disabled")

        # Naming-Radiobuttons
        naming_frame = tk.Frame(bottom, bg=C["bg2"])
        naming_frame.pack(side="right", padx=(0, 16))
        self._naming_var = tk.StringVar(value="folder")
        self._rb_folder = tk.Radiobutton(
            naming_frame, text="Ordnername verwenden",
            variable=self._naming_var, value="folder",
            bg=C["bg2"], fg=C["fg"], selectcolor=C["bg3"],
            activebackground=C["bg2"], font=("Monospace", 9))
        self._rb_folder.pack(anchor="w")
        self._rb_repo = tk.Radiobutton(
            naming_frame, text="Repo-Name verwenden",
            variable=self._naming_var, value="repo",
            bg=C["bg2"], fg=C["fg"], selectcolor=C["bg3"],
            activebackground=C["bg2"], font=("Monospace", 9))
        self._rb_repo.pack(anchor="w")

        # Status
        self._status_lbl = tk.Label(self, text="",
                                    bg=C["bg3"], fg=C["fg_dim"],
                                    font=("Monospace", 8), anchor="w", padx=8)
        self._status_lbl.pack(fill="x", side="bottom")

    # ── Hilfsmethoden ─────────────────────────────────────────────────────

    def _status(self, text: str, farbe: str = None):
        self._status_lbl.config(text=text, fg=farbe or C["fg_dim"])

    def _waehle_verzeichnis(self):
        pfad = filedialog.askdirectory(title="Git-Verzeichnis auswaehlen",
                                       parent=self)
        if not pfad:
            return
        self._dir_var.set(pfad)
        self._lade_branches(Path(pfad))

    def _lade_branches(self, pfad: Path):
        self._repo_pfad = pfad
        # Repo-Name aus git remote -v ermitteln
        result = git_run(pfad, ["remote", "-v"])
        remote_name = ""
        for line in result.stdout.splitlines():
            if "(fetch)" in line:
                url_part = line.split()[1] if len(line.split()) > 1 else ""
                remote_name = url_part.rstrip("/").split("/")[-1]
                if remote_name.endswith(".git"):
                    remote_name = remote_name[:-4]
                break
        self._repo_name = remote_name or pfad.name

        # Naming-Radiobuttons: falls Ordnername == Repo-Name -> beide ausgrauen
        folder_name = pfad.name
        if folder_name == self._repo_name:
            self._rb_folder.config(state="disabled")
            self._rb_repo.config(state="disabled")
            self._naming_var.set("folder")
        else:
            self._rb_folder.config(state="normal")
            self._rb_repo.config(state="normal")

        # Branches laden
        result = git_run(pfad, ["branch", "-a"])
        branches = []
        for line in result.stdout.splitlines():
            b = line.strip().lstrip("* ").strip()
            if b and not b.startswith("(HEAD") and "->" not in b:
                # Nur den kurzen Namen ohne "remotes/origin/"
                if b.startswith("remotes/"):
                    parts = b.split("/", 2)
                    if len(parts) == 3:
                        b = parts[2]
                if b not in branches:
                    branches.append(b)

        if not branches:
            self._status("Keine Branches gefunden.", C["danger"])
            return

        self._branches = branches
        self._branch_cb["values"] = branches
        self._branch_cb.config(state="readonly")
        self._branch_var.set(branches[0])
        self._status("Branches geladen. Branch waehlen und 'Load Commits' druecken.")
        self._load_btn.config(state="normal", fg=C["fg"],
                              bg=C["accent"], activebackground=C["accent2"])

    def _branch_gewaehlt(self):
        self._load_btn.config(state="normal", bg=C["accent"],
                              fg=C["bg"], activebackground=C["accent2"])

    def _lade_commits(self):
        if not self._repo_pfad:
            return
        self._page = 0
        self._commits = []
        self._lb1.config(state="normal")
        self._lb1.delete(0, "end")
        self._lb2.delete(0, "end")
        self._lb2.config(state="disabled")
        self._commit1 = None
        self._commit2 = None
        self._compare_btn.config(state="disabled")
        self._lbl2.config(fg=C["fg_dim"])
        self._append_commits()

    def _append_commits(self):
        branch = self._branch_var.get()
        skip   = self._page * 50
        result = git_run(self._repo_pfad,
                         ["log", "--format=%H\t%ad\t%s",
                          "--date=short", "-50",
                          "--skip={}".format(skip), branch])
        neue = []
        for line in result.stdout.splitlines():
            parts = line.split("\t", 2)
            if len(parts) == 3:
                neue.append((parts[0][:12], parts[2][:70], parts[1]))
        if not neue:
            self._status("Keine weiteren Commits.", C["fg_dim"])
            self._mehr_btn.config(state="disabled")
            return
        self._commits.extend(neue)
        for h, subj, date in neue:
            self._lb1.insert("end", "  {}  {}  {}".format(h, date, subj))
        self._mehr_btn.config(state="normal",
                              text="Weitere Commits laden ({} geladen)".format(
                                  len(self._commits)))
        self._status("{} Commits geladen.".format(len(self._commits)))

    def _mehr_commits(self):
        self._page += 1
        self._append_commits()

    def _commit1_gewaehlt(self, event=None):
        sel = self._lb1.curselection()
        if not sel:
            return
        idx = sel[0]
        if idx >= len(self._commits):
            return
        self._commit1 = self._commits[idx]
        h, subj, date = self._commit1
        self._status("Commit 1: {}  {}".format(h, subj))
        self._lbl2.config(fg=C["accent"])

        # Zweite Liste fuellen (gleiche Commits, HEAD zuerst, commit1 ausgegraut)
        self._lb2.config(state="normal")
        self._lb2.delete(0, "end")

        # HEAD als ersten Eintrag
        head_result = git_run(self._repo_pfad,
                              ["log", "--format=%H\t%ad\t%s",
                               "--date=short", "-1", "HEAD"])
        head_commits = []
        for line in head_result.stdout.splitlines():
            parts = line.split("\t", 2)
            if len(parts) == 3:
                head_commits.append((parts[0][:12], parts[2][:70], parts[1]))

        # Alle geladenen Commits in Liste 2 einfuegen
        alle = head_commits[:]
        for c in self._commits:
            if c not in alle:
                alle.append(c)

        self._commits2 = alle
        for i, (ch, csubj, cdate) in enumerate(alle):
            eintrag = "  {}  {}  {}".format(ch, cdate, csubj)
            self._lb2.insert("end", eintrag)
            if ch == h:
                # Commit1 ausgrauen und markieren
                self._lb2.itemconfig(i, fg=C["fg_dim"],
                                     selectbackground=C["bg3"],
                                     selectforeground=C["fg_dim"])

        # HEAD vorauswaehlen
        if alle:
            self._lb2.selection_set(0)
            self._commit2 = alle[0]
            self._compare_btn.config(state="normal")

    def _commit2_gewaehlt(self, event=None):
        sel = self._lb2.curselection()
        if not sel:
            return
        idx = sel[0]
        if not hasattr(self, "_commits2") or idx >= len(self._commits2):
            return
        self._commit2 = self._commits2[idx]
        # Commit1 nicht als Commit2 zulassen
        if self._commit1 and self._commit2[0] == self._commit1[0]:
            self._status("Bitte einen anderen Commit als Commit 2 auswaehlen.",
                         C["warning"])
            self._compare_btn.config(state="disabled")
            return
        self._compare_btn.config(state="normal")
        h, subj, date = self._commit2
        self._status("Commit 1: {}  |  Commit 2: {}  {}".format(
            self._commit1[0] if self._commit1 else "?", h, subj))

    # ── Vergleich durchfuehren ─────────────────────────────────────────────

    def _repo_anzeigename(self) -> str:
        if self._naming_var.get() == "repo":
            return self._repo_name
        return self._repo_pfad.name if self._repo_pfad else self._repo_name

    def _starte_vergleich(self):
        if not self._commit1 or not self._commit2:
            return
        if self._commit1[0] == self._commit2[0]:
            messagebox.showwarning("Gleiche Commits",
                                   "Bitte zwei verschiedene Commits auswaehlen.",
                                   parent=self)
            return
        self._compare_btn.config(state="disabled", text="Bereite vor ...")

        hash1 = self._commit1[0]
        hash2 = self._commit2[0]

        def run():
            try:
                self._checkout_und_vergleich(hash1, hash2)
            except Exception as e:
                self.after(0, lambda: messagebox.showerror(
                    "Fehler", str(e), parent=self))
            finally:
                self.after(0, lambda: self._compare_btn.config(
                    state="normal", text="Compare"))

        threading.Thread(target=run, daemon=True).start()

    def _checkout_und_vergleich(self, hash1: str, hash2: str):
        anzeige = self._repo_anzeigename()
        ziel1   = self.BASE_DIR / anzeige / hash1
        ziel2   = self.BASE_DIR / anzeige / hash2
        git1    = self.DOTGIT_BASE / anzeige / hash1
        git2    = self.DOTGIT_BASE / anzeige / hash2

        self.after(0, lambda: self._status("Klone und checke aus ..."))

        for ziel, commit_hash in [(ziel1, hash1), (ziel2, hash2)]:
            ziel.mkdir(parents=True, exist_ok=True)

            # Lokale Kopie klonen (schnell, kein Netzwerk)
            result = subprocess.run(
                ["git", "clone", "--local", str(self._repo_pfad), str(ziel)],
                capture_output=True, text=True,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
            if result.returncode != 0 and not ziel.exists():
                raise RuntimeError("Clone fehlgeschlagen: " + result.stderr)

            # Commit auschecken
            result = git_run(ziel, ["checkout", commit_hash])
            if result.returncode != 0:
                raise RuntimeError("Checkout {} fehlgeschlagen: {}".format(
                    commit_hash, result.stderr))

        # .git-Verzeichnisse verstecken
        self.after(0, lambda: self._status(".git-Ordner sichern ..."))
        for ziel, git_sicher in [(ziel1, git1), (ziel2, git2)]:
            dot_git = ziel / ".git"
            if dot_git.exists():
                git_sicher.mkdir(parents=True, exist_ok=True)
                ziel_dotgit = git_sicher / ".git"
                if ziel_dotgit.exists():
                    shutil.rmtree(str(ziel_dotgit))
                shutil.move(str(dot_git), str(ziel_dotgit))

        self.after(0, lambda: self._status("Starte Diff-Programm ...", C["success"]))

        # Diff-Programm starten
        launcher = self._dl_var.get().strip() or "/usr/bin/meldlauncher"
        try:
            subprocess.Popen([launcher, str(ziel1), str(ziel2)],
                             start_new_session=True, env={**os.environ})
        except FileNotFoundError:
            self.after(0, lambda: messagebox.showwarning(
                "Launcher nicht gefunden",
                "'{}' nicht gefunden.\nBitte in Einstellungen anpassen.".format(launcher),
                parent=self))

        # Aufraeum-Dialog
        self.after(500, lambda: AufraeumdialogCP(
            self,
            anzeige, hash1, hash2,
            ziel1, ziel2,
            git1 / ".git", git2 / ".git"))


# ---------------------------------------------------------------------------
# Aufraeum-Dialog fuer Cherrypicker
# ---------------------------------------------------------------------------
class AufraeumdialogCP(tk.Toplevel):
    """
    Zeigt die ausgecheckten Pfade und erlaubt:
    - .git Verzeichnisse wiederherstellen
    - Ausgecheckte Ordner + gesicherte .git loeschen
    """

    def __init__(self, parent, repo_name: str,
                 hash1: str, hash2: str,
                 pfad1: Path, pfad2: Path,
                 dotgit1: Path, dotgit2: Path):
        super().__init__(parent)
        self.title("Cherrypicker – Aufraumen")
        self.resizable(False, False)
        self.configure(bg=C["bg2"])
        # Kein grab_set – Meld laeuft parallel
        self.repo_name = repo_name
        self.hash1, self.hash2 = hash1, hash2
        self.pfad1, self.pfad2 = pfad1, pfad2
        self.dotgit1, self.dotgit2 = dotgit1, dotgit2
        self._baue_ui()
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        zentriere(self, parent)

    def _baue_ui(self):
        tk.Label(self, text="Cherrypicker – Aufraumen",
                 font=("Monospace", 12, "bold"),
                 bg=C["bg2"], fg=C["accent"]).pack(padx=16, pady=(14, 4), anchor="w")

        for label, pfad in [
            ("Commit 1:", self.pfad1),
            ("Commit 2:", self.pfad2),
        ]:
            f = tk.Frame(self, bg=C["bg2"])
            f.pack(fill="x", padx=16, pady=1)
            tk.Label(f, text=label, bg=C["bg2"], fg=C["fg_dim"],
                     font=("Monospace", 9), width=10, anchor="w").pack(side="left")
            tk.Label(f, text=str(pfad), bg=C["bg2"], fg=C["fg"],
                     font=("Monospace", 9), anchor="w").pack(side="left")

        tk.Frame(self, bg=C["border"], height=1).pack(fill="x", padx=16, pady=8)

        # Buttons: .git wiederherstellen
        tk.Label(self, text=".git wiederherstellen:",
                 bg=C["bg2"], fg=C["fg_dim"],
                 font=("Monospace", 9)).pack(padx=16, anchor="w")

        rf = tk.Frame(self, bg=C["bg2"])
        rf.pack(fill="x", padx=16, pady=4)
        flat_btn(rf,
                 ".git fuer {}".format(self.hash1),
                 lambda: self._restore_dotgit(self.pfad1, self.dotgit1),
                 C["bg3"], C["accent"]).pack(side="left", padx=(0, 8))
        flat_btn(rf,
                 ".git fuer {}".format(self.hash2),
                 lambda: self._restore_dotgit(self.pfad2, self.dotgit2),
                 C["bg3"], C["accent"]).pack(side="left")

        tk.Frame(self, bg=C["border"], height=1).pack(fill="x", padx=16, pady=8)

        # Loeschen
        bf = tk.Frame(self, bg=C["bg2"])
        bf.pack(fill="x", padx=16, pady=(0, 14))
        flat_btn(bf, "Alles loeschen (beide Kopien + .git)",
                 self._alles_loeschen,
                 C["danger"], C["bg"], bold=True).pack(side="left")
        flat_btn(bf, "Schliessen (Dateien behalten)",
                 self.destroy,
                 C["bg3"], C["fg_dim"]).pack(side="right")

    def _restore_dotgit(self, ziel_pfad: Path, dotgit_sicher: Path):
        if not dotgit_sicher.exists():
            messagebox.showinfo("Nicht gefunden",
                                "Gesichertes .git nicht gefunden:\n{}".format(
                                    dotgit_sicher),
                                parent=self)
            return
        try:
            ziel = ziel_pfad / ".git"
            if ziel.exists():
                shutil.rmtree(str(ziel))
            shutil.move(str(dotgit_sicher), str(ziel))
            messagebox.showinfo("Fertig",
                                ".git wiederhergestellt:\n{}".format(ziel),
                                parent=self)
        except Exception as e:
            messagebox.showerror("Fehler", str(e), parent=self)

    def _alles_loeschen(self):
        if not messagebox.askyesno(
            "Wirklich loeschen?",
            "Alle ausgecheckten Kopien und gesicherten .git-Ordner loeschen?\n\n"
            "{}  UND  {}".format(self.pfad1, self.pfad2),
            icon="warning", parent=self):
            return
        fehler = []
        for pfad in [self.pfad1, self.pfad2]:
            try:
                if pfad.exists():
                    shutil.rmtree(str(pfad))
            except Exception as e:
                fehler.append(str(e))
        # Gesicherte .git-Ordner ebenfalls entfernen
        for dg in [self.dotgit1.parent, self.dotgit2.parent]:
            try:
                if dg.exists():
                    shutil.rmtree(str(dg))
            except Exception as e:
                fehler.append(str(e))
        if fehler:
            messagebox.showwarning("Teilweise Fehler",
                                   "\n".join(fehler), parent=self)
        else:
            messagebox.showinfo("Geloescht", "Alle Dateien entfernt.", parent=self)
        self.destroy()


# ---------------------------------------------------------------------------
# Haupt-Applikation
# ---------------------------------------------------------------------------
class App(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("Gitea Repository Manager")
        self.geometry("1200x640")
        self.minsize(900, 480)
        self.configure(bg=C["bg"])

        # WICHTIG: self.cfg, NICHT self.config  (tk.Tk.config() ist eine Methode!)
        self.cfg    = {}
        self.client = None
        self.repos  = []
        self.orgs   = []
        self._counts = {}

        self._style_setup()
        self._baue_menue()
        self._baue_fenster()

        saved = lade_config()
        if saved:
            self._verbinden(saved)
        else:
            self.after(100, self._oeffne_konfig)

    def _style_setup(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure("Treeview",
                    background=C["bg2"], foreground=C["fg"],
                    fieldbackground=C["bg2"], borderwidth=0,
                    rowheight=26, font=("Monospace", 9))
        s.configure("Treeview.Heading",
                    background=C["bg3"], foreground=C["accent"],
                    borderwidth=0, font=("Monospace", 9, "bold"))
        s.map("Treeview",
              background=[("selected", C["bg3"])],
              foreground=[("selected", C["accent"])])
        s.configure("TCombobox",
                    fieldbackground=C["bg3"], background=C["bg3"],
                    foreground=C["fg"],
                    selectbackground=C["bg3"], selectforeground=C["fg"])
        s.configure("Vertical.TScrollbar",
                    background=C["bg3"], troughcolor=C["bg2"],
                    borderwidth=0, arrowsize=12)

    def _baue_menue(self):
        kw = dict(bg=C["bg2"], fg=C["fg"],
                  activebackground=C["bg3"], activeforeground=C["accent"])
        mb = tk.Menu(self, **kw)
        dm = tk.Menu(mb, tearoff=0, **kw)
        dm.add_command(label="Einstellungen", command=self._oeffne_konfig)
        dm.add_separator()
        dm.add_command(label="Beenden", command=self.destroy)
        mb.add_cascade(label="Datei", menu=dm)
        km = tk.Menu(mb, tearoff=0, **kw)
        km.add_command(label="Einzel-Clone",   command=self._clone_repo)
        km.add_command(label="Bulk-Clone ...", command=self._oeffne_bulk_clone)
        mb.add_cascade(label="Clone", menu=km)
        hm = tk.Menu(mb, tearoff=0, **kw)
        hm.add_command(label="Ueber...", command=self._ueber)
        mb.add_cascade(label="Hilfe", menu=hm)
        tk.Tk.config(self, menu=mb)

    def _baue_fenster(self):
        hdr = tk.Frame(self, bg=C["bg2"], pady=10)
        hdr.pack(fill="x")
        tk.Label(hdr, text="  Gitea Repository Manager",
                 font=("Monospace", 15, "bold"),
                 bg=C["bg2"], fg=C["accent"]).pack(side="left", padx=10)
        self._hdr_lbl = tk.Label(hdr, text="Nicht verbunden",
                                 font=("Monospace", 9),
                                 bg=C["bg2"], fg=C["fg_dim"])
        self._hdr_lbl.pack(side="right", padx=16)
        tk.Frame(self, bg=C["border"], height=1).pack(fill="x")

        tb = tk.Frame(self, bg=C["bg"], pady=8)
        tb.pack(fill="x", padx=12)
        tk.Label(tb, text="Organisation:", bg=C["bg"],
                 fg=C["fg_dim"], font=("Monospace", 10)).pack(side="left")
        self._org_var = tk.StringVar()
        self._org_cb  = ttk.Combobox(tb, textvariable=self._org_var,
                                     state="readonly", width=22, font=("Monospace", 10))
        self._org_cb.pack(side="left", padx=(4, 10))
        self._org_cb.bind("<<ComboboxSelected>>", lambda _: self._lade_repos())
        self._ref_btn  = self._tbtn(tb, "Aktualisieren", self._lade_repos, C["bg3"], C["accent"])
        self._neu_btn  = self._tbtn(tb, "+ Neu",          self._oeffne_neues_repo, C["accent"], C["bg"])
        self._cln_btn  = self._tbtn(tb, "Klonen",         self._clone_repo, C["bg3"], C["accent2"])
        tk.Label(tb, text="Suche:", bg=C["bg"],
                 fg=C["fg_dim"], font=("Monospace", 10)).pack(side="right")
        self._suche = tk.StringVar()
        self._suche.trace_add("write", lambda *_: self._filter())
        tk.Entry(tb, textvariable=self._suche, width=20,
                 bg=C["bg3"], fg=C["fg"], insertbackground=C["fg"],
                 relief="flat", font=("Monospace", 10)).pack(side="right", padx=(0, 6))

        tbl = tk.Frame(self, bg=C["bg"])
        tbl.pack(fill="both", expand=True, padx=12, pady=(4, 0))
        cols = ("name", "beschreibung", "sichtbarkeit",
                "sprache", "branches", "commits", "sterne", "aktualisiert")
        self._tree = ttk.Treeview(tbl, columns=cols,
                                  show="headings", selectmode="browse")
        for cid, heading, w, anchor in [
            ("name",         "Repository",   180, "w"),
            ("beschreibung", "Beschreibung", 195, "w"),
            ("sichtbarkeit", "Sichtbarkeit",  80, "center"),
            ("sprache",      "Sprache",        90, "center"),
            ("branches",     "Branches",       70, "center"),
            ("commits",      "Commits",        70, "center"),
            ("sterne",       "Sterne",         55, "center"),
            ("aktualisiert", "Aktualisiert",  145, "center"),
        ]:
            self._tree.heading(cid, text=heading,
                               command=lambda c=cid: self._sortiere(c))
            self._tree.column(cid, width=w, anchor=anchor, minwidth=30)
        self._tree.tag_configure("private", foreground=C["priv"])
        self._tree.tag_configure("public",  foreground=C["pub"])
        sb = ttk.Scrollbar(tbl, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=sb.set)
        self._tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        kw2 = dict(bg=C["bg2"], fg=C["fg"],
                   activebackground=C["bg3"], activeforeground=C["accent"])
        self._km = tk.Menu(self, tearoff=0, **kw2)
        self._km.add_command(label="Klonen",            command=self._clone_repo)
        self._km.add_command(label="Bulk-Clone",        command=self._oeffne_bulk_clone)
        self._km.add_command(label="Push-Assistent",    command=self._push_assistent)
        self._km.add_separator()
        self._km.add_command(label="URL kopieren",      command=self._kopiere_url)
        self._km.add_command(label="Loeschen",          command=self._loesche_repo)
        self._tree.bind("<Button-3>", self._zeige_km)
        self._tree.bind("<Double-1>", lambda _: self._clone_repo())

        # ── Special-Leiste (unterhalb der Liste) ──────────────────────────────
        special_bar = tk.Frame(self, bg=C["bg2"], pady=5)
        special_bar.pack(fill="x", padx=12, side="bottom")
        tk.Frame(special_bar, bg=C["border"], height=1).pack(fill="x", pady=(0, 4))
        tk.Label(special_bar, text="Special:",
                 bg=C["bg2"], fg=C["fg_dim"],
                 font=("Monospace", 10, "bold")).pack(side="left", padx=(4, 8))
        self._bulk_btn = flat_btn(special_bar, "Bulk-Clone",
                                  self._oeffne_bulk_clone,
                                  C["bg3"], C["warning"], bold=True)
        self._bulk_btn.pack(side="left", padx=(0, 6))
        flat_btn(special_bar, "Cherrypicker",
                 self._oeffne_cherrypicker,
                 C["bg3"], C["accent2"], bold=True).pack(side="left")

        self._sb_lbl = tk.Label(self, text="Bereit.", anchor="w",
                                bg=C["bg3"], fg=C["fg_dim"],
                                font=("Monospace", 8), padx=8)
        self._sb_lbl.pack(fill="x", side="bottom")
        self._ui_aktiv(False)

    def _tbtn(self, parent, text, cmd, bg, fg):
        b = tk.Button(parent, text=text, command=cmd,
                      bg=bg, fg=fg,
                      activebackground=C["accent2"], activeforeground=C["bg"],
                      relief="flat", font=("Monospace", 10, "bold"), padx=10, pady=4)
        b.pack(side="left", padx=4)
        return b

    def _ui_aktiv(self, a):
        st = "normal" if a else "disabled"
        for w in (self._org_cb, self._ref_btn, self._neu_btn,
                  self._cln_btn, self._bulk_btn):
            w.config(state=st)

    def _sb(self, text):
        self._sb_lbl.config(text=text)

    def _verbinden(self, cfg):
        self.cfg    = cfg
        self.client = GiteaClient(cfg["url"], cfg["token"])
        self._sb("Verbinde ...")

        def check():
            try:
                user = self.client.test_connection()
                orgs = self.client.get_orgs()
                self.after(0, lambda: self._nach_verbindung(user, orgs))
            except requests.HTTPError as e:
                msg = http_fehler(e)
                self.after(0, lambda: self._verbindungsfehler(msg))
            except Exception as e:
                self.after(0, lambda: self._verbindungsfehler(str(e)))
        threading.Thread(target=check, daemon=True).start()

    def _nach_verbindung(self, user, orgs):
        self.orgs = orgs
        self._hdr_lbl.config(
            text="Verbunden als {}  |  {}".format(
                user.get("login", "?"), self.cfg["url"]),
            fg=C["success"])
        namen = [o["username"] for o in orgs]
        self._org_cb["values"] = namen
        if namen:
            self._org_cb.current(0)
        self._ui_aktiv(True)
        self._sb("{} Organisation(en) geladen.".format(len(namen)))
        self._lade_repos()

    def _verbindungsfehler(self, msg):
        self._hdr_lbl.config(text="Verbindungsfehler", fg=C["danger"])
        self._sb("Verbindung fehlgeschlagen.")
        messagebox.showerror("Verbindungsfehler", msg, parent=self)

    def _lade_repos(self):
        org = self._org_var.get()
        if not org or not self.client:
            return
        self._sb("Lade Repositories ...")
        self._ref_btn.config(state="disabled")
        self._counts = {}

        def fetch():
            try:
                repos = self.client.get_repos(org)
                self.after(0, lambda: self._zeige_repos(repos))
            except requests.HTTPError as e:
                msg = http_fehler(e)
                self.after(0, lambda: self._lade_fehler(msg))
            except Exception as e:
                self.after(0, lambda: self._lade_fehler(str(e)))
        threading.Thread(target=fetch, daemon=True).start()

    def _zeige_repos(self, repos):
        self.repos = repos
        self._ref_btn.config(state="normal")
        self._filter()
        n = len(repos)
        self._sb("{} Repositor{} gefunden.".format(
            n, "y" if n == 1 else "ies"))
        self._lade_counts()

    def _filter(self):
        suche = self._suche.get().lower()
        self._tree.delete(*self._tree.get_children())
        for r in self.repos:
            name = r.get("name", "")
            if suche and suche not in name.lower():
                continue
            privat = r.get("private", False)
            cnt    = self._counts.get(name, {})
            self._tree.insert("", "end", iid=name,
                values=(
                    name,
                    r.get("description", "") or "-",
                    "Private" if privat else "Public",
                    r.get("language", "") or "-",
                    str(cnt["branches"]) if "branches" in cnt else "...",
                    str(cnt["commits"])  if "commits"  in cnt else "...",
                    r.get("stars_count", 0),
                    format_datum(r),
                ),
                tags=("private" if privat else "public",))

    def _lade_fehler(self, msg):
        self._ref_btn.config(state="normal")
        self._sb("Fehler beim Laden.")
        messagebox.showerror("Ladefehler", msg, parent=self)

    def _lade_counts(self):
        org        = self._org_var.get()
        repo_names = [r.get("name", "") for r in self.repos if r.get("name")]
        sem        = threading.Semaphore(5)

        def fetch_one(repo_name):
            with sem:
                try:
                    b = self.client.get_branch_count(org, repo_name)
                    c = self.client.get_commit_count(org, repo_name)
                except Exception:
                    b, c = -1, -1
                self.after(0, lambda: self._set_count(repo_name, b, c))
        for name in repo_names:
            threading.Thread(target=fetch_one, args=(name,), daemon=True).start()

    def _set_count(self, repo_name, branches, commits):
        self._counts[repo_name] = {"branches": branches, "commits": commits}
        if self._tree.exists(repo_name):
            self._tree.set(repo_name, "branches",
                           str(branches) if branches >= 0 else "-")
            self._tree.set(repo_name, "commits",
                           str(commits)  if commits  >= 0 else "-")

    def _sortiere(self, col):
        def key(val):
            if col in ("branches", "commits", "sterne"):
                try:    return (0, int(val))
                except: return (1, val)
            return (0, val.lower())
        items = [(self._tree.set(k, col), k) for k in self._tree.get_children("")]
        for i, (_, k) in enumerate(sorted(items, key=lambda x: key(x[0]))):
            self._tree.move(k, "", i)

    def _zeige_km(self, event):
        row = self._tree.identify_row(event.y)
        if row:
            self._tree.selection_set(row)
            self._km.post(event.x_root, event.y_root)

    def _sel(self):
        sel = self._tree.selection()
        return sel[0] if sel else None

    def _kopiere_url(self):
        name = self._sel()
        if not name:
            return
        url = "{}/{}/{}".format(
            self.cfg.get("url", "").rstrip("/"), self._org_var.get(), name)
        self.clipboard_clear()
        self.clipboard_append(url)
        self._sb("URL kopiert: {}".format(url))

    def _clone_repo(self):
        name = self._sel()
        if not name:
            return
        org = self._org_var.get()
        rd  = next((r for r in self.repos if r.get("name") == name), None)
        clone_url = rd.get("clone_url", "") if rd else \
            "{}/{}/{}.git".format(self.cfg.get("url", "").rstrip("/"), org, name)
        CloneDialog(self, org, name, clone_url, self.cfg.get("token", ""))

    def _push_assistent(self):
        """Oeffnet Push-Assistent fuer ein lokal vorhandenes Repo."""
        name = self._sel()
        if not name:
            return
        pfad = Path(CLONE_ZIEL) / name
        if not pfad.exists():
            pfad = filedialog.askdirectory(
                title="Lokales Repository-Verzeichnis fuer '{}' auswaehlen".format(name),
                initialdir=str(CLONE_ZIEL))
            if not pfad:
                return
            pfad = Path(pfad)
        PushAssistant(self, pfad, name)

    def _loesche_repo(self):
        name = self._sel()
        if not name:
            return
        org = self._org_var.get()
        if not messagebox.askyesno(
            "Repository loeschen",
            "Soll '{}/{}' wirklich dauerhaft geloescht werden?\n\n"
            "Diese Aktion kann NICHT rueckgaengig gemacht werden!".format(org, name),
            icon="warning", parent=self):
            return
        eingabe = simpledialog.askstring(
            "Bestaetigung", "Repository-Namen '{}' eintippen:".format(name),
            parent=self)
        if eingabe != name:
            messagebox.showinfo("Abgebrochen", "Name stimmt nicht ueberein.")
            return

        def loeschen():
            try:
                self.client.delete_repo(org, name)
                self.after(0, lambda: self._nach_loeschen(name))
            except requests.HTTPError as e:
                msg = http_fehler(e)
                self.after(0, lambda: messagebox.showerror("Fehler", msg, parent=self))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("Fehler", str(e), parent=self))
        threading.Thread(target=loeschen, daemon=True).start()

    def _nach_loeschen(self, name):
        self._sb("Repository '{}' geloescht.".format(name))
        self._lade_repos()

    def _oeffne_konfig(self):
        KonfigDialog(self, lade_config(),
                     callback=lambda cfg: self._verbinden(cfg) if cfg else None)

    def _oeffne_neues_repo(self):
        org = self._org_var.get()
        if not org:
            messagebox.showinfo("Hinweis",
                                "Bitte zuerst eine Organisation auswaehlen.")
            return
        NeuesRepoDialog(self, org, self.client, on_success=self._lade_repos)

    def _oeffne_cherrypicker(self):
        CherrypickerDialog(self,
                           diff_launcher=self.cfg.get(
                               "diff_launcher", "/usr/bin/meldlauncher"))

    def _oeffne_bulk_clone(self):
        if not self.client:
            messagebox.showinfo("Hinweis", "Bitte zuerst verbinden.")
            return
        BulkCloneDialog(self, self.client, self.orgs, self._org_var.get())

    def _ueber(self):
        messagebox.showinfo(
            "Ueber Gitea Repository Manager",
            "Gitea Repository Manager  v1.5\n\n"
            "Features:\n"
            "  - Repositories anzeigen, anlegen, loeschen\n"
            "  - Einzel-Clone mit Editor-Auswahl\n"
            "  - Vorhandene-Repos-Dialog mit git pull\n"
            "  - Bulk-Clone: Organisation oder gesamtes Gitea\n"
            "  - Push-Assistent: Diff, LINT, PUSH, AUTOcommit\n"
            "  - Dateimanager-Integration\n\n"
            "Abhaengigkeiten: Python 3.10+, requests\n"
            "System: git  meld  (sudo apt install git meld)",
            parent=self)


# ---------------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app = App()
    app.mainloop()
