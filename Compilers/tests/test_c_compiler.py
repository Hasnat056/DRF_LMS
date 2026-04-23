"""
test_c_compiler.py
-------------------
Tests for the FastAPI C/C++ compiler service.
Uses TestClient with mocked subprocess and filesystem.
"""
import subprocess
from unittest.mock import patch, mock_open, call

import pytest
from fastapi.testclient import TestClient

from Compilers.c_compiler.api import app

client = TestClient(app)


class TestCCompilerAPI:

    @patch('Compilers.c_compiler.api.os.path.exists', return_value=False)
    def test_folder_not_exists_returns_400(self, mock_exists):
        r = client.post('/run', json={
            'folder_path': '/code/test',
            'language': 'c',
        })
        assert r.status_code == 400
        assert 'Folder does not exist' in r.json()['detail']

    @patch('Compilers.c_compiler.api.os.path.isdir', return_value=False)
    @patch('Compilers.c_compiler.api.os.path.exists', return_value=True)
    def test_path_not_directory_returns_400(self, mock_exists, mock_isdir):
        r = client.post('/run', json={
            'folder_path': '/code/test',
            'language': 'c',
        })
        assert r.status_code == 400
        assert 'Path must be a folder' in r.json()['detail']

    @patch('Compilers.c_compiler.api.subprocess.run')
    @patch('Compilers.c_compiler.api.os.path.isdir', return_value=True)
    @patch('Compilers.c_compiler.api.os.path.exists', return_value=True)
    def test_compilation_error_returns_stderr(self, mock_exists, mock_isdir, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout='', stderr='error: undefined reference',
        )
        r = client.post('/run', json={
            'folder_path': '/code/test',
            'language': 'c',
        })
        assert r.status_code == 200
        data = r.json()
        assert 'undefined reference' in data['stderr']

    @patch('Compilers.c_compiler.api.subprocess.run',
           side_effect=subprocess.TimeoutExpired(cmd='gcc', timeout=10))
    @patch('Compilers.c_compiler.api.os.path.isdir', return_value=True)
    @patch('Compilers.c_compiler.api.os.path.exists', return_value=True)
    def test_compilation_timeout(self, mock_exists, mock_isdir, mock_run):
        r = client.post('/run', json={
            'folder_path': '/code/test',
            'language': 'c',
        })
        assert r.status_code == 200
        assert 'Compilation timed out' in r.json()['stderr']

    @patch('Compilers.c_compiler.api.subprocess.run')
    @patch('Compilers.c_compiler.api.os.path.isdir', return_value=True)
    @patch('Compilers.c_compiler.api.os.path.exists', return_value=True)
    def test_successful_c_execution(self, mock_exists, mock_isdir, mock_run):
        # First call: compilation success; second call: execution success
        mock_run.side_effect = [
            subprocess.CompletedProcess(args=[], returncode=0, stdout='', stderr=''),
            subprocess.CompletedProcess(args=[], returncode=0, stdout='42\n', stderr=''),
        ]
        r = client.post('/run', json={
            'folder_path': '/code/test',
            'language': 'c',
        })
        assert r.status_code == 200
        assert r.json()['stdout'] == '42\n'
        # Verify gcc was used (not g++)
        compile_call = mock_run.call_args_list[0]
        assert 'gcc' in str(compile_call)

    @patch('Compilers.c_compiler.api.subprocess.run')
    @patch('Compilers.c_compiler.api.os.path.isdir', return_value=True)
    @patch('Compilers.c_compiler.api.os.path.exists', return_value=True)
    def test_successful_cpp_execution(self, mock_exists, mock_isdir, mock_run):
        mock_run.side_effect = [
            subprocess.CompletedProcess(args=[], returncode=0, stdout='', stderr=''),
            subprocess.CompletedProcess(args=[], returncode=0, stdout='hi\n', stderr=''),
        ]
        r = client.post('/run', json={
            'folder_path': '/code/test',
            'language': 'cpp',
        })
        assert r.status_code == 200
        assert r.json()['stdout'] == 'hi\n'
        compile_call = mock_run.call_args_list[0]
        assert 'g++' in str(compile_call)

    @patch('builtins.open', mock_open(read_data='5\n'))
    @patch('Compilers.c_compiler.api.subprocess.run')
    @patch('Compilers.c_compiler.api.os.path.isdir', return_value=True)
    @patch('Compilers.c_compiler.api.os.path.exists', return_value=True)
    def test_run_with_input_file(self, mock_exists, mock_isdir, mock_run):
        mock_run.side_effect = [
            subprocess.CompletedProcess(args=[], returncode=0, stdout='', stderr=''),
            subprocess.CompletedProcess(args=[], returncode=0, stdout='5\n', stderr=''),
        ]
        r = client.post('/run', json={
            'folder_path': '/code/test',
            'language': 'c',
            'input_file_path': '/code/test/input.txt',
        })
        assert r.status_code == 200
        assert r.json()['stdout'] == '5\n'

    @patch('Compilers.c_compiler.api.subprocess.run')
    @patch('Compilers.c_compiler.api.os.path.isdir', return_value=True)
    @patch('Compilers.c_compiler.api.os.path.exists')
    def test_run_input_file_not_exists(self, mock_exists, mock_isdir, mock_run):
        # folder exists, but input file does not
        mock_exists.side_effect = lambda p: p != '/code/test/input.txt'
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout='', stderr='',
        )
        r = client.post('/run', json={
            'folder_path': '/code/test',
            'language': 'c',
            'input_file_path': '/code/test/input.txt',
        })
        assert r.status_code == 400
        assert 'Input file does not exist' in r.json()['detail']

    @patch('Compilers.c_compiler.api.subprocess.run')
    @patch('Compilers.c_compiler.api.os.path.isdir', return_value=True)
    @patch('Compilers.c_compiler.api.os.path.exists', return_value=True)
    def test_run_timeout(self, mock_exists, mock_isdir, mock_run):
        # Compilation succeeds, execution times out
        mock_run.side_effect = [
            subprocess.CompletedProcess(args=[], returncode=0, stdout='', stderr=''),
            subprocess.TimeoutExpired(cmd='./a.out', timeout=10),
        ]
        r = client.post('/run', json={
            'folder_path': '/code/test',
            'language': 'c',
        })
        assert r.status_code == 200
        assert 'Execution timed out' in r.json()['stderr']
