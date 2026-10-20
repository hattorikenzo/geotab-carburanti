#!/usr/bin/env python3
"""Merge conservativo prezzi MIMIT nel formato usato da Distributori Convenienti."""
import csv
import gzip
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path.cwd()
DATA = ROOT / "data"
MIMIT = "https://www.mimit.gov.it/images/exportCSV/"
MIRROR = "https://raw.githubusercontent.com/LucaDDDD/benzina-data/main/data/"
NAMES = ("prezzo_alle_8.csv", "anagrafica_impianti_attivi.csv")

def download(url, dest, timeout=100):
    subprocess.run(["curl", "-4", "-fLsS", "--connect-timeout", "15",
                    "--max-time", str(timeout), "--retry", "2",
                    "--retry-delay", "5", "--retry-all-errors",
                    "-A", "Mozilla/5.0", url, "-o", str(dest)], check=True)

def read_csv(path, mandatory):
    # I CSV MIMIT possono avere righe con separatori extra in campi testuali.
    # Le righe malformate vengono scartate e conteggiate, non provocano AttributeError.
    with path.open(encoding="utf-8-sig", errors="replace", newline="") as f:
        for _ in range(12):
            line = f.readline()
            if "idimpianto" in line.lower():
                header = line
                break
        else:
            raise ValueError(f"Intestazione idImpianto mancante: {path}")
        sep = ";" if header.count(";") >= header.count("|") else "|"
        headers = next(csv.reader([header], delimiter=sep))
        fields = [h.strip().lower() for h in headers]
        if not set(mandatory).issubset(fields):
            raise ValueError(f"Colonne mancanti in {path}: {set(mandatory)-set(fields)}")
        # Gestisce anche le righe con un campo extra: il parser non deve
        # interpretare una lista come stringa, né inventare una colonna.
        reader = csv.reader(f, delimiter=sep)
        rows = []
        malformed = 0
        for values in reader:
            if len(values) != len(fields):
                malformed += 1
                continue
            row = dict(zip(fields, (v.strip() for v in values)))
            if row.get("idimpianto"):
                rows.append(row)
        print(f"CSV {path.name}: {len(rows)} righe valide, {malformed} righe scartate", flush=True)
        if len(rows) < 100:
            raise ValueError(f"CSV troppo piccolo o malformato: {path}")
        if malformed > max(100, len(rows)//10):
            raise ValueError(f"Troppe righe CSV malformate: {path} ({malformed})")
    with path.open(encoding="utf-8-sig", errors="replace") as f:
        first = f.readline()
    return rows, first

def extraction_day(line):
    m = re.search(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})", line)
    if m: return date(*map(int,m.groups()))
    m = re.search(r"(\d{1,2})/(\d{1,2})/(20\d{2})", line)
    if m:
        d,mo,y=map(int,m.groups())
        return date(y,mo,d)
    return None

def acquire(tmp):
    paths = {n:tmp/n for n in NAMES}
    try:
        for n,p in paths.items(): download(MIMIT+n,p)
        prices, pline = read_csv(paths[NAMES[0]], ("idimpianto","desccarburante","prezzo","isself","dtcomu"))
        stations, sline = read_csv(paths[NAMES[1]], ("idimpianto","latitudine","longitudine"))
        days=[extraction_day(pline),extraction_day(sline)]
        if all(days) and days[0]!=days[1]: raise ValueError("Date estrazione incoerenti")
        return prices, stations, "MIMIT", (days[0] or days[1])
    except Exception as exc:
        print("MIMIT non raggiungibile o CSV non valido:",exc,flush=True)
        req=Request(MIRROR+"last_update.json",headers={"User-Agent":"geotab-carburanti"})
        with urlopen(req,timeout=25) as response: meta=json.load(response)
        d=date.fromisoformat(meta["ultima_estrazione"])
        age=(datetime.now(timezone.utc).date()-d).days
        if not 0<=age<=7: raise RuntimeError(f"Mirror troppo vecchio: {d} ({age} giorni)")
        for n,p in paths.items():
            archive=tmp/(n+".gz")
            download(f"{MIRROR}{d:%Y}/{d:%m}/{n[:-4]}-{d:%Y%m%d}.csv.gz",archive)
            with gzip.open(archive,"rb") as src,p.open("wb") as dst: shutil.copyfileobj(src,dst)
        prices,_=read_csv(paths[NAMES[0]],("idimpianto","desccarburante","prezzo","isself","dtcomu"))
        stations,_=read_csv(paths[NAMES[1]],("idimpianto","latitudine","longitudine"))
        return prices,stations,"GitHub benzina-data",d

