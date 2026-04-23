"""
test_java_compiler.py
----------------------
Tests for the FastAPI Java compiler service.
Uses TestClient with mocked subprocess and filesystem.
"""
import subprocess
from unittest.mock import patch, mock_open

import pytest
from fastapi.testclient import TestClient

from Compilers.java_compiler.api import app

client = TestClient(app)


class TestJavaCompilerAPI:

    @patch('Compilers.java_compiler.api.os.path.exists', return_value=False)
    def test_file_not_exists_returns_400(self, mock_exists):
        r = client.post('/run', json={
            'file_path': '/code/test/Main.java',
        })
        assert r.status_code == 400
        assert 'File does not exist' in r.json()['detail']

    @patch('Compilers.java_compiler.api.os.path.exists', return_value=True)
    def test_not_java_file_returns_400(self, mock_exists):
        r = client.post('/run', json={
            'file_path': '/code/test/Main.py',
        })
        assert r.status_code == 400
        assert 'Unsupported file type' in r.json()['detail']

    @patch('Compilers.java_compiler.api.subprocess.run')
    @patch('Compilers.java_compiler.api.os.path.exists', return_value=True)
    def test_compilation_error(self, mock_exists, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout='', stderr='error: `;` expected',
        )
        r = client.post('/run', json={
            'file_path': '/code/test/Main.java',
        })
        assert r.status_code == 200
        assert '`;` expected' in r.json()['stderr']

    @patch('Compilers.java_compiler.api.subprocess.run',
           side_effect=subprocess.TimeoutExpired(cmd='javac', timeout=10))
    @patch('Compilers.java_compiler.api.os.path.exists', return_value=True)
    def test_compilation_timeout(self, mock_exists, mock_run):
        r = client.post('/run', json={
            'file_path': '/code/test/Main.java',
        })
        assert r.status_code == 200
        assert 'Compilation timed out' in r.json()['stderr']

    @patch('Compilers.java_compiler.api.subprocess.run')
    @patch('Compilers.java_compiler.api.os.path.exists', return_value=True)
    def test_successful_execution(self, mock_exists, mock_run):
        mock_run.side_effect = [
            subprocess.CompletedProcess(args=[], returncode=0, stdout='', stderr=''),
            subprocess.CompletedProcess(args=[], returncode=0, stdout='Hello Java\n', stderr=''),
        ]
        r = client.post('/run', json={
            'file_path': '/code/test/Main.java',
        })
        assert r.status_code == 200
        assert r.json()['stdout'] == 'Hello Java\n'

    @patch('builtins.open', mock_open(read_data='42\n'))
    @patch('Compilers.java_compiler.api.subprocess.run')
    @patch('Compilers.java_compiler.api.os.path.exists', return_value=True)
    def test_with_input_file(self, mock_exists, mock_run):
        mock_run.side_effect = [
            subprocess.CompletedProcess(args=[], returncode=0, stdout='', stderr=''),
            subprocess.CompletedProcess(args=[], returncode=0, stdout='42\n', stderr=''),
        ]
        r = client.post('/run', json={
            'file_path': '/code/test/Main.java',
            'input_file_path': '/code/test/input.txt',
        })
        assert r.status_code == 200
        assert r.json()['stdout'] == '42\n'

    @patch('Compilers.java_compiler.api.os.path.exists')
    @patch('Compilers.java_compiler.api.subprocess.run')
    def test_input_file_not_exists(self, mock_run, mock_exists):
        # Main file exists, input file does not
        mock_exists.side_effect = lambda p: p == '/code/test/Main.java'
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout='', stderr='',
        )
        r = client.post('/run', json={
            'file_path': '/code/test/Main.java',
            'input_file_path': '/code/test/missing.txt',
        })
        assert r.status_code == 400
        assert 'Input file does not exist' in r.json()['detail']

    @patch('Compilers.java_compiler.api.subprocess.run')
    @patch('Compilers.java_compiler.api.os.path.exists', return_value=True)
    def test_run_timeout(self, mock_exists, mock_run):
        mock_run.side_effect = [
            subprocess.CompletedProcess(args=[], returncode=0, stdout='', stderr=''),
            subprocess.TimeoutExpired(cmd='java', timeout=10),
        ]
        r = client.post('/run', json={
            'file_path': '/code/test/Main.java',
        })
        assert r.status_code == 200
        assert 'Execution timed out' in r.json()['stderr']
