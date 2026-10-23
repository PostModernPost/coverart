"""
Shazam song recognition in plain Python (numpy only), for places shazamio's compiled core isn't
available, like the Android app.

The signature code (SignatureGenerator, DecodedMessage and helpers) is from shazamio
(https://github.com/shazamio/ShazamIO, MIT License, Copyright (c) 2021 dotX12), which in turn
follows SongRec by marin-m. Only the imports were changed.
"""
import asyncio
import json
import random
import time
import uuid
import wave
from enum import IntEnum
from typing import List, Optional, Any, Dict
from copy import copy
from base64 import b64decode, b64encode
from math import exp, sqrt
from binascii import crc32
from io import BytesIO
from ctypes import *
import numpy as np


class SampleRate(IntEnum):
    _8000 = 1
    _11025 = 2
    _16000 = 3
    _32000 = 4
    _44100 = 5
    _48000 = 6


class FrequencyBand(IntEnum):
    hz_0_250 = -1
    hz_250_520 = 0
    hz_520_1450 = 1
    hz_1450_3500 = 2
    hz_3500_5500 = 3


DATA_URI_PREFIX = "data:audio/vnd.shazam.sig;base64,"


class RawSignatureHeader(LittleEndianStructure):
    _pack = True

    _fields_ = [
        ("magic1", c_uint32),  # Fixed 0xcafe2580 - 80 25 fe ca
        (
            "crc32",
            c_uint32,
        ),  # CRC-32 for all following (so excluding these first 8 bytes)
        ("size_minus_header", c_uint32),
        # Total size of the message, minus the size of the current header (which is 48 bytes)
        ("magic2", c_uint32),  # Fixed 0x94119c00 - 00 9c 11 94
        ("void1", c_uint32 * 3),  # Void
        ("shifted_sample_rate_id", c_uint32),
        # A member of SampleRate (usually 3 for 16000 Hz), left-shifted by 27 (usually giving
        # 0x18000000 - 00 00 00 18)
        ("void2", c_uint32 * 2),  # Void, or maybe used only in "rolling window" mode?
        ("number_samples_plus_divided_sample_rate", c_uint32),
        # int(number_of_samples + sample_rate * 0.24) - As the sample rate is known thanks to the
        # field above,
        # it can be inferred and subtracted so that we obtain the number of samples,
        # and from the number of samples and sample rate we can obtain the length of the recording
        ("fixed_value", c_uint32),
        # Calculated as ((15 << 19) + 0x40000) - 0x7c0000 or 00 00 7c 00 - seems pretty constant,
        # may be different in the "SigType.STREAMING" mode
    ]


class FrequencyPeak:
    fft_pass_number: int = None
    peak_magnitude: int = None
    corrected_peak_frequency_bin: int = None
    sample_rate_hz: int = None

    def __init__(
        self,
        fft_pass_number: int,
        peak_magnitude: int,
        corrected_peak_frequency_bin: int,
        sample_rate_hz: int,
    ):
        self.fft_pass_number = fft_pass_number
        self.peak_magnitude = peak_magnitude
        self.corrected_peak_frequency_bin = corrected_peak_frequency_bin
        self.sample_rate_hz = sample_rate_hz

    def get_frequency_hz(self) -> float:
        return self.corrected_peak_frequency_bin * (self.sample_rate_hz / 2 / 1024 / 64)

        # ^ Convert back FFT bin to a frequency, given a 16 KHz sample
        # rate, 1024 useful bins and the multiplication by 64 made before
        # storing the information

    def get_amplitude_pcm(self) -> float:
        return sqrt(exp((self.peak_magnitude - 6144) / 1477.3) * (1 << 17) / 2) / 1024

        # ^ Not sure about this calculation but gives small enough numbers

    def get_seconds(self) -> float:
        return (self.fft_pass_number * 128) / self.sample_rate_hz

        # ^ Assume that new FFT bins are emitted every 128 samples, on a
        # standard 16 KHz sample rate basis.


