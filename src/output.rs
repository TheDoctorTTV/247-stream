//! Local HLS output for streaming software (OBS, Streamlabs, etc.) and the
//! shared HLS directory helpers also used by the debug preview.
use crate::runtime::App;
use anyhow::{Context, Result};
use std::{
    fs,
    net::{IpAddr, Ipv4Addr, Ipv6Addr},
    path::{Path, PathBuf},
};

/// Destination base URL that selects local output instead of an RTMP platform.
pub const BASE: &str = "local://output";
/// Dashboard route the playlist is served from.
pub const PLAYLIST_PATH: &str = "/api/output/index.m3u8";

pub fn dir(app: &App) -> PathBuf {
    app.dir.join("local-output")
}

pub fn is_segment(name: &str) -> bool {
    name.strip_prefix("segment-")
        .and_then(|v| v.strip_suffix(".ts"))
        .is_some_and(|v| v.len() >= 6 && v.len() <= 20 && v.bytes().all(|b| b.is_ascii_digit()))
}

/// Create the HLS directory and remove playlists and segments from an earlier run.
pub fn prepare_hls(dir: &Path, label: &str) -> Result<()> {
    if let Ok(metadata) = fs::symlink_metadata(dir) {
        anyhow::ensure!(
            metadata.is_dir() && !metadata.file_type().is_symlink(),
            "{label} directory must be a real directory"
        );
    }
    fs::create_dir_all(dir)?;
    for item in fs::read_dir(dir)? {
        let item = item?;
        let name = item.file_name().to_string_lossy().into_owned();
        let plain = name.strip_suffix(".tmp").unwrap_or(&name);
        if (plain == "index.m3u8" || is_segment(plain)) && item.file_type()?.is_file() {
            fs::remove_file(item.path())?;
        }
    }
    Ok(())
}

/// Read a playlist or segment from an HLS directory; `None` when it does not exist.
pub fn hls_asset(dir: &Path, name: &str) -> Result<Option<(&'static str, Vec<u8>)>> {
    let kind = if name == "index.m3u8" {
        "application/vnd.apple.mpegurl"
    } else if is_segment(name) {
        "video/mp2t"
    } else {
        return Ok(None);
    };
    let path = dir.join(name);
    let metadata = match fs::symlink_metadata(&path) {
        Ok(meta) => meta,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(e.into()),
    };
    anyhow::ensure!(
        metadata.is_file() && metadata.len() <= 64 * 1024 * 1024,
        "Invalid HLS asset"
    );
    Ok(Some((kind, fs::read(path).context("Read HLS asset")?)))
}

/// A private address of this machine that streaming software can connect to.
pub struct Address {
    pub interface: String,
    pub ip: IpAddr,
}
impl Address {
    pub fn kind(&self) -> &'static str {
        match self.ip {
            IpAddr::V4(ip) if ip.is_loopback() => "This machine",
            IpAddr::V6(ip) if ip.is_loopback() => "This machine",
            IpAddr::V4(ip) if ip.octets()[0] == 100 => "Tailscale or overlay network",
            IpAddr::V4(_) => "Local network",
            IpAddr::V6(_) => "Private IPv6",
        }
    }
    pub fn url(&self, port: i64) -> String {
        match self.ip {
            IpAddr::V4(ip) => format!("http://{ip}:{port}{PLAYLIST_PATH}"),
            IpAddr::V6(ip) => format!("http://[{ip}]:{port}{PLAYLIST_PATH}"),
        }
    }
}

