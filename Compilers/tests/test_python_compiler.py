"""
test_python_compiler.py
------------------------
Tests for the FastAPI Python compiler service.
Uses TestClient with mocked subprocess and filesystem.
"""
import subprocess
from unittest.mock import patch, mock_open

import pytest
from fastapi.testclient import TestClient

from Compilers.python_compiler.api import app

client = TestClient(app)


class TestPythonCompilerAPI:

    @patch('Compilers.python_compiler.api.os.path.exists', return_value=False)
    def test_file_not_exists_returns_400(self, mock_exists):
        r = client.post('/run', json={
            'file_path': '/code/test/main.py',
        })
        assert r.status_code == 400
        assert 'File does not exist' in r.json()['detail']

    @patch('Compilers.python_compiler.api.subprocess.run')
    @patch('Compilers.python_compiler.api.os.path.exists', return_value=True)
    def test_successful_execution(self, mock_exists, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=['python3', '/code/test/main.py'],
            returncode=0, stdout='hello\n', stderr='',
        )
        r = client.post('/run', json={
            'file_path': '/code/test/main.py',
        })
        assert r.status_code == 200
        data = r.json()
        assert data['stdout'] == 'hello\n'
        assert data['stderr'] == ''

    @patch('builtins.open', mock_open(read_data='1\n2\n'))
    @patch('Compilers.python_compiler.api.subprocess.run')
    @patch('Compilers.python_compiler.api.os.path.exists', return_value=True)
    def test_with_input_file(self, mock_exists, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout='result\n', stderr='',
        )
        r = client.post('/run', json={
            'file_path': '/code/test/main.py',
            'input_file_path': '/code/test/input.txt',
        })
        assert r.status_code == 200
        assert r.json()['stdout'] == 'result\n'

    @patch('Compilers.python_compiler.api.subprocess.run')
    @patch('Compilers.python_compiler.api.os.path.exists', return_value=True)
    def test_run_with_explicit_null_input_file_path(self, mock_exists, mock_run):
        """The Django backend always sends input_file_path as a key, using
        None when stdin is blank. Explicit null must not fail schema validation."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=['python3', '/code/test/main.py'],
            returncode=0, stdout='hello\n', stderr='',
        )
        r = client.post('/run', json={
            'file_path': '/code/test/main.py',
            'input_file_path': None,
        })
        assert r.status_code == 200
        assert r.json()['stdout'] == 'hello\n'

    @patch('Compilers.python_compiler.api.os.path.exists')
    def test_input_file_not_exists_returns_400(self, mock_exists):
        # file_path exists but input_file_path does not
        mock_exists.side_effect = lambda p: p == '/code/test/main.py'
        r = client.post('/run', json={
            'file_path': '/code/test/main.py',
            'input_file_path': '/code/test/missing_input.txt',
        })
        assert r.status_code == 400
        assert 'Input file does not exist' in r.json()['detail']

    @patch('Compilers.python_compiler.api.subprocess.run',
           side_effect=subprocess.TimeoutExpired(cmd='python3', timeout=10))
    @patch('Compilers.python_compiler.api.os.path.exists', return_value=True)
    def test_timeout_returns_timeout_message(self, mock_exists, mock_run):
        r = client.post('/run', json={
            'file_path': '/code/test/main.py',
            'timeout': 10,
        })
        assert r.status_code == 200
        data = r.json()
        assert data['stdout'] == ''
        assert 'timed out' in data['stderr'].lower()
