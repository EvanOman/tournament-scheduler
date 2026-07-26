"""Deployment configuration tests that do not contact Modal."""

from __future__ import annotations

from unittest.mock import Mock, call

import modal
import pytest

from deploy import modal_app


@pytest.fixture(autouse=True)
def clean_deployment_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TOURNEYDESK_OTEL_SECRET_NAME", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_AUTHORIZATION", raising=False)


def test_deployment_without_otlp_environment_only_uses_named_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    named_secret = Mock(spec=modal.Secret)
    from_name = Mock(return_value=named_secret)
    from_dict = Mock(side_effect=AssertionError("optional secret must not be created"))
    monkeypatch.setattr(modal_app.modal.Secret, "from_name", from_name)
    monkeypatch.setattr(modal_app.modal.Secret, "from_dict", from_dict)

    assert modal_app._deployment_secrets() == [named_secret]
    from_name.assert_called_once_with("tourneydesk")
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

    assert modal_app._deployment_secrets() == [named_secret, otlp_secret]
    from_name.assert_called_once_with("tourneydesk")
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


def test_deployment_attaches_selected_named_secret_without_leaking_its_name(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    selected_name = "otel-sensitive-production"
    monkeypatch.setenv("TOURNEYDESK_OTEL_SECRET_NAME", selected_name)
    application_secret = Mock(spec=modal.Secret)
    otlp_secret = Mock(spec=modal.Secret)
    from_name = Mock(side_effect=[application_secret, otlp_secret])
    monkeypatch.setattr(modal_app.modal.Secret, "from_name", from_name)
    from_dict = Mock(side_effect=AssertionError("inline secret must not be created"))
    monkeypatch.setattr(modal_app.modal.Secret, "from_dict", from_dict)

    assert modal_app._deployment_secrets() == [application_secret, otlp_secret]
    assert from_name.call_args_list == [
        call("tourneydesk"),
        call(selected_name, required_keys=["OTEL_EXPORTER_OTLP_ENDPOINT"]),
    ]
    from_dict.assert_not_called()
    captured = capsys.readouterr()
    assert selected_name not in captured.out
    assert selected_name not in captured.err


def test_inline_otlp_values_override_the_selected_named_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected_name = "otel-grafana"
    endpoint = "https://collector.example/otlp"
    authorization = "Basic sensitive-credential"
    monkeypatch.setenv("TOURNEYDESK_OTEL_SECRET_NAME", selected_name)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", endpoint)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_AUTHORIZATION", authorization)
    application_secret = Mock(spec=modal.Secret)
    named_otlp_secret = Mock(spec=modal.Secret)
    inline_otlp_secret = Mock(spec=modal.Secret)
    from_name = Mock(side_effect=[application_secret, named_otlp_secret])
    monkeypatch.setattr(modal_app.modal.Secret, "from_name", from_name)
    from_dict = Mock(return_value=inline_otlp_secret)
    monkeypatch.setattr(modal_app.modal.Secret, "from_dict", from_dict)

    assert modal_app._deployment_secrets() == [
        application_secret,
        named_otlp_secret,
        inline_otlp_secret,
    ]
    assert from_name.call_args_list == [
        call("tourneydesk"),
        call(selected_name, required_keys=["OTEL_EXPORTER_OTLP_ENDPOINT"]),
    ]
    from_dict.assert_called_once_with(
        {
            "OTEL_EXPORTER_OTLP_ENDPOINT": endpoint,
            "OTEL_EXPORTER_OTLP_AUTHORIZATION": authorization,
        }
    )


@pytest.mark.parametrize(
    "invalid_name",
    ["", "UPPERCASE", "-otel", "otel-", "otel_grafana", "../otel", "a" * 65],
)
def test_deployment_rejects_invalid_bounded_secret_names_without_echoing_them(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    invalid_name: str,
) -> None:
    monkeypatch.setenv("TOURNEYDESK_OTEL_SECRET_NAME", invalid_name)
    from_name = Mock()
    monkeypatch.setattr(modal_app.modal.Secret, "from_name", from_name)

    with pytest.raises(ValueError) as error:
        modal_app._deployment_secrets()

    if invalid_name:
        assert invalid_name not in str(error.value)
    from_name.assert_not_called()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


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

    assert modal_app._deployment_secrets() == [named_secret, otlp_secret]
    from_dict.assert_called_once_with(
        {
            "OTEL_EXPORTER_OTLP_ENDPOINT": endpoint,
            # Modal documents that Secret.from_dict omits keys whose values are None.
            "OTEL_EXPORTER_OTLP_AUTHORIZATION": None,
        }
    )