/// Private addresses on interfaces that are up, network addresses first and
/// loopback last. IPv6 link-local is skipped because its URLs need a zone index.
pub fn addresses() -> Vec<Address> {
    let mut out: Vec<Address> = Vec::new();
    // SAFETY: getifaddrs returns a linked list owned by libc that stays valid
    // until freeifaddrs; every pointer is checked before it is dereferenced.
    unsafe {
        let mut head: *mut libc::ifaddrs = std::ptr::null_mut();
        if libc::getifaddrs(&mut head) != 0 {
            return out;
        }
        let mut cursor = head;
        while !cursor.is_null() {
            let entry = &*cursor;
            cursor = entry.ifa_next;
            if entry.ifa_addr.is_null()
                || entry.ifa_name.is_null()
                || entry.ifa_flags & libc::IFF_UP as libc::c_uint == 0
            {
                continue;
            }
            let ip = match i32::from((*entry.ifa_addr).sa_family) {
                libc::AF_INET => {
                    let addr = &*(entry.ifa_addr as *const libc::sockaddr_in);
                    IpAddr::V4(Ipv4Addr::from(u32::from_be(addr.sin_addr.s_addr)))
                }
                libc::AF_INET6 => {
                    let addr = &*(entry.ifa_addr as *const libc::sockaddr_in6);
                    IpAddr::V6(Ipv6Addr::from(addr.sin6_addr.s6_addr))
                }
                _ => continue,
            };
            let link_local_v6 =
                matches!(ip, IpAddr::V6(v6) if (v6.segments()[0] & 0xffc0) == 0xfe80);
            if link_local_v6 || !client_allowed(ip) || out.iter().any(|a| a.ip == ip) {
                continue;
            }
            let interface = std::ffi::CStr::from_ptr(entry.ifa_name)
                .to_string_lossy()
                .into_owned();
            out.push(Address { interface, ip });
        }
        libc::freeifaddrs(head);
    }
    // Stable sort: keep interface order, IPv4 before IPv6, loopback last.
    out.sort_by_key(|a| (a.ip.is_loopback(), a.ip.is_ipv6()));
    out
}

/// Local output is only served to loopback, LAN and private overlay networks
/// (Tailscale, Headscale, NetBird, ZeroTier, WireGuard). Requests arriving
/// directly from a public address, such as through a port forward, are refused.
/// Reverse proxies connect from a private address and are allowed by design.
pub fn client_allowed(addr: IpAddr) -> bool {
    match addr {
        IpAddr::V4(ip) => private_v4(ip),
        IpAddr::V6(ip) => match ip.to_ipv4_mapped() {
            Some(v4) => private_v4(v4),
            None => private_v6(ip),
        },
    }
}

fn private_v4(ip: Ipv4Addr) -> bool {
    let [a, b, ..] = ip.octets();
    ip.is_loopback()
        || ip.is_private()
        || ip.is_link_local()
        // 100.64.0.0/10 shared address space (Tailscale, Headscale, NetBird).
        || (a == 100 && (b & 0xc0) == 64)
}

fn private_v6(ip: Ipv6Addr) -> bool {
    let first = ip.segments()[0];
    ip.is_loopback()
        // fc00::/7 unique local addresses (includes Tailscale's fd7a:115c:a1e0::/48).
        || (first & 0xfe00) == 0xfc00
        // fe80::/10 link-local.
        || (first & 0xffc0) == 0xfe80
}

#[cfg(test)]
mod tests {
    use super::client_allowed;

    #[test]
    fn private_networks_are_allowed() {
        for addr in [
            "127.0.0.1",
            "10.1.2.3",
            "172.16.0.5",
            "172.31.255.255",
            "192.168.1.20",
            "169.254.10.10",
            "100.64.0.1",
            "100.127.255.254",
            "::1",
            "fd7a:115c:a1e0::1",
            "fc00::1",
            "fe80::1",
            "::ffff:192.168.1.5",
        ] {
            assert!(client_allowed(addr.parse().unwrap()), "{addr}");
        }
    }

    #[test]
    fn public_addresses_are_refused() {
        for addr in [
            "8.8.8.8",
            "172.32.0.1",
            "100.128.0.1",
            "100.63.255.255",
            "2001:4860:4860::8888",
            "::ffff:8.8.8.8",
        ] {
            assert!(!client_allowed(addr.parse().unwrap()), "{addr}");
        }
    }
}
