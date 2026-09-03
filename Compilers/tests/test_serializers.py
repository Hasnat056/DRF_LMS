"""
test_serializers.py
--------------------
Tests for CompilerSerializer (Compilers/serializers.py).

All HTTP calls to compiler services are mocked.
Filesystem operations use tmp_path where possible, otherwise mocked.
"""
import os
import subprocess
import zipfile
from unittest.mock import patch, MagicMock, call

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile, InMemoryUploadedFile
from django.http import QueryDict
from rest_framework.response import Response

from Compilers.serializers import CompilerSerializer


# ---------------------------------------------------------------------------
# to_internal_value
# ---------------------------------------------------------------------------


class TestCompilerSerializerToInternalValue:

    def test_querydict_single_file(self, py_file):
        """QueryDict (multipart upload) normalizes file list correctly."""
        qd = QueryDict(mutable=True)
        qd.setlist('file', [py_file])
        qd['input_list'] = ''
        serializer = CompilerSerializer(data=qd)
        internal = serializer.to_internal_value(qd)
        assert isinstance(internal['file'], list)
        assert len(internal['file']) == 1

    def test_plain_dict_single_inmemoryfile(self, py_file):
        """Plain dict with a single InMemoryUploadedFile wraps it in a list."""
        data = {'file': py_file, 'input_list': None}
        serializer = CompilerSerializer(data=data)
        internal = serializer.to_internal_value(data)
        assert isinstance(internal['file'], list)
        assert len(internal['file']) == 1

    def test_plain_dict_list_of_files(self, py_file, helper_py_file):
        """Plain dict with a list of files passes through correctly."""
        data = {'file': [py_file, helper_py_file], 'input_list': None}
        serializer = CompilerSerializer(data=data)
        internal = serializer.to_internal_value(data)
        assert len(internal['file']) == 2

    def test_plain_dict_no_file(self):
        """When file field is None, files list is empty."""
        data = {'file': None, 'input_list': None}
        serializer = CompilerSerializer(data=data)
        internal = serializer.to_internal_value(data)
        assert internal['file'] == []

    def test_input_list_passthrough(self, py_file):
        """input_list value is preserved through normalization."""
        data = {'file': py_file, 'input_list': '1\n2\n3'}
        serializer = CompilerSerializer(data=data)
        internal = serializer.to_internal_value(data)
        assert internal['input_list'] == '1\n2\n3'