class DecodedMessage:
    sample_rate_hz: int = None
    number_samples: int = None

    frequency_band_to_sound_peaks: Dict[FrequencyBand, List[FrequencyPeak]] = None

    @classmethod
    def decode_from_binary(cls, data: bytes):
        self = cls()

        buf = BytesIO(data)

        buf.seek(8)
        check_summable_data = buf.read()
        buf.seek(0)

        # Read and check the header

        header = RawSignatureHeader()
        buf.readinto(header)

        assert header.magic1 == 0xCAFE2580
        assert header.size_minus_header == len(data) - 48
        assert crc32(check_summable_data) & 0xFFFFFFFF == header.crc32
        assert header.magic2 == 0x94119C00

        self.sample_rate_hz = int(SampleRate(header.shifted_sample_rate_id >> 27).name.strip("_"))

        self.number_samples = int(
            header.number_samples_plus_divided_sample_rate - self.sample_rate_hz * 0.24
        )

        # Read the type-length-value sequence that follows the header

        # The first chunk is fixed and has no value, but instead just repeats
        # the length of the message size minus the header:
        assert int.from_bytes(buf.read(4), "little") == 0x40000000
        assert int.from_bytes(buf.read(4), "little") == len(data) - 48

        # Then, lists of frequency peaks for respective bands follow

        self.frequency_band_to_sound_peaks = {}

        while True:
            tlv_header = buf.read(8)
            if not tlv_header:
                break

            frequency_band_id = int.from_bytes(tlv_header[:4], "little")
            frequency_peaks_size = int.from_bytes(tlv_header[4:], "little")

            frequency_peaks_padding = -frequency_peaks_size % 4

            frequency_peaks_buf = BytesIO(buf.read(frequency_peaks_size))
            buf.read(frequency_peaks_padding)

            # Decode frequency peaks

            frequency_band = FrequencyBand(frequency_band_id - 0x60030040)

            fft_pass_number = 0

            self.frequency_band_to_sound_peaks[frequency_band] = []

            while True:
                raw_fft_pass: bytes = frequency_peaks_buf.read(1)
                if not raw_fft_pass:
                    break

                fft_pass_offset: int = raw_fft_pass[0]
                if fft_pass_offset == 0xFF:
                    fft_pass_number = int.from_bytes(frequency_peaks_buf.read(4), "little")
                    continue
                else:
                    fft_pass_number += fft_pass_offset

                peak_magnitude = int.from_bytes(frequency_peaks_buf.read(2), "little")
                corrected_peak_frequency_bin = int.from_bytes(frequency_peaks_buf.read(2), "little")

                self.frequency_band_to_sound_peaks[frequency_band].append(
                    FrequencyPeak(
                        fft_pass_number,
                        peak_magnitude,
                        corrected_peak_frequency_bin,
                        self.sample_rate_hz,
                    )
                )

        return self

    def encode_to_binary(self) -> bytes:
        header = RawSignatureHeader()

        header.magic1 = 0xCAFE2580
        header.magic2 = 0x94119C00
        header.shifted_sample_rate_id = int(getattr(SampleRate, "_%s" % self.sample_rate_hz)) << 27
        header.fixed_value = (15 << 19) + 0x40000
        header.number_samples_plus_divided_sample_rate = int(
            self.number_samples + self.sample_rate_hz * 0.24
        )

        contents_buf = BytesIO()

        for frequency_band, frequency_peaks in sorted(self.frequency_band_to_sound_peaks.items()):
            peaks_buf = BytesIO()

            fft_pass_number = 0

            # NOTE: Correctly filtering and sorting the peaks within the members
            # of "self.frequency_band_to_sound_peaks" is the responsibility of the
            # caller

            for frequency_peak in frequency_peaks:
                assert frequency_peak.fft_pass_number >= fft_pass_number

                if frequency_peak.fft_pass_number - fft_pass_number >= 255:
                    peaks_buf.write(b"\xff")
                    peaks_buf.write(frequency_peak.fft_pass_number.to_bytes(4, "little"))

                    fft_pass_number = frequency_peak.fft_pass_number

                peaks_buf.write(bytes([frequency_peak.fft_pass_number - fft_pass_number]))
                peaks_buf.write(frequency_peak.peak_magnitude.to_bytes(2, "little"))
                peaks_buf.write(frequency_peak.corrected_peak_frequency_bin.to_bytes(2, "little"))

                fft_pass_number = frequency_peak.fft_pass_number

            contents_buf.write((0x60030040 + int(frequency_band)).to_bytes(4, "little"))
            contents_buf.write(len(peaks_buf.getvalue()).to_bytes(4, "little"))
            contents_buf.write(peaks_buf.getvalue())
            contents_buf.write(b"\x00" * (-len(peaks_buf.getvalue()) % 4))

        # Below, write the full message as a binary stream

        header.size_minus_header = len(contents_buf.getvalue()) + 8

        buf = BytesIO()
        buf.write(header)  # We will rewrite it just after in order to include the final CRC-32

        buf.write((0x40000000).to_bytes(4, "little"))
        buf.write((len(contents_buf.getvalue()) + 8).to_bytes(4, "little"))

        buf.write(contents_buf.getvalue())

        buf.seek(8)
        header.crc32 = crc32(buf.read()) & 0xFFFFFFFF
        buf.seek(0)
        buf.write(header)

        return buf.getvalue()

    def encode_to_uri(self) -> str:
        return DATA_URI_PREFIX + b64encode(self.encode_to_binary()).decode("ascii")

