"""Pre-processes loop packets and possibly make modifications."""

# Standard Python Libraries
import logging

logger = logging.getLogger(__name__)

TX_BATTERY_STATUS_FIELDS = [
    "batteryStatusISS",
    "batteryStatusChannel1",
    "batteryStatusChannel2",
    "batteryStatusChannel3",
    "batteryStatusChannel4",
    "batteryStatusChannel5",
    "batteryStatusChannel6",
    "batteryStatusChannel7",
]


class PacketPreprocessor:
    """Pre-process loop packets."""

    def process_packet(self, packet: dict) -> dict:
        """Modify the packet before it is processed."""
        if "txBatteryStatus" in packet:
            bitmap = packet.pop("txBatteryStatus")
            if bitmap is None:
                # The station reported no battery status. That says nothing
                # about the transmitters, so publish nothing for them rather
                # than guessing -- and never raise: process_packet runs on a
                # worker thread whose completion handler answers an exception
                # by logging and returning, which would discard every
                # measurement in the packet along with the battery fields.
                logger.debug("No txBatteryStatus reading in this packet")
                return packet
            # txBatteryStatus is a bitmap, one bit per transmitter. Shift the
            # bit down to 0/1 instead of masking it in place: the catalogue
            # declares these fields as binary sensors matching payload_on: 1,
            # so a masked bit would publish its weight (2, 4, ... 128) and only
            # batteryStatusISS on bit 0 would ever read as low.
            for i, field in enumerate(TX_BATTERY_STATUS_FIELDS):
                packet[field] = (bitmap >> i) & 1
        return packet
