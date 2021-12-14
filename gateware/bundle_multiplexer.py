import operator
from functools import reduce

from nmigen                 import *
from nmigen.build           import Platform
from nmigen.lib.fifo        import SyncFIFO
from nmigen_library.stream  import StreamInterface, connect_stream_to_fifo
from nmigen_library.test    import GatewareTestCase, sync_test_case

class BundleMultiplexer(Elaboratable):
    NO_CHANNELS_ADAT = 8
    SAMPLE_WIDTH     = 24

    def __init__(self, no_bundles=4):
        # parameters
        self._no_bundles           = no_bundles
        self._channel_bits         = Shape.cast(range(no_bundles * self.NO_CHANNELS_ADAT)).width
        self._bundle_channel_bits  = Shape.cast(range(self.NO_CHANNELS_ADAT)).width

        # ports
        self.channel_stream_out  = StreamInterface(name="channel_stream",
                                                   payload_width=self.SAMPLE_WIDTH,
                                                   extra_fields=[("channel_nr", self._channel_bits)])

        self.bundles_in          = Array(StreamInterface(name=f"input_bundle_{i}",
                                                         payload_width=self.SAMPLE_WIDTH,
                                                         extra_fields=[("channel_nr", self._bundle_channel_bits)])
                                         for i in range(no_bundles))

    def elaborate(self, platform: Platform) -> Module:
        m = Module()
        sample_width = self.SAMPLE_WIDTH
        bundle_bits  = self._bundle_channel_bits

        bundle_ready   = Array(Signal(              name=f"bundle{i}_ready")   for i in range(self._no_bundles))
        bundle_sample  = Array(Signal(sample_width, name=f"bundle{i}_sample")  for i in range(self._no_bundles))
        bundle_channel = Array(Signal(bundle_bits,  name=f"bundle{i}_channel") for i in range(self._no_bundles))
        last           = Array(Signal(              name=f"bundle{i}_last")    for i in range(self._no_bundles))
        read_enable    = Array(Signal(              name=f"bundle{i}_read_en") for i in range(self._no_bundles))

        for i in range(self._no_bundles):
            fifo = SyncFIFO(width=sample_width + bundle_bits + 1, depth=self.NO_CHANNELS_ADAT)
            setattr(m.submodules, f"receive_fifo{i}", fifo)

            m.d.comb += [
                *connect_stream_to_fifo(self.bundles_in[i], fifo),
                fifo.w_data[sample_width:].eq(self.bundles_in[i].channel_nr),
                fifo.w_data[-1].eq(self.bundles_in[i].last),

                bundle_ready[i]   .eq(fifo.r_rdy),
                bundle_sample[i]  .eq(fifo.r_data[:sample_width]),
                bundle_channel[i] .eq(fifo.r_data[sample_width:sample_width + bundle_bits]),

                last[i].eq(fifo.r_data[-1]),
                fifo.r_en.eq(read_enable[i]),
            ]

        current_bundle       = Signal(range(self._no_bundles))
        first_bundle_channel = Signal(self._channel_bits)

        with m.If(bundle_ready[current_bundle] & self.channel_stream_out.ready):
            m.d.comb += [
                self.channel_stream_out.payload.eq(bundle_sample[current_bundle]),
                self.channel_stream_out.channel_nr.eq(first_bundle_channel + bundle_channel[current_bundle]),
                read_enable[current_bundle].eq(1),
                self.channel_stream_out.valid.eq(1),
                self.channel_stream_out.first.eq((current_bundle == 0) & (bundle_channel[current_bundle] == 0))
            ]

            with m.If(last[current_bundle]):
                last_bundle = (self._no_bundles - 1)
                with m.If(current_bundle == last_bundle):
                    m.d.comb += self.channel_stream_out.last.eq(1)
                    m.d.sync += [
                        current_bundle.eq(0),
                        first_bundle_channel.eq(0),
                    ]
                with m.Else():
                    m.d.sync += [
                        current_bundle.eq(current_bundle + 1),
                        first_bundle_channel.eq(first_bundle_channel + self.NO_CHANNELS_ADAT)
                    ]

        return m

class BundleMultiplexerTest(GatewareTestCase):
    FRAGMENT_UNDER_TEST = BundleMultiplexer
    FRAGMENT_ARGUMENTS  = dict()

    def send_one_frame(self, bundle: int, sample: int, channel: int, wait=False):
        yield self.dut.bundles_in[bundle].channel_nr.eq(channel)
        yield self.dut.bundles_in[bundle].payload.eq(sample)
        yield self.dut.bundles_in[bundle].valid.eq(1)
        yield self.dut.bundles_in[bundle].first.eq(channel == 0)
        yield self.dut.bundles_in[bundle].last.eq(channel == 7)
        yield
        yield self.dut.bundles_in[bundle].valid.eq(0)
        if wait:
            yield

    def send_bundle_frame(self, bundle: int, sample: int):
        for channel in range(self.dut.NO_CHANNELS_ADAT):
            yield from self.send_one_frame(bundle, (sample << 8) + channel, channel)


    @sync_test_case
    def test_smoke(self):
        dut = self.dut
        yield
        yield dut.channel_stream_out.ready.eq(1)
        yield
        for bundle in range(4):
            yield from self.send_bundle_frame(bundle, bundle)

        yield
        yield