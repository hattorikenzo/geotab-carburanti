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
    return items, {'file_esaminati': files, 'file_non_letti': errors}


def add_station_names(items):
    ids = {r['id'] for r in items}
    found = {}
    for path in (ROOT / 'geo').glob('*.json'):
        if len(found) == len(ids):
            break
        try:
            with path.open(encoding='utf-8') as f:
                rows = json.load(f)
            for row in rows:
                if not isinstance(row, list) or len(row) < 10:
                    continue
                try:
                    sid = int(row[0])
                except (ValueError, TypeError):
                    continue
                if sid in ids and sid not in found:
                    found[sid] = {
                        'nome': str(row[3] or row[6] or row[4] or f'Impianto {sid}'),
                        'indirizzo': ' — '.join(str(x) for x in row[7:10] if x),
                    }
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
    for item in items:
        item.update(found.get(item['id'], {
            'nome': f"Impianto {item['id']}",
            'indirizzo': '',
        }))


def main():
    if not ROOT.is_dir():
        raise SystemExit(f'Cartella dataset non trovata: {ROOT}')
    mimit, mstat = collect('prezzi', 'MIMIT', records_mimit)
    mappa, astat = collect('prezzi_mappa', 'Mappa Carburanti Italia', records_mappa)
    add_station_names(mimit + mappa)
    report = {
        'generato_utc': datetime.now(timezone.utc).isoformat(),
        'nota': ('Data comunicazione del prezzo, non data importazione. '
                 'Top 200 per fonte. Le date MIMIT prive di fuso sono riportate come archiviate.'),
        'mimit': mimit,
        'mappa': mappa,
        'statistiche': {'mimit': mstat, 'mappa': astat},
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Report creato: {OUT} | MIMIT {len(mimit)} | Mappa {len(mappa)}')
    print('Ultimo MIMIT:', mimit[0] if mimit else 'nessuno')
    print('Ultimo Mappa:', mappa[0] if mappa else 'nessuno')
    print('Statistiche:', report['statistiche'])


if __name__ == '__main__':
    main()
