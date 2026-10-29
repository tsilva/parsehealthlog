"""New runtime profiles can start from a tracked, anonymized example."""

from pathlib import Path

from parsehealthlog.config import DEFAULT_BASE_URL, ProfileConfig


def test_profile_template_uses_default_gateway_and_no_other_person_sources(tmp_path):
    template = Path(__file__).parents[1] / "profiles/template.yaml.example"
    copied = tmp_path / "example.yaml"
    copied.write_bytes(template.read_bytes())
    profile = ProfileConfig.from_file(copied)

    assert profile.health_log_path.is_absolute()
    assert profile.output_path.is_absolute()
    assert profile.base_url == DEFAULT_BASE_URL
    assert profile.workers is None
    assert profile.labs_parser_output_path is None
    assert profile.medical_exams_parser_output_path is None
