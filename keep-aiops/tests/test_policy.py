"""Policy stub: fail-closed on anything but read-class tools."""

import pytest

from aiops_api.modules.policy import PolicyDenied, assert_tool_allowed


def test_read_tools_allowed():
    assert_tool_allowed("get_pods", "read")  # must not raise


@pytest.mark.parametrize("execution_class", ["mutate", "write", "admin", "", None])
def test_non_read_tools_denied(execution_class):
    with pytest.raises(PolicyDenied):
        assert_tool_allowed("delete_pods", execution_class)


def test_mutate_denial_message_names_tool():
    with pytest.raises(PolicyDenied) as exc_info:
        assert_tool_allowed("restart_pod", "mutate")
    assert "restart_pod" in str(exc_info.value)