# ---------------------------------------------------------------------------
# create — routing logic
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCompilerSerializerCreate:

    @patch('Compilers.serializers.shutil.rmtree')
    @patch('Compilers.serializers.requests.post')
    @patch('Compilers.serializers.os.makedirs')
    def test_single_py_file_calls_python_compiler(
        self, mock_makedirs, mock_post, mock_rmtree, py_file, tmp_path
    ):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {'stdout': 'hello\n', 'stderr': ''}
        mock_post.return_value = mock_resp

        serializer = CompilerSerializer()
        with patch('Compilers.serializers.uuid.uuid4') as mock_uuid:
            mock_uuid.return_value = MagicMock(hex='abc123')
            with patch('builtins.open', MagicMock()):
                result = serializer.create({'file': [py_file], 'input_list': None})

        mock_post.assert_called_once()
        url = mock_post.call_args[0][0]
        assert 'python-compiler' in url
        # Python compiler service parses the body as JSON (Pydantic model) —
        # sending form-encoded data= fails schema validation.
        assert 'json' in mock_post.call_args[1]
        assert 'data' not in mock_post.call_args[1]

    @patch('Compilers.serializers.shutil.rmtree')
    @patch('Compilers.serializers.requests.post')
    @patch('Compilers.serializers.os.makedirs')
    def test_single_c_file_calls_c_compiler(
        self, mock_makedirs, mock_post, mock_rmtree, c_file
    ):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {'stdout': '42\n', 'stderr': ''}
        mock_post.return_value = mock_resp

        serializer = CompilerSerializer()
        with patch('builtins.open', MagicMock()):
            result = serializer.create({'file': [c_file], 'input_list': None})

        url = mock_post.call_args[0][0]
        assert 'c-compiler' in url

    @patch('Compilers.serializers.shutil.rmtree')
    @patch('Compilers.serializers.requests.post')
    @patch('Compilers.serializers.os.makedirs')
    def test_single_cpp_file_calls_c_compiler(
        self, mock_makedirs, mock_post, mock_rmtree, cpp_file
    ):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {'stdout': 'hi\n', 'stderr': ''}
        mock_post.return_value = mock_resp

        serializer = CompilerSerializer()
        with patch('builtins.open', MagicMock()):
            result = serializer.create({'file': [cpp_file], 'input_list': None})

        url = mock_post.call_args[0][0]
        assert 'c-compiler' in url
        data = mock_post.call_args[1].get('json') or mock_post.call_args[0][1] if len(mock_post.call_args[0]) > 1 else mock_post.call_args[1]
        # Verify language is 'cpp'
        if 'json' in mock_post.call_args[1]:
            assert mock_post.call_args[1]['json']['language'] == 'cpp'

    @patch('Compilers.serializers.os.makedirs')
    def test_single_invalid_extension_returns_error(self, mock_makedirs, java_file):
        serializer = CompilerSerializer()
        with patch('builtins.open', MagicMock()):
            result = serializer.create({'file': [java_file], 'input_list': None})
        assert isinstance(result, Response)
        assert 'Invalid file extension' in str(result.data) or 'error' in str(result.data).lower()

    @patch('Compilers.serializers.os.makedirs')
    def test_no_files_returns_error(self, mock_makedirs):
        serializer = CompilerSerializer()
        result = serializer.create({'file': [], 'input_list': None})
        assert isinstance(result, Response)
        assert 'No file provided' in str(result.data)

    @patch('Compilers.serializers.shutil.rmtree')
    @patch('Compilers.serializers.requests.post')
    @patch('Compilers.serializers.os.makedirs')
    def test_multiple_files_routes_to_handle_multiple(
        self, mock_makedirs, mock_post, mock_rmtree, py_file, helper_py_file
    ):
        # Rename py_file to main.py is already done
        mock_resp = MagicMock()
        mock_resp.json.return_value = {'stdout': 'ok\n', 'stderr': ''}
        mock_post.return_value = mock_resp

        serializer = CompilerSerializer()
        with patch('builtins.open', MagicMock()):
            result = serializer.create({
                'file': [py_file, helper_py_file],
                'input_list': None,
            })

        mock_post.assert_called_once()
        assert 'python-compiler' in mock_post.call_args[0][0]

    @patch('Compilers.serializers.os.makedirs')
    def test_called_process_error_caught(self, mock_makedirs, py_file):
        serializer = CompilerSerializer()
        with patch.object(
            serializer, '_handle_single_file',
            side_effect=subprocess.CalledProcessError(1, 'python3'),
        ):
            with patch('builtins.open', MagicMock()):
                result = serializer.create({'file': [py_file], 'input_list': None})
        assert isinstance(result, Response)
        assert 'stderr' in result.data

    @patch('Compilers.serializers.shutil.rmtree')
    @patch('Compilers.serializers.requests.post')
    @patch('Compilers.serializers.os.makedirs')
    def test_input_list_creates_input_file(
        self, mock_makedirs, mock_post, mock_rmtree, py_file
    ):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {'stdout': '', 'stderr': ''}
        mock_post.return_value = mock_resp

        written_data = []
        real_open = open

        def mock_open_func(path, mode='r', *args, **kwargs):
            if 'input.txt' in str(path) and 'w' in mode:
                m = MagicMock()
                m.__enter__ = MagicMock(return_value=m)
                m.__exit__ = MagicMock(return_value=False)
                m.write = lambda data: written_data.append(data)
                m.name = str(path)
                return m
            # For writing code files
            m = MagicMock()
            m.__enter__ = MagicMock(return_value=m)
            m.__exit__ = MagicMock(return_value=False)
            return m

        serializer = CompilerSerializer()
        with patch('builtins.open', side_effect=mock_open_func):
            result = serializer.create({'file': [py_file], 'input_list': '1\n2\n3'})

        assert any('1' in d for d in written_data)

    @patch('Compilers.serializers.shutil.rmtree')
    @patch('Compilers.serializers.requests.post')
    @patch('Compilers.serializers.os.makedirs')
    def test_input_list_none_skips_file(
        self, mock_makedirs, mock_post, mock_rmtree, py_file
    ):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {'stdout': '', 'stderr': ''}
        mock_post.return_value = mock_resp

        serializer = CompilerSerializer()
        with patch('builtins.open', MagicMock()):
            result = serializer.create({'file': [py_file], 'input_list': None})

        # Verify the call to compiler has input_file_path=None
        call_kwargs = mock_post.call_args
        # For python: data= kwarg
        if 'data' in call_kwargs[1]:
            assert call_kwargs[1]['data']['input_file_path'] is None
        elif 'json' in call_kwargs[1]:
            assert call_kwargs[1]['json']['input_file_path'] is None


