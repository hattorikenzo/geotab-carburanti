#!/usr/bin/env python3
"""Importa Mappa Carburanti in archivio separato e produce diagnostica di freschezza.

Non assegna isSelf, non modifica data/prezzi e non trasforma dati vecchi in recenti.
"""
import csv
import gzip
import io
import json
import math
import os
import tempfile
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

URL = 'https://mappacarburanti.it/data/stazioni-italia.csv'
ROOT = Path.cwd() / 'data'
FUEL = {'benzina': 'Benzina', 'gasolio': 'Gasolio', 'gpl': 'GPL', 'metano': 'Metano'}
REQUIRED = {'id_impianto', 'fuel_type', 'price', 'updated_at'}


def dt(value):
    parsed = datetime.fromisoformat(str(value).strip().replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('timestamp senza timezone')
    return parsed.astimezone(timezone.utc)


def load(path):
    if not path.exists():
        return {'fonte': 'Mappa Carburanti Italia', 'modalita': 'non_verificata', 'carburanti': {}}
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        obj = json.load(stream)
    if not isinstance(obj, dict) or not isinstance(obj.get('carburanti'), dict):
        raise ValueError(f'Archivio alternativo non valido: {path}')
    return obj


def save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(obj, ensure_ascii=False, separators=(',', ':'), sort_keys=True).encode('utf-8')
    tmp_name = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
            tmp_name = Path(tmp.name)
            with gzip.GzipFile(filename='', fileobj=tmp, mode='wb', mtime=0) as gz:
                gz.write(payload)
        os.replace(tmp_name, path)
    finally:
        if tmp_name is not None and tmp_name.exists():
            tmp_name.unlink()


def main():
    request = urllib.request.Request(URL, headers={'User-Agent': 'geotab-carburanti/1.0'})
    with urllib.request.urlopen(request, timeout=90) as response:
        raw = response.read(30_000_001)
    if len(raw) > 30_000_000:
        raise RuntimeError('CSV troppo grande')

    reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig')))
    if not reader.fieldnames or not REQUIRED.issubset(set(reader.fieldnames)):
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
                raise ValueError('prezzo/ID non valido')
            if when > now:
                raise ValueError('data futura')
            newest = max(newest, when) if newest else when
            prev = selected[sid].get(fuel)
            record = {
                'data_utc': when.isoformat().replace('+00:00', 'Z'),
                'prezzo': price,
                'modalita': 'non_verificata',
                'fonte': 'Mappa Carburanti Italia',
            }
            if prev is None or when > dt(prev['data_utc']):
                selected[sid][fuel] = record
        except (KeyError, ValueError, TypeError, OverflowError, AttributeError):
            invalid += 1

    count = sum(len(fuels) for fuels in selected.values())
    if count < 10000 or newest is None:
        raise RuntimeError(f'Dataset incompleto: {count} prezzi')

    age_days = (now - newest).total_seconds() / 86400
    if age_days > 7:
        raise RuntimeError(f'Dataset alternativo vecchio ({age_days:.1f} giorni): non pubblicato')

    # Diagnostica sul dataset selezionato, non sul numero grezzo di righe CSV.
    recent_24 = 0
    recent_48 = 0
    by_fuel = {}
    recent_station_ids_48 = set()
    for sid, fuels in selected.items():
        for fuel, record in fuels.items():
            when = dt(record['data_utc'])
            stats = by_fuel.setdefault(fuel, {'totale': 0, 'ultime_24_ore': 0, 'ultime_48_ore': 0})
            stats['totale'] += 1
            if now - when <= timedelta(hours=24):
                recent_24 += 1
                stats['ultime_24_ore'] += 1
            if now - when <= timedelta(hours=48):
                recent_48 += 1
                stats['ultime_48_ore'] += 1
                recent_station_ids_48.add(sid)

    changed = 0
    for sid, fuels in selected.items():
        path = ROOT / 'prezzi_mappa' / f'{sid // 1000:03d}' / f'{sid}.json.gz'
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

    metadata = {
        'fonte': 'Mappa Carburanti Italia',
        'url': URL,
        'data_massima_utc': newest.isoformat(),
        'eta_dato_piu_recente_giorni': round(age_days, 2),
        'aggiornato_utc': now.isoformat(),
        'righe_valide': count,
        'righe_non_valide': invalid,
        'impianti': len(selected),
        'file_modificati': changed,
        'prezzi_ultime_24_ore': recent_24,
        'prezzi_ultime_48_ore': recent_48,
        'impianti_con_almeno_un_prezzo_ultime_48_ore': len(recent_station_ids_48),
        'statistiche_per_carburante': by_fuel,
        'modalita': 'non_verificata',
        'nota': 'Archivio separato: NON usato per sovrascrivere self/servito MIMIT. Le statistiche si riferiscono ai prezzi selezionati dal CSV, non a tutti i file storici.',
    }
    ROOT.mkdir(parents=True, exist_ok=True)
    (ROOT / 'ultimo_aggiornamento_mappa.json').write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + '\n', encoding='utf-8'
    )
    print(json.dumps(metadata, indent=2, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
