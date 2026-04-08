#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gitea Repository Manager v0.5
==============================
Grapical Linux-application to manage gitea repositories
within organizations and personal account with a token
Usge:   python3 gitea_manager.py
Depends: git ,  pip install requests tk

Verwaltung von Gitea-Repositories inkl.:
  - git clone mit Live-Ausgabe
  - Editor-Auswahl nach Clone (VSCode / Geany / Pulsar)
  - Dateimanager-Start parallel oder explizit
  - Bulk-Clone: gesamte Organisation oder gesamtes Gitea

Verwendung:        python3 gitea_manager.py
Abhaengigkeiten:   pip install requests
System (Clone):    sudo apt install git
"""

import tkinter as tk
from tkinter import ttk, messagebox, simpledialog, filedialog
import json
import os
import shutil
import subprocess
import threading
import requests
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, urlunparse

# ---------------------------------------------------------------------------
# Konstanten
# ---------------------------------------------------------------------------
CONFIG_DIR  = Path.home() / ".config" / "gitea-manager"
CONFIG_FILE = CONFIG_DIR / "config.json"
CLONE_ZIEL  = Path("/home/guest/code")

GITIGNORE_TEMPLATES = [
    "", "Python", "Node", "Go", "Java", "Rust", "C", "C++",
    "Ruby", "PHP", "Swift", "Kotlin", "Dart", "Unity",
]
LIZENZEN = [
    "", "MIT", "Apache-2.0", "GPL-3.0", "LGPL-3.0",
    "BSD-2-Clause", "BSD-3-Clause", "MPL-2.0", "AGPL-3.0",
]

# Editoren: (Anzeigename, Befehl, Pruefe-ob-vorhanden)
EDITOREN = [
    ("VS Code",  "code",   True),
    ("Geany",    "geany",  True),
    ("Pulsar",   "pulsar", True),
]

# Dateimanager (werden der Reihe nach probiert)
DATEIMANAGER = ["nautilus", "thunar", "nemo", "pcmanfm", "dolphin",
                "caja", "xdg-open"]

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
        403: "Zugriff verweigert.\nFehlende Berechtigungen.",
        404: "Ressource nicht gefunden.\nURL oder Namen pruefen.",
        409: "Konflikt - Repository existiert moeglicherweise bereits.",
        422: "Ungueltige Eingabe. Bitte alle Felder pruefen.",
    }
    return meldungen.get(code, "HTTP-Fehler {}: {}".format(code, exc))


def zentriere(fenster, parent):
    fenster.update_idletasks()
    x = parent.winfo_rootx() + (parent.winfo_width()  - fenster.winfo_width())  // 2
    y = parent.winfo_rooty() + (parent.winfo_height() - fenster.winfo_height()) // 2
    fenster.geometry("+{}+{}".format(x, y))


def flat_btn(parent, text, cmd, bg, fg, bold=False, **kw):
    font = ("Monospace", 10, "bold") if bold else ("Monospace", 10)
    return tk.Button(
        parent, text=text, command=cmd,
        bg=bg, fg=fg,
        activebackground=C["accent2"],
        activeforeground=C["bg"],
        relief="flat", font=font,
        padx=12, pady=5, **kw)


def finde_dateimanager():
    """Gibt den ersten verfuegbaren Dateimanager-Befehl zurueck."""
    for dm in DATEIMANAGER:
        if shutil.which(dm):
            return dm
    return None


def oeffne_verzeichnis(pfad):
    """Oeffnet ein Verzeichnis im Dateimanager."""
    dm = finde_dateimanager()
    if dm:
        subprocess.Popen([dm, str(pfad)],
                         env={**os.environ},
                         start_new_session=True)
    else:
        messagebox.showwarning(
            "Kein Dateimanager",
            "Kein Dateimanager gefunden.\nBitte manuell installieren:\n"
            "z.B. sudo apt install nautilus")


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
        resp = self.session.get(
            self.base_url + "/api/v1" + path,
            params=params, timeout=15)
        resp.raise_for_status()
        return resp

    def _get(self, path, params=None):
        return self._raw_get(path, params).json()

    def _post(self, path, payload):
        resp = self.session.post(
            self.base_url + "/api/v1" + path,
            json=payload, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def _delete(self, path):
        self.session.delete(
            self.base_url + "/api/v1" + path,
            timeout=15).raise_for_status()

    def _total_count(self, path):
        try:
            resp = self._raw_get(path, {"limit": 1, "page": 1})
            hdr  = resp.headers.get("X-Total-Count", "")
            return int(hdr) if hdr else len(resp.json())
        except Exception:
            return -1

    def _alle_seiten(self, path, params=None):
        """Laedt alle Seiten einer paginierten API-Ressource."""
        result, page = [], 1
        base_params  = dict(params or {})
        base_params["limit"] = 50
        while True:
            base_params["page"] = page
            batch = self._get(path, base_params)
            if not batch:
                break
            result.extend(batch)
            if len(batch) < 50:
                break
            page += 1
        return result

    def test_connection(self):
        return self._get("/user")

    def get_orgs(self):
        return self._alle_seiten("/user/orgs")

    def get_repos(self, org):
        return self._alle_seiten("/orgs/{}/repos".format(org))

    def get_alle_repos(self):
        """Alle Repos aller sichtbaren Orgs + eigene Repos des Token-Nutzers."""
        orgs  = self.get_orgs()
        repos = []
        for org in orgs:
            try:
                repos.extend(self.get_repos(org["username"]))
            except Exception:
                pass
        # Eigene Repos (koennen ausserhalb von Orgs existieren)
        try:
            eigene = self._alle_seiten("/repos/search",
                                       {"token": self.token, "limit": 50})
            # Duplikate vermeiden
            vorh  = {r.get("full_name") for r in repos}
            for r in eigene:
                if r.get("full_name") not in vorh:
                    repos.append(r)
        except Exception:
            pass
        return repos

    def get_branches(self, org, repo):
        return self._alle_seiten("/repos/{}/{}/branches".format(org, repo))

    def get_branch_count(self, org, repo):
        return self._total_count("/repos/{}/{}/branches".format(org, repo))

    def get_commit_count(self, org, repo):
        return self._total_count("/repos/{}/{}/commits".format(org, repo))

    def create_repo(self, org, payload):
        return self._post("/orgs/{}/repos".format(org), payload)

    def delete_repo(self, org, repo):
        self._delete("/repos/{}/{}".format(org, repo))


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
            row=0, column=0, columnspan=2,
            pady=(16, 4), padx=16, sticky="w")

        tk.Label(self, text="Gitea-URL:", bg=C["bg2"],
                 fg=C["fg"], font=("Monospace", 10)).grid(
            row=1, column=0, sticky="w", **PAD)
        self.url_var = tk.StringVar(value=cfg.get("url", "https://"))
        tk.Entry(self, textvariable=self.url_var, width=44,
                 bg=C["bg3"], fg=C["fg"], insertbackground=C["fg"],
                 relief="flat", font=("Monospace", 10)).grid(
            row=1, column=1, **PAD)

        tk.Label(self, text="API-Token:", bg=C["bg2"],
                 fg=C["fg"], font=("Monospace", 10)).grid(
            row=2, column=0, sticky="w", **PAD)
        self._tv = tk.StringVar(value=cfg.get("token", ""))
        self._te = tk.Entry(self, textvariable=self._tv, width=44,
                            show="\u2022", bg=C["bg3"], fg=C["fg"],
                            insertbackground=C["fg"],
                            relief="flat", font=("Monospace", 10))
        self._te.grid(row=2, column=1, **PAD)

        sv = tk.BooleanVar()
        tk.Checkbutton(self, text="Token anzeigen", variable=sv,
                       command=lambda: self._te.config(
                           show="" if sv.get() else "\u2022"),
                       bg=C["bg2"], fg=C["fg_dim"],
                       selectcolor=C["bg3"], activebackground=C["bg2"],
                       font=("Monospace", 9)).grid(
            row=3, column=1, sticky="w", padx=16)

        tk.Label(
            self,
            text="Token: Gitea > Einstellungen > Anwendungen > Token generieren",
            bg=C["bg2"], fg=C["fg_dim"], font=("Monospace", 8)).grid(
            row=4, column=0, columnspan=2, padx=16, pady=(0, 8), sticky="w")

        f = tk.Frame(self, bg=C["bg2"])
        f.grid(row=5, column=0, columnspan=2,
               pady=(4, 16), padx=16, sticky="e")
        flat_btn(f, "Abbrechen", self._abbrechen,
                 C["bg3"], C["fg_dim"]).pack(side="left", padx=(0, 8))
        flat_btn(f, "Verbinden & Speichern", self._speichern,
                 C["accent"], C["bg"], bold=True).pack(side="left")

    def _speichern(self):
        url   = self.url_var.get().strip()
        token = self._tv.get().strip()
        if not url or not token:
            messagebox.showwarning(
                "Eingabe fehlt", "Bitte URL und Token angeben.", parent=self)
            return
        cfg = {"url": url, "token": token}
        speichere_config(cfg)
        self.destroy()
        self._cb(cfg)

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
                 bg=C["bg2"], fg=C["accent"]).pack(
            padx=16, pady=(14, 8), anchor="w")

        form = tk.Frame(self, bg=C["bg2"])
        form.pack(padx=16, fill="x")

        def lbl(row, text, req=False):
            tk.Label(form, text=text + (" *" if req else ""),
                     bg=C["bg2"],
                     fg=C["danger"] if req else C["fg_dim"],
                     font=("Monospace", 9)).grid(
                row=row, column=0, sticky="w", padx=12, pady=4)

        def entry(row, var):
            tk.Entry(form, textvariable=var, width=38,
                     bg=C["bg3"], fg=C["fg"],
                     insertbackground=C["fg"],
                     relief="flat",
                     font=("Monospace", 10)).grid(
                row=row, column=1, padx=8, pady=4, sticky="w")

        self.name_var = tk.StringVar()
        lbl(0, "Repository-Name", req=True)
        entry(0, self.name_var)

        self.desc_var = tk.StringVar()
        lbl(1, "Beschreibung")
        entry(1, self.desc_var)

        lbl(2, "Sichtbarkeit")
        self.privat_var = tk.BooleanVar(value=False)
        vf = tk.Frame(form, bg=C["bg2"])
        vf.grid(row=2, column=1, padx=8, pady=4, sticky="w")
        for txt, val in [("Public", False), ("Private", True)]:
            tk.Radiobutton(vf, text=txt, variable=self.privat_var, value=val,
                           bg=C["bg2"], fg=C["fg"], selectcolor=C["bg3"],
                           activebackground=C["bg2"],
                           font=("Monospace", 10)).pack(
                side="left", padx=(0, 12))

        lbl(3, "Initialisieren")
        self.init_var = tk.BooleanVar(value=True)
        tk.Checkbutton(form, text="README.md anlegen",
                       variable=self.init_var,
                       bg=C["bg2"], fg=C["fg"],
                       selectcolor=C["bg3"], activebackground=C["bg2"],
                       font=("Monospace", 10)).grid(
            row=3, column=1, padx=8, pady=4, sticky="w")

        lbl(4, ".gitignore-Template")
        self.gitignore_var = tk.StringVar(value="")
        ttk.Combobox(form, textvariable=self.gitignore_var,
                     values=GITIGNORE_TEMPLATES, state="readonly", width=20,
                     font=("Monospace", 10)).grid(
            row=4, column=1, padx=8, pady=4, sticky="w")

        lbl(5, "Lizenz")
        self.lizenz_var = tk.StringVar(value="")
        ttk.Combobox(form, textvariable=self.lizenz_var,
                     values=LIZENZEN, state="readonly", width=20,
                     font=("Monospace", 10)).grid(
            row=5, column=1, padx=8, pady=4, sticky="w")

        tk.Label(self, text="* Pflichtfeld", bg=C["bg2"],
                 fg=C["fg_dim"], font=("Monospace", 8)).pack(
            anchor="w", padx=16)
        tk.Frame(self, bg=C["border"], height=1).pack(
            fill="x", padx=16, pady=8)

        bf = tk.Frame(self, bg=C["bg2"])
        bf.pack(padx=16, pady=(0, 14), anchor="e")
        flat_btn(bf, "Abbrechen", self.destroy,
                 C["bg3"], C["fg_dim"]).pack(side="left", padx=(0, 8))
        self._ok = flat_btn(bf, "Repository erstellen", self._erstellen,
                            C["success"], C["bg"], bold=True)
        self._ok.pack(side="left")

    def _erstellen(self):
        name = self.name_var.get().strip()
        if not name:
            messagebox.showwarning("Pflichtfeld",
                                   "Bitte einen Repository-Namen eingeben.",
                                   parent=self)
            return
        payload = {
            "name":        name,
            "description": self.desc_var.get().strip(),
            "private":     self.privat_var.get(),
            "auto_init":   self.init_var.get(),
        }
        gi = self.gitignore_var.get().strip()
        lz = self.lizenz_var.get().strip()
        if gi:
            payload["gitignores"] = gi
        if lz:
            payload["license"] = lz
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
    """
    Erscheint nach einem erfolgreichen Clone.
    Ermoeglicht:
      - README.md anlegen (falls nicht vorhanden) und in einem Editor oeffnen
      - Dateimanager parallel oder explizit oeffnen
    """

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
        # Titel
        tk.Label(self,
                 text="Repository erfolgreich geklont",
                 font=("Monospace", 12, "bold"),
                 bg=C["bg2"], fg=C["success"]).pack(
            padx=20, pady=(16, 2), anchor="w")
        tk.Label(self,
                 text=str(self.repo_pfad),
                 font=("Monospace", 9),
                 bg=C["bg2"], fg=C["fg_dim"]).pack(
            padx=20, pady=(0, 12), anchor="w")

        tk.Frame(self, bg=C["border"], height=1).pack(fill="x", padx=20, pady=(0, 10))

        # ── Editor-Auswahl ────────────────────────────────────────────────
        tk.Label(self, text="README.md oeffnen mit:",
                 font=("Monospace", 10, "bold"),
                 bg=C["bg2"], fg=C["accent"]).pack(
            padx=20, pady=(0, 6), anchor="w")

        self._editor_var = tk.StringVar(value="")
        ed_frame = tk.Frame(self, bg=C["bg2"])
        ed_frame.pack(padx=20, fill="x")

        self._ed_btns = []
        for anzeige, befehl, _ in EDITOREN:
            verfuegbar = shutil.which(befehl) is not None
            farbe = C["fg"]     if verfuegbar else C["fg_dim"]
            state = "normal"    if verfuegbar else "disabled"
            rb = tk.Radiobutton(
                ed_frame,
                text=anzeige + ("" if verfuegbar else "  (nicht installiert)"),
                variable=self._editor_var,
                value=befehl,
                state=state,
                bg=C["bg2"], fg=farbe,
                selectcolor=C["bg3"],
                activebackground=C["bg2"],
                font=("Monospace", 10),
                disabledforeground=C["fg_dim"])
            rb.pack(anchor="w", padx=8, pady=2)
            self._ed_btns.append(rb)
            # Ersten verfuegbaren Editor vorauswaehlen
            if verfuegbar and not self._editor_var.get():
                self._editor_var.set(befehl)

        # Checkbox: Dateimanager parallel oeffnen
        tk.Frame(self, bg=C["border"], height=1).pack(
            fill="x", padx=20, pady=(10, 6))

        self._fm_var = tk.BooleanVar(value=False)
        dm = finde_dateimanager()
        dm_lbl = "Dateimanager parallel oeffnen"
        if dm:
            dm_lbl += "  ({})".format(dm)
        else:
            dm_lbl += "  (kein Dateimanager gefunden)"

        tk.Checkbutton(
            self, text=dm_lbl,
            variable=self._fm_var,
            state="normal" if dm else "disabled",
            bg=C["bg2"], fg=C["fg"],
            selectcolor=C["bg3"],
            activebackground=C["bg2"],
            disabledforeground=C["fg_dim"],
            font=("Monospace", 10)).pack(
            padx=20, pady=(0, 6), anchor="w")

        # ── Buttons ───────────────────────────────────────────────────────
        tk.Frame(self, bg=C["border"], height=1).pack(fill="x", padx=20, pady=(4, 8))

        bf = tk.Frame(self, bg=C["bg2"])
        bf.pack(fill="x", padx=20, pady=(0, 16))

        flat_btn(bf, "Nur Dateimanager", self._nur_fm,
                 C["bg3"], C["accent2"]).pack(side="left")
        flat_btn(bf, "Schliessen", self.destroy,
                 C["bg3"], C["fg_dim"]).pack(side="left", padx=(8, 0))
        flat_btn(bf, "README oeffnen", self._oeffne,
                 C["accent"], C["bg"], bold=True).pack(side="right")

    # ── Aktionen ──────────────────────────────────────────────────────────

    def _stelle_readme_sicher(self) -> Path:
        """Legt README.md an falls nicht vorhanden. Gibt Pfad zurueck."""
        readme = self.repo_pfad / "README.md"
        if not readme.exists():
            with open(readme, "w", encoding="utf-8") as fh:
                fh.write("# {}\n\n".format(self.repo_name))
        return readme

    def _oeffne(self):
        editor = self._editor_var.get()
        if not editor:
            messagebox.showwarning("Kein Editor",
                                   "Bitte einen Editor auswaehlen.",
                                   parent=self)
            return
        readme = self._stelle_readme_sicher()
        try:
            subprocess.Popen([editor, str(readme)],
                             start_new_session=True,
                             env={**os.environ})
        except Exception as e:
            messagebox.showerror("Fehler beim Oeffnen",
                                 "{} konnte nicht gestartet werden:\n{}".format(
                                     editor, e),
                                 parent=self)
            return
        if self._fm_var.get():
            oeffne_verzeichnis(self.repo_pfad)
        self.destroy()

    def _nur_fm(self):
        oeffne_verzeichnis(self.repo_pfad)
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
        self.org       = org
        self.repo_name = repo_name
        self.clone_url = clone_url
        self.token     = token
        self._proc     = None
        self._running  = False
        self._fertig_pfad = None   # wird nach erfolgreichem Clone gesetzt
        self._baue_ui()
        self.protocol("WM_DELETE_WINDOW", self._schliessen)
        zentriere(self, parent)

    def _baue_ui(self):
        tk.Label(
            self,
            text="Repository klonen: {}/{}".format(self.org, self.repo_name),
            font=("Monospace", 12, "bold"),
            bg=C["bg2"], fg=C["accent"]).pack(
            padx=16, pady=(14, 6), anchor="w")

        # Optionen
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
                  relief="flat", font=("Monospace", 9), padx=4).grid(
            row=0, column=4)

        # URL-Vorschau
        pf = tk.Frame(self, bg=C["bg3"])
        pf.pack(fill="x", padx=16, pady=(0, 8))
        tk.Label(pf, text=" git clone ", bg=C["bg3"],
                 fg=C["fg_dim"], font=("Monospace", 9)).pack(side="left")
        self._prev_var = tk.StringVar()
        tk.Label(pf, textvariable=self._prev_var,
                 bg=C["bg3"], fg=C["warning"],
                 font=("Monospace", 9)).pack(side="left")
        self._upd_prev()

        # Terminal
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

        # Fortschritt
        self._pb = ttk.Progressbar(self, mode="indeterminate")
        self._pb.pack(fill="x", padx=16, pady=(0, 6))

        # Buttons
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
            return urlunparse(
                p._replace(netloc="{}@{}".format(self.token, p.netloc)))
        if proto == "HTTPS (anonym)":
            return base
        p = urlparse(base)
        return "git@{}:{}".format(p.netloc, p.path.lstrip("/"))

    def _upd_prev(self):
        url = self._build_url()
        anzeige = url.replace(self.token, "***") if self.token in url else url
        self._prev_var.set(anzeige)

    def _waehle_ziel(self):
        v = filedialog.askdirectory(
            title="Zielverzeichnis", initialdir=self._ziel.get(), parent=self)
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
        if repo_pfad.exists():
            if not messagebox.askyesno(
                "Existiert bereits",
                "'{}' existiert bereits.\nTrotzdem fortfahren?".format(repo_pfad),
                parent=self):
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
                command=self._oeffne_editor_dialog)
            self._cancel_btn.config(text="Schliessen", command=self.destroy)
        else:
            self._log("\ngit clone fehlgeschlagen (Exit-Code {})\n".format(rc), "err")
            self._clone_btn.config(
                state="normal", text="Erneut versuchen",
                bg=C["warning"], fg=C["bg"], command=self._starte)

    def _oeffne_editor_dialog(self):
        """Oeffnet den Editor/FM-Dialog und schliesst danach dieses Fenster."""
        pfad = self._fertig_pfad
        self.destroy()
        OeffnenDialog(self.master, pfad, self.repo_name)

    def _fehler(self, msg):
        self._running = False
        self._pb.stop()
        self._log("\nFehler: {}\n".format(msg), "err")
        self._clone_btn.config(
            state="normal", text="Erneut versuchen",
            bg=C["warning"], fg=C["bg"], command=self._starte)

    def _log(self, text, tag=""):
        self._out.config(state="normal")
        self._out.insert("end", text, tag)
        self._out.see("end")
        self._out.config(state="disabled")

    def _schliessen(self):
        if self._running and self._proc:
            if not messagebox.askyesno(
                "Abbrechen?", "Clone laeuft noch. Wirklich abbrechen?",
                parent=self):
                return
            try:
                self._proc.terminate()
            except Exception:
                pass
        self.destroy()


# ---------------------------------------------------------------------------
# Dialog: Bulk-Clone (Organisation oder gesamtes Gitea)
# ---------------------------------------------------------------------------
class BulkCloneDialog(tk.Toplevel):
    """
    Klon-Dialog fuer eine komplette Organisation oder alle sichtbaren Repos.
    Klont jedes Repo in ein Unterverzeichnis und optional alle Branches.
    """

    def __init__(self, parent, client: GiteaClient, orgs: list,
                 aktuell_org: str):
        super().__init__(parent)
        self.title("Bulk-Clone")
        self.resizable(True, True)
        self.minsize(700, 560)
        self.configure(bg=C["bg2"])
        self.grab_set()
        self.client     = client
        self.orgs       = orgs
        self._threads   = []
        self._stopp     = threading.Event()
        self._laufend   = False
        self._zaehler   = {"ok": 0, "err": 0, "gesamt": 0}
        self._lock      = threading.Lock()
        self._baue_ui(aktuell_org)
        self.protocol("WM_DELETE_WINDOW", self._schliessen)
        zentriere(self, parent)

    def _baue_ui(self, aktuell_org):
        tk.Label(self, text="Bulk-Clone",
                 font=("Monospace", 13, "bold"),
                 bg=C["bg2"], fg=C["accent"]).pack(
            padx=16, pady=(14, 6), anchor="w")

        opt = tk.Frame(self, bg=C["bg2"])
        opt.pack(fill="x", padx=16, pady=(0, 8))

        # Umfang
        tk.Label(opt, text="Umfang:", bg=C["bg2"],
                 fg=C["fg_dim"], font=("Monospace", 10)).grid(
            row=0, column=0, sticky="w", padx=(0, 10), pady=4)
        self._umfang_var = tk.StringVar(value="org")
        org_rb = tk.Radiobutton(opt, text="Aktuelle Organisation",
                                variable=self._umfang_var, value="org",
                                command=self._umfang_geaendert,
                                bg=C["bg2"], fg=C["fg"],
                                selectcolor=C["bg3"],
                                activebackground=C["bg2"],
                                font=("Monospace", 10))
        org_rb.grid(row=0, column=1, sticky="w")
        alle_rb = tk.Radiobutton(opt, text="Gesamtes Gitea (alle sichtbaren Repos)",
                                 variable=self._umfang_var, value="alle",
                                 command=self._umfang_geaendert,
                                 bg=C["bg2"], fg=C["fg"],
                                 selectcolor=C["bg3"],
                                 activebackground=C["bg2"],
                                 font=("Monospace", 10))
        alle_rb.grid(row=0, column=2, sticky="w", padx=(16, 0))

        # Organisation
        tk.Label(opt, text="Organisation:", bg=C["bg2"],
                 fg=C["fg_dim"], font=("Monospace", 10)).grid(
            row=1, column=0, sticky="w", padx=(0, 10), pady=4)
        self._org_var = tk.StringVar(value=aktuell_org)
        org_namen = [o["username"] for o in self.orgs]
        self._org_cb = ttk.Combobox(opt, textvariable=self._org_var,
                                    values=org_namen, state="readonly",
                                    width=24, font=("Monospace", 10))
        self._org_cb.grid(row=1, column=1, columnspan=2, sticky="w")

        # Zielverzeichnis
        tk.Label(opt, text="Zielverzeichnis:", bg=C["bg2"],
                 fg=C["fg_dim"], font=("Monospace", 10)).grid(
            row=2, column=0, sticky="w", padx=(0, 10), pady=4)
        self._ziel_var = tk.StringVar(value=str(CLONE_ZIEL))
        ziel_frame = tk.Frame(opt, bg=C["bg2"])
        ziel_frame.grid(row=2, column=1, columnspan=2, sticky="w")
        tk.Entry(ziel_frame, textvariable=self._ziel_var, width=30,
                 bg=C["bg3"], fg=C["fg"], insertbackground=C["fg"],
                 relief="flat", font=("Monospace", 10)).pack(side="left")
        tk.Button(ziel_frame, text="...", command=self._waehle_ziel,
                  bg=C["bg3"], fg=C["accent"],
                  relief="flat", font=("Monospace", 9), padx=4).pack(
            side="left", padx=(4, 0))

        # Protokoll
        tk.Label(opt, text="Protokoll:", bg=C["bg2"],
                 fg=C["fg_dim"], font=("Monospace", 10)).grid(
            row=3, column=0, sticky="w", padx=(0, 10), pady=4)
        self._proto_var = tk.StringVar(value="HTTPS (Token)")
        ttk.Combobox(opt, textvariable=self._proto_var,
                     values=["HTTPS (Token)", "HTTPS (anonym)", "SSH"],
                     state="readonly", width=18,
                     font=("Monospace", 10)).grid(row=3, column=1, sticky="w")

        # Alle Branches klonen
        self._branches_var = tk.BooleanVar(value=True)
        tk.Checkbutton(opt, text="Alle Branches klonen (--mirror)",
                       variable=self._branches_var,
                       bg=C["bg2"], fg=C["fg"],
                       selectcolor=C["bg3"], activebackground=C["bg2"],
                       font=("Monospace", 10)).grid(
            row=4, column=0, columnspan=3, sticky="w", pady=(4, 0))

        # Parallele Worker
        tk.Label(opt, text="Parallele Verbindungen:", bg=C["bg2"],
                 fg=C["fg_dim"], font=("Monospace", 10)).grid(
            row=5, column=0, sticky="w", padx=(0, 10), pady=4)
        self._worker_var = tk.IntVar(value=3)
        ttk.Spinbox(opt, textvariable=self._worker_var,
                    from_=1, to=10, width=5,
                    font=("Monospace", 10)).grid(row=5, column=1, sticky="w")

        tk.Frame(self, bg=C["border"], height=1).pack(
            fill="x", padx=16, pady=(4, 8))

        # Fortschritt-Label
        self._prog_lbl = tk.Label(self, text="Bereit.",
                                  bg=C["bg2"], fg=C["fg_dim"],
                                  font=("Monospace", 9))
        self._prog_lbl.pack(padx=16, anchor="w")
        self._pb = ttk.Progressbar(self, mode="determinate")
        self._pb.pack(fill="x", padx=16, pady=(2, 6))

        # Log-Ausgabe
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

        # Buttons
        bf = tk.Frame(self, bg=C["bg2"])
        bf.pack(fill="x", padx=16, pady=(0, 14))
        self._stop_btn = flat_btn(bf, "Stopp", self._stoppe,
                                  C["danger"], C["bg"])
        self._stop_btn.pack(side="left")
        self._stop_btn.config(state="disabled")
        flat_btn(bf, "Schliessen", self._schliessen,
                 C["bg3"], C["fg_dim"]).pack(side="left", padx=(8, 0))
        self._start_btn = flat_btn(bf, "Clone starten", self._starte,
                                   C["accent"], C["bg"], bold=True)
        self._start_btn.pack(side="right")

        self._umfang_geaendert()

    # ── Hilfsmethoden ─────────────────────────────────────────────────────

    def _umfang_geaendert(self):
        st = "readonly" if self._umfang_var.get() == "org" else "disabled"
        self._org_cb.config(state=st)

    def _waehle_ziel(self):
        v = filedialog.askdirectory(title="Zielverzeichnis",
                                    initialdir=self._ziel_var.get(),
                                    parent=self)
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
        pct  = int(done / ges * 100) if ges else 0
        self._pb["value"] = pct
        self._prog_lbl.config(
            text="Fortschritt: {}/{} — OK: {}  Fehler: {}".format(
                done, ges, ok, err))

    def _build_clone_url(self, repo_data):
        base = repo_data.get("clone_url", "")
        proto = self._proto_var.get()
        token = self.client.token
        if proto == "HTTPS (Token)":
            p = urlparse(base)
            return urlunparse(
                p._replace(netloc="{}@{}".format(token, p.netloc)))
        if proto == "HTTPS (anonym)":
            return base
        p = urlparse(base)
        return "git@{}:{}".format(p.netloc, p.path.lstrip("/"))

    # ── Clone-Prozess ─────────────────────────────────────────────────────

    def _starte(self):
        self._laufend = True
        self._stopp.clear()
        self._zaehler = {"ok": 0, "err": 0, "gesamt": 0}
        self._start_btn.config(state="disabled")
        self._stop_btn.config(state="normal")

        def run_all():
            # Repos ermitteln
            self.after(0, lambda: self._log("Lade Repository-Liste ...\n", "info"))
            try:
                if self._umfang_var.get() == "org":
                    org   = self._org_var.get()
                    repos = self.client.get_repos(org)
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
                "{} Repositories gefunden. Starte Clone ...\n\n".format(n), "info"))
            self.after(0, self._upd_prog)

            ziel_basis = Path(self._ziel_var.get())
            alle_branches = self._branches_var.get()
            sem = threading.Semaphore(self._worker_var.get())

            def clone_one(rd):
                if self._stopp.is_set():
                    return
                full = rd.get("full_name", rd.get("name", "?"))
                name = rd.get("name", "unbekannt")
                # Unterverzeichnis: ziel/org/repo
                owner = rd.get("owner", {}).get("login", "") or \
                        rd.get("owner", {}).get("username", "")
                repo_ziel = (ziel_basis / owner / name
                             if owner else ziel_basis / name)
                with sem:
                    if self._stopp.is_set():
                        return
                    url = self._build_clone_url(rd)
                    try:
                        repo_ziel.parent.mkdir(parents=True, exist_ok=True)
                        git_args = ["git", "clone"]
                        if alle_branches:
                            # --mirror klont alle Branches als bare repo,
                            # danach checkout des default branch
                            git_args += ["--mirror"]
                            ziel_str = str(repo_ziel) + ".git"
                        else:
                            ziel_str = str(repo_ziel)
                        git_args += [url, ziel_str]

                        proc = subprocess.run(
                            git_args,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
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
                            self.after(0, lambda f=full, o=proc.stdout: self._log(
                                "  ERR {}: {}\n".format(f, o.strip()[:120]), "err"))
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
            self._threads = threads
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
        if self._stopp.is_set():
            self._log("\nGestoppt. {}/{} geklont, {} Fehler.\n".format(
                ok, ges, err), "dim")
        else:
            self._log(
                "\nFertig. {}/{} erfolgreich geklont, {} Fehler.\n".format(
                    ok, ges, err),
                "ok" if err == 0 else "warning")
        self._stop_btn.config(state="disabled")
        self._start_btn.config(state="normal", text="Erneut starten")

    def _stoppe(self):
        self._stopp.set()
        self._log("\nStopp angefordert ...\n", "dim")
        self._stop_btn.config(state="disabled")

    def _schliessen(self):
        if self._laufend:
            if not messagebox.askyesno(
                "Laufend",
                "Bulk-Clone laeuft noch.\nWirklich schliessen?",
                parent=self):
                return
            self._stoppe()
        self.destroy()


# ---------------------------------------------------------------------------
# Haupt-Applikation
# ---------------------------------------------------------------------------
class App(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("Gitea Repository Manager")
        self.geometry("1100x640")
        self.minsize(800, 480)
        self.configure(bg=C["bg"])

        # WICHTIG: self.cfg, NICHT self.config!
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
                  activebackground=C["bg3"],
                  activeforeground=C["accent"])
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
        # Header
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

        # Toolbar
        tb = tk.Frame(self, bg=C["bg"], pady=8)
        tb.pack(fill="x", padx=12)
        tk.Label(tb, text="Organisation:", bg=C["bg"],
                 fg=C["fg_dim"], font=("Monospace", 10)).pack(side="left")
        self._org_var = tk.StringVar()
        self._org_cb  = ttk.Combobox(
            tb, textvariable=self._org_var,
            state="readonly", width=22, font=("Monospace", 10))
        self._org_cb.pack(side="left", padx=(4, 10))
        self._org_cb.bind("<<ComboboxSelected>>", lambda _: self._lade_repos())
        self._ref_btn  = self._tbtn(
            tb, "Aktualisieren", self._lade_repos, C["bg3"], C["accent"])
        self._neu_btn  = self._tbtn(
            tb, "+ Neu", self._oeffne_neues_repo, C["accent"], C["bg"])
        self._cln_btn  = self._tbtn(
            tb, "Klonen", self._clone_repo, C["bg3"], C["accent2"])
        self._bulk_btn = self._tbtn(
            tb, "Bulk-Clone", self._oeffne_bulk_clone, C["bg3"], C["warning"])

        tk.Label(tb, text="Suche:", bg=C["bg"],
                 fg=C["fg_dim"], font=("Monospace", 10)).pack(side="right")
        self._suche = tk.StringVar()
        self._suche.trace_add("write", lambda *_: self._filter())
        tk.Entry(tb, textvariable=self._suche, width=20,
                 bg=C["bg3"], fg=C["fg"], insertbackground=C["fg"],
                 relief="flat", font=("Monospace", 10)).pack(
            side="right", padx=(0, 6))

        # Tabelle
        tbl = tk.Frame(self, bg=C["bg"])
        tbl.pack(fill="both", expand=True, padx=12, pady=(4, 0))
        cols = ("name", "beschreibung", "sichtbarkeit",
                "sprache", "branches", "commits", "sterne", "aktualisiert")
        self._tree = ttk.Treeview(
            tbl, columns=cols, show="headings", selectmode="browse")
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
                   activebackground=C["bg3"],
                   activeforeground=C["accent"])
        self._km = tk.Menu(self, tearoff=0, **kw2)
        self._km.add_command(label="Klonen",       command=self._clone_repo)
        self._km.add_command(label="Bulk-Clone",   command=self._oeffne_bulk_clone)
        self._km.add_separator()
        self._km.add_command(label="URL kopieren", command=self._kopiere_url)
        self._km.add_command(label="Loeschen",     command=self._loesche_repo)
        self._tree.bind("<Button-3>", self._zeige_km)
        self._tree.bind("<Double-1>", lambda _: self._clone_repo())

        self._sb_lbl = tk.Label(
            self, text="Bereit.", anchor="w",
            bg=C["bg3"], fg=C["fg_dim"], font=("Monospace", 8), padx=8)
        self._sb_lbl.pack(fill="x", side="bottom")
        self._ui_aktiv(False)

    def _tbtn(self, parent, text, cmd, bg, fg):
        b = tk.Button(parent, text=text, command=cmd,
                      bg=bg, fg=fg,
                      activebackground=C["accent2"],
                      activeforeground=C["bg"],
                      relief="flat", font=("Monospace", 10, "bold"),
                      padx=10, pady=4)
        b.pack(side="left", padx=4)
        return b

    def _ui_aktiv(self, a):
        st = "normal" if a else "disabled"
        for w in (self._org_cb, self._ref_btn, self._neu_btn,
                  self._cln_btn, self._bulk_btn):
            w.config(state=st)

    def _sb(self, text):
        self._sb_lbl.config(text=text)

    # ── Verbindung ─────────────────────────────────────────────────────────

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

    # ── Repositories ───────────────────────────────────────────────────────

    def _lade_repos(self):
        org = self._org_var.get()
        if not org or not self.client:
            return
        self._sb("Lade Repositories fuer '{}' ...".format(org))
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
            self._tree.insert(
                "", "end", iid=name,
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
                tags=("private" if privat else "public",),
            )

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
            threading.Thread(
                target=fetch_one, args=(name,), daemon=True).start()

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
                try:
                    return (0, int(val))
                except ValueError:
                    return (1, val)
            return (0, val.lower())
        items = [(self._tree.set(k, col), k)
                 for k in self._tree.get_children("")]
        for i, (_, k) in enumerate(sorted(items, key=lambda x: key(x[0]))):
            self._tree.move(k, "", i)

    # ── Kontextmenue ───────────────────────────────────────────────────────

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
            self.cfg.get("url", "").rstrip("/"),
            self._org_var.get(), name)
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
            "{}/{}/{}.git".format(
                self.cfg.get("url", "").rstrip("/"), org, name)
        CloneDialog(self, org, name, clone_url, self.cfg.get("token", ""))

    def _loesche_repo(self):
        name = self._sel()
        if not name:
            return
        org = self._org_var.get()
        if not messagebox.askyesno(
            "Repository loeschen",
            "Soll '{}/{}' wirklich dauerhaft geloescht werden?\n\n"
            "Diese Aktion kann NICHT rueckgaengig gemacht werden!".format(
                org, name),
            icon="warning", parent=self):
            return
        eingabe = simpledialog.askstring(
            "Bestaetigung",
            "Repository-Namen '{}' eintippen:".format(name),
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
                self.after(0, lambda: messagebox.showerror(
                    "Fehler", msg, parent=self))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror(
                    "Fehler", str(e), parent=self))

        threading.Thread(target=loeschen, daemon=True).start()

    def _nach_loeschen(self, name):
        self._sb("Repository '{}' geloescht.".format(name))
        self._lade_repos()

    # ── Dialoge ────────────────────────────────────────────────────────────

    def _oeffne_konfig(self):
        KonfigDialog(
            self, lade_config(),
            callback=lambda cfg: self._verbinden(cfg) if cfg else None)

    def _oeffne_neues_repo(self):
        org = self._org_var.get()
        if not org:
            messagebox.showinfo("Hinweis",
                                "Bitte zuerst eine Organisation auswaehlen.")
            return
        NeuesRepoDialog(self, org, self.client, on_success=self._lade_repos)

    def _oeffne_bulk_clone(self):
        if not self.client:
            messagebox.showinfo("Hinweis", "Bitte zuerst verbinden.")
            return
        BulkCloneDialog(self, self.client, self.orgs, self._org_var.get())

    def _ueber(self):
        messagebox.showinfo(
            "Ueber Gitea Repository Manager",
            "Gitea Repository Manager  v1.4\n\n"
            "Features:\n"
            "  - Repositories anzeigen, anlegen, loeschen\n"
            "  - Einzel-Clone mit Editor-Auswahl\n"
            "  - Bulk-Clone: Organisation oder gesamtes Gitea\n"
            "  - Dateimanager-Integration\n\n"
            "Abhaengigkeiten: Python 3.10+, requests\n"
            "System: git  (sudo apt install git)",
            parent=self)


# ---------------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app = App()
    app.mainloop()
