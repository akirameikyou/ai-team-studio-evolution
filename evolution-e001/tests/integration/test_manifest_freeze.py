"""E001-D: the Run Manifest is frozen at run start and anchored in RUN_STARTED."""
import json
from unittest import mock

from application import provenance as pv
from infrastructure.storage.event_log import SYSTEM_ACTOR_ID
from infrastructure.storage.record_store import DuplicateRecord
from tests.integration.support import StudioTestCase
from tests.persistence.support import tamper


class ManifestFreezeTests(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.trial = self.w.trial()
        self.run = self.w.start(self.trial)
        self.man = self.studio.manifest_of(self.run)

    def started(self):
        return next(e for e in self.studio.events.read_all()[0] if e['eventType'] == 'RUN_STARTED' and e['targetId'] == self.run)

    def test_manifest_freezes_the_execution_context(self):
        m, w = self.man, self.w
        self.assertEqual((m['caseId'], m['trialId'], m['runId']), (w.case, self.trial, self.run))
        self.assertEqual((m['patternRef'], m['routeRef'], m['protocolRef']), (w.p001, w.route1, w.proto1))
        self.assertEqual({n['nodeId'] for n in m['nodes']}, {*w.obs.values(), w.human, SYSTEM_ACTOR_ID})
        self.assertEqual({a['role'] for a in m['roleAssignments']}, {'OBSERVER', 'SYNTHESIZER'})
        self.assertEqual([p['providerId'] for p in m['providers']], [w.provider])
        self.assertEqual(m['sources'][0]['sourceId'], w.source)
        self.assertEqual(m['initialArtifactIds'], [w.case_input, w.source_artifact])
        self.assertEqual(m['configuration'], {'contextAssembly': 'manual-test'})

    def test_run_started_anchors_manifest_and_every_frozen_record(self):
        p = self.started()['payload']
        self.assertEqual(p['manifestHash'], self.studio.records.get('runManifest', self.man['runManifestId']).record_hash)
        self.assertEqual(set(p['recordHashes']), {'case', 'trial', 'designSpace', 'pattern', 'route', 'protocol', 'node', 'provider', 'source'})
        self.assertEqual(set(p['recordHashes']['node']), {n['nodeId'] for n in self.man['nodes']})
        self.assertEqual(p['recordHashes']['pattern'], {'PAT-P001@1.0': self.studio.records.get('pattern', 'PAT-P001', '1.0').record_hash})
        self.assertEqual(pv.reconstruct_run(self.studio, self.run)[1], [])

    def test_manifest_cannot_be_written_twice(self):
        with self.assertRaises(DuplicateRecord):
            self.studio.records.put('runManifest', self.man)

    def test_manifest_edited_after_start_is_detected(self):
        path = self.studio.records.dir / 'runManifest' / f'{self.man["runManifestId"]}.json'
        m = json.loads(path.read_bytes())
        m['configuration'] = {'contextAssembly': 'something else'}
        tamper(path, json.dumps(m).encode())
        self.assertIn('MANIFEST_MISMATCH', self.codes(pv.reconstruct_run(self.studio, self.run)[1]))
        self.assertIn('MANIFEST_MISMATCH', self.codes(pv.verify_store(self.studio)))

    def test_frozen_records_edited_after_start_are_detected(self):
        for rel, key, value in ((f'pattern/PAT-P001/1.0.json', 'name', 'Renamed'),
                                (f'node/{self.w.obs["b"]}.json', 'displayName', 'Someone else'),
                                (f'designSpace/{self.w.ds}.json', 'notes', 'changed')):
            with self.subTest(rel):
                path = self.studio.records.dir / rel
                rec = json.loads(path.read_bytes())
                original = path.read_bytes()
                rec[key] = value
                tamper(path, json.dumps(rec).encode())
                self.assertIn('FROZEN_RECORD_MISMATCH', self.codes(pv.reconstruct_run(self.studio, self.run)[1]))
                tamper(path, original)
        self.assertEqual(pv.reconstruct_run(self.studio, self.run)[1], [])

    def test_crash_before_run_started_leaves_an_unanchored_run(self):
        real = self.studio.events.append

        def fail_run_started(**kw):
            if kw['event_type'] == 'RUN_STARTED':
                raise OSError('simulated crash')
            return real(**kw)

        with mock.patch.object(self.studio.events, 'append', side_effect=fail_run_started):
            with self.assertRaises(OSError):
                self.w.start(self.trial)
        run = next(r['runId'] for r in self.studio.runs_of_trial(self.trial) if r['runId'] != self.run)
        self.assertEqual(self.studio.run_status(run), 'UNANCHORED')
        self.assertIn('UNANCHORED_RUN', self.codes(pv.verify_store(self.studio)))

    def test_crash_between_manifest_and_run_leaves_an_orphan_manifest(self):
        real = self.studio.records.put

        def fail_run(entity, record):
            if entity == 'run':
                raise OSError('simulated crash')
            return real(entity, record)

        with mock.patch.object(self.studio.records, 'put', side_effect=fail_run):
            with self.assertRaises(OSError):
                self.w.start(self.trial)
        self.assertIn('ORPHAN_MANIFEST', self.codes(pv.verify_store(self.studio)))
