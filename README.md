<p align="center">
  <img src="https://raw.githubusercontent.com/tsilva/parsehealthlog/main/logo.png" alt="parsehealthlog" width="512" />
  <br />
  <!-- repo-tagline:start -->
  <strong>📓 Transform health journal entries into structured, validated data 🏥</strong>
  <!-- repo-tagline:end -->
</p>

parsehealthlog combines a date-sectioned Markdown health journal with optional
lab CSVs and medical exam files. It writes one newest-first `health_log.md`
with sections for the sources you supplied.

## Quickstart

From a checkout with Python 3.10+ and uv:

```bash
uv sync --frozen
mkdir -p ~/.config/parsehealthlog/profiles
cp profiles/template.yaml.example ~/.config/parsehealthlog/profiles/myprofile.yaml
```

Set the input and output paths in the copied profile and configure the
approved model route for your environment. Then run:

```bash
uv run --frozen parsehealthlog --profile myprofile
```

Open `health_log.md` in the profile's output directory. The
[pipeline guide](https://github.com/tsilva/parsehealthlog/blob/main/docs/pipeline.md) explains source handling.

[Parsehealthlog configuration and commands](https://github.com/tsilva/parsehealthlog/blob/main/docs/parsehealthlog-guide.md).

## License

[MIT License](https://github.com/tsilva/parsehealthlog/blob/main/LICENSE).
