# Personal KIE video MCP

Endpoint: `https://<existing-app-domain>/api/kie-mcp/` (trailing slash).
The existing backend serves the MCP Streamable HTTP transport through the existing `/api` reverse proxy.

The server reads `KIE_API_KEY` using the existing backend `.env` settings loader.
The controlled release provisions a separate random `KIE_MCP_TOKEN` in that server `.env`
via `deploy/configure-kie-mcp.py`, before service restart. It preserves existing tokens
and all other settings and uses the existing private atomic environment patch helper.
No token means all MCP requests return 401. Browser Origin requests are rejected.
Never commit or print these values. The endpoint is a personal account capability;
it bypasses application users and application credit billing, and spends the server's KIE balance.

Tools:

- `list_video_models`: eleven supported model IDs, selection guidance, per-model durations, resolutions, image modes and audio behavior.
- `create_video`: submit one paid job, returning immediately with its task ID.
- `get_video_task`: query progress and retrieve HTTPS output URLs. Poll no faster than every 10 seconds.

## Model selection and input mapping

Official model contracts checked 2026-11-20. Four families, eleven model IDs:

| Model | Implemented inputs | Duration | Output resolution | Audio |
| --- | --- | --- | --- | --- |
| `kling-2.6/text-to-video` | Text | 5/10 s | Provider default | Optional |
| `kling-2.6/image-to-video` | Text + one image | 5/10 s | Provider default | Optional |
| `kling-3.0/video` | Text, optional first/last frame; single-shot only | 3–15 s | 720p/1080p/4K | Optional |
| `bytedance/seedance-2` | Text, first/last frames OR up to 9 reference images | 4–15 s | 480p/720p/1080p/4K | Optional |
| `bytedance/seedance-2-fast` | Same image modes as Seedance 2.0 | 4–15 s | 480p/720p | Optional |
| `wan/3-0-video` | Text, first/last frames OR up to 10 images/5 videos/5 audios | 2–30 s | 480p/720p/1080p | Optional |
| `wan/3-0-video-prime` | Wan high-speed variant; same input modes | 2–30 s | 480p/720p/1080p | Optional |
| `wan/2-6-flash-image-to-video` | Text + one image; single-shot mode | 5/10/15 s | 720p/1080p | Optional |
| `bytedance/seedance-2-5` | Text, first/last frames OR up to 30 images/10 videos/10 audios | 4–30 s | 480p/720p/1080p/4K | Optional |
| `pixverse-v6-max/text-to-video` | Text | 1–15 s | 360p/540p/720p/1080p | Optional |
| `pixverse-v6-max/image-to-video` | Text + one first-frame image | 1–15 s | 360p/540p/720p/1080p | Optional |

GPT should first call `list_video_models`, honor an explicitly requested model, then
choose according to input type, requested length/resolution and audio. These are
capability-based choices, not benchmarked quality or price rankings. No silent model
fallback and no automatic submission of multiple paid candidates.

Omit `duration_seconds` for 5 seconds on every supported model. Omit
`resolution` for tool defaults (Kling 3.0/Seedance/PixVerse: 720p; Wan: 1080p). Explicit
unsupported durations, resolutions, image modes or ratios fail before reservation or
provider submission. Kling 2.6 does not expose a resolution parameter here.

All image inputs currently require public HTTPS URLs. `image_url` is the first frame;
`end_image_url` requires it and is supported only by Kling 3.0, Seedance and Wan 3.0/Prime. For Seedance and Wan 3.0/Prime,
`reference_image_urls`, `reference_video_urls`, and `reference_audio_urls` cannot be mixed
with first/last frames. Seedance 2.0/Fast supports up to 9 images/3 videos/3 audios.
Limits for Seedance 2.5 and Wan 3.0 are listed above. Reference durations/total durations,
media sizes and formats are validated by the provider; GPT must consult the linked
model docs before selecting material. In particular Wan 3.0 reference-video duration
plus output duration must not exceed 30 seconds. Automatic duration (-1) is not exposed. Provider image size and
format limits still apply (Kling 2.6: JPEG/PNG, 10 MB;
Seedance 2.0: JPEG/PNG/WEBP/BMP/TIFF/GIF, under 30 MB; Seedance 2.5 reference images: JPEG/PNG/WEBP, under 30 MB; PixVerse: JPEG/PNG/WEBP, at most 20 MB). Media bytes are not fetched or
validated by this tool. Kling/PixVerse image-to-video outputs follow the first image; PixVerse image-to-video
requests never send `aspect_ratio`. Other models expose their allowed aspect ratios. Seedance 4K uses HEVC and needs a compatible player.