# ---------------------------------------------------------------------------
# _handle_zip
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCompilerSerializerHandleZip:

    @patch('Compilers.serializers.shutil.rmtree')
    @patch('Compilers.serializers.requests.post')
    @patch('Compilers.serializers.os.makedirs')
    def test_zip_with_main_py(
        self, mock_makedirs, mock_post, mock_rmtree, zip_with_main_py, tmp_path
    ):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {'stdout': 'zipped!\n', 'stderr': ''}
        mock_post.return_value = mock_resp

        serializer = CompilerSerializer()
        request_folder = str(tmp_path)

        result = serializer._handle_zip(zip_with_main_py, None, request_folder)
        assert isinstance(result, Response)
        assert 'python-compiler' in mock_post.call_args[0][0]

    @patch('Compilers.serializers.shutil.rmtree')
    @patch('Compilers.serializers.requests.post')
    @patch('Compilers.serializers.os.makedirs')
    def test_zip_with_main_c(
        self, mock_makedirs, mock_post, mock_rmtree, zip_with_main_c, tmp_path
    ):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {'stdout': '', 'stderr': ''}
        mock_post.return_value = mock_resp

        serializer = CompilerSerializer()
        request_folder = str(tmp_path)

        result = serializer._handle_zip(zip_with_main_c, None, request_folder)
        assert isinstance(result, Response)
        assert 'c-compiler' in mock_post.call_args[0][0]

    def test_zip_no_main_file_returns_error(self, zip_without_main, tmp_path):
        serializer = CompilerSerializer()
        request_folder = str(tmp_path / 'test_zip_nomain')
        os.makedirs(request_folder, exist_ok=True)

        result = serializer._handle_zip(zip_without_main, None, request_folder)
        assert isinstance(result, Response)
        assert 'main file' in str(result.data).lower()

    def test_zip_unsupported_main_extension(self, zip_with_main_java, tmp_path):
        serializer = CompilerSerializer()
        request_folder = str(tmp_path / 'test_zip_java')
        os.makedirs(request_folder, exist_ok=True)

        result = serializer._handle_zip(zip_with_main_java, None, request_folder)
        assert isinstance(result, Response)
        assert 'not supported' in str(result.data).lower() or 'Languages' in str(result.data)

    @patch('Compilers.serializers.shutil.rmtree')
    @patch('Compilers.serializers.requests.post')
    def test_zip_request_exception(self, mock_post, mock_rmtree, zip_with_main_py, tmp_path):
        import requests as req
        mock_post.side_effect = req.exceptions.ConnectionError('refused')

        serializer = CompilerSerializer()
        request_folder = str(tmp_path / 'test_zip_err')
        os.makedirs(request_folder, exist_ok=True)

        result = serializer._handle_zip(zip_with_main_py, None, request_folder)
        assert isinstance(result, Response)
        assert 'refused' in str(result.data).lower() or 'stderr' in result.data

    @patch('Compilers.serializers.shutil.rmtree')
    @patch('Compilers.serializers.requests.post')
    def test_zip_subdirectory_extraction(
        self, mock_post, mock_rmtree, zip_with_subdirectory, tmp_path
    ):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {'stdout': 'nested\n', 'stderr': ''}
        mock_post.return_value = mock_resp

        serializer = CompilerSerializer()
        request_folder = str(tmp_path / 'test_zip_nested')
        os.makedirs(request_folder, exist_ok=True)

        result = serializer._handle_zip(zip_with_subdirectory, None, request_folder)
        assert isinstance(result, Response)
        # Should use the nested 'project' folder as extracted_folder
        call_data = mock_post.call_args
        file_path = call_data[1].get('json', {}).get('file_path', '')
        assert 'project' in file_path


