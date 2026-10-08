"""E001-D: Artifact -> Transfer -> Run -> Trial -> Case and back, by ID references only."""
import json

from application import provenance as pv
from application.execution_context import ContextReferenceError, ExecutionContextError, new_id
from infrastructure.storage.event_log import SYSTEM_ACTOR_ID
from tests.integration.support import StudioTestCase
from tests.persistence.support import tamper


class ProvenanceChainTests(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.p1 = self.w.run_p001()

    def test_upward_trace_of_a_response(self):
        w, p1 = self.w, self.p1
        tr = pv.trace_artifact(self.studio, p1['raw']['b'])
        self.assertEqual(tr.problems, [])
        self.assertEqual((tr.transfer_id, tr.edge_id, tr.from_node_id, tr.to_node_id),
                         (p1['transfers']['b'], 'case-b', SYSTEM_ACTOR_ID, w.obs['b']))
        self.assertEqual((tr.run_id, tr.trial_id, tr.case_id), (p1['run'], p1['trial'], w.case))
        self.assertEqual((tr.role, tr.provider_id), ('OBSERVER', w.provider))
        self.assertEqual((tr.pattern_ref, tr.route_ref, tr.protocol_ref), (w.p001, w.route1, w.proto1))

    def test_upward_trace_of_a_packet_and_of_case_level_artifacts(self):
        tr = pv.trace_artifact(self.studio, self.p1['packets']['a'])
        self.assertEqual((tr.problems, tr.producer_node_id, tr.run_id), ([], SYSTEM_ACTOR_ID, self.p1['run']))
        tr = pv.trace_artifact(self.studio, self.w.case_input)
        self.assertEqual((tr.problems, tr.run_id, tr.case_id), ([], None, self.w.case))

    def test_downward_trace_from_case(self):
        tree = pv.trace_case(self.studio, self.w.case)
        (trial,) = tree['trials']
        (run,) = trial['runs']
        self.assertEqual((trial['trialId'], run['runId'], run['status']), (self.p1['trial'], self.p1['run'], 'RUNNING'))
        by_edge = {t['edgeId']: t for t in run['transfers']}
        self.assertEqual(set(by_edge), {'case-a', 'case-b', 'case-c', 'syn-a', 'syn-b', 'syn-c'})
        self.assertEqual(by_edge['case-a']['responses'], [self.p1['raw']['a']])
        self.assertEqual(by_edge['syn-a']['carried'], [self.p1['raw']['a']])

    def test_whole_store_verifies(self):
        self.assertEqual(pv.verify_store(self.studio), [])

    def test_response_naming_the_wrong_run_is_detected(self):
        other_run = self.w.start(self.w.trial('P002'))
        bad = self.studio.artifacts.create(content=b'misfiled', kind='RAW_RESPONSE', case_id=self.w.case, source_kind='AI',
                                           received_via='MANUAL_PASTE', run_id=other_run, transfer_id=self.p1['transfers']['a'],
                                           producer_node_id=self.w.obs['a']).artifact_id
        self.assertIn('PROVENANCE_MISMATCH', self.codes(pv.trace_artifact(self.studio, bad).problems))
        self.assertIn('SR-17', {p.message.split()[0] for p in pv.verify_store(self.studio)})

    def test_response_from_a_node_that_was_not_sent_the_packet_is_detected(self):
        bad = self.w.respond(self.p1['run'], self.p1['transfers']['a'], self.w.obs['c'], 'C answering A\'s packet')
        self.assertIn('PROVENANCE_MISMATCH', self.codes(pv.trace_artifact(self.studio, bad).problems))

    def test_tampered_case_record_is_detected_against_its_event(self):
        path = self.studio.records.dir / 'case' / f'{self.w.case}.json'
        rec = json.loads(path.read_bytes())
        rec['question'] = 'a different question'
        tamper(path, json.dumps(rec).encode())
        codes = self.codes(pv.verify_store(self.studio))
        self.assertIn('RECORD_MISMATCH', codes)
        self.assertIn('FROZEN_RECORD_MISMATCH', codes)


class TransferRecordTests(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.run = self.w.start(self.w.trial())

    def packet(self, tid, kind='PACKET', run=None):
        return self.studio.artifacts.create(content=b'packet', kind=kind, case_id=self.w.case, source_kind='SYSTEM',
                                            received_via='SYSTEM_GENERATED', run_id=run or self.run, transfer_id=tid,
                                            producer_node_id=SYSTEM_ACTOR_ID).artifact_id

    def test_transfer_records_the_actual_movement(self):
        tid = new_id('XFER')
        pkt = self.packet(tid)
        self.studio.record_transfer(self.run, 'case-a', pkt, artifact_ids=[self.w.case_input], transfer_id=tid)
        t = self.studio.records.get('transfer', tid).record()
        self.assertEqual((t['edgeId'], t['fromNodeId'], t['toNodeId'], t['method'], t['externalDelivery'], t['isolationAssurance']),
                         ('case-a', SYSTEM_ACTOR_ID, self.w.obs['a'], 'MANUAL_CLIPBOARD', 'UNKNOWN', 'PACKET_CONTROLLED'))
        self.assertEqual(t['packetHash'], self.studio.artifacts.read(pkt).content_hash)

    def test_transfer_rules(self):
        tid = new_id('XFER')
        pkt = self.packet(tid)
        refused = {
            'edge not in route': lambda: self.studio.record_transfer(self.run, 'a-to-b', pkt, transfer_id=tid),
            'packet names another transfer': lambda: self.studio.record_transfer(self.run, 'case-a', pkt, transfer_id=new_id('XFER')),
            'not a packet': lambda: self.studio.record_transfer(self.run, 'case-a', self.w.case_input, transfer_id=tid),
            'kind not allowed on edge': lambda: self.studio.record_transfer(self.run, 'syn-a', pkt, artifact_ids=[self.w.case_input], transfer_id=tid),
            'carried artifact does not exist yet': lambda: self.studio.record_transfer(self.run, 'case-a', pkt, artifact_ids=['ART-not-yet-1'], transfer_id=tid),
        }
        for name, action in refused.items():
            with self.subTest(name):
                with self.assertRaises(ContextReferenceError):
                    action()
        for claim in ('ENFORCED',):
            with self.assertRaises(ExecutionContextError):
                self.studio.record_transfer(self.run, 'case-a', pkt, transfer_id=tid, isolation_assurance=claim)
        self.assertEqual(self.studio.records.list('transfer'), [])
