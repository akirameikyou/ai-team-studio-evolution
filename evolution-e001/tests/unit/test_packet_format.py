"""E001-F1/U2: AITS-PACKET/1 encode/decode and canonical artifact order (domain/protocol/packet.py).

U2 checks the format only. Header values here are checked for form, not for agreement with any
Manifest, Route or Transfer (that is U4). Fixture-based tests use the E001-B artifacts and routes.
"""
import copy
import hashlib
import json
import unittest
from pathlib import Path

from domain.protocol.packet import (DecodedPacket, PacketArtifact, PacketFormatError, PacketHeader, canonical_order,
                                    decode_packet, encode_packet, packet_sha256)

FIXTURE = json.loads((Path(__file__).resolve().parents[1] / 'fixtures' / 'p001_bundle_valid.json').read_text(encoding='utf-8'))
REC = FIXTURE['records']
ART = {a['artifactId']: a for a in REC['artifact']}
MANIFEST = REC['runManifest'][0]


def from_fixture(artifact_id):
    a = ART[artifact_id]
    return PacketArtifact(artifact_id, a['kind'], a['content'].encode('utf-8'))


def edge(route_id, edge_id):
    route = next(r for r in REC['route'] if r['routeId'] == route_id)
    e = next(e for e in route['edges'] if e['edgeId'] == edge_id)
    return e['fromNodeId'], e['toNodeId']


HEADER = PacketHeader('RUN-FIX-P001-1', 'case-a', *edge('ROUTE-P001-FIX', 'case-a'), 'PROTO-P001', '1.0')
CASE_IN, SOURCE = from_fixture('ART-case-input'), from_fixture('ART-source-001')
RAW_A, RAW_B = from_fixture('ART-raw-a'), from_fixture('ART-raw-b')


def build(*artifacts, header=HEADER):
    return encode_packet(header, artifacts)


class RoundTripTests(unittest.TestCase):
    def test_encode_produces_the_documented_layout(self):
        data = build(CASE_IN)
        body = CASE_IN.content
        expected = (b'AITS-PACKET/1\nrun: RUN-FIX-P001-1\nedge: case-a\nfrom: NODE-studio\nto: NODE-observer-a\n'
                    b'protocol: PROTO-P001@1.0\nartifacts: 1\n'
                    + f'--- ARTIFACT 1/1 id=ART-case-input kind=CASE_INPUT sha256={hashlib.sha256(body).hexdigest()} '
                      f'bytes={len(body)}\n'.encode() + body + b'\n--- END ARTIFACT 1/1\n--- END PACKET\n')
        self.assertEqual(data, expected)

    def test_decode_restores_header_and_bodies(self):
        decoded = decode_packet(build(CASE_IN, SOURCE, RAW_B))
        self.assertIsInstance(decoded, DecodedPacket)
        self.assertEqual(decoded.header, HEADER)
        self.assertEqual(decoded.artifacts, (CASE_IN, SOURCE, RAW_B))

    def test_same_input_gives_same_bytes_and_reencoding_is_identical(self):
        data = build(CASE_IN, RAW_A)
        self.assertEqual(build(CASE_IN, RAW_A), data)
        decoded = decode_packet(data)
        self.assertEqual(encode_packet(decoded.header, decoded.artifacts), data)
        self.assertEqual(packet_sha256(data), hashlib.sha256(data).hexdigest())

    def test_crlf_and_frame_like_text_in_a_body_survive(self):
        self.assertIn(b'\r\n', RAW_B.content)
        tricky = PacketArtifact('ART-tricky-001', 'HUMAN_NOTE', b'line\r\n--- END ARTIFACT 1/1\n--- END PACKET\n')
        decoded = decode_packet(build(RAW_B, tricky))
        self.assertEqual([a.content for a in decoded.artifacts], [RAW_B.content, tricky.content])

    def test_empty_body_and_zero_artifacts_are_allowed(self):
        empty = PacketArtifact('ART-empty-001', 'HUMAN_NOTE', b'')
        self.assertEqual(decode_packet(build(empty)).artifacts, (empty,))
        data = build()
        self.assertTrue(data.endswith(b'artifacts: 0\n--- END PACKET\n'))
        self.assertEqual(decode_packet(data).artifacts, ())


