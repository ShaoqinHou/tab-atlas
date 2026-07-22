from __future__ import annotations

from .protocol_archive import ArchiveCapability
from .protocol_capture import CaptureCapability
from .protocol_command import CommandCapability
from .protocol_mutation import MutationCapability
from .protocol_pairing import PairingCapability
from .protocol_transport import (
    EXPECTED_EXTENSION_ID as EXPECTED_EXTENSION_ID,
    HOST as HOST,
    PAIRING_ALPHABET as PAIRING_ALPHABET,
    PORT as PORT,
    ProtocolTransportHandler,
    ReceiverSession as ReceiverSession,
    TabAtlasHTTPServer as TabAtlasHTTPServer,
)


class TabAtlasHandler(
    CommandCapability,
    PairingCapability,
    CaptureCapability,
    MutationCapability,
    ArchiveCapability,
    ProtocolTransportHandler,
):
    pass
