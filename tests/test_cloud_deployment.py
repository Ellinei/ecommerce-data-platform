from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock, patch

from dags.dbt_pipeline_dag import OLIST_FILES, _ensure_olist_data_available

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_requirements_pins_amazon_provider():
    requirements = (REPO_ROOT / "requirements.txt").read_text()
    assert "apache-airflow-providers-amazon==8.20.0" in requirements


def test_dockerfile_bakes_dags_and_dbt_project():
    dockerfile = (REPO_ROOT / "Dockerfile").read_text()
    assert "COPY --chown=airflow:0 dags/ /opt/airflow/dags/" in dockerfile
    assert "COPY --chown=airflow:0 dbt_project/ /opt/airflow/dbt_project/" in dockerfile
    assert re.search(r"^RUN .*dbt deps", dockerfile, re.MULTILINE)
    assert "COPY plugins/" not in dockerfile


def test_ensure_olist_data_skips_download_when_files_present_and_bucket_set(tmp_path):
    for filename in OLIST_FILES:
        (tmp_path / filename).write_text("stub")
    with patch("boto3.client") as mock_client:
        _ensure_olist_data_available(tmp_path, OLIST_FILES, "some-bucket")
    mock_client.assert_not_called()


def test_ensure_olist_data_skips_download_when_bucket_unset(tmp_path):
    with patch("boto3.client") as mock_client:
        _ensure_olist_data_available(tmp_path, OLIST_FILES, None)
    mock_client.assert_not_called()


def test_ensure_olist_data_downloads_when_files_missing_and_bucket_set(tmp_path):
    mock_s3 = MagicMock()
    with patch("boto3.client", return_value=mock_s3) as mock_client:
        _ensure_olist_data_available(tmp_path, OLIST_FILES, "some-bucket")
    mock_client.assert_called_once_with("s3")
    assert mock_s3.download_file.call_count == len(OLIST_FILES)
    called_keys = {call.args[1] for call in mock_s3.download_file.call_args_list}
    assert called_keys == {f"olist-raw/{f}" for f in OLIST_FILES}
    called_dests = {call.args[2] for call in mock_s3.download_file.call_args_list}
    assert called_dests == {str(tmp_path / f) for f in OLIST_FILES}
