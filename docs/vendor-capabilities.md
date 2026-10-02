# Vendor capability matrix

- Status: research record, verified 2026-10-02
- Purpose: the single source of truth for what each vendor surface can and cannot do.
  The capability registry in the code is derived from this document; the README matrix is
  generated from the registry.

> Insta360 is a trademark of Arashi Vision Inc. SphereLoom is an independent, unaffiliated
> project and is neither endorsed by nor associated with Arashi Vision Inc. This document
> describes publicly documented vendor behaviour for compatibility purposes only.

## Why this document exists

The three vendor surfaces overlap confusingly, and the most expensive mistakes in this
project would be promising something the vendor does not offer. Every "not supported" claim
below is a documented vendor statement, not an inference from experiment. When this document
and reality disagree, reality wins — but the correction is recorded here first.

## Surfaces at a glance

| | Wi-Fi OSC | Desktop Camera SDK | Desktop Media SDK |
| --- | --- | --- | --- |
| Access | Public documentation, no application | Application + approval (~3 business days), click-through EULA | Application + approval, click-through EULA |
| Transport | HTTP to the camera's access point | USB only (no Wi-Fi/Bluetooth on desktop) | Local files |
| Language | Any HTTP client | C++ | C++ |
| Platforms | Any | Windows 7+, Ubuntu 22.04 x86_64, Linux ARM64. **No macOS** | Windows 10+ x64 (VS2019, CUDA 10.2), Ubuntu 22.04 x64 (GCC 11+, CUDA 11.7), Conan 2. **Requires a discrete NVIDIA GPU. No WSL** |
| Privileges | None | Administrator/root; emulators unsupported | — |
| SphereLoom milestone | M1 | M3 | M4 |

## Wi-Fi OSC (Open Spherical Camera)

Supported camera models per the vendor's list: ONE X, ONE X2, ONE R, ONE RS, X3, X4,
X4 Air, **X5**.

### Connectivity facts

- The camera is **access-point only**: it runs its own hotspot. It cannot join a router.
- Fixed address `192.168.42.1`; the subnet cannot be changed.
- **No application-level authentication.** The only protocol marker is a static
  `X-XSRF-Protected: 1` header. Possession of the Wi-Fi credentials is the entire gate.

### Endpoints

| Endpoint | Method |
| --- | --- |
| `/osc/info` | GET |
| `/osc/state` | POST |
| `/osc/checkForUpdates` | POST |
| `/osc/commands/execute` | POST |
| `/osc/commands/status` | POST |

### Commands

`camera.getOptions`, `camera.setOptions`, `camera.takePicture`, `camera.startCapture`,
`camera.stopCapture`, `camera.listFiles`, `camera.delete`, plus timelapse options.

### Vendor-specific options

Underscore-prefixed extensions exist and are documented: `_sensorModuleType`, `_videoType`,
`_timelapseResolution`, `_topBottomCorrection`, `_MuteEnable`, `_batteryCapacity`,
`_sysTimestamp`, `_fileGroup` / `_localFileGroup`. SphereLoom treats them as optional —
absence is never an error.

### Pacing rules (vendor guidance)

- Never send a new `/osc/commands` request before the previous response has arrived.
- Poll `/osc/info` at most once per second.

### Not available over OSC

| Capability | Status | Note |
| --- | --- | --- |
| Live preview / live streaming | **Not supported** | Officially confirmed by the vendor's integration FAQ |
| Exposure parameter adjustment | **Not supported** | Officially confirmed |
| In-camera video stitching | **Not supported** | In-camera stitching applies to **photos only**; video requires the Media SDK |

## Desktop Camera SDK (USB)

Documented capabilities:

- `TakePhoto` with HDR / AEB / RAW variants and photo-size selection
- `StartRecording` / `StopRecording`, TimeLapse
- **Exposure and white balance control** (unavailable over OSC)
- Battery state, `IsConnected`, media time
- `GetCameraFilesList`, `DownloadCameraFile`, `DeleteCameraFile`
- `GetStorageState`, `FormatStorage`
- `StartLiveStreaming` / `StopLiveStreaming` / `SetStreamDelegate` — raw H.264/H.265
  dual-fisheye at roughly 1920×960 over USB