def number(v):
    return float(str(v).replace(",","."))

def stamp(v):
    v=v.strip().replace(" ","T",1)
    # Mantiene la data e l'ora MIMIT senza introdurre conversioni di fuso.
    return v

def read_gz(path):
    if not path.exists(): return {"corrente":{},"prezzi":{}}
    with gzip.open(path,"rt",encoding="utf-8") as f: obj=json.load(f)
    if not isinstance(obj,dict): raise ValueError(f"Schema prezzi non valido: {path}")
    if not isinstance(obj.get("corrente",{}),dict) or not isinstance(obj.get("prezzi",{}),dict):
        raise ValueError(f"Schema prezzi incompatibile: {path}")
    return obj

def save_gz(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    raw=json.dumps(obj,ensure_ascii=False,separators=(",",":")).encode("utf-8")
    with gzip.GzipFile(filename="",mode="wb",fileobj=path.open("wb"),mtime=0) as f: f.write(raw)

def main():
    with tempfile.TemporaryDirectory() as directory:
        prices,stations,source,day=acquire(Path(directory))
    print(f"Fonte {source}; estrazione {day}; righe prezzi {len(prices)}; impianti {len(stations)}")
    if len(prices)<1000 or len(stations)<1000: raise RuntimeError("Dataset troppo piccolo: non pubblicato")
    # Se il mirror ripropone lo stesso giorno, il merge è idempotente.
    by_id=defaultdict(dict)
    for r in prices:
        try:
            sid=int(r["idimpianto"])
            fuel=r["desccarburante"].strip()
            mode=str(r["isself"]).strip().lower()
            mode="1" if mode in ("1","true","si","sì") else "0"
            value=number(r["prezzo"])
            when=stamp(r["dtcomu"])
            if not fuel or not when or not (0.1<=value<=10): continue
            key=f"{fuel}|{mode}"
            old=by_id[sid].get(key)
            if old is None or when>old[0]: by_id[sid][key]=[when,value]
        except (ValueError,KeyError): continue
    if len(by_id)<1000: raise RuntimeError("Troppi prezzi non validi: non pubblicato")
    changed=0
    for sid, entries in by_id.items():
        path=DATA/"prezzi"/f"{sid//1000:03d}"/f"{sid}.json.gz"
        obj=read_gz(path)
        current=obj.setdefault("corrente",{})
        history=obj.setdefault("prezzi",{})
        dirty=False
        for key,record in entries.items():
            old=current.get(key)
            if old and str(old[0])>record[0]: continue  # non retrocedere mai
            if old!=record:
                current[key]=record
                dirty=True
            arr=history.setdefault(key,[])
            if not isinstance(arr,list): raise ValueError(f"Storico inatteso {path} {key}")
            if not any(isinstance(x,list) and len(x)>=2 and x[0]==record[0] and float(x[1])==record[1] for x in arr):
                arr.append([record[0],record[1],source])
                arr.sort(key=lambda x:str(x[0]),reverse=True)
                dirty=True
        if dirty:
            save_gz(path,obj)
            changed+=1
    # NON rigenerare la griglia geografica senza conoscere il layout originale:
    # le celle esistenti vengono preservate.
    metadata={"fonte":source,"data_estrazione":day.isoformat() if day else None,
              "aggiornato_utc":datetime.now(timezone.utc).isoformat(),
              "impianti_con_prezzi":len(by_id),"file_prezzi_modificati":changed,
              "nota":"Griglia geo esistente preservata"}
    (DATA/"ultimo_aggiornamento_prezzi.json").write_text(
        json.dumps(metadata,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(metadata,ensure_ascii=False))

if __name__=="__main__": main()
