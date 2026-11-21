//! Timestamp ownership for the private, FFmpeg-generated H.264/AAC transport.
//! One offset per producer preserves A/V timing; one clock per bridge survives
//! source, fallback and playlist changes. Never independently reset audio/video.
use anyhow::{bail, ensure, Context, Result};

pub(super) const PACKET: usize = 188;
const WRAP: i64 = 1 << 33;
const VIDEO: usize = 256;
const AUDIO: usize = 257;

#[derive(Default)]
pub(super) struct Epoch {
    offset: Option<i64>,
    reference: i64,
    first_pts: [Option<i64>; 2],
    end_pts: [Option<i64>; 2],
}

impl Epoch {
    /// Media duration actually forwarded for both tracks, independent of stalls
    /// in resolver work or how far ahead FFmpeg has encoded into its socket.
    pub(super) fn elapsed(&self) -> f64 {
        let duration = |i: usize| -> Option<i64> { Some(self.end_pts[i]? - self.first_pts[i]?) };
        duration(0)
            .zip(duration(1))
            .map_or(0.0, |(v, a)| v.min(a).max(0) as f64 / 90_000.0)
    }

    pub(super) fn started(&self) -> bool {
        self.offset.is_some()
    }
}

pub(super) struct Clock {
    next_pts: [i64; 2],
    last_dts: [Option<i64>; 2],
    last_pcr: Option<i64>,
    continuity: Vec<Option<u8>>,
    video_step: i64,
}
impl Clock {
    pub(super) fn new(fps: i64) -> Self {
        Self {
            // Leave room for encoder delay/PCR preceding the first presentation.
            // The bridge removes this initial offset once, with start_at_zero.
            next_pts: [900_000; 2],
            last_dts: [None; 2],
            last_pcr: None,
            continuity: vec![None; 8192],
            video_step: (90_000 + fps.max(1) - 1) / fps.max(1),
        }
    }
    /// Wait for both first PES headers in the actual socket bytes, not merely
    /// stderr progress. Headers/packets stay buffered until a common offset exists.
    pub(super) fn begin(&self, epoch: &mut Epoch, data: &[u8]) -> Result<Option<i64>> {
        let Some((video, audio, pcr)) = first_timestamps(data)? else {
            return Ok(None);
        };
        epoch.reference = video.1;
        let mut offset = i64::MIN;
        for (i, (pts, dts)) in [video, audio].into_iter().enumerate() {
            let pts = unwrap(pts, epoch.reference);
            let dts = unwrap(dts, epoch.reference);
            offset = offset.max(self.next_pts[i] - pts);
            if let Some(last) = self.last_dts[i] {
                offset = offset.max(last + 1 - dts);
            }
        }
        if let (Some(last), Some(first)) = (self.last_pcr, pcr) {
            offset = offset.max(last + 1 - unwrap(first, epoch.reference));
        }
        epoch.offset = Some(offset);
        Ok(Some(offset))
    }
    /// Compare incoming presentation times with the outgoing track ends before
    /// forwarding anything, to distinguish a feeder gap from downstream repeats.
    pub(super) fn handoff_gaps(&self, epoch: &Epoch, data: &[u8]) -> Result<[f64; 2]> {
        let (video, audio, _) = first_timestamps(data)?.context("Handoff timestamps missing")?;
        let offset = epoch.offset.context("Handoff offset missing")?;
        let first = [video, audio].map(|(pts, _)| unwrap(pts, epoch.reference) + offset);
        Ok(std::array::from_fn(|track| {
            (first[track] - self.next_pts[track]) as f64 / 90_000.0
        }))
    }
    pub(super) fn rewrite(&mut self, epoch: &mut Epoch, data: &mut [u8]) -> Result<()> {
        let offset = epoch
            .offset
            .context("Transport producer has no clock offset")?;
        for packet in data.chunks_exact_mut(PACKET) {
            let info = Info::parse(packet)?;
            if let Some(track) = info.track() {
                if let Some((pts, dts)) = info.timestamps(packet)? {
                    let pts = unwrap(pts, epoch.reference);
                    let dts = unwrap(dts, epoch.reference);
                    epoch.reference = epoch.reference.max(dts);
                    let shifted_pts = pts + offset;
                    let shifted_dts = dts + offset;
                    // AAC is one 1024-sample frame per PES (pes_payload_size=0).
                    let step = if track == 0 {
                        self.video_step
                    } else {
                        (1024 * 90_000 + 44_099) / 44_100
                    };
                    epoch.first_pts[track].get_or_insert(pts);
                    epoch.end_pts[track] =
                        Some(epoch.end_pts[track].map_or(pts + step, |end| end.max(pts + step)));
                    self.next_pts[track] = self.next_pts[track].max(shifted_pts + step);
                    self.last_dts[track] =
                        Some(self.last_dts[track].map_or(shifted_dts, |v| v.max(shifted_dts)));
                    let at = info.payload.unwrap() + 9;
                    write_pts(&mut packet[at..at + 5], shifted_pts);
                    if packet[info.payload.unwrap() + 7] & 0xc0 == 0xc0 {
                        write_pts(&mut packet[at + 5..at + 10], shifted_dts);
                    }
                }
            }
            for at in [info.pcr, info.opcr].into_iter().flatten() {
                let shifted = unwrap(read_pcr(&packet[at..at + 6]), epoch.reference) + offset;
                write_pcr(&mut packet[at..at + 6], shifted);
                if Some(at) == info.pcr {
                    self.last_pcr = Some(self.last_pcr.map_or(shifted, |v| v.max(shifted)));
                }
            }
            // Counters belong to the combined transport, not a child muxer.
            // Adaptation-only packets repeat the last payload counter.
            let last = &mut self.continuity[info.pid];
            let cc = match (*last, info.payload.is_some()) {
                (Some(cc), true) => (cc + 1) & 15,
                (Some(cc), false) => cc,
                (None, _) => 0,
            };
            packet[3] = (packet[3] & 0xf0) | cc;
            *last = Some(cc);
            if info.has_adaptation_flags {
                // Child initial_discontinuity no longer describes this program.
                packet[5] &= !0x80;
            }
        }
        Ok(())
    }
}