- `UploadFile` (firmware), log retrieval, shutdown, time synchronisation
- `DeviceDiscovery` → `Open`

X5 is explicitly supported. The public sample repository contains a README and a demo only —
**not** the binaries.

## Desktop Media SDK

Documented capabilities:

- `VideoStitcher` / `ImageStitcher`, including optical-flow stitching
- `EnableFlowState` (stabilisation)
- `EnableColorPlus`
- Colour grading (`SetWarmth` and related, range −100 to 100)
- `RealTimeStitcher`
- `SetStabDataOutputPath`
- `EnableH265Encoder`; H.264 hardware encoding caps at 4096 px
- `EnableCuda`
- File-trailer metadata parser (camera model, firmware version, serial number, IMU data,
  exposure time), added 2026-08-28

Format support:

| Input | Output | Supported |
| --- | --- | --- |
| `.insv` | `.mp4` | Yes |
| `.insp` / JPEG | `.jpg` | Yes |
| DNG | `.jpg` | **No** |

### Not documented anywhere — treated as unavailable

Trimming or cutting clips, concatenating or merging clips, and any timeline or multi-clip
editing. SphereLoom **does not promise these**. If a future vendor release documents them,
the capability registry changes in a reviewed pull request citing the documentation.

Nor will SphereLoom deliver them by automating the vendor's desktop application through its
graphical interface: that option was evaluated and rejected in
[ADR-0013](adr/0013-desktop-gui-automation-rejected.md).

## SphereLoom capability mapping

| Capability | OSC (M1) | Camera SDK (M3) | Media SDK (M4) |
| --- | --- | --- | --- |
| `camera.connect` | ✅ | ✅ | — |
| `camera.status` | ✅ | ✅ | — |
| `options.read` | ✅ | partial | — |
| `options.write` | ✅ | partial | — |
| `photo.capture` | ✅ | ✅ (HDR/AEB/RAW) | — |
| `video.record` | ✅ | ✅ | — |
| `files.list` | ✅ | ✅ | — |
| `files.download` | ✅ | ✅ | — |
| `files.delete` | ✅ (guarded) | ✅ (guarded) | — |
| `exposure.control` | ❌ vendor API does not offer it | ✅ | — |
| `preview.live` | ❌ vendor API does not offer it | ✅ (raw dual-fisheye) | — |
| `storage.format` | ❌ | ✅ (guarded) | — |
| `media.stitch.photo` | ✅ in camera | ✅ in camera | ✅ |
| `media.stitch.video` | ❌ | ❌ | ✅ |
| `media.stabilise` | ❌ | ❌ | ✅ |
| `media.colour` | ❌ | ❌ | ✅ |
| `media.trim_merge` | ❌ | ❌ | ❌ not documented by the vendor |

Legend: ✅ available on that surface · ❌ not available · "M*" indicates the SphereLoom
milestone in which SphereLoom exposes it.

## Sources

- OSC tutorial and supported-model list — <https://www.insta360.com/us/developer/tutorial?type=osc>
- OSC reference implementation repository — <https://github.com/Insta360Develop/Insta360_OSC>
- Camera resource FAQ (access-point behaviour, fixed IP) — <https://onlinemanual.insta360.com/developer/en-us/resource/camera>
- Integration FAQ (no live preview, USB-only desktop, no exposure control over OSC, video
  stitching requires the Media SDK) — <https://onlinemanual.insta360.com/developer/en-us/resource/integration>
- SDK resource FAQ (platforms, privileges) — <https://onlinemanual.insta360.com/developer/en-us/resource/sdk>
- Desktop Camera SDK repository — <https://github.com/Insta360Develop/Desktop-CameraSDK-Cpp>
- Desktop Media SDK repository — <https://github.com/Insta360Develop/Desktop-MediaSDK-Cpp>
- SDK application form — <https://www.insta360.com/us/sdk/apply>
- SDK EULA (last updated 21 September 2023) — <https://www.insta360.com/us/support/supportcourse?post_id=20734>

## Maintenance rule

Any change to this document must cite a vendor source. Any change to a ✅/❌ must be
accompanied by the corresponding change to the capability registry and its tests, in the
same pull request.
