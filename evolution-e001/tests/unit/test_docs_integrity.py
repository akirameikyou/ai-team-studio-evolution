"""E001-B: the fixed specification documents stay byte-identical to what was approved."""
import hashlib
import unittest
from pathlib import Path

DOCS = Path(__file__).resolve().parents[2] / 'docs'
REQUIRED = {
    'AI_Team_Studio_Accepted_Specification_E002.md',
    'AI_Team_Studio_Claude_Code_Implementation_Request_E001.md',
    'reference/AI_Team_Studio_STUDIO_SCHEMA_E001.json',
}


def read_sums():
    sums = {}
    for line in (DOCS / 'SHA256SUMS.txt').read_text(encoding='utf-8').splitlines():
        digest, name = line.split(None, 1)
        sums[name.lstrip('*').strip()] = digest
    return sums


class DocsIntegrityTests(unittest.TestCase):
    def test_required_documents_are_recorded(self):
        self.assertEqual(set(read_sums()), REQUIRED)

    def test_documents_match_recorded_sha256(self):
        for name, digest in read_sums().items():
            with self.subTest(name=name):
                self.assertEqual(hashlib.sha256((DOCS / name).read_bytes()).hexdigest(), digest)

    def test_approved_hashes_from_human_gate(self):
        # Values reported in E001-A and approved at the E001-B Human Gate.
        sums = read_sums()
        self.assertEqual(sums['AI_Team_Studio_Accepted_Specification_E002.md'],
                         '97a9bfaf8019c9a5c84a2e1bbfa4d72acb410995a42de8ea8525813236736a23')
        self.assertEqual(sums['AI_Team_Studio_Claude_Code_Implementation_Request_E001.md'],
                         'a6fe229f725e3a6c2f4b86c91f493d16b8e2d50bcb904875e247eb85fc763655')
        self.assertEqual(sums['reference/AI_Team_Studio_STUDIO_SCHEMA_E001.json'],
                         '53b8b5a071bdb603718d9f946832cf0ea75cb731cf2494c9589f1d4a4d741590')


if __name__ == '__main__':
    unittest.main()