HANNING_MATRIX = np.hanning(2050)[1:-1]  # Wipe trailing and leading zeroes


class RingBuffer(list):
    def __init__(self, buffer_size: int, default_value: Any = None):
        if default_value is not None:
            list.__init__(self, [copy(default_value) for _ in range(buffer_size)])
        else:
            list.__init__(self, [None] * buffer_size)

        self.position: int = 0
        self.buffer_size: int = buffer_size
        self.num_written: int = 0

    def append(self, value: Any):
        self[self.position] = value

        self.position += 1
        self.position %= self.buffer_size
        self.num_written += 1


class SignatureGenerator:
    def __init__(self):
        # Used when storing input that will be processed when requiring to
        # generate a signature:

        self.input_pending_processing: List[int] = []
        # Signed 16-bits, 16 KHz mono samples to be processed

        self.samples_processed: int = 0
        # Number of samples processed out of "self.input_pending_processing"

        # Used when processing input:

        self.ring_buffer_of_samples: RingBuffer[int] = RingBuffer(buffer_size=2048, default_value=0)

        self.fft_outputs: RingBuffer[List[float]] = RingBuffer(
            buffer_size=256, default_value=[0.0 * 1025]
        )
        # Lists of 1025 floats, premultiplied with a Hanning function before being
        # passed through FFT, computed from
        # the ring buffer every new 128 samples

        self.spread_fft_output: RingBuffer[List[float]] = RingBuffer(
            buffer_size=256, default_value=[0] * 1025
        )

        # How much data to send to Shazam at once?

        self.MAX_TIME_SECONDS = 3.1
        self.MAX_PEAKS = 255

        # The object that will hold information about the next fingerpring
        # to be produced

        self.next_signature = DecodedMessage()
        self.next_signature.sample_rate_hz = 16000
        self.next_signature.number_samples = 0
        self.next_signature.frequency_band_to_sound_peaks = {}

    """
        Add data to be generated a signature for, which will be
        processed when self.get_next_signature() is called. This
        function expects signed 16-bit 16 KHz mono PCM samples.
    """

    def feed_input(self, s16le_mono_samples: List[int]):
        self.input_pending_processing += s16le_mono_samples

    """
        Consume some of the samples fed to self.feed_input(), and return
        a Shazam signature (DecodedMessage object) to be sent to servers
        once "enough data has been gathered".

        Except if there are no more samples to be consumed, in this case
        we will return None.
    """

    def get_next_signature(self) -> Optional[DecodedMessage]:
        if len(self.input_pending_processing) - self.samples_processed < 128:
            return None
        while len(self.input_pending_processing) - self.samples_processed >= 128 and (
            self.next_signature.number_samples / self.next_signature.sample_rate_hz
            < self.MAX_TIME_SECONDS
            or sum(
                len(peaks) for peaks in self.next_signature.frequency_band_to_sound_peaks.values()
            )
            < self.MAX_PEAKS
        ):
            self.process_input(
                self.input_pending_processing[self.samples_processed : self.samples_processed + 128]
            )
            self.samples_processed += 128

        self.ring_buffer_of_samples: RingBuffer[int] = RingBuffer(buffer_size=2048, default_value=0)
        self.fft_outputs: RingBuffer[List[float]] = RingBuffer(
            buffer_size=256, default_value=[0.0 * 1025]
        )
        self.spread_fft_output: RingBuffer[List[float]] = RingBuffer(
            buffer_size=256, default_value=[0] * 1025
        )

        return self.next_signature

    def process_input(self, s16le_mono_samples: List[int]):
        self.next_signature.number_samples += len(s16le_mono_samples)
        for position_of_chunk in range(0, len(s16le_mono_samples), 128):
            self.do_fft(s16le_mono_samples[position_of_chunk : position_of_chunk + 128])
            self.do_peak_spreading_and_recognition()

    def do_fft(self, batch_of_128_s16le_mono_samples):
        type_ring = self.ring_buffer_of_samples.position + len(batch_of_128_s16le_mono_samples)
        self.ring_buffer_of_samples[self.ring_buffer_of_samples.position : type_ring] = (
            batch_of_128_s16le_mono_samples
        )
        self.ring_buffer_of_samples.position += len(batch_of_128_s16le_mono_samples)
        self.ring_buffer_of_samples.position %= 2048
        self.ring_buffer_of_samples.num_written += len(batch_of_128_s16le_mono_samples)

        excerpt_from_ring_buffer: list = (
            self.ring_buffer_of_samples[self.ring_buffer_of_samples.position :]
            + self.ring_buffer_of_samples[: self.ring_buffer_of_samples.position]
        )

        # The pre multiplication of the array is for applying a windowing function before the DFT
        # (slight rounded Hanning without zeros at edges)

        fft_results: np.array = np.fft.rfft(HANNING_MATRIX * excerpt_from_ring_buffer)

        fft_results = (fft_results.real**2 + fft_results.imag**2) / (1 << 17)
        fft_results = np.maximum(fft_results, 0.0000000001)

        self.fft_outputs.append(fft_results)

    def do_peak_spreading_and_recognition(self):
        self.do_peak_spreading()
        if self.spread_fft_output.num_written >= 46:
            self.do_peak_recognition()

    def do_peak_spreading(self):
        origin_last_fft: List[float] = self.fft_outputs[self.fft_outputs.position - 1]

        temporary_array_1 = np.tile(origin_last_fft, 3).reshape((3, -1))
        temporary_array_1[1] = np.roll(temporary_array_1[1], -1)
        temporary_array_1[2] = np.roll(temporary_array_1[2], -2)

        origin_last_fft_np = np.hstack([temporary_array_1.max(axis=0)[:-3], origin_last_fft[-3:]])

        i1, i2, i3 = [
            (self.spread_fft_output.position + former_fft_num) % self.spread_fft_output.buffer_size
            for former_fft_num in [-1, -3, -6]
        ]

        temporary_array_2 = np.vstack(
            [
                origin_last_fft_np,
                self.spread_fft_output[i1],
                self.spread_fft_output[i2],
                self.spread_fft_output[i3],
            ]
        )

        temporary_array_2[1] = np.max(temporary_array_2[:2, :], axis=0)
        temporary_array_2[2] = np.max(temporary_array_2[:3, :], axis=0)
        temporary_array_2[3] = np.max(temporary_array_2[:4, :], axis=0)

        self.spread_fft_output[i1] = temporary_array_2[1].tolist()
        self.spread_fft_output[i2] = temporary_array_2[2].tolist()
        self.spread_fft_output[i3] = temporary_array_2[3].tolist()

        self.spread_fft_output.append(list(origin_last_fft_np))

    def do_peak_recognition(self):
        fft_minus_46 = self.fft_outputs[
            (self.fft_outputs.position - 46) % self.fft_outputs.buffer_size
        ]
        fft_minus_49 = self.spread_fft_output[
            (self.spread_fft_output.position - 49) % self.spread_fft_output.buffer_size
        ]

        for bin_position in range(10, 1015):
            # Ensure that the bin is large enough to be a peak

            if fft_minus_46[bin_position] >= 1 / 64 and (
                fft_minus_46[bin_position] >= fft_minus_49[bin_position - 1]
            ):
                # Ensure that it is frequency-domain local minimum

                max_neighbor_in_fft_minus_49 = 0

                for neighbor_offset in [*range(-10, -3, 3), -3, 1, *range(2, 9, 3)]:
                    max_neighbor_in_fft_minus_49 = max(
                        fft_minus_49[bin_position + neighbor_offset],
                        max_neighbor_in_fft_minus_49,
                    )

                if fft_minus_46[bin_position] > max_neighbor_in_fft_minus_49:
                    # Ensure that it is a time-domain local minimum

                    max_neighbor_in_other_adjacent_ffts = max_neighbor_in_fft_minus_49

                    for other_offset in [
                        -53,
                        -45,
                        *range(165, 201, 7),
                        *range(214, 250, 7),
                    ]:
                        max_neighbor_in_other_adjacent_ffts = max(
                            self.spread_fft_output[
                                (self.spread_fft_output.position + other_offset)
                                % self.spread_fft_output.buffer_size
                            ][bin_position - 1],
                            max_neighbor_in_other_adjacent_ffts,
                        )

                    if fft_minus_46[bin_position] > max_neighbor_in_other_adjacent_ffts:
                        # This is a peak, store the peak

                        fft_number = self.spread_fft_output.num_written - 46

                        peak_magnitude = (
                            np.log(max(1 / 64, fft_minus_46[bin_position])) * 1477.3 + 6144
                        )
                        peak_magnitude_before = (
                            np.log(max(1 / 64, fft_minus_46[bin_position - 1])) * 1477.3 + 6144
                        )
                        peak_magnitude_after = (
                            np.log(max(1 / 64, fft_minus_46[bin_position + 1])) * 1477.3 + 6144
                        )

                        peak_variation_1 = (
                            peak_magnitude * 2 - peak_magnitude_before - peak_magnitude_after
                        )
                        peak_variation_2 = (
                            (peak_magnitude_after - peak_magnitude_before) * 32 / peak_variation_1
                        )

                        corrected_peak_frequency_bin = bin_position * 64 + peak_variation_2

                        assert peak_variation_1 > 0

                        frequency_hz = corrected_peak_frequency_bin * (16000 / 2 / 1024 / 64)

                        if 250 < frequency_hz < 520:
                            band = FrequencyBand.hz_250_520
                        elif 520 < frequency_hz < 1450:
                            band = FrequencyBand.hz_520_1450
                        elif 1450 < frequency_hz < 3500:
                            band = FrequencyBand.hz_1450_3500
                        elif 5500 < frequency_hz <= 5500:
                            band = FrequencyBand.hz_3500_5500
                        else:
                            continue

                        if band not in self.next_signature.frequency_band_to_sound_peaks:
                            self.next_signature.frequency_band_to_sound_peaks[band] = []

                        self.next_signature.frequency_band_to_sound_peaks[band].append(
                            FrequencyPeak(
                                fft_number,
                                int(peak_magnitude),
                                int(corrected_peak_frequency_bin),
                                16000,
                            )
                        )


