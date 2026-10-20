#!/usr/bin/env python3
"""Importa prezzi selezionati Mappa Carburanti SENZA assegnare isSelf.

Output separato: data/prezzi_mappa/NNN/ID.json.gz. Non modifica data/prezzi.
"""
import csv
import gzip
import io
import json
import math
import tempfile
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

URL = 'https://mappacarburanti.it/data/stazioni-italia.csv'
ROOT = Path.cwd() / 'data'
FUEL = {'benzina':'Benzina', 'gasolio':'Gasolio', 'gpl':'GPL', 'metano':'Metano'}
REQUIRED = {'id_impianto','fuel_type','price','updated_at'}


def dt(value):
    parsed = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('timestamp senza timezone')
    return parsed.astimezone(timezone.utc)


def load(path):
    if not path.exists():
        return {'fonte':'Mappa Carburanti Italia', 'modalita':'non_verificata', 'carburanti':{}}
    with gzip.open(path, 'rt', encoding='utf-8') as f:
        obj = json.load(f)
    if not isinstance(obj, dict) or not isinstance(obj.get('carburanti'), dict):
        raise ValueError(f'Archivio alternativo non valido: {path}')
    return obj


def save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(obj, ensure_ascii=False, separators=(',', ':'), sort_keys=True).encode()
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
        name = Path(tmp.name)
        with gzip.GzipFile(filename='', fileobj=tmp, mode='wb', mtime=0) as gz:
            gz.write(payload)
    name.replace(path)


def main():
    request = urllib.request.Request(URL, headers={'User-Agent':'geotab-carburanti/1.0'})
    with urllib.request.urlopen(request, timeout=90) as resp:
        raw = resp.read(30_000_001)
    if len(raw) > 30_000_000:
        raise RuntimeError('CSV troppo grande')
    reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig')))
    if not reader.fieldnames or not REQUIRED.issubset(reader.fieldnames):
        raise RuntimeError(f'Colonne CSV incompatibili: {reader.fieldnames}')
    now = datetime.now(timezone.utc)
    selected = defaultdict(dict)
    invalid = 0
    newest = None
    for row in reader:
        try:
            sid = int(row['id_impianto'])
            fuel = FUEL[row['fuel_type'].strip().lower()]
            price = float(row['price'].replace(',', '.'))
            when = dt(row['updated_at'])
            if sid <= 0 or not math.isfinite(price) or not 0.1 <= price <= 10:
                raise ValueError('prezzo/ID invalido')
            if when > now.replace(microsecond=0):
                raise ValueError('data futura')
            newest = max(newest, when) if newest else when
            prev = selected[sid].get(fuel)
            record = {'data_utc':when.isoformat().replace('+00:00','Z'), 'prezzo':price,
                      'modalita':'non_verificata', 'fonte':'Mappa Carburanti Italia'}
            if prev is None or dt(record['data_utc']) > dt(prev['data_utc']):
                selected[sid][fuel] = record
        except (KeyError, ValueError, TypeError, OverflowError):
            invalid += 1
    count = sum(map(len, selected.values()))
    if count < 10000 or not newest:
        raise RuntimeError(f'Dataset incompleto: {count} prezzi')
    age = (now - newest).total_seconds()/86400
    if age > 7:
        raise RuntimeError(f'Dataset alternativo vecchio ({age:.1f} giorni): non pubblicato')
    changed = 0
    for sid, fuels in selected.items():
        path = ROOT/'prezzi_mappa'/f'{sid//1000:03d}'/f'{sid}.json.gz'
        obj = load(path)
        saved = obj['carburanti']
        dirty = False
        for fuel, record in fuels.items():
            old = saved.get(fuel)
            if old is not None and dt(old['data_utc']) >= dt(record['data_utc']):
                continue
            saved[fuel] = record
            dirty = True
        if dirty:
            save(path, obj)
            changed += 1
    metadata = {'fonte':'Mappa Carburanti Italia', 'url':URL,
                'data_massima_utc':newest.isoformat(), 'aggiornato_utc':now.isoformat(),
                'righe_valide':count, 'righe_non_valide':invalid,
                'impianti':len(selected), 'file_modificati':changed,
                'modalita':'non_verificata',
                'nota':'Archivio separato: NON usato per sovrascrivere self/servito MIMIT'}
    ROOT.mkdir(parents=True, exist_ok=True)
    (ROOT/'ultimo_aggiornamento_mappa.json').write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')
    print(json.dumps(metadata, indent=2, ensure_ascii=False))

if __name__ == '__main__':
    main()