class FixturePatternTests(unittest.TestCase):
    """Real E001-B data: P001 packet to observer A, P002 hop A -> B."""

    def test_p001_packet_to_observer_a(self):
        data = build(*canonical_order(MANIFEST['initialArtifactIds'], [SOURCE, CASE_IN]))
        decoded = decode_packet(data)
        self.assertEqual([a.artifact_id for a in decoded.artifacts], ['ART-case-input', 'ART-source-001'])
        for secret in ('SECRET-A', 'SECRET-B', 'SECRET-C'):
            self.assertNotIn(secret.encode(), data)

    def test_p002_hop_a_to_b_carries_a_answer_bytes_exactly(self):
        header = PacketHeader('RUN-test-p002-1', 'a-to-b', *edge('ROUTE-P002-FIX', 'a-to-b'), 'PROTO-P002', '1.0')
        decoded = decode_packet(encode_packet(header, [RAW_A]))
        self.assertEqual((decoded.header.from_node_id, decoded.header.to_node_id), ('NODE-observer-a', 'NODE-observer-b'))
        self.assertEqual(decoded.artifacts[0].content, ART['ART-raw-a']['content'].encode('utf-8'))
        self.assertEqual(hashlib.sha256(decoded.artifacts[0].content).hexdigest(), ART['ART-raw-a']['contentHash'])


class CanonicalOrderTests(unittest.TestCase):
    def test_manifest_order_then_ascii_order(self):
        z = PacketArtifact('ART-zz-001', 'RAW_RESPONSE', b'z')
        upper = PacketArtifact('ART-Zz-001', 'RAW_RESPONSE', b'Z')
        ordered = canonical_order(['ART-source-001', 'ART-case-input'], [z, CASE_IN, RAW_B, upper, SOURCE, RAW_A])
        self.assertEqual([a.artifact_id for a in ordered],
                         ['ART-source-001', 'ART-case-input', 'ART-Zz-001', 'ART-raw-a', 'ART-raw-b', 'ART-zz-001'])

    def test_order_does_not_depend_on_input_order(self):
        items = [RAW_B, SOURCE, RAW_A, CASE_IN]
        expected = canonical_order(MANIFEST['initialArtifactIds'], items)
        for shift in range(len(items)):
            self.assertEqual(canonical_order(MANIFEST['initialArtifactIds'], items[shift:] + items[:shift]), expected)

    def test_nothing_is_added_or_dropped(self):
        ordered = canonical_order(['ART-case-input', 'ART-not-present'], [RAW_A])
        self.assertEqual(ordered, (RAW_A,))

    def test_duplicates_are_rejected(self):
        with self.assertRaises(PacketFormatError):
            canonical_order([], [RAW_A, RAW_A])
        with self.assertRaises(PacketFormatError):
            canonical_order(['ART-case-input', 'ART-case-input'], [CASE_IN])


