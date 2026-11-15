"""Archiviazione di un progetto: una cartella autonoma con il progetto e (a scelta) i
file originali, che si può spostare su un altro disco e riaprire. Nessuna dipendenza da Qt.

Cartella creata:
    Nome_archivio/
      Nome.moviola          progetto, con i percorsi relativi alla cartella
      media/                file originali copiati (codice_nomefile.ext)
      esportazioni/         video esportati aggiunti a mano
      copie_di_sicurezza/   le ultime versioni salvate del progetto
      elenco-file.txt       cosa c'è, da dove arriva
      LEGGIMI.txt           come riaprirlo
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
import zipfile

from . import paths
from .project import Project

MODES = ("project", "used", "all")
MODE_LABELS = {
    "project": "Solo progetto",
    "used": "Progetto + clip usate in timeline",
    "all": "Progetto + tutte le clip",
}
CHUNK = 8 * 1024 * 1024


class ArchiveError(Exception):
    pass


def _load(project_path):
    pr = Project.load(project_path)       # ritrova anche i file spostati (percorsi relativi)
    return pr


def used_source_ids(pr) -> set:
    return {i.get("source") for i in pr.items if i.get("source")}


def plan(project_path, mode="used"):
    """Che cosa verrebbe copiato: {'items': [...], 'bytes': totale, 'missing': [...]}.
    Ogni voce: id, nome, percorso, dimensione, usata, mancante, copiare."""
    pr = _load(project_path)
    used = used_source_ids(pr)
    items, missing, total = [], [], 0
    for s in pr.sources:
        path = s.get("path", "")
        ok = os.path.exists(path)
        size = os.path.getsize(path) if ok else 0
        copy_it = ok and (mode == "all" or (mode == "used" and s["id"] in used))
        e = {"id": s["id"], "name": s.get("label") or s.get("name") or os.path.basename(path), "path": path,
             "size": size, "used": s["id"] in used, "missing": not ok, "copy": copy_it}
        items.append(e)
        if not ok and (mode == "all" or s["id"] in used):
            missing.append(e)
        if copy_it:
            total += size
    return {"items": items, "bytes": total, "missing": missing}


def backups_of(project_path):
    d = paths.internal_dir("copie_di_sicurezza")
    base = os.path.splitext(os.path.basename(project_path))[0]
    try:
        files = sorted(f for f in os.listdir(d) if f.startswith(base + "_") and f.endswith(".moviola"))
    except OSError:
        return []
    return [os.path.join(d, f) for f in files[-5:]]


def _unique_dir(parent, name):
    d = os.path.join(parent, name)
    n = 2
    while os.path.exists(d):
        d = os.path.join(parent, f"{name} {n}")
        n += 1
    return d


def _copy_file(src, dst, progress, cancel):
    """Copia a blocchi (con avanzamento e annullamento) e controlla la dimensione."""
    size = os.path.getsize(src)
    with open(src, "rb") as fi, open(dst, "wb") as fo:
        while True:
            if cancel is not None and cancel.is_set():
                raise ArchiveError("Archiviazione annullata.")
            buf = fi.read(CHUNK)
            if not buf:
                break
            fo.write(buf)
            progress(len(buf))
    try:
        shutil.copystat(src, dst)
    except OSError:
        pass
    if os.path.getsize(dst) != size:
        raise ArchiveError(f"La copia di «{os.path.basename(src)}» non è completa (spazio esaurito?).")


def run(project_path, dest_parent, mode="used", extra_files=(), make_zip=False, progress=None,
        cancel: threading.Event | None = None):
    """Crea l'archivio in dest_parent. progress(frazione 0..1). Restituisce il percorso
    della cartella (o dello zip). Solleva ArchiveError se qualcosa non va."""
    if mode not in MODES:
        raise ArchiveError("Modo di archiviazione sconosciuto.")
    progress = progress or (lambda f: None)
    name = os.path.splitext(os.path.basename(project_path))[0]
    if not os.path.isdir(dest_parent):
        raise ArchiveError("La cartella di destinazione non esiste.")
    pl = plan(project_path, mode)
    extra = [p for p in extra_files if os.path.isfile(p)]
    need = pl["bytes"] + sum(os.path.getsize(p) for p in extra)
    free = shutil.disk_usage(dest_parent).free
    if need * 1.02 + 50 * 1024 * 1024 > free:
        raise ArchiveError(f"Spazio insufficiente: servono {paths.human_size(need)}, "
                           f"liberi {paths.human_size(free)}.")
    out = _unique_dir(dest_parent, f"{name}_archivio")
    os.makedirs(out)
    done = [0]
    total = max(need, 1)

    def tick(n):
        done[0] += n
        progress(min(0.98, done[0] / total))

    try:
        pr = _load(project_path)
        copied = {}
        media_dir = os.path.join(out, "media")
        for e in pl["items"]:
            if not e["copy"]:
                continue
            os.makedirs(media_dir, exist_ok=True)
            if e["path"] in copied:                      # stesso file usato da più clip
                continue
            fname = f"{e['id']}_{os.path.basename(e['path'])}"
            dst = os.path.join(media_dir, fname)
            _copy_file(e["path"], dst, tick, cancel)
            copied[e["path"]] = dst
        for s in pr.sources:
            new = copied.get(s.get("path"))
            if new:
                s["path"] = new                           # il nome mostrato (name/label) resta uguale
        pr.data["archive"] = {"date": time.strftime("%Y-%m-%d %H:%M"), "mode": mode,
                              "from": os.path.abspath(project_path)}
        new_project = os.path.join(out, name + ".moviola")
        pr.save(new_project)                              # percorsi relativi alla nuova cartella
        if extra:
            ex = os.path.join(out, "esportazioni")
            os.makedirs(ex, exist_ok=True)
            for p in extra:
                _copy_file(p, os.path.join(ex, os.path.basename(p)), tick, cancel)
        bk = backups_of(project_path)
        if bk:
            d = os.path.join(out, "copie_di_sicurezza")
            os.makedirs(d, exist_ok=True)
            for p in bk:
                shutil.copy2(p, d)
        _write_lists(out, name, project_path, pl, mode, extra)
        # controllo finale: il progetto archiviato deve ritrovare tutti i file copiati
        chk = Project.load(new_project)
        wanted = {x["id"] for x in pl["items"] if x["copy"]}
        lost = [s["id"] for s in chk.sources if s["id"] in wanted and not os.path.exists(s["path"])]
        if lost:
            raise ArchiveError("Controllo non riuscito: file non ritrovati nell'archivio: " + ", ".join(lost))
        result = out
        if make_zip:
            result = _zip_folder(out)
            shutil.rmtree(out, ignore_errors=True)
        progress(1.0)
        return result
    except BaseException:
        shutil.rmtree(out, ignore_errors=True)            # niente cartelle a metà
        raise


def _zip_folder(folder):
    zpath = folder.rstrip("/\\") + ".zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _dirs, files in os.walk(folder):
            for f in files:
                full = os.path.join(root, f)
                z.write(full, os.path.relpath(full, os.path.dirname(folder)))
    return zpath


def _write_lists(out, name, project_path, pl, mode, extra):
    lines = [f"Archivio del progetto «{name}»", time.strftime("Creato il %d/%m/%Y alle %H:%M"),
             f"Contenuto: {MODE_LABELS[mode]}", f"Progetto originale: {os.path.abspath(project_path)}", ""]
    for e in pl["items"]:
        if e["copy"]:
            state = f"copiato in media/{e['id']}_{os.path.basename(e['path'])}"
        elif e["missing"]:
            state = "NON TROVATO sul disco (non copiato)"
        else:
            state = "non copiato (resta dov'era)"
        lines.append(f"{e['id']}  {e['name']}  ·  {paths.human_size(e['size'])}  ·  "
                     f"{'usata' if e['used'] else 'non usata'} in timeline  ·  {state}")
        lines.append(f"    originale: {e['path']}")
    if extra:
        lines += ["", "Video esportati:"] + [f"  esportazioni/{os.path.basename(p)}" for p in extra]
    with open(os.path.join(out, "elenco-file.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    with open(os.path.join(out, "LEGGIMI.txt"), "w", encoding="utf-8") as f:
        f.write(
            f"Come riaprire «{name}»\n\n"
            "1. Apri Moviola, scheda ARCHIVIO PROGETTI.\n"
            f"2. Clicca «Apri altro…» e scegli il file {name}.moviola di questa cartella\n"
            "   (oppure «Cartella…» e scegli la cartella che contiene questo archivio).\n"
            "3. I file in media/ si ritrovano da soli: puoi spostare l'intera cartella su un altro\n"
            "   disco o computer senza ricollegare nulla.\n\n"
            "Le anteprime e i file di lavoro (proxy, miniature) non sono inclusi: Moviola li rifà\n"
            "da sola alla prima apertura.\n")


def summary_json(project_path, mode):
    """Per i test e per la finestra: numero di file e byte."""
    pl = plan(project_path, mode)
    return json.dumps({"files": sum(1 for e in pl["items"] if e["copy"]), "bytes": pl["bytes"],
                       "missing": len(pl["missing"])})