# ---------------------------------------------------------------- the Shazam service (same requests as shazamio)
_TAG = ("https://amp.shazam.com/discovery/v5/{language}/{country}/{device}/-/tag/{u1}/{u2}?sync=true&webv3=true"
        "&sampling=true&connected=&shazamapiversion=v3&sharehub=true&hubv5minorversion=v5.1&hidelb=true&video=v3")
_SEARCH = ("https://www.shazam.com/services/search/v3/{language}/{country}/web/search"
           "?query={query}&numResults={limit}&offset={offset}&types=songs")
_AGENTS = [
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
]


def _samples_16k(path_or_bytes) -> np.ndarray:
    """A WAV file as 16 kHz mono 16-bit samples, the only format the signature takes."""
    src = BytesIO(path_or_bytes) if isinstance(path_or_bytes, (bytes, bytearray)) else open(path_or_bytes, "rb")
    with src, wave.open(src, "rb") as w:
        ch, width, rate, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    if width == 2:
        a = np.frombuffer(raw, dtype="<i2").astype(np.float64)
    elif width == 4:
        a = np.frombuffer(raw, dtype="<i4").astype(np.float64) / 65536.0
    elif width == 1:
        a = (np.frombuffer(raw, dtype=np.uint8).astype(np.float64) - 128) * 256
    else:
        raise ValueError("unsupported WAV sample width")
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    if rate != 16000 and len(a):
        m = int(round(len(a) * 16000 / rate))
        a = np.interp(np.linspace(0, len(a) - 1, m), np.arange(len(a)), a)
    return np.clip(np.round(a), -32768, 32767).astype(np.int16)


