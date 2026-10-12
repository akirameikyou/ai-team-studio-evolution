"""E001-F1/U3-3: packet assembly for one edge of one Run (read only, minimal version).

Spec: reports/U3-3_MINIMAL_IMPLEMENTATION_SPEC_001.md and U3-3_IMPLEMENTATION_INSTRUCTIONS_001.md
(review copy). From a U3-2 Selection, build the deterministic packet bytes, their SHA-256 and the
ordered source artifact ids - the material for E002 section 15 items 4-6. The first stage that applies
decides the result:

  P1  the selection is INCOMPLETE                 -> SELECTION_INCOMPLETE
  P2  any candidate is UNRESOLVED (H-1)           -> UNRESOLVED_PRESENT
  P3  nothing was accepted (H-2)                  -> NO_ACCEPTED_CANDIDATES
  P4  an accepted artifact cannot be read         -> ARTIFACT_UNREADABLE
  P5  the run's manifest cannot be read           -> RECORD_UNREADABLE
  P6  the U2 packet format refuses the content    -> PACKET_FORMAT_ERROR

Nothing is written (H-3): no packet artifact, no PACKET_ASSEMBLED event, no record_transfer. Only the
reading calls at P4 and P5 catch OSError; any other error propagates. The status names and reason codes
below are return values of this module only, not Schema states.
"""
from typing import NamedTuple

from application.candidate_selection import INCOMPLETE
from application.execution_context import ContextReferenceError
from domain.protocol.packet import (PacketArtifact, PacketFormatError, PacketHeader, canonical_order, encode_packet,
                                    packet_sha256)
from infrastructure.storage.artifact_store import StoreError
from infrastructure.storage.record_store import RecordStoreError

ASSEMBLED, NOT_ASSEMBLED = 'ASSEMBLED', 'NOT_ASSEMBLED'


class AssembledPacket(NamedTuple):
    status: str            # ASSEMBLED | NOT_ASSEMBLED
    header: object         # PacketHeader; None when NOT_ASSEMBLED
    artifact_ids: tuple    # ids in packet order; () when NOT_ASSEMBLED
    packet_bytes: bytes    # b'' when NOT_ASSEMBLED
    packet_sha256: object  # hex str; None when NOT_ASSEMBLED
    problems: tuple        # ((code, message), ...); () when ASSEMBLED


def _not_assembled(problems):
    return AssembledPacket(NOT_ASSEMBLED, None, (), b'', None, tuple(problems))


def _os_detail(e):
    return f'{type(e).__name__}: {e} (filename={e.filename!r})'


def assemble_packet(studio, selection):
    """Build the packet for a U3-2 Selection. Read only."""
    if selection.status == INCOMPLETE:                                                    # P1
        return _not_assembled((('SELECTION_INCOMPLETE', f'selection for {selection.edge_id!r} is incomplete'),)
                              + tuple(selection.problems))
    if selection.unresolved:                                                              # P2
        return _not_assembled(tuple(('UNRESOLVED_PRESENT', f'{u.artifact_id} (producer {u.producer_node_id}): {u.code}')
                                    for u in selection.unresolved))
    if not selection.accepted:                                                            # P3
        return _not_assembled((('NO_ACCEPTED_CANDIDATES', f'nothing was accepted for {selection.edge_id!r}'),))

    stored = []
    for aid in selection.accepted:                                                        # P4
        try:
            stored.append(studio.artifacts.read(aid))
        except StoreError as e:
            return _not_assembled((('ARTIFACT_UNREADABLE', f'{aid}: {e}'),))
        except OSError as e:
            return _not_assembled((('ARTIFACT_UNREADABLE', f'{aid}: {_os_detail(e)}'),))
    artifacts = [PacketArtifact(s.artifact_id, s.metadata()['kind'], s.content) for s in stored]

    try:                                                                                  # P5
        manifest = studio.manifest_of(selection.run_id)
    except (ContextReferenceError, RecordStoreError) as e:
        return _not_assembled((('RECORD_UNREADABLE', f'manifest of {selection.run_id}: {e}'),))
    except OSError as e:
        return _not_assembled((('RECORD_UNREADABLE', f'manifest of {selection.run_id}: {_os_detail(e)}'),))
    initial_ids = manifest['initialArtifactIds']

    header = PacketHeader(selection.run_id, selection.edge_id, selection.from_node_id, selection.to_node_id,
                          selection.protocol_ref['id'], selection.protocol_ref['version'])
    try:                                                                                  # P6
        ordered = canonical_order(initial_ids, artifacts)
        data = encode_packet(header, ordered)
    except PacketFormatError as e:
        return _not_assembled(tuple(('PACKET_FORMAT_ERROR', p) for p in e.problems))
    return AssembledPacket(ASSEMBLED, header, tuple(a.artifact_id for a in ordered), data, packet_sha256(data), ())
