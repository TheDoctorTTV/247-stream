# Stream247 YouTube Connect

A Chrome extension that pairs your signed-in browser with one Stream247 server and sends YouTube cookies
directly to it. Automatic sync is off by default.

## Install

Extract the dashboard's **Download extension** archive, or use this source folder.
On Windows, [7-Zip](https://www.7-zip.org/) or
[WinRAR](https://www.win-rar.com/open-zip-file.html?L=0) can extract the `.tar.gz` file.
If extraction produces a `.tar` file, extract that too.

In **Chrome 121+**, open `chrome://extensions`, enable Developer mode, choose
**Load unpacked**, and select the folder containing `manifest.json`. Click the
extension's toolbar icon to open its connection page in a new tab.

## Pair

1. Sign in at YouTube in the browser profile where the extension is installed.
2. Open Stream247 and choose **Settings → YouTube authentication → Connect browser**.
3. Open the extension and enter the displayed server address and pairing code.
4. Confirm the destination permission and choose **Pair and sync**. Codes expire
   after ten minutes and can be used once.

Use **Sync now** for manual updates. Enable **Automatic sync** only if you want
future cookie changes sent automatically. The extension waits briefly after cookie
changes and checks once a minute for missed changes or failed transfers while the
browser is running. Unchanged cookies are not uploaded again. Signing out clears
saved server cookies on the next enabled sync. The default cookie store is used;
Private browsing is not supported by this version.

**Disconnect** revokes the pairing and removes saved server cookies. Disconnecting
from the dashboard also stops further uploads from that browser. If the server is
unreachable, disconnect there when it becomes available; uninstalling the extension
alone does not erase cookies already saved on the server. A new pairing preserves
the saved cookies until its first successful sync replaces them.

## HTTPS for another device

Other devices require an HTTPS server address and a certificate trusted by the
browser. HTTP is accepted only for `localhost` and `127.0.0.1` on the same device.
Stream247's HTTP backend accepts pairing/sync requests only over loopback, so run
the HTTPS reverse proxy on the Stream247 host and forward to `127.0.0.1:7788`.

For example, with Caddy on the server (replace the IP with your server's LAN IP):

```caddyfile
https://192.168.1.50 {
    tls internal
    reverse_proxy 127.0.0.1:7788
}
```

Trust Caddy's local CA on each client, then open `https://192.168.1.50` to pair.
A publicly trusted certificate for your own hostname also works. Keep dashboard
access restricted to trusted users; pairing does not add dashboard login protection.
See [Caddy TLS](https://caddyserver.com/docs/caddyfile/directives/tls) and
[reverse proxy](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy).

## Privacy and limits

Only YouTube cookies are read; unrelated Google and other sites' cookies are not
collected. They go directly to the paired server, with no third-party relay. The
extension keeps the server pairing credential and a cookie fingerprint, not a
stored cookie export. Cookie contents and pairing credentials are never logged.

Cookies may help yt-dlp access restricted or private content your account can
access. They do not guarantee playback, bypass account permissions, or provide PO
tokens. Server-side cookies and pairing credentials are stored unencrypted with
owner-only permissions. Use a trusted server and revoke account sessions if the
cookies are exposed; disconnecting alone does not revoke the Google session.
