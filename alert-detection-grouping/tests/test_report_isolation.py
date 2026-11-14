import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import prepare_docker


class IsolatedManifestTests(unittest.TestCase):
    def test_test_detector_and_probe_share_only_an_isolated_report_volume(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'feature'
            dummy = Path(tmp) / 'dummy'
            root.mkdir(); dummy.mkdir()
            (dummy / 'docker-compose.yml').write_text('services: {}')
            base = {'services': {'traffic-generator': {'environment': {}}, 'fault-injector': {}},
                    'networks': {'observability': {}}, 'volumes': {'postgres-data': {}}}
            detector = {'services': {'alerts': {'build': '.', 'volumes': [
                {'type': 'volume', 'source': 'alert-state', 'target': '/app/data'},
                {'type': 'bind', 'source': '/live/reports', 'target': '/app/reports'}]}},
                'networks': {'telemetry': {}}, 'volumes': {'alert-state': {}}}
            with patch.object(prepare_docker, 'ROOT', root), patch.object(prepare_docker, 'DUMMY', dummy), patch.object(
                    prepare_docker.subprocess, 'check_output', side_effect=[json.dumps(base), json.dumps(detector)]), contextlib.redirect_stdout(io.StringIO()):
                prepare_docker.main()
            run = json.loads((root / 'validation/current-run.json').read_text())
            app = json.loads(Path(run['dummy']).read_text())
            feature = json.loads(Path(run['detector']).read_text())
            mount = next(v for v in feature['services']['alerts']['volumes'] if v['target']=='/app/reports')
            self.assertEqual(mount['type'], 'volume')
            volume = feature['volumes'][mount['source']]['name']
            self.assertTrue(volume.startswith(run['run_id']))
            self.assertEqual(app['volumes']['detector-reports']['name'], volume)
            self.assertNotIn('/live/reports', json.dumps(feature))
            self.assertTrue(app['services']['probe']['volumes'][-1]['read_only'])