# ---------------------------------------------------------------------------
# _handle_multiple_files
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCompilerSerializerHandleMultipleFiles:

    @patch('Compilers.serializers.shutil.rmtree')
    @patch('Compilers.serializers.requests.post')
    def test_multiple_with_main_py(self, mock_post, mock_rmtree, py_file, helper_py_file, tmp_path):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {'stdout': 'ok\n', 'stderr': ''}
        mock_post.return_value = mock_resp

        serializer = CompilerSerializer()
        request_folder = str(tmp_path / 'multi_py')
        os.makedirs(request_folder, exist_ok=True)

        result = serializer._handle_multiple_files(
            [py_file, helper_py_file], None, request_folder
        )
        assert isinstance(result, Response)
        assert 'python-compiler' in mock_post.call_args[0][0]

    @patch('Compilers.serializers.shutil.rmtree')
    @patch('Compilers.serializers.requests.post')
    def test_multiple_with_main_c(self, mock_post, mock_rmtree, c_file, helper_c_file, tmp_path):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {'stdout': '', 'stderr': ''}
        mock_post.return_value = mock_resp

        serializer = CompilerSerializer()
        request_folder = str(tmp_path / 'multi_c')
        os.makedirs(request_folder, exist_ok=True)

        result = serializer._handle_multiple_files(
            [c_file, helper_c_file], None, request_folder
        )
        assert isinstance(result, Response)
        assert 'c-compiler' in mock_post.call_args[0][0]

    def test_multiple_no_main_returns_error(self, helper_py_file, tmp_path):
        helper2 = SimpleUploadedFile('utils.py', b'y = 2\n', content_type='text/plain')

        serializer = CompilerSerializer()
        request_folder = str(tmp_path / 'multi_nomain')
        os.makedirs(request_folder, exist_ok=True)

        result = serializer._handle_multiple_files(
            [helper_py_file, helper2], None, request_folder
        )
        assert isinstance(result, Response)
        assert 'main file' in str(result.data).lower()

    def test_multiple_unsupported_main_extension(self, java_file, helper_py_file, tmp_path):
        # main.java has name='Main.java', but the code looks for name.split('.')[0] == 'main'
        # Since name is 'Main.java' (capital M), it won't match 'main'. Use lowercase.
        main_java = SimpleUploadedFile('main.java', b'public class main{}', content_type='text/plain')

        serializer = CompilerSerializer()
        request_folder = str(tmp_path / 'multi_java')
        os.makedirs(request_folder, exist_ok=True)

        result = serializer._handle_multiple_files(
            [main_java, helper_py_file], None, request_folder
        )
        assert isinstance(result, Response)
        assert 'not supported' in str(result.data).lower() or 'Languages' in str(result.data)
