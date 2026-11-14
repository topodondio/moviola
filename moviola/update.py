"""Aggiornamenti da GitHub, senza Git né GitHub Desktop.

Il programma installato (vedi LEGGIMI.txt) è una copia del codice in una
cartella del computer. Qui si controlla se su GitHub c'è una versione più
nuova, si scarica il codice (meno di 1 MB) e lo si sostituisce; al riavvio
parte la versione nuova. Progetti, impostazioni e cache non vengono toccati.

Se il repository è privato serve una chiave d'accesso di sola lettura
(Preferenze → Aggiornamenti).
"""
from __future__ import annotations

import io
import os
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile

from . import __version__

REPO = "topodondio/moviola"
BRANCH = "main"
API = "https://api.github.com/repos/"


class UpdateError(Exception):
    pass


def app_root() -> str:
    """Cartella che contiene il pacchetto `moviola` (e i file di avvio)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def is_checkout(root=None) -> bool:
    """True se si sta usando la cartella di GitHub Desktop: lì si aggiorna con Pull."""
    return os.path.isdir(os.path.join(root or app_root(), ".git"))


def parse_version(text: str):
    nums = re.findall(r"\d+", text or "")
    return tuple(int(n) for n in nums[:4]) or (0,)


def is_newer(remote: str, local: str = __version__) -> bool:
    return parse_version(remote) > parse_version(local)


def _context():
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _get(url, token=None, accept="application/vnd.github+json", timeout=20) -> bytes:
    req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "Moviola",
                                               "X-GitHub-Api-Version": "2022-11-28"})
    if token:
        req.add_header("Authorization", "Bearer " + token.strip())
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_context()) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code in (401, 403) and token:
            raise UpdateError("La chiave d'accesso di GitHub non è valida o è scaduta "
                              "(Preferenze → Aggiornamenti).")
        if e.code in (401, 403, 404):
            raise UpdateError("Il repository su GitHub è privato: per cercare gli aggiornamenti rendilo "
                              "pubblico oppure inserisci una chiave d'accesso (vedi LEGGIMI.txt, "
                              "«Aggiornamenti»).")
        raise UpdateError(f"GitHub ha risposto con un errore ({e.code}).")
    except Exception as e:
        raise UpdateError(f"Impossibile contattare GitHub: controlla la connessione.\n({e})")


def remote_version(token=None, repo=REPO, branch=BRANCH) -> str:
    """Numero di versione pubblicato su GitHub."""
    raw = _get(f"{API}{repo}/contents/moviola/__init__.py?ref={branch}", token,
               accept="application/vnd.github.raw+json")
    m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', raw.decode("utf-8", "replace"))
    if not m:
        raise UpdateError("Non riesco a leggere la versione pubblicata su GitHub.")
    return m.group(1)


def check(token=None):
    """(c'è un aggiornamento?, versione su GitHub)."""
    v = remote_version(token)
    return is_newer(v), v


def download(token=None, repo=REPO, branch=BRANCH) -> bytes:
    return _get(f"{API}{repo}/zipball/{branch}", token, timeout=120)


def apply_zip(data: bytes, root=None) -> str:
    """Sostituisce il codice nella cartella del programma con quello dello zip
    scaricato da GitHub. Restituisce la versione installata."""
    root = root or app_root()
    if is_checkout(root):
        raise UpdateError("Questa è la cartella di GitHub Desktop: aggiorna con Pull.")
    tmp = tempfile.mkdtemp(prefix="moviola_agg_")
    try:
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            raise UpdateError("Lo scaricamento non è riuscito bene: riprova.")
        with zf as z:
            base = os.path.realpath(tmp)
            for n in z.namelist():  # niente percorsi che escono dalla cartella
                dest = os.path.realpath(os.path.join(tmp, n))
                if dest != base and not dest.startswith(base + os.sep):
                    raise UpdateError("L'archivio scaricato non è valido.")
            z.extractall(tmp)
        tops = [d for d in os.listdir(tmp) if os.path.isdir(os.path.join(tmp, d))]
        src = os.path.join(tmp, tops[0]) if len(tops) == 1 else tmp
        init = os.path.join(src, "moviola", "__init__.py")
        if not os.path.exists(init) or not os.path.exists(os.path.join(src, "moviola", "app.py")):
            raise UpdateError("L'archivio scaricato non contiene Moviola.")
        with open(init, encoding="utf-8") as f:
            m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', f.read())
        version = m.group(1) if m else "?"
        # file nuovi sopra ai vecchi, poi via quelli che nel codice nuovo non esistono più
        shutil.copytree(src, root, dirs_exist_ok=True)
        pkg_new, pkg_old = os.path.join(src, "moviola"), os.path.join(root, "moviola")
        for dirpath, dirnames, filenames in os.walk(pkg_old, topdown=False):
            rel = os.path.relpath(dirpath, pkg_old)
            if os.path.basename(dirpath) == "__pycache__":
                shutil.rmtree(dirpath, ignore_errors=True)
                continue
            for name in filenames:
                if not os.path.exists(os.path.join(pkg_new, rel, name)):
                    try:
                        os.remove(os.path.join(dirpath, name))
                    except OSError:
                        pass
            if rel != "." and not os.path.isdir(os.path.join(pkg_new, rel)):
                shutil.rmtree(dirpath, ignore_errors=True)
        for name in ("avvia_mac.command", "installa_mac.command"):
            p = os.path.join(root, name)
            if os.path.exists(p):
                try:
                    os.chmod(p, 0o755)
                except OSError:
                    pass
        return version
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def update(token=None) -> str:
    """Scarica e installa l'ultima versione. Restituisce il numero di versione."""
    return apply_zip(download(token))


def restart():
    """Fa ripartire Moviola con il file di avvio (che installa anche eventuali
    librerie nuove), un paio di secondi dopo. Da chiamare subito prima di
    chiudere il programma."""
    root = app_root()
    if os.name == "nt":
        target = ["cmd", "/c", os.path.join(root, "avvia_windows.bat")]
    else:
        target = ["/bin/bash", os.path.join(root, "avvia_mac.command")]
    if not os.path.exists(target[-1]):
        target = [sys.executable, "-m", "moviola"]
    code = "import subprocess,sys,time; time.sleep(2); subprocess.Popen(sys.argv[1:])"
    kw = {"creationflags": 0x08000000 | 0x00000008} if os.name == "nt" else {"start_new_session": True}
    try:
        subprocess.Popen([sys.executable, "-c", code] + target, cwd=root, close_fds=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)
        return True
    except Exception:
        return False


# ---------------------------------------------------- controllo all'avvio
def due(cfg: dict, now: float, every_hours: float = 20.0) -> bool:
    """Al massimo un controllo automatico al giorno."""
    if not cfg.get("auto_update_check", True) or is_checkout():
        return False
    return now - float(cfg.get("last_update_check", 0) or 0) > every_hours * 3600
