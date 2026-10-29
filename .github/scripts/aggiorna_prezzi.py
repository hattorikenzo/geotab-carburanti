#!/usr/bin/env python3
"""Aggiornamento conservativo MIMIT: mai pubblicare dati obsoleti come recenti."""
import csv
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections import defaultdict
from datetime import date, datetime, timezone, timedelta
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
    return rows, header, path.read_text(encoding="utf-8-sig", errors="replace").splitlines()[:12]


def extraction_day(lines):
    """Cerca una data di estrazione SOLO nel preambolo, mai in dtComu."""
    for line in lines:
        if 'idimpianto' in line.lower():
            break
        for pattern, order in [
            (r'(20\d{2})[-/](\d{1,2})[-/](\d{1,2})', 'ymd'),
            (r'(\d{1,2})[-/](\d{1,2})[-/](20\d{2})', 'dmy'),
        ]:
            match = re.search(pattern, line)
            if match:
                a, b, c = map(int, match.groups())
                try:
                    return date(a, b, c) if order == 'ymd' else date(c, b, a)
                except ValueError:
                    pass
    return None


def file_sha256(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.digest()


def archived_extraction_day(csv_path):
    """Accetta la data dello snapshot esistente solo se il CSV e identico byte per byte."""
    meta_path = ROOT / 'mimit_snapshots' / 'last_update.json'
    if not meta_path.is_file():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding='utf-8'))
        if meta.get('fonte') != 'MIMIT':
            return None
        day = date.fromisoformat(meta['ultima_estrazione'])
        snapshot = (ROOT / meta['file']).resolve()
        base = (ROOT / 'mimit_snapshots').resolve()
        if not snapshot.is_relative_to(base) or not snapshot.is_file():
            return None
        with gzip.open(snapshot, 'rb') as stream:
            h = hashlib.sha256()
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                h.update(block)
        if h.digest() == file_sha256(csv_path):
            print(f'Data estrazione verificata da snapshot identico: {day}', flush=True)
            return day
        print('Snapshot precedente diverso dal CSV appena scaricato', flush=True)
    except (ValueError, KeyError, OSError, json.JSONDecodeError) as exc:
        print(f'Metadati snapshot non utilizzabili: {exc}', flush=True)
    return None


def parse_dtcomu(value):
    value = str(value).strip()
    for fmt in ('%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S',
                '%d/%m/%Y %H:%M:%S', '%d-%m-%Y %H:%M:%S',
                '%Y-%m-%d', '%d/%m/%Y'):
        try:
            return datetime.strptime(value[:19], fmt)
        except ValueError:
            continue
    raise ValueError(f'Data comunicazione non valida: {value!r}')


def validate_freshness(rows):
    """Se manca la data estrazione, richiede comunicazioni recenti senza falsificarla."""
    today = datetime.now(timezone.utc).date()
    recent = 0
    newest = None
    for row in rows:
        try:
            d = parse_dtcomu(row['dtcomu']).date()
        except (KeyError, ValueError):
            continue
        if d > today:
            continue
        newest = max(newest, d) if newest else d
        if 0 <= (today - d).days <= 7:
            recent += 1
    if recent < 100:
        raise ValueError(f'CSV privo di comunicazioni recenti sufficienti: {recent}; ultima={newest}')
    print(f'Data estrazione non dichiarata; comunicazioni ultimi 7 giorni: {recent}; ultima={newest}', flush=True)
    return newest


def acquire(tmp):
    paths = {name: tmp / name for name in NAMES}
    try:
        for name, path in paths.items():
            download(MIMIT + name, path)
        prices, _, ppreamble = read_csv(paths[NAMES[0]], ('idimpianto', 'desccarburante', 'prezzo', 'isself', 'dtcomu'))
        stations, _, spreamble = read_csv(paths[NAMES[1]], ('idimpianto', 'latitudine', 'longitudine'))
        pday, sday = extraction_day(ppreamble), extraction_day(spreamble)
        if pday and sday and pday != sday:
            raise ValueError(f'Date estrazione incoerenti: {pday} / {sday}')
        day = pday or sday or archived_extraction_day(paths[NAMES[0]])
        if day is not None:
            age = (datetime.now(timezone.utc).date() - day).days
            if not 0 <= age <= 7:
                raise ValueError(f'Estrazione MIMIT obsoleta o futura: {day}')
            date_method = 'preambolo CSV' if pday or sday else 'snapshot verificato SHA256'
        else:
            validate_freshness(prices)
            date_method = 'non disponibile; freschezza verificata tramite dtComu'
        # Non attribuire al CSV una data di estrazione ricavata da dtComu.
        # Non creare snapshot con data inventata: gli snapshot ufficiali restano intatti.
        if day is not None:
            snapshot = ROOT / 'mimit_snapshots' / f'{day:%Y}' / f'{day:%m}' / f'prezzo_alle_8-{day:%Y%m%d}.csv.gz'
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            if not snapshot.exists():
                with paths[NAMES[0]].open('rb') as inp, gzip.open(snapshot, 'wb', compresslevel=6) as gz:
                    shutil.copyfileobj(inp, gz)
            meta = {'fonte': 'MIMIT', 'ultima_estrazione': day.isoformat(),
                    'scaricato_utc': datetime.now(timezone.utc).isoformat(),
                    'file': str(snapshot.relative_to(ROOT))}
            (ROOT / 'mimit_snapshots' / 'last_update.json').write_text(
                json.dumps(meta, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        return prices, stations, 'MIMIT', day, date_method
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
                f'Nessuna fonte sufficientemente recente: MIMIT non valida; '
                f'mirror del {day} ({age} giorni). Nessun dato pubblicato.'
            ) from exc
        for name, path in paths.items():
            archive = tmp / (name + '.gz')
            download(f'{MIRROR}{day:%Y}/{day:%m}/{name[:-4]}-{day:%Y%m%d}.csv.gz', archive)
            with gzip.open(archive, 'rb') as src, path.open('wb') as dst:
                shutil.copyfileobj(src, dst)
        prices, _, _ = read_csv(paths[NAMES[0]], ('idimpianto', 'desccarburante', 'prezzo', 'isself', 'dtcomu'))
        stations, _, _ = read_csv(paths[NAMES[1]], ('idimpianto', 'latitudine', 'longitudine'))
        return prices, stations, 'GitHub benzina-data', day, 'metadati mirror'


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
        prices, stations, source, day, date_method = acquire(Path(directory))
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
            when = parse_dtcomu(row['dtcomu']).strftime('%Y-%m-%dT%H:%M:%S')
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
        'verifica_data_estrazione': date_method,
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
    manifest_path = DATA / 'manifest.json'
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        if not isinstance(manifest, dict):
            raise ValueError('Manifest non valido: non sovrascritto')
        # Conserva versione, storico, griglia e altri metadati precedenti.
        manifest['aggiornato_il'] = metadata['aggiornato_utc']
        manifest['ultimo_aggiornamento_prezzi_correnti'] = metadata['aggiornato_utc']
        manifest['ultima_estrazione_mimit'] = metadata['data_estrazione'] if source == 'MIMIT' else None
        manifest['fonte_prezzi_correnti'] = source
        manifest['impianti_con_prezzi_correnti'] = len(by_id)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    else:
        print('ATTENZIONE: data/manifest.json assente; non viene creato uno schema fittizio', flush=True)
    print(json.dumps(metadata, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
