"""Deployment configuration tests that do not contact Modal."""

from __future__ import annotations

from unittest.mock import Mock

import modal
import pytest

from deploy import modal_app


@pytest.fixture(autouse=True)
def clean_deployment_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_AUTHORIZATION", raising=False)
    monkeypatch.delenv("TOURNEYDESK_TELEMETRY_SECRET_NAME", raising=False)


def test_deployment_without_otlp_environment_only_uses_named_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    named_secret = Mock(spec=modal.Secret)
    from_name = Mock(return_value=named_secret)
    from_dict = Mock(side_effect=AssertionError("optional secret must not be created"))
    monkeypatch.setattr(modal_app.modal.Secret, "from_name", from_name)
    monkeypatch.setattr(modal_app.modal.Secret, "from_dict", from_dict)

    assert modal_app._deployment_secrets() == [named_secret, named_secret]
    assert from_name.call_args_list == [(("tourneydesk",),), (("tourneydesk-origin-auth",),)]
    from_dict.assert_not_called()


def test_deployment_attaches_otlp_secret_after_named_secret_without_logging_values(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    endpoint = "https://collector.example/otlp"
    authorization = "Basic sensitive-credential"
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", endpoint)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_AUTHORIZATION", authorization)
    named_secret = Mock(spec=modal.Secret)
    otlp_secret = Mock(spec=modal.Secret)
    from_name = Mock(return_value=named_secret)
    from_dict = Mock(return_value=otlp_secret)
    monkeypatch.setattr(modal_app.modal.Secret, "from_name", from_name)
    monkeypatch.setattr(modal_app.modal.Secret, "from_dict", from_dict)

    assert modal_app._deployment_secrets() == [named_secret, named_secret, otlp_secret]
    assert from_name.call_args_list == [(("tourneydesk",),), (("tourneydesk-origin-auth",),)]
    from_dict.assert_called_once_with(
        {
            "OTEL_EXPORTER_OTLP_ENDPOINT": endpoint,
            "OTEL_EXPORTER_OTLP_AUTHORIZATION": authorization,
        }
    )
    captured = capsys.readouterr()
    assert endpoint not in captured.out
    assert endpoint not in captured.err
    assert authorization not in captured.out
    assert authorization not in captured.err


def test_deployment_allows_an_unauthenticated_otlp_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = "http://collector.internal:4318"
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", endpoint)
    named_secret = Mock(spec=modal.Secret)
    otlp_secret = Mock(spec=modal.Secret)
    monkeypatch.setattr(modal_app.modal.Secret, "from_name", Mock(return_value=named_secret))
    from_dict = Mock(return_value=otlp_secret)
    monkeypatch.setattr(modal_app.modal.Secret, "from_dict", from_dict)

    assert modal_app._deployment_secrets() == [named_secret, named_secret, otlp_secret]
    from_dict.assert_called_once_with(
        {
            "OTEL_EXPORTER_OTLP_ENDPOINT": endpoint,
            # Modal documents that Secret.from_dict omits keys whose values are None.
            "OTEL_EXPORTER_OTLP_AUTHORIZATION": None,
        }
    )


def test_dedicated_telemetry_reference_preserves_values_without_export(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TOURNEYDESK_TELEMETRY_SECRET_NAME", "tourneydesk-telemetry")
    named_secret = Mock(spec=modal.Secret)
    from_name = Mock(return_value=named_secret)
    monkeypatch.setattr(modal_app.modal.Secret, "from_name", from_name)
    assert modal_app._deployment_secrets() == [named_secret] * 3
    assert from_name.call_args_list[-1].args == ("tourneydesk-telemetry",)
