import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app import generate_api_spec


def test_generate_api_spec_returns_structured_output():
    spec = generate_api_spec('Create a weather API for a city')
    assert spec['api_name']
    assert spec['endpoint'].startswith('/')
    assert spec['http_method'] in {'GET', 'POST'}
    assert isinstance(spec['success_response'], dict)
    assert spec['mock_mode'] is False
