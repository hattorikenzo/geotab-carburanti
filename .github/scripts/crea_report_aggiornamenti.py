#!/usr/bin/env python3
"""Report delle ultime comunicazioni dei prezzi MIMIT e Mappa Carburanti."""
import gzip
import heapq
import itertools
import json
import math
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('site/data')
OUT = ROOT / 'ultime_comunicazioni.json'
LIMIT = 200


def datekey(value):
    value = str(value or '').strip()
    if not value:
        return ''
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        # Mantiene la data locale archivio per i timestamp senza fuso.
        return parsed.strftime('%Y-%m-%dT%H:%M:%S')
    except ValueError:
        return value[:19] if len(value) >= 19 else ''


def push(heap, item, counter):
    key = datekey(item['data'])
    if not key:
        return
    # Il contatore impedisce il confronto tra dizionari in caso di parità.
    entry = (key, next(counter), item)
    if len(heap) < LIMIT:
        heapq.heappush(heap, entry)
    elif entry[:2] > heap[0][:2]:
        heapq.heapreplace(heap, entry)


def records_mimit(path):
    with gzip.open(path, 'rt', encoding='utf-8') as f:
        obj = json.load(f)
    current = obj.get('corrente') or {}
    historical = obj.get('prezzi') or {}
    for key in set(current) | set(historical):
        parts = key.split('|')
        fuel = parts[0]
        mode = parts[1] if len(parts) > 1 else ''
        rows = []
        if key in current:
            rows.append(current[key])
        rows.extend(historical.get(key) or [])
        best = max(
            (r for r in rows if isinstance(r, (list, tuple)) and len(r) >= 2 and datekey(r[0])),
            key=lambda r: datekey(r[0]),
            default=None,
        )
        if best is not None:
            yield fuel, mode, best[0], best[1]


def records_mappa(path):
    with gzip.open(path, 'rt', encoding='utf-8') as f:
        obj = json.load(f)
    for fuel, record in (obj.get('carburanti') or {}).items():
        if isinstance(record, dict):
            yield fuel, 'non_verificata', record.get('data_utc'), record.get('prezzo')


def collect(folder, source, parser):
    heap = []
    counter = itertools.count()
    files = 0
    errors = 0
    for path in (ROOT / folder).glob('*/*.json.gz'):
        files += 1
        try:
            station_id = int(path.name.removesuffix('.json.gz'))
            for fuel, mode, date, price in parser(path):
                try:
                    price = float(price)
                except (ValueError, TypeError):
                    continue
                if not math.isfinite(price) or price <= 0:
                    continue
                push(heap, {
                    'id': station_id,
                    'carburante': fuel,
                    'modalita': mode,
                    'data': str(date),
                    'prezzo': price,
                    'fonte': source,
                }, counter)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            errors += 1
    items = [entry[2] for entry in sorted(heap, key=lambda entry: entry[:2], reverse=True)]