class EncodeRejectionTests(unittest.TestCase):
    def assertRejected(self, header, artifacts):
        with self.assertRaises(PacketFormatError):
            encode_packet(header, artifacts)

    def test_duplicate_ids(self):
        self.assertRejected(HEADER, [RAW_A, RAW_A])

    def test_invalid_utf8_body(self):
        self.assertRejected(HEADER, [PacketArtifact('ART-bin-001', 'HUMAN_NOTE', b'\xff\xfe')])

    def test_invalid_artifact_fields(self):
        for bad in (PacketArtifact('raw-a', 'RAW_RESPONSE', b'x'), PacketArtifact('ART-x-001', 'OPINION', b'x'),
                    PacketArtifact('ART-x-001', 'RAW_RESPONSE', 'text')):
            with self.subTest(bad=bad):
                self.assertRejected(HEADER, [bad])

    def test_invalid_header_values(self):
        for field, value in (('run_id', 'run-1'), ('edge_id', 'case a'), ('from_node_id', 'studio'),
                             ('to_node_id', 'NODE-a\nrun: RUN-x-1'), ('protocol_id', 'P001'), ('protocol_version', '1')):
            with self.subTest(field=field):
                self.assertRejected(HEADER._replace(**{field: value}), [CASE_IN])

    def problems_of(self, header, artifacts):
        with self.assertRaises(PacketFormatError) as cm:
            encode_packet(header, artifacts)
        return cm.exception.problems

    def test_duplicate_is_reported_together_with_a_header_problem(self):
        dup = [PacketArtifact('ART-x-001', 'RAW_RESPONSE', b'a'), PacketArtifact('ART-x-001', 'RAW_RESPONSE', b'b')]
        problems = self.problems_of(HEADER._replace(run_id='bad'), dup)
        self.assertIn("run: invalid value 'bad'", problems)
        self.assertIn("duplicate artifact id 'ART-x-001'", problems)

    def test_duplicate_is_reported_together_with_an_artifact_field_problem(self):
        items = [PacketArtifact('ART-x-001', 'RAW_RESPONSE', b'a'), PacketArtifact('ART-x-001', 'OPINION', b'b')]
        problems = self.problems_of(HEADER, items)
        self.assertIn("artifact 2: unknown kind 'OPINION'", problems)
        self.assertIn("duplicate artifact id 'ART-x-001'", problems)

    def test_items_that_cannot_be_inspected_are_reported_and_skipped_by_the_duplicate_check(self):
        items = [CASE_IN, 'not an artifact', PacketArtifact(['unhashable'], 'RAW_RESPONSE', b'x'), CASE_IN]
        problems = self.problems_of(HEADER, items)
        self.assertIn('artifact 2: must be a PacketArtifact, got str', problems)
        self.assertIn("artifact 3: invalid id ['unhashable']", problems)
        self.assertIn("duplicate artifact id 'ART-case-input'", problems)

    def test_inputs_are_not_modified(self):
        artifacts = [CASE_IN, RAW_B]
        before = copy.deepcopy(artifacts)
        encode_packet(HEADER, artifacts)
        canonical_order(MANIFEST['initialArtifactIds'], artifacts)
        self.assertEqual(artifacts, before)


