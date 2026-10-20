#!/usr/bin/env python3
"""Report delle comunicazioni più recenti dagli archivi già pubblicati."""
import gzip, json, heapq
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path('site/data')
OUT = ROOT / 'ultime_comunicazioni.json'
LIMIT = 200

def datekey(value):
    value = str(value or '').strip()
    if not value:
        return ''
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).strftime('%Y-%m-%dT%H:%M:%S')
    except ValueError:
        return value[:19] if len(value) >= 10 else ''

def push(heap, item):
    key = datekey(item['data'])
    if not key:
        return
    entry = (key, item['id'], item['carburante'], item)
    if len(heap) < LIMIT:
        heapq.heappush(heap, entry)
    elif entry[:3] > heap[0][:3]:
        heapq.heapreplace(heap, entry)

def records_mimit(path):
    with gzip.open(path, 'rt', encoding='utf-8') as f:
        obj = json.load(f)
    current = obj.get('corrente') or {}
    historical = obj.get('prezzi') or {}
    keys = set(current) | set(historical)
    for key in keys:
        parts = key.split('|')
        fuel = parts[0]
        mode = parts[1] if len(parts) > 1 else ''
        rows = [current[key]] if key in current else (historical.get(key) or [])
        best = max((r for r in rows if isinstance(r, (list, tuple)) and len(r) >= 2 and datekey(r[0])), key=lambda r: datekey(r[0]), default=None)
        if best is None:
            continue
        yield fuel, mode, best[0], best[1]

def records_mappa(path):
    with gzip.open(path, 'rt', encoding='utf-8') as f:
        obj = json.load(f)
    for fuel, record in (obj.get('carburanti') or {}).items():
        if isinstance(record, dict):
            yield fuel, 'non_verificata', record.get('data_utc'), record.get('prezzo')

def collect(folder, source, parser):
    heap = []
    files = 0
    errors = 0
    for path in (ROOT / folder).glob('*/*.json.gz'):
        files += 1
        try:
            station_id = int(path.stem.split('.')[0])
            for fuel, mode, date, price in parser(path):
                try:
                    price = float(price)
                except (ValueError, TypeError):
                    continue
                if price <= 0:
                    continue
                push(heap, {'id': station_id, 'carburante': fuel, 'modalita': mode,
                            'data': str(date), 'prezzo': price, 'fonte': source})
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            errors += 1
    items = [v[3] for v in sorted(heap, reverse=True)]
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
                if isinstance(row, list) and len(row) > 9:
                    try:
                        sid = int(row[0])
                    except (ValueError, TypeError):
                        continue
                    if sid in ids and sid not in found:
                        found[sid] = {'nome': str(row[3] or row[6] or row[4] or f'Impianto {sid}'),
                                      'indirizzo': ' — '.join(str(x) for x in row[7:10] if x)}
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
    for item in items:
        item.update(found.get(item['id'], {'nome': f"Impianto {item['id']}", 'indirizzo': ''}))

def main():
    mimit, mstat = collect('prezzi', 'MIMIT', records_mimit)
    mappa, astat = collect('prezzi_mappa', 'Mappa Carburanti Italia', records_mappa)
    allitems = mimit + mappa
    add_station_names(allitems)
    report = {'generato_utc': datetime.now(timezone.utc).isoformat(),
              'nota': 'Data comunicazione del prezzo, non data importazione. Top 200 per fonte; le date MIMIT prive di fuso sono riportate come archiviate.',
              'mimit': mimit, 'mappa': mappa,
              'statistiche': {'mimit': mstat, 'mappa': astat}}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Report creato: {OUT} | MIMIT {len(mimit)} | Mappa {len(mappa)}')
    print('Ultimo MIMIT:', mimit[0] if mimit else 'nessuno')
    print('Ultimo Mappa:', mappa[0] if mappa else 'nessuno')

if __name__ == '__main__':
    main()
