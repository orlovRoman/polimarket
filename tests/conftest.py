import pytest
from unittest.mock import patch
from core.onchain_gate import GateResult

@pytest.fixture(autouse=True)
def mock_onchain_gate_for_tests(request):
    module_name = request.module.__name__
    if "test_onchain_gate" in module_name or "test_workflow_gate_integration" in module_name:
        yield
        return
        
    with patch("core.onchain_gate.check_onchain_gate", return_value=GateResult(allow=True, reason="Mocked gate pass", blocked_by="pass")):
        yield
