# Capabilities

This page lists what SphereLoom can do **today**. Every capability is in exactly one of
three states:

- **Available**: it works now.
- **Not yet available**: SphereLoom does not provide it yet.
- **Not supported by the vendor API**: the camera's API for this connection does not offer
  it, so SphereLoom cannot provide it over this connection.

New capabilities are announced in the [changelog](../CHANGELOG.md) when they ship.

## Wi-Fi (Open Spherical Camera API)

SphereLoom is designed to reach the camera over its Wi-Fi access point. Camera control over
Wi-Fi is not available yet; the table shows the state of each capability on this connection.

| Capability | Identifier | Status | Notes |
| --- | --- | --- | --- |
| Connect to the camera | `camera.connect` | Not yet available | Requires joining the camera's Wi-Fi access point |
| Read battery, storage and capture state | `camera.status` | Not yet available | |
| Read capture settings | `options.read` | Not yet available | Limited to what the camera's Wi-Fi API exposes |
| Change capture settings | `options.write` | Not yet available | Limited to what the camera's Wi-Fi API exposes |
| Take a 360° photo | `photo.capture` | Not yet available | Photos are stitched in the camera |
| Record video | `video.record` | Not yet available | Video is recorded **unstitched** |
| Browse the gallery | `files.list` | Not yet available | |
| Download media to the workspace | `files.download` | Not yet available | Runs as a cancellable background job |
| Delete media from the camera | `files.delete` | Not yet available | Disabled by default; needs a confirmation token |
| Exposure and white balance control | `exposure.control` | Not supported by the vendor API | The camera's Wi-Fi API does not expose exposure parameters |
| Live preview or streaming | `preview.live` | Not supported by the vendor API | The camera's Wi-Fi API does not provide a live stream |
| Format the storage card | `storage.format` | Not yet available | |
| Stitch video into a 360° video | `media.stitch.video` | Not yet available | Needs the Insta360 Media SDK, which you obtain yourself, and an NVIDIA GPU |
| Export processed media | `media.export` | Not yet available | Needs the Insta360 Media SDK, which you obtain yourself, and an NVIDIA GPU |

## Not offered by Insta360

SphereLoom does not promise features Insta360 does not document. Trimming, cutting or
merging clips, timeline editing and DNG to JPG conversion are not documented by the vendor,
so SphereLoom does not provide them, and it will not imitate them by automating Insta360's
desktop application.

> Insta360 is a trademark of Arashi Vision Inc. SphereLoom is an independent, unaffiliated
> project and is neither endorsed by, sponsored by, nor associated with Arashi Vision Inc.
