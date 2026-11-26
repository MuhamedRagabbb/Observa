import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from observa_alerts.config import settings
from observa_alerts.demo import snapshot
from observa_alerts.engine import Engine
from observa_alerts import report
from observa_alerts.evidence import read_evidence, load_manifest
from observa_alerts.repack import repack


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        config = settings()
        engine = Engine(self.root / 'data', config)
        try:
            for t in (1000, 1010, 1020):
                items = engine.tick(snapshot(config, t, 'payments'))
            self.original = copy.deepcopy(items[0])
        finally:
            engine.close()
        self.reports = self.root / 'reports'
        self.bundle = report.export([self.original], self.reports)[0]

    def tearDown(self):
        self.temp.cleanup()

    def read(self, ref, bundle=None, **kwargs):
        b = bundle or self.bundle
        return read_evidence(self.reports, b['id'], b['snapshot_id'], ref, **kwargs)

    def all_rows(self, ref, bundle=None):
        result, cursor = [], 0
        while True:
            page = self.read(ref, bundle, cursor=cursor, limit=100, max_bytes=65536)
            self.assertIsNone(page['error'])
            result.extend(page['items'])
            if page['next_cursor'] is None:
                return result
            self.assertGreater(page['next_cursor'], cursor)
            cursor = page['next_cursor']

    def test_layout_and_lossless_evidence_reconstruction(self):
        folder = self.reports / self.bundle['id']
        for name in ('incident.json', 'summary.md', 'evidence/logs.jsonl', 'evidence/metrics.json', 'evidence/traces/index.json'):
            self.assertTrue((folder / name).is_file(), name)
        self.assertTrue(list((folder / 'evidence/traces').glob('trace-*.json')))
        self.assertNotIn('evidence', self.bundle)
        self.assertNotIn('timeline', self.bundle)
        self.assertTrue(all('trigger_evidence' not in a for a in self.bundle['alerts'].values()))
        self.assertTrue(all('witnesses' not in r for r in self.bundle['relationships']))
        details = self.all_rows('details')
        reconstructed = details[0]['value']
        reconstructed['alerts'] = {r['alert_id']: r['value'] for r in details[1:]}
        reconstructed['timeline'] = self.all_rows('timeline')
        reconstructed['relationships'] = self.all_rows('relationships')
        traces = []
        for row in self.all_rows('traces'):
            meta = self.read(row['ref_id'])['metadata']
            traces.append({**meta, 'spans': self.all_rows(row['ref_id'])})
        reconstructed['evidence'] = {'logs': self.all_rows('logs'), 'traces': traces}
        self.assertEqual(reconstructed, self.original)

    def test_old_snapshot_is_stable_after_update_and_same_revision_change(self):
        old_logs = self.all_rows('logs')
        changed = copy.deepcopy(self.original)
        changed['evidence']['logs'].append({'event': 'later-evidence', 'service.name': 'orders-service'})
        newer = report.export([changed], self.reports)[0]
        self.assertNotEqual(newer['snapshot_id'], self.bundle['snapshot_id'])
        self.assertEqual(self.all_rows('logs'), old_logs)
        self.assertEqual(self.all_rows('logs', newer), changed['evidence']['logs'])
        (self.reports / newer['id'] / 'evidence/logs.jsonl').write_text('current view changed')
        self.assertEqual(self.all_rows('logs'), old_logs)

    def test_pagination_and_budget(self):
        changed = copy.deepcopy(self.original)
        changed['evidence']['logs'] = [{'n': n, 'message': 'x' * 160} for n in range(30)]
        b = report.export([changed], self.reports)[0]
        records, cursor = [], 0
        while True:
            page = self.read('logs', b, cursor=cursor, limit=10, max_bytes=1500)
            self.assertLessEqual(len(report.encoded(page).encode()), 1500)
            records.extend(page['items'])
            if page['next_cursor'] is None:
                break
            self.assertGreater(page['next_cursor'], cursor)
            cursor = page['next_cursor']
        self.assertEqual(records, changed['evidence']['logs'])
        changed['evidence']['logs'] = [{'message': 'x' * 3000}]
        b = report.export([changed], self.reports)[0]
        page = self.read('logs', b, max_bytes=1500)
        self.assertEqual(page['error']['code'], 'record_too_large')
        self.assertEqual(page['next_cursor'], 0)
        self.assertEqual(self.read('logs', b, max_bytes=6000)['items'], changed['evidence']['logs'])

    def test_rejects_invalid_ref_and_checksum_and_paths(self):
        for kwargs in ({'ref_id': '../outside'}, {'ref_id': 'logs', 'cursor': -1}, {'ref_id': 'logs', 'limit': 1000}):
            with self.assertRaises(ValueError):
                read_evidence(self.reports, self.bundle['id'], self.bundle['snapshot_id'], **kwargs)
        with self.assertRaises(ValueError):
            read_evidence(self.reports, '../outside', self.bundle['snapshot_id'], 'logs')
        folder, manifest = load_manifest(self.reports, self.bundle['id'], self.bundle['snapshot_id'])
        target = folder / manifest['refs']['logs']['path']
        target.write_text('{}\n')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            self.read('logs')

    def test_manifest_cannot_reference_outside_incident(self):
        folder, manifest = load_manifest(self.reports, self.bundle['id'], self.bundle['snapshot_id'])
        manifest['refs']['logs']['path'] = '../outside.jsonl'
        raw = json.dumps(manifest)
        (folder / self.bundle['manifest']).write_text(raw, encoding='utf-8')
        immutable = folder / 'snapshots' / self.bundle['snapshot_id'] / 'incident.json'
        saved = json.loads(immutable.read_text())
        saved['manifest_sha256'] = hashlib.sha256(raw.encode()).hexdigest()
        immutable.write_text(json.dumps(saved), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'path'):
            self.read('logs')

    def test_interrupted_export_does_not_publish_broken_current_input(self):
        current = self.reports / self.bundle['id'] / 'incident.json'
        before = current.read_bytes()
        changed = copy.deepcopy(self.original)
        changed['revision'] += 1
        real = report.write_atomic
        def fail_manifest(path, body):
            if path.name == 'manifest.json':
                raise OSError('simulated write failure')
            return real(path, body)
        with patch('observa_alerts.report.write_atomic', side_effect=fail_manifest):
            with self.assertRaises(OSError):
                report.export([changed], self.reports)
        self.assertEqual(current.read_bytes(), before)
        self.assertEqual(self.all_rows('logs'), self.original['evidence']['logs'])

    def test_repeat_export_reuses_immutable_objects(self):
        paths = {p: p.stat().st_mtime_ns for p in self.reports.rglob('.objects/*.jsonl')}
        repeated = report.export([self.original], self.reports)[0]
        self.assertEqual(self.bundle, repeated)
        self.assertEqual(paths, {p: p.stat().st_mtime_ns for p in self.reports.rglob('.objects/*.jsonl')})

    def test_migration_preserves_originals_and_is_repeatable(self):
        legacy = self.reports / (self.original['id'] + '.json')
        raw = json.dumps(self.original)
        legacy.write_text(raw, encoding='utf-8')
        legacy.with_suffix('.md').write_text('original markdown', encoding='utf-8')
        result = repack(self.reports)
        self.assertEqual(result['converted'], 1)
        self.assertFalse(legacy.exists())
        self.assertEqual((Path(result['archive']) / legacy.name).read_text(encoding='utf-8'), raw)
        self.assertEqual(repack(self.reports)['converted'], 0)
        self.assertEqual(self.all_rows('logs'), self.original['evidence']['logs'])


if __name__ == '__main__':
    unittest.main()