/// Both tracks must be present in the buffered transport before filler stops.
pub(super) fn ready(data: &[u8]) -> Result<bool> {
    Ok(first_timestamps(data)?.is_some())
}
type Timestamps = (i64, i64);
fn first_timestamps(data: &[u8]) -> Result<Option<(Timestamps, Timestamps, Option<i64>)>> {
    let mut first = [None; 2];
    let mut pcr = None;
    for packet in data.chunks_exact(PACKET) {
        let info = Info::parse(packet)?;
        if pcr.is_none() {
            pcr = info.pcr.map(|at| read_pcr(&packet[at..at + 6]));
        }
        if let Some(track) = info.track() {
            if first[track].is_none() {
                first[track] = info.timestamps(packet)?;
            }
        }
        if let [Some(video), Some(audio)] = first {
            return Ok(Some((video, audio, pcr)));
        }
    }
    Ok(None)
}

struct Info {
    pid: usize,
    payload: Option<usize>,
    start: bool,
    has_adaptation_flags: bool,
    pcr: Option<usize>,
    opcr: Option<usize>,
}
impl Info {
    fn parse(p: &[u8]) -> Result<Self> {
        ensure!(
            p.len() == PACKET && p[0] == 0x47,
            "Invalid private transport packet"
        );
        ensure!(
            p[1] & 0x80 == 0 && p[3] & 0xc0 == 0,
            "Damaged/encrypted private transport packet"
        );
        let control = (p[3] >> 4) & 3;
        ensure!(control != 0, "Invalid private transport adaptation control");
        let mut at = 4;
        let mut info = Self {
            pid: (usize::from(p[1] & 0x1f) << 8) | usize::from(p[2]),
            payload: None,
            start: p[1] & 0x40 != 0,
            has_adaptation_flags: false,
            pcr: None,
            opcr: None,
        };
        if control & 2 != 0 {
            let end = 5 + usize::from(p[4]);
            ensure!(end <= PACKET, "Invalid private transport adaptation length");
            if p[4] > 0 {
                info.has_adaptation_flags = true;
                let mut cursor = 6;
                for (flag, field) in [(0x10, &mut info.pcr), (0x08, &mut info.opcr)] {
                    if p[5] & flag != 0 {
                        ensure!(cursor + 6 <= end, "Truncated private transport clock");
                        *field = Some(cursor);
                        cursor += 6;
                    }
                }
            }
            at = end;
        }
        if control & 1 != 0 && at < PACKET {
            info.payload = Some(at);
        }
        Ok(info)
    }
    fn track(&self) -> Option<usize> {
        match self.pid {
            VIDEO => Some(0),
            AUDIO => Some(1),
            _ => None,
        }
    }
    fn timestamps(&self, p: &[u8]) -> Result<Option<(i64, i64)>> {
        if !self.start || self.track().is_none() {
            return Ok(None);
        }
        let Some(at) = self.payload else {
            return Ok(None);
        };
        // Our FFmpeg MPEG-TS muxers always write the complete PES timestamp
        // header into the first TS packet. Reject a changed/private format rather
        // than silently passing an unshifted timestamp to the output.
        ensure!(
            at + 9 <= PACKET && p[at..at + 3] == [0, 0, 1],
            "Invalid private PES header"
        );
        let flags = p[at + 7] >> 6;
        if flags == 0 {
            return Ok(None);
        }
        ensure!(
            flags == 2 || flags == 3,
            "Invalid private PES timestamp flags"
        );
        let bytes = if flags == 3 { 10 } else { 5 };
        ensure!(
            usize::from(p[at + 8]) >= bytes && at + 9 + bytes <= PACKET,
            "Truncated private PES timestamps"
        );
        let pts = read_pts(&p[at + 9..at + 14])?;
        let dts = if flags == 3 {
            read_pts(&p[at + 14..at + 19])?
        } else {
            pts
        };
        Ok(Some((pts, dts)))
    }
}
fn unwrap(raw: i64, reference: i64) -> i64 {
    reference + (raw - reference + WRAP / 2).rem_euclid(WRAP) - WRAP / 2
}
fn read_pts(p: &[u8]) -> Result<i64> {
    if p[0] & 1 == 0 || p[2] & 1 == 0 || p[4] & 1 == 0 {
        bail!("Invalid private PES timestamp markers");
    }
    Ok((i64::from((p[0] >> 1) & 7) << 30)
        | (i64::from(p[1]) << 22)
        | (i64::from(p[2] >> 1) << 15)
        | (i64::from(p[3]) << 7)
        | i64::from(p[4] >> 1))
}
fn write_pts(p: &mut [u8], timestamp: i64) {
    let t = timestamp.rem_euclid(WRAP) as u64;
    p[0] = (p[0] & 0xf0) | (((t >> 30) as u8 & 7) << 1) | 1;
    p[1] = (t >> 22) as u8;
    p[2] = ((t >> 15) as u8 & 0x7f) << 1 | 1;
    p[3] = (t >> 7) as u8;
    p[4] = (t as u8 & 0x7f) << 1 | 1;
}
fn read_pcr(p: &[u8]) -> i64 {
    (i64::from(p[0]) << 25)
        | (i64::from(p[1]) << 17)
        | (i64::from(p[2]) << 9)
        | (i64::from(p[3]) << 1)
        | i64::from(p[4] >> 7)
}
fn write_pcr(p: &mut [u8], timestamp: i64) {
    let t = timestamp.rem_euclid(WRAP) as u64;
    p[0] = (t >> 25) as u8;
    p[1] = (t >> 17) as u8;
    p[2] = (t >> 9) as u8;
    p[3] = (t >> 1) as u8;
    // Preserve reserved bits and the full 27 MHz extension.
    p[4] = (p[4] & 0x7f) | ((t as u8 & 1) << 7);
}