def signature_for(path_or_bytes) -> Optional[DecodedMessage]:
    samples = _samples_16k(path_or_bytes)
    gen = SignatureGenerator()
    gen.feed_input(samples.tolist())
    gen.MAX_TIME_SECONDS = 12
    seconds = len(samples) / 16000
    if seconds > 12 * 3:
        gen.samples_processed += 16000 * (int(seconds / 2) - 6)
    return gen.get_next_signature()


class Shazam:
    """The two calls Liner Notes makes: recognize a clip, and search by name."""

    def __init__(self, language: str = "en-US", endpoint_country: str = "US"):
        self.language = language
        self.country = endpoint_country

    def _headers(self):
        return {"X-Shazam-Platform": "IPHONE", "X-Shazam-AppVersion": "14.1.0", "Accept": "*/*",
                "Accept-Language": self.language, "Accept-Encoding": "gzip, deflate",
                "User-Agent": random.choice(_AGENTS)}

    async def _request(self, method, url, body=None):
        from aiohttp import ClientSession, ClientTimeout
        async with ClientSession(timeout=ClientTimeout(total=15)) as s:
            async with s.request(method, url, json=body, headers=self._headers()) as r:
                try:
                    return json.loads(await r.text())
                except Exception:
                    return {}

    async def recognize(self, data) -> Dict[str, Any]:
        sig = await asyncio.get_running_loop().run_in_executor(None, signature_for, data)
        if sig is None:
            return {"matches": []}
        body = {"timezone": "America/Los_Angeles",
                "signature": {"uri": sig.encode_to_uri(), "samplems": int(sig.number_samples / sig.sample_rate_hz * 1000)},
                "timestamp": int(time.time() * 1000), "context": {}, "geolocation": {}}
        url = _TAG.format(language=self.language, country=self.country, device=random.choice(["iphone", "android", "web"]),
                          u1=str(uuid.uuid4()).upper(), u2=str(uuid.uuid4()).upper())
        return await self._request("POST", url, body)

    recognize_song = recognize

    async def search_track(self, query: str, limit: int = 10, offset: int = 0) -> Dict[str, Any]:
        from urllib.parse import quote
        return await self._request("GET", _SEARCH.format(language=self.language, country=self.country,
                                                         query=quote(query), limit=limit, offset=offset))
