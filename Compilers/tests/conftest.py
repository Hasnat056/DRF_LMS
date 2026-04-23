"""
Shared fixtures for Compilers module tests.
"""
import io
import zipfile
import pytest
from unittest.mock import patch, MagicMock

from django.core.files.uploadedfile import SimpleUploadedFile


# ---------------------------------------------------------------------------
# Single-file fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def py_file():
    return SimpleUploadedFile('main.py', b'print("Hello, World!")\n', content_type='text/plain')


@pytest.fixture
def c_file():
    return SimpleUploadedFile('main.c', b'#include<stdio.h>\nint main(){printf("42\\n");return 0;}\n', content_type='text/plain')


@pytest.fixture
def cpp_file():
    return SimpleUploadedFile('main.cpp', b'#include<iostream>\nint main(){std::cout<<"hi";return 0;}\n', content_type='text/plain')


@pytest.fixture
def java_file():
    return SimpleUploadedFile('Main.java', b'public class Main{public static void main(String[] a){}}', content_type='text/plain')


@pytest.fixture
def helper_py_file():
    return SimpleUploadedFile('helper.py', b'x = 1\n', content_type='text/plain')


@pytest.fixture
def helper_c_file():
    return SimpleUploadedFile('helper.c', b'int helper(){return 1;}\n', content_type='text/plain')


# ---------------------------------------------------------------------------
# ZIP fixtures
# ---------------------------------------------------------------------------

def _make_zip(files: dict) -> SimpleUploadedFile:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    buf.seek(0)
    return SimpleUploadedFile('bundle.zip', buf.read(), content_type='application/zip')


@pytest.fixture
def zip_with_main_py():
    return _make_zip({'main.py': 'print("zipped!")\n'})


@pytest.fixture
def zip_with_main_c():
    return _make_zip({'main.c': '#include<stdio.h>\nint main(){return 0;}\n'})


@pytest.fixture
def zip_without_main():
    return _make_zip({'helper.py': 'x = 1\n'})


@pytest.fixture
def zip_with_main_java():
    return _make_zip({'main.java': 'public class main{}'})


@pytest.fixture
def zip_with_subdirectory():
    """ZIP where all files are inside a single subdirectory."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        zf.writestr('project/main.py', 'print("nested")\n')
        zf.writestr('project/helper.py', 'x = 1\n')
    buf.seek(0)
    return SimpleUploadedFile('bundle.zip', buf.read(), content_type='application/zip')


# ---------------------------------------------------------------------------
# Mock fixtures for compiler HTTP calls
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_compiler_success():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {'stdout': 'ok\n', 'stderr': ''}
    with patch('Compilers.serializers.requests.post', return_value=mock_resp) as m:
        yield m


@pytest.fixture
def mock_compiler_connection_error():
    import requests as req
    with patch('Compilers.serializers.requests.post', side_effect=req.exceptions.ConnectionError('refused')) as m:
        yield m
