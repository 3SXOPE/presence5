# Presence5 in PS5 Payload Manager

[PS5 Payload Manager](https://github.com/itsPLK/ps5-payload-manager) shows payloads from
"repositories": JSON lists of payloads, each with an icon. These two files are a repository with
just Presence5 in it, using the logo in `assets/presence5.svg`.

- `payloads.json`: the list. Replace `YOUR-USERNAME` with the GitHub account the release is on.
- `icons.json`: the logo as a base64 PNG, the format Payload Manager needs (under 12,288
  characters, about 128x128).

To use it, open Payload Manager, go to **Manage Sources**, choose **Add Source** and enter:

```
https://raw.githubusercontent.com/YOUR-USERNAME/presence5/main/payload-manager/payloads.json
```

To get Presence5 into Payload Manager's official list instead, open an issue on
[ps5-payloads-mirror](https://github.com/itsPLK/ps5-payloads-mirror/issues/new) asking for it to
be added, with a link to the release and the logo.
