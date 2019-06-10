#
# Copyright (c) 2019 UAVCAN Development Team
# This software is distributed under the terms of the MIT License.
# Author: Pavel Kirienko <pavel.kirienko@zubax.com>
#

import time
import typing
import pytest
import pyuavcan
import pyuavcan.transport.can
import tests.transport.can


_RX_TIMEOUT = 10e-3


# noinspection PyProtectedMember
@pytest.mark.asyncio    # type: ignore
async def _unittest_slow_presentation_pub_sub(generated_packages: typing.List[pyuavcan.dsdl.GeneratedPackageInfo]) \
        -> None:
    assert generated_packages
    import uavcan.node
    import uavcan.time
    import uavcan.diagnostic
    from pyuavcan.transport import Priority

    bus: typing.Set[tests.transport.can.media.mock.MockMedia] = set()
    media_a = tests.transport.can.media.mock.MockMedia(bus, 8, 1)
    media_b = tests.transport.can.media.mock.MockMedia(bus, 64, 2)      # Look, a heterogeneous setup!
    assert bus == {media_a, media_b}

    tran_a = pyuavcan.transport.can.CANTransport(media_a)
    tran_b = pyuavcan.transport.can.CANTransport(media_b)

    pres_a = pyuavcan.presentation.Presentation(tran_a)
    pres_b = pyuavcan.presentation.Presentation(tran_b)

    assert pres_a.transport is tran_a

    pub_heart = await pres_a.make_publisher_with_fixed_subject_id(uavcan.node.Heartbeat_1_0)
    sub_heart = await pres_b.make_subscriber_with_fixed_subject_id(uavcan.node.Heartbeat_1_0)

    pub_record = await pres_b.make_publisher_with_fixed_subject_id(uavcan.diagnostic.Record_1_0)
    sub_record = await pres_a.make_subscriber_with_fixed_subject_id(uavcan.diagnostic.Record_1_0)

    assert pub_heart._impl.proxy_count == 1
    # TODO: "async with await" is an antipattern https://github.com/python/asyncio/issues/316
    async with await pres_a.make_publisher_with_fixed_subject_id(uavcan.node.Heartbeat_1_0) as pub_heart_new:
        assert pub_heart is not pub_heart_new
        assert pub_heart._impl is pub_heart_new._impl
        assert pub_heart._impl.proxy_count == 2
    assert pub_heart._impl.proxy_count == 1

    pub_heart_impl_old = pub_heart._impl
    await pub_heart.close()
    assert pub_heart_impl_old.proxy_count == 0

    pub_heart = await pres_a.make_publisher_with_fixed_subject_id(uavcan.node.Heartbeat_1_0)
    assert pub_heart._impl is not pub_heart_impl_old

    assert pub_heart.transport_session.destination_node_id is None
    assert sub_heart.transport_session.specifier.data_specifier == pub_heart.transport_session.specifier.data_specifier
    assert pub_heart.port_id == pyuavcan.dsdl.get_fixed_port_id(uavcan.node.Heartbeat_1_0)
    assert sub_heart.dtype is uavcan.node.Heartbeat_1_0

    heart = uavcan.node.Heartbeat_1_0(uptime=123456,
                                      health=uavcan.node.Heartbeat_1_0.HEALTH_CAUTION,
                                      mode=uavcan.node.Heartbeat_1_0.MODE_OPERATIONAL,
                                      vendor_specific_status_code=0xc0fe)
    assert pub_heart.priority == pyuavcan.presentation.DEFAULT_PRIORITY
    pub_heart.priority = Priority.SLOW
    assert pub_heart.priority == Priority.SLOW
    await pub_heart.publish(heart)
    rx, transfer = await sub_heart.receive_with_transfer()  # type: typing.Any, pyuavcan.transport.TransferFrom
    assert repr(rx) == repr(heart)
    assert transfer.source_node_id is None
    assert transfer.priority == Priority.SLOW
    assert transfer.transfer_id == 0

    stat = sub_heart.sample_statistics()
    assert stat.transfer.transfers == 1
    assert stat.transfer.frames == 1
    assert stat.transfer.overruns == 0
    assert stat.deserialization_failures == 0
    assert stat.messages == 1

    await tran_a.set_local_node_id(123)
    await tran_b.set_local_node_id(42)

    pub_heart.transfer_id_counter.override(23)
    await pub_heart.publish(heart)
    rx, transfer = await sub_heart.receive_with_transfer()
    assert repr(rx) == repr(heart)
    assert transfer.source_node_id == 123
    assert transfer.priority == Priority.SLOW
    assert transfer.transfer_id == 23

    stat = sub_heart.sample_statistics()
    assert stat.transfer.transfers == 2
    assert stat.transfer.frames == 2
    assert stat.transfer.overruns == 0
    assert stat.deserialization_failures == 0
    assert stat.messages == 2

    await pub_heart.publish(heart)
    rx = await sub_heart.receive()
    assert repr(rx) == repr(heart)

    await pub_heart.publish(heart)
    rx = await sub_heart.try_receive(time.monotonic() + _RX_TIMEOUT)
    assert repr(rx) == repr(heart)
    rx = await sub_heart.try_receive(time.monotonic() + _RX_TIMEOUT)
    assert rx is None

    await sub_heart.close()
    with pytest.raises(pyuavcan.transport.ResourceClosedError):
        await sub_heart.close()

    record = uavcan.diagnostic.Record_1_0(timestamp=uavcan.time.SynchronizedTimestamp_1_0(1234567890),
                                          severity=uavcan.diagnostic.Severity_1_0(uavcan.diagnostic.Severity_1_0.ALERT),
                                          text='Hello world!')
    assert pub_record.priority == pyuavcan.presentation.DEFAULT_PRIORITY
    pub_record.priority = Priority.NOMINAL
    assert pub_record.priority == Priority.NOMINAL
    with pytest.raises(ValueError, match='.*Heartbeat.*'):
        # noinspection PyTypeChecker
        await pub_heart.publish(record)  # type: ignore

    await pub_record.publish(record)
    rx, transfer = await sub_record.receive_with_transfer()
    assert repr(rx) == repr(record)
    assert transfer.source_node_id == 42
    assert transfer.priority == Priority.NOMINAL
    assert transfer.transfer_id == 0

    # Broken transfer
    stat = sub_record.sample_statistics()
    assert stat.transfer.transfers == 1
    assert stat.transfer.frames == 1
    assert stat.transfer.overruns == 0
    assert stat.deserialization_failures == 0
    assert stat.messages == 1

    await pub_record.transport_session.send(pyuavcan.transport.Transfer(
        timestamp=pyuavcan.transport.Timestamp.now(),
        priority=Priority.NOMINAL,
        transfer_id=12,
        fragmented_payload=[memoryview(b'Broken')],
    ))
    assert (await sub_record.try_receive(time.monotonic() + _RX_TIMEOUT)) is None

    stat = sub_record.sample_statistics()
    assert stat.transfer.transfers == 2
    assert stat.transfer.frames == 2
    assert stat.transfer.overruns == 0
    assert stat.deserialization_failures == 1
    assert stat.messages == 1

    await pres_a.close()
    await pres_b.close()
    with pytest.raises(pyuavcan.transport.ResourceClosedError):
        await pres_a.close()
