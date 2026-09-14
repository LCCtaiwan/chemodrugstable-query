"""Reproduce the NHI–TFDA join audit from downloaded official source files."""
import argparse
import csv
import hashlib
import json
from datetime import date
from pathlib import Path

from build_nhi_lookup import load_current_records, load_tfda_labels, link_nhi_licenses


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--csv', type=Path, required=True)
    parser.add_argument('--tfda-licenses', type=Path, required=True)
    parser.add_argument('--as-of', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    records, source_stats = load_current_records(args.csv, date.fromisoformat(args.as_of))
    labels, _ = load_tfda_labels(args.tfda_licenses)
    stats = link_nhi_licenses(records, labels, args.tfda_licenses)
    args.output.mkdir(parents=True, exist_ok=True)
    report = {'asOf': args.as_of, 'stats': stats, 'nhiSourceStats': source_stats,
              'sourceSha256': {name: hashlib.sha256(path.read_bytes()).hexdigest()
                               for name, path in [('nhi', args.csv), ('tfda', args.tfda_licenses)]}}
    (args.output / 'summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    with (args.output / 'links.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(['健保碼','品名','官方識別碼','對應許可證','狀態','官方來源'])
        for r in records:
            writer.writerow([r['code'], r['zh'], r['licenseId'], ';'.join(r['licenseCandidates']),r['licenseStatus'],r['drugUrl']])
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