class DecodeRejectionTests(unittest.TestCase):
    good = build(CASE_IN, RAW_B)

    def assertRejected(self, data):
        with self.assertRaises(PacketFormatError):
            decode_packet(data)

    def test_count_mismatch(self):
        self.assertRejected(self.good.replace(b'artifacts: 2\n', b'artifacts: 3\n', 1))
        self.assertRejected(self.good.replace(b'artifacts: 2\n', b'artifacts: 1\n', 1))

    def test_byte_count_mismatch(self):
        n = len(CASE_IN.content)
        for wrong in (n - 1, n + 1):
            self.assertRejected(self.good.replace(f'bytes={n}\n'.encode(), f'bytes={wrong}\n'.encode(), 1))

    def test_sha256_mismatch(self):
        data = bytearray(self.good)
        i = data.index(CASE_IN.content) + 3
        data[i] ^= 0x01
        self.assertRejected(bytes(data))

    def test_bad_header_and_unknown_version(self):
        self.assertRejected(self.good.replace(b'AITS-PACKET/1\n', b'AITS-PACKET/2\n', 1))
        self.assertRejected(self.good.replace(b'AITS-PACKET/1\n', b'HELLO\n', 1))
        self.assertRejected(b'')

    def test_field_order_and_frame_errors(self):
        swapped = self.good.replace(b'run: RUN-FIX-P001-1\nedge: case-a\n', b'edge: case-a\nrun: RUN-FIX-P001-1\n', 1)
        for data in (swapped,
                     self.good.replace(b'run: ', b'run:  ', 1),
                     self.good.replace(b'protocol: PROTO-P001@1.0', b'protocol: PROTO-P001', 1),
                     self.good.replace(b'artifacts: 2\n', b'artifacts: 02\n', 1),
                     self.good.replace(b'--- ARTIFACT 2/2', b'--- ARTIFACT 3/2', 1),
                     self.good.replace(b'--- END ARTIFACT 1/2\n', b'--- END ARTIFACT 2/2\n', 1),
                     self.good.replace(b'\n', b'\r\n', 1)):
            with self.subTest(data=data[:60]):
                self.assertRejected(data)

    def test_wrong_field_name_with_a_valid_value(self):
        # Same length and a valid run id: only the field-name check can reject this.
        self.assertRejected(self.good.replace(b'run: RUN-FIX-P001-1', b'rux: RUN-FIX-P001-1', 1))

    def test_malformed_artifact_header_line(self):
        for old, new in ((b'kind=CASE_INPUT sha256', b'kind=CASE_INPUT  sha256'),
                         (b' bytes=', b' size='),
                         (b'--- ARTIFACT 1/2', b'--- artifact 1/2')):
            with self.subTest(new=new):
                self.assertRejected(self.good.replace(old, new, 1))

    def test_truncation_inside_a_body_is_reported_as_truncation(self):
        cut = self.good.index(CASE_IN.content) + 2
        with self.assertRaises(PacketFormatError) as cm:
            decode_packet(self.good[:cut])
        self.assertTrue(cm.exception.problems[0].startswith('truncated'), cm.exception.problems)

    def test_body_must_be_followed_by_a_line_feed(self):
        # Correct length and hash, then 'X' instead of LF, then a valid end line:
        # only the body-terminator check can reject this.
        body = b'abc'
        frame = (b'AITS-PACKET/1\nrun: RUN-FIX-P001-1\nedge: case-a\nfrom: NODE-studio\nto: NODE-observer-a\n'
                 b'protocol: PROTO-P001@1.0\nartifacts: 1\n'
                 + f'--- ARTIFACT 1/1 id=ART-x-001 kind=HUMAN_NOTE sha256={hashlib.sha256(body).hexdigest()} bytes=3\n'.encode()
                 + body + b'X--- END ARTIFACT 1/1\n--- END PACKET\n')
        self.assertRejected(frame)

    def test_corrupted_end_of_packet_line(self):
        self.assertRejected(self.good.replace(b'--- END PACKET\n', b'--- END PACKXT\n', 1))

    def test_non_ascii_header(self):
        self.assertRejected(self.good.replace(b'edge: case-a', 'edge: case-é'.encode('utf-8'), 1))

    def test_non_ascii_byte_in_artifact_id_is_a_format_error(self):
        data = self.good.replace(b'id=ART-case-input', 'id=ART-case-inpué'.encode('utf-8'), 1)
        with self.assertRaises(PacketFormatError) as cm:  # not UnicodeDecodeError
            decode_packet(data)
        self.assertTrue(cm.exception.problems[0].startswith('artifact 1: malformed header'), cm.exception.problems)

    def test_non_ascii_byte_in_artifact_kind_is_a_format_error(self):
        data = self.good.replace(b'kind=CASE_INPUT', 'kind=CASE_INPUé'.encode('utf-8'), 1)
        with self.assertRaises(PacketFormatError) as cm:  # not UnicodeDecodeError
            decode_packet(data)
        self.assertTrue(cm.exception.problems[0].startswith('artifact 1: malformed header'), cm.exception.problems)

    def test_truncated_anywhere(self):
        for cut in range(len(self.good)):
            with self.subTest(cut=cut):
                self.assertRejected(self.good[:cut])

    def test_trailing_bytes(self):
        self.assertRejected(self.good + b'x')
        self.assertRejected(self.good + b'\n')

    def test_duplicate_ids(self):
        data = build(CASE_IN, RAW_A).replace(b'id=ART-raw-a ', b'id=ART-case-input ', 1)
        data = data.replace(b'kind=RAW_RESPONSE', b'kind=CASE_INPUT', 1)
        self.assertRejected(data)

    def test_invalid_utf8_body(self):
        body = b'\xff'
        frame = (b'AITS-PACKET/1\nrun: RUN-FIX-P001-1\nedge: case-a\nfrom: NODE-studio\nto: NODE-observer-a\n'
                 b'protocol: PROTO-P001@1.0\nartifacts: 1\n'
                 + f'--- ARTIFACT 1/1 id=ART-bin-001 kind=HUMAN_NOTE sha256={hashlib.sha256(body).hexdigest()} bytes=1\n'.encode()
                 + body + b'\n--- END ARTIFACT 1/1\n--- END PACKET\n')
        self.assertRejected(frame)

    def test_invalid_ids_and_kinds_in_frame(self):
        for old, new in ((b'id=ART-case-input', b'id=case-input'), (b'kind=CASE_INPUT', b'kind=OPINION'),
                         (b'to: NODE-observer-a', b'to: observer-a'), (b'run: RUN-FIX-P001-1', b'run: RUN-1')):
            with self.subTest(old=old):
                self.assertRejected(self.good.replace(old, new, 1))

    def test_not_bytes(self):
        self.assertRejected(self.good.decode('utf-8'))


if __name__ == '__main__':
    unittest.main()
