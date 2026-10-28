#!/usr/bin/env python3
"""Aggiornamento conservativo MIMIT: mai pubblicare dati obsoleti come recenti."""
import csv
import gzip
import json
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
DATA = ROOT / 'data'
MIMIT = 'https://www.mimit.gov.it/images/exportCSV/'
MIRROR = 'https://raw.githubusercontent.com/LucaDDDD/benzina-data/main/data/'
NAMES = ('prezzo_alle_8.csv', 'anagrafica_impianti_attivi.csv')
MAX_MIRROR_DAYS = 7


def download(url, dest, timeout=100):
    subprocess.run([
        'curl', '-4', '-fLsS',
        *(['--noproxy', '*', '--resolve', 'www.mimit.gov.it:443:81.126.92.6']
          if url.startswith(MIMIT) else []),
        '--connect-timeout', '15',
        '--max-time', str(timeout), '--retry', '3', '--retry-delay', '8',
        '--retry-all-errors', '-A', 'Mozilla/5.0', url, '-o', str(dest)
    ], check=True)
    if dest.stat().st_size < 100:
        raise ValueError(f'Download troppo piccolo: {url}')


def read_csv(path, mandatory):
    with path.open(encoding='utf-8-sig', errors='replace', newline='') as f:
        for _ in range(12):
            line = f.readline()
            if 'idimpianto' in line.lower():
                header = line
                break
        else:
            raise ValueError(f'Intestazione idImpianto mancante: {path}')
        sep = ';' if header.count(';') >= header.count('|') else '|'
        fields = [x.strip().lower() for x in next(csv.reader([header], delimiter=sep))]
        missing = set(mandatory) - set(fields)
        if missing:
            raise ValueError(f'Colonne mancanti in {path}: {missing}')
        rows, malformed = [], 0
        for values in csv.reader(f, delimiter=sep):
            if len(values) != len(fields):
                malformed += 1
                continue
            row = dict(zip(fields, (v.strip() for v in values)))
            if row.get('idimpianto'):
                rows.append(row)
    print(f'CSV {path.name}: {len(rows)} righe valide, {malformed} scartate', flush=True)
    if len(rows) < 100 or malformed > max(100, len(rows) // 10):
        raise ValueError(f'CSV insufficiente o malformato: {path}')
    return rows, header


def extraction_day(line):
    for pattern, order in [
        (r'(20\d{2})[-/](\d{1,2})[-/](\d{1,2})', 'ymd'),
        (r'(\d{1,2})/(\d{1,2})/(20\d{2})', 'dmy'),
    ]:
        match = re.search(pattern, line)
        if match:
            a, b, c = map(int, match.groups())
            try:
                return date(a, b, c) if order == 'ymd' else date(c, b, a)
            except ValueError:
                return None
    return None


def acquire(tmp):
    paths = {name: tmp / name for name in NAMES}
    try:
        for name, path in paths.items():
            download(MIMIT + name, path)
        prices, pline = read_csv(paths[NAMES[0]], ('idimpianto', 'desccarburante', 'prezzo', 'isself', 'dtcomu'))
        stations, sline = read_csv(paths[NAMES[1]], ('idimpianto', 'latitudine', 'longitudine'))
        pday, sday = extraction_day(pline), extraction_day(sline)
        if pday and sday and pday != sday:
            raise ValueError(f'Date estrazione incoerenti: {pday} / {sday}')
        day = pday or sday
        if day and (date.today() - day).days not in range(0, 8):
            raise ValueError(f'Estrazione MIMIT obsoleta o futura: {day}')
        # Archivia il CSV ufficiale solo dopo le verifiche di struttura e data.
        # Il file viene poi pubblicato insieme ai prezzi correnti sul ramo gh-pages.
        if day is None:
            raise ValueError('Data estrazione MIMIT non verificabile')
        snapshot = ROOT / 'mimit_snapshots' / f'{day:%Y}' / f'{day:%m}' / f'prezzo_alle_8-{day:%Y%m%d}.csv.gz'
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        if not snapshot.exists():
            with paths[NAMES[0]].open('rb') as inp, gzip.open(snapshot, 'wb', compresslevel=6) as gz:
                shutil.copyfileobj(inp, gz)
        meta = {'fonte': 'MIMIT', 'ultima_estrazione': day.isoformat(),
                'scaricato_utc': datetime.now(timezone.utc).isoformat(), 'file': str(snapshot.relative_to(ROOT))}
        (ROOT / 'mimit_snapshots' / 'last_update.json').write_text(
            json.dumps(meta, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        return prices, stations, 'MIMIT', day
    except Exception as exc:
        print(f'Fonte MIMIT indisponibile/non valida: {exc}', flush=True)
        req = Request(MIRROR + 'last_update.json', headers={'User-Agent': 'geotab-carburanti'})
        with urlopen(req, timeout=25) as response:
            meta = json.load(response)
        day = date.fromisoformat(meta['ultima_estrazione'])
        age = (datetime.now(timezone.utc).date() - day).days
        print(f'Mirror: estrazione={day}, eta={age} giorni, limite={MAX_MIRROR_DAYS}', flush=True)
        if not 0 <= age <= MAX_MIRROR_DAYS:
            raise RuntimeError(
                f'Nessuna fonte sufficientemente recente: MIMIT non raggiungibile; '
                f'mirror del {day} ({age} giorni). Nessun dato pubblicato.'
            ) from exc
        for name, path in paths.items():
            archive = tmp / (name + '.gz')
            download(f'{MIRROR}{day:%Y}/{day:%m}/{name[:-4]}-{day:%Y%m%d}.csv.gz', archive)
            with gzip.open(archive, 'rb') as src, path.open('wb') as dst:
                shutil.copyfileobj(src, dst)
        prices, _ = read_csv(paths[NAMES[0]], ('idimpianto', 'desccarburante', 'prezzo', 'isself', 'dtcomu'))
        stations, _ = read_csv(paths[NAMES[1]], ('idimpianto', 'latitudine', 'longitudine'))
        return prices, stations, 'GitHub benzina-data', day


def number(value):
    return float(str(value).replace(',', '.'))


def stamp(value):
    return value.strip().replace(' ', 'T', 1)


def read_gz(path):
    if not path.exists():
        return {'corrente': {}, 'prezzi': {}}
    with gzip.open(path, 'rt', encoding='utf-8') as f:
        obj = json.load(f)
    if not isinstance(obj, dict) or not isinstance(obj.get('corrente', {}), dict) or not isinstance(obj.get('prezzi', {}), dict):
        raise ValueError(f'Schema prezzi incompatibile: {path}')
    return obj


def save_gz(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(obj, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            with gzip.GzipFile(filename='', mode='wb', fileobj=output, mtime=0) as gz:
                gz.write(raw)
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def main():
    with tempfile.TemporaryDirectory() as directory:
        prices, stations, source, day = acquire(Path(directory))
    print(f'Fonte={source}; estrazione={day}; righe prezzi={len(prices)}; impianti={len(stations)}', flush=True)
    if len(prices) < 1000 or len(stations) < 1000:
        raise RuntimeError('Dataset troppo piccolo: nessuna pubblicazione')
    by_id = defaultdict(dict)
    invalid = 0
    for row in prices:
        try:
            sid = int(row['idimpianto'])
            fuel = row['desccarburante'].strip()
            mode = row['isself'].strip().lower()
            if mode not in ('1', '0', 'true', 'false', 'si', 'sì', 'no'):
                invalid += 1
                continue
            mode = '1' if mode in ('1', 'true', 'si', 'sì') else '0'
            value = number(row['prezzo'])
            when = stamp(row['dtcomu'])
            if not fuel or len(when) < 10 or not 0.1 <= value <= 10:
                invalid += 1
                continue
            communication_day = date.fromisoformat(when[:10])
            if communication_day > datetime.now(timezone.utc).date():
                invalid += 1
                continue
            key = f'{fuel}|{mode}'
            old = by_id[sid].get(key)
            if old is None or when > old[0]:
                by_id[sid][key] = [when, value]
        except (ValueError, KeyError, TypeError):
            invalid += 1
    if len(by_id) < 1000:
        raise RuntimeError('Troppi prezzi non validi: nessuna pubblicazione')
    changed = 0
    for sid, entries in by_id.items():
        path = DATA / 'prezzi' / f'{sid // 1000:03d}' / f'{sid}.json.gz'
        obj = read_gz(path)
        current = obj.setdefault('corrente', {})
        history = obj.setdefault('prezzi', {})
        dirty = False
        for key, record in entries.items():
            old = current.get(key)
            if isinstance(old, list) and old and str(old[0]) > record[0]:
                continue
            if old != record:
                current[key] = record
                dirty = True
            arr = history.setdefault(key, [])
            if not isinstance(arr, list):
                raise ValueError(f'Storico inatteso: {path} {key}')
            if not any(isinstance(x, list) and len(x) >= 2 and x[0] == record[0]
                       and number(x[1]) == record[1] for x in arr):
                arr.append([record[0], record[1], source])
                arr.sort(key=lambda x: str(x[0]), reverse=True)
                dirty = True
        if dirty:
            save_gz(path, obj)
            changed += 1
    metadata = {
        'fonte': source,
        'data_estrazione': day.isoformat() if day else None,
        'aggiornato_utc': datetime.now(timezone.utc).isoformat(),
        'impianti_con_prezzi': len(by_id),
        'file_prezzi_modificati': changed,
        'righe_prezzo_scartate': invalid,
        'nota': 'Griglia geografica preservata. La data di aggiornamento non sostituisce dtComu.'
    }
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / 'ultimo_aggiornamento_prezzi.json').write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + '\n', encoding='utf-8'
    )
    print(json.dumps(metadata, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
