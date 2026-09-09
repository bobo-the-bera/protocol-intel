import pytest
import yaml
from pydantic import ValidationError
from typer.testing import CliRunner

from protocol_intel.cli import app
from protocol_intel.release import release_status


def write_register(tmp_path, status="open", evidence=None):
    path = tmp_path / "requirements.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "requirements": [
                    {
                        "id": "required",
                        "title": "Required behavior",
                        "gate": "first_production",
                        "status": status,
                        "owner": "Codex",
                        "specification": "35",
                        "acceptance": "Observed passing behavior",
                        "evidence": evidence or [],
                    }
                ],
            }
        )
    )
    return path


@pytest.mark.parametrize("status", ["open", "implemented_unverified"])
def test_open_or_unverified_work_blocks_release(tmp_path, status):
    path = write_register(tmp_path, status)
    assert not release_status(path, "first_production")["ready"]
    assert CliRunner().invoke(app, ["release-check", "--register", str(path)]).exit_code == 1


def test_verified_without_evidence_cannot_close_a_requirement(tmp_path):
    with pytest.raises(ValidationError, match="evidence"):
        release_status(write_register(tmp_path, "verified"), "first_production")


def test_documenting_a_limitation_is_not_an_allowed_completion_state(tmp_path):
    with pytest.raises(ValidationError):
        release_status(write_register(tmp_path, "known_limitation"), "first_production")


def test_later_release_gate_cannot_skip_unfinished_production_work(tmp_path):
    path = write_register(tmp_path)
    assert not release_status(path, "full_specification")["ready"]
    assert not release_status(path, "scale")["ready"]