Native multi-shot/element control for Kling 3.0, Wan Flash multi-shot mode, Seedance 2.5
edit/extend mode selectors and Wan 3.0 file/link inputs are not exposed in this version. This version does not
upload local images or archive output videos; download completed output promptly.

Sound defaults to `sound=false` for **all eleven model IDs**. The model catalog, server
instructions, tool description and parameter schema tell GPT to leave audio off unless
the user requests it; the server never relies on the provider default. For requested narration, dialogue, music, effects or
ambient sound, GPT should pass `sound=true` and describe the sound and exact lines in
`prompt`. Mapping to upstream parameters:

| Family | Explicit audio flag sent upstream |
| --- | --- |
| Kling | `sound` |
| Seedance (including 2.5) | `generate_audio` |
| Wan 3.0 / Prime / 2.6 Flash | `audio` |
| PixVerse V6 Max | `generate_audio_switch` |

The server sends `false` by default even when the provider defaults to `true`. It sends
`true` only when the caller explicitly sets `sound=true`. Audio can increase charges;
consult current provider pricing. Reference audio does not automatically enable output
audio. The unified `resolution` parameter maps to `quality` on PixVerse and upper-case
`resolution` (e.g. `1080P`) on Wan 3.0; incorrect vendor-specific flags are not forwarded.

A unique caller-supplied `request_key` identifies each intended creation. Redis reserves
it before sending the POST and retains the parameter fingerprint and returned task ID.
A repeated key with the same parameters returns the existing ID. Changed parameters
are rejected. An ambiguous outcome remains blocked rather than spending again; inspect
KIE account task history to reconcile it. Creation is never automatically retried.
These small records have no expiry; Redis persistence is required to preserve this
protection across Redis loss/restarts. Do not clear them to retry an uncertain job.

Deploy through `deploy/release.sh` only. Do not replace another task's pending production
candidate. Validate authentication, MCP initialization and tools/list after release;
these checks must not submit a paid video.

Codex configuration in `~/.codex/config.toml`:

```toml
[mcp_servers.kie_video]
url = "https://<existing-app-domain>/api/kie-mcp/"
bearer_token_env_var = "KIE_MCP_TOKEN"
```

The desktop application must inherit this environment variable; setting it only in
an unrelated terminal is insufficient. Alternatively use an authenticated local stdio
bridge that reads a mode-0600 token file, keeping the credential out of tracked config.
Configure the real endpoint only after deployment and verify with an MCP client.

Protocol references (checked 2026-11-20):

- https://docs.kie.ai/market/kling/text-to-video
- https://docs.kie.ai/market/kling/image-to-video
- https://docs.kie.ai/market/kling/kling-3-0
- https://docs.kie.ai/market/bytedance/seedance-2
- https://docs.kie.ai/market/bytedance/seedance-2-fast
- https://docs.kie.ai/market/common/get-task-detail
- https://developers.openai.com/codex/mcp

Additional model protocol references (checked 2026-11-20):

- https://docs.kie.ai/market/wan/3-0-video
- https://docs.kie.ai/market/wan/3-0-video-prime
- https://docs.kie.ai/market/wan/2-6-flash-image-to-video
- https://docs.kie.ai/market/bytedance/seedance-2-5
- https://docs.kie.ai/market/pixverse/v6-max-text-to-video
- https://docs.kie.ai/market/pixverse/v6-max-image-to-video

## Installed desktop connection

For Codex versions without an HTTP credential helper, use `scripts/kie-mcp-bridge.py`
as the stdio command. It forwards the stateless JSON-RPC messages to the HTTPS MCP
endpoint and reads a private 0600 credential file. No credential is embedded in the
script, process arguments, or tracked/global Codex configuration. Redirects are denied
and uncertain requests are never retried. The bridge forwards MCP results/errors
unchanged and carries the negotiated protocol version on subsequent HTTP calls.

Install a copy in `~/.codex/mcp/kie-video/bridge.py`, store the independent MCP token
in `~/.codex/mcp/kie-video/token` (directory 0700, file 0600), then register:

```sh
codex mcp add kie_video -- /usr/bin/python3 /Users/wucong/.codex/mcp/kie-video/bridge.py https://everydayai.com.cn/api/kie-mcp/ /Users/wucong/.codex/mcp/kie-video/token
```

After registration verify initialization, tools/list and list_video_models through
the actual bridge; do not submit a paid generation during deployment checks.
